"""RecipientCompany helpers shared by CRUD routes, invoice->company and Pohoda sync."""

from sqlmodel import Session, col, delete

from app.models import BankAccount, BankAccountCreate, RecipientCompany


def apply_bank_accounts(
    session: Session, company: RecipientCompany, rows: list[BankAccountCreate] | None
) -> None:
    """Replace the company's bank account rows (None keeps them untouched)."""
    if rows is None:
        return
    session.exec(
        delete(BankAccount).where(col(BankAccount.recipient_company_id) == company.id)
    )
    for position, row in enumerate(rows):
        if not row.account and not row.iban:
            continue
        session.add(
            BankAccount(
                recipient_company_id=company.id,
                account=row.account,
                iban=row.iban,
                position=position,
            )
        )
    if not company.bank_account:
        first = next((row.account for row in rows if row.account), None)
        if first:
            company.bank_account = first
