"""Payload building and result handling for the accounting connector API.

The Windows connector service pulls batches of invoices (with their
suppliers) here, replays them into the accounting system (Pohoda mServer
XML today, other ERPs later) and reports the outcome per record. All state
transitions stay in `app.services.invoices`; this module only translates
between the connector's results and those transitions.
"""

import logging
import re

from sqlmodel import Session, select

from app.models import (
    ErpBatchOut,
    ErpInvoicePayload,
    ErpSupplierPayload,
    ErpSyncItem,
    Invoice,
    InvoiceState,
    RecipientCompany,
    Supplier,
    SupplierBankAccount,
    get_datetime_utc,
)
from app.services import invoices as invoice_service

logger = logging.getLogger(__name__)

# the connector downloads the document through the read-only data API
ATTACHMENT_URL = "/api/v1/data/invoices/{invoice_id}/file"

# the Czech variable symbol carries at most 8 digits
VARIABLE_SYMBOL_MAX = 8


def derive_variable_symbol(invoice_number: str | None) -> str | None:
    """VS derived from the document number: digits only, last 8 of them."""
    digits = re.sub(r"\D", "", invoice_number or "")
    if not digits:
        return None
    return digits[-VARIABLE_SYMBOL_MAX:].lstrip() or None


def _supplier_payload(session: Session, invoice: Invoice) -> ErpSupplierPayload:
    registry: Supplier | None = None
    if invoice.supplier_ico:
        registry = session.exec(
            select(Supplier).where(Supplier.ico == invoice.supplier_ico)
        ).first()
    elif invoice.supplier_name:
        registry = session.exec(
            select(Supplier).where(Supplier.name == invoice.supplier_name)
        ).first()
    accounts = [
        SupplierBankAccount.model_validate(row, from_attributes=True)
        for row in invoice.supplier_bank_accounts or []
    ]
    return ErpSupplierPayload(
        id=registry.id if registry else None,
        erp_remote_id=registry.erp_remote_id if registry else None,
        name=invoice.supplier_name,
        ico=invoice.supplier_ico,
        dic=invoice.supplier_dic,
        email=invoice.supplier_email,
        bank_accounts=accounts,
    )


def build_batch(
    session: Session, company: RecipientCompany, invoices: list[Invoice]
) -> ErpBatchOut:
    """Compose the batch payload; suppliers are listed before the invoices."""
    batch = ErpBatchOut(
        company_name=company.name,
        company_ico=company.ico,
        claimed_at=get_datetime_utc(),
    )
    seen_suppliers: set[tuple[str | None, str | None]] = set()
    for invoice in invoices:
        supplier = _supplier_payload(session, invoice)
        key = (supplier.ico, supplier.name)
        if key not in seen_suppliers:
            seen_suppliers.add(key)
            batch.suppliers.append(supplier)
        batch.invoices.append(
            ErpInvoicePayload(
                id=invoice.id,
                invoice_number=invoice.invoice_number,
                variable_symbol=derive_variable_symbol(invoice.invoice_number),
                document_type=invoice.document_type,
                issue_date=invoice.issue_date,
                delivery_date=invoice.delivery_date,
                due_date=invoice.due_date,
                kh_date=invoice.kh_date,
                payment_method=invoice.payment_method,
                currency=invoice.currency,
                total_price=invoice.total_price,
                total_vat=invoice.total_vat,
                total_without_vat=invoice.total_without_vat,
                vat_items=list(invoice.vat_items or []),
                supplier=supplier,
                erp_remote_id=invoice.erp_remote_id,
                external_number=invoice.external_number,
                retry_count=invoice.export_retry_count,
                attachment_url=ATTACHMENT_URL.format(invoice_id=invoice.id),
            )
        )
    return batch


def _apply_invoice_result(
    session: Session, invoice: Invoice, item: ErpSyncItem
) -> tuple[bool, str | None]:
    state = InvoiceState(invoice.state)
    if item.status == "synced":
        if not item.remote_number:
            return False, "synced vyžaduje remote_number (číslo dokladu z ERP)"
        invoice.erp_remote_id = item.remote_id or invoice.erp_remote_id
        invoice.external_number = item.remote_number
        if state is InvoiceState.EXPORTED:
            session.add(invoice)  # idempotent re-report: only number may change
            return True, None
        invoice_service.record_export_result(session, invoice, success=True)
        return True, None
    if item.status == "retry_later":
        invoice_service.release_export_claim(
            session, invoice, reason="Konektor: ERP nedostupný (zkusí znovu)"
        )
        return True, None
    # error: counts as one automatic attempt only while actually claimed
    if state is InvoiceState.PROCESSING:
        invoice.export_retry_count += 1
        invoice_service.record_export_result(
            session, invoice, success=False, error=item.error_message
        )
        invoice_service.log_audit(
            session,
            "erp_import_error",
            invoice=invoice,
            user=None,
            details={
                "attempt": invoice.export_retry_count,
                "message": item.error_message,
            },
        )
    else:
        invoice.erp_error_message = (item.error_message or "error")[:500]
        session.add(invoice)
    return True, None


def apply_results(
    session: Session, company: RecipientCompany, items: list[ErpSyncItem]
) -> list[tuple[ErpSyncItem, bool, str | None]]:
    """Apply one connector result per item; a failure never stops the batch."""
    outcomes: list[tuple[ErpSyncItem, bool, str | None]] = []
    for item in items:
        if item.entity_type == "supplier":
            supplier = session.get(Supplier, item.local_id)
            if supplier is None:
                outcomes.append((item, False, "dodavatel nenalezen"))
                continue
            if item.status == "synced" and item.remote_id:
                supplier.erp_remote_id = item.remote_id
                session.add(supplier)
            elif item.status == "error":
                logger.info(
                    "supplier %s: connector error: %s", supplier.id, item.error_message
                )
            outcomes.append((item, True, None))
            continue
        invoice = session.get(Invoice, item.local_id)
        if invoice is None or invoice.recipient_company_id != company.id:
            outcomes.append((item, False, "doklad nenalezen v firmě konektoru"))
            continue
        try:
            ok, detail = _apply_invoice_result(session, invoice, item)
        except invoice_service.InvalidTransitionError as exc:
            ok, detail = False, f"neplatný přechod stavu: {exc}"
        outcomes.append((item, ok, detail))
    session.commit()
    return outcomes
