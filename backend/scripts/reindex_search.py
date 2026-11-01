"""One-off reindex of invoice full-text search vectors.

Run after deploying changes to `update_search_vector` so existing rows pick
up the newly indexed fields:

    uv run python scripts/reindex_search.py
"""

from sqlmodel import Session, select

from app.core.db import engine
from app.models import Invoice
from app.services import invoices as invoice_service


def main() -> None:
    with Session(engine) as session:
        ids = session.exec(select(Invoice.id)).all()
        for invoice_id in ids:
            invoice = session.get(Invoice, invoice_id)
            if invoice is not None:
                invoice_service.update_search_vector(session, invoice)
    print(f"Reindexed {len(ids)} invoices")


if __name__ == "__main__":
    main()
