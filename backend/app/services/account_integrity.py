"""Bank-account integrity: supplier registry + invoice fraud detection.

Two baselines exist for the accounts of an invoice supplier:

* the **document** – what parsing extracted from the imported file
  (``Invoice.parsed_snapshot``),
* the supplier **history** – accounts of earlier invoices from the same
  supplier; entries the user explicitly confirmed are the reference.

A saved invoice whose account deviates from either baseline must be saved with
``confirm_account_change`` – the UI shows a tampering warning first. Confirming
promotes the account in the supplier registry to ``confirmed``, so later
invoices are compared against it.
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import (
    Invoice,
    Supplier,
    SupplierKnownAccount,
    User,
    get_datetime_utc,
    normalize_bank_account,
)
from app.services import invoices as invoice_service

ACCOUNT_FIELDS = ("supplier_bank_account", "supplier_iban", "supplier_bank_accounts")


def _normalize(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        return normalize_bank_account(value)
    except ValueError:
        return value.replace(" ", "").upper()


def accounts_of(invoice: Invoice) -> set[str]:
    """Normalized account/IBAN set currently stored on the invoice."""
    rows: list[Any] = []
    if invoice.supplier_bank_account:
        rows.append(invoice.supplier_bank_account)
    if invoice.supplier_iban:
        rows.append(invoice.supplier_iban)
    for row in invoice.supplier_bank_accounts or []:
        rows.extend([row.get("account"), row.get("iban")])
    return {n for value in rows if (n := _normalize(value))}


def document_accounts(invoice: Invoice) -> set[str] | None:
    """Normalized accounts as they were parsed from the document."""
    snapshot = invoice.parsed_snapshot
    if snapshot is None:
        return None
    rows: list[Any] = [
        snapshot.get("supplier_bank_account"),
        snapshot.get("supplier_iban"),
    ]
    for row in snapshot.get("supplier_bank_accounts") or []:
        rows.extend([row.get("account"), row.get("iban")])
    return {n for value in rows if (n := _normalize(value))}


def matches_document(invoice: Invoice) -> bool | None:
    """None when the document baseline is unknown or names no account."""
    document = document_accounts(invoice)
    current = accounts_of(invoice)
    if document is None or not document or not current:
        return None
    return current == document


def find_supplier(session: Session, invoice: Invoice) -> Supplier | None:
    """The registry dedup key: IČO when the invoice carries one, name only
    when it does not (a name match across different IČOs would be a false link)."""
    if invoice.supplier_ico:
        return session.exec(
            select(Supplier).where(col(Supplier.ico) == invoice.supplier_ico)
        ).first()
    if invoice.supplier_name:
        return session.exec(
            select(Supplier).where(col(Supplier.name) == invoice.supplier_name)
        ).first()
    return None


def _known_rows(supplier: Supplier) -> list[SupplierKnownAccount]:
    return [
        SupplierKnownAccount.model_validate(row) for row in supplier.bank_accounts or []
    ]


def known_accounts(supplier: Supplier) -> dict[str, bool]:
    """normalized account -> confirmed flag for the supplier registry."""
    known: dict[str, bool] = {}
    for row in _known_rows(supplier):
        for value in (row.account, row.iban):
            if key := _normalize(value):
                known[key] = known.get(key, False) or row.confirmed
    return known


def matches_supplier(session: Session, invoice: Invoice) -> bool | None:
    """None when the supplier or its history is unknown; else subset check.

    An invoice with no account at all cannot mismatch anything.
    """
    current = accounts_of(invoice)
    if not current:
        return None
    supplier = find_supplier(session, invoice)
    if supplier is None:
        return None
    confirmed = {
        acc for acc, was_confirmed in known_accounts(supplier).items() if was_confirmed
    }
    if not confirmed:
        return None  # nothing user-verified yet -> no signal either way
    return current <= confirmed


def upsert_from_invoice(session: Session, invoice: Invoice) -> Supplier | None:
    """Register/refresh the invoice's supplier after parsing (never downgrades
    confirmed accounts). Returns the supplier, or None for account-less rows."""
    if not invoice.supplier_name and not invoice.supplier_ico:
        return None
    supplier = find_supplier(session, invoice)
    if supplier is None:
        supplier = Supplier(
            name=(invoice.supplier_name or invoice.supplier_ico or "?")[:255],
            ico=invoice.supplier_ico or None,
            dic=invoice.supplier_dic,
            bank_accounts=[],
        )
        session.add(supplier)
        session.flush()
    else:
        supplier.updated_at = get_datetime_utc()
        if invoice.supplier_name and supplier.name != invoice.supplier_name:
            supplier.name = invoice.supplier_name
        if invoice.supplier_dic and not supplier.dic:
            supplier.dic = invoice.supplier_dic
    rows = _known_rows(supplier)
    seen = {
        k for row in rows for k in (_normalize(row.account), _normalize(row.iban)) if k
    }
    for value in sorted(accounts_of(invoice) - seen):
        rows.append(SupplierKnownAccount.model_validate({"account": value}))
    supplier.bank_accounts = [row.model_dump(mode="json") for row in rows]
    session.add(supplier)
    return supplier


def require_confirmation(
    session: Session, invoice: Invoice, changes: dict[str, Any], *, confirmed: bool
) -> None:
    """Raise 409 when the proposed accounts deviate and were not confirmed."""
    if confirmed or not any(field in changes for field in ACCOUNT_FIELDS):
        return
    proposed = Invoice.model_validate(
        {
            **invoice.model_dump(),
            **{k: v for k, v in changes.items() if k in ACCOUNT_FIELDS},
        },
        from_attributes=False,
    )
    current = accounts_of(invoice)
    proposed_accounts = accounts_of(proposed)
    if proposed_accounts == current:
        return  # no actual change in this save – nothing to confirm
    document = document_accounts(invoice)
    if document and proposed_accounts != current and proposed_accounts != document:
        raise HTTPException(status_code=409, detail="errors.account_changed_confirm")
    supplier = find_supplier(session, invoice)
    if supplier is not None:
        confirmed_accounts = {acc for acc, ok in known_accounts(supplier).items() if ok}
        if (
            confirmed_accounts
            and proposed_accounts != current
            and not proposed_accounts <= confirmed_accounts
        ):
            raise HTTPException(
                status_code=409, detail="errors.account_changed_confirm"
            )


def confirm(session: Session, invoice: Invoice, *, user: User) -> None:
    """Record the user's confirmation and promote the accounts in the registry."""
    invoice.account_confirmed_at = get_datetime_utc()
    session.add(invoice)
    supplier = find_supplier(session, invoice)
    if supplier is not None:
        rows = _known_rows(supplier)
        current = accounts_of(invoice)
        # confirm the rows that already carry one of the invoice's accounts
        for row in rows:
            if any(
                (n := _normalize(v)) and n in current for v in (row.account, row.iban)
            ):
                row.confirmed = True
        seen = {
            key
            for row in rows
            for key in (_normalize(row.account), _normalize(row.iban))
            if key
        }
        # and register the remaining ones as confirmed new entries
        for value in sorted(current - seen):
            rows.append(
                SupplierKnownAccount.model_validate(
                    {"account": value, "confirmed": True}
                )
            )
        supplier.bank_accounts = [row.model_dump(mode="json") for row in rows]
        session.add(supplier)
    invoice_service.log_audit(
        session,
        "account_change_confirmed",
        invoice=invoice,
        user=user,
        details={
            "from": sorted(document_accounts(invoice) or []),
            "to": sorted(accounts_of(invoice)),
        },
    )


def supplier_public_extras(session: Session, supplier: Supplier) -> tuple[int, Any]:
    """(invoice count, last invoice date) – kept simple: all invoices."""
    if supplier.ico:
        condition = col(Invoice.supplier_ico) == supplier.ico
    else:
        condition = col(Invoice.supplier_name) == supplier.name
    row = session.exec(
        select(func.count(), func.max(Invoice.created_at)).where(condition)
    ).one()
    return int(row[0] or 0), row[1]
