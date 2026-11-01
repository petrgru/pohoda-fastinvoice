"""One-off reindex of invoice full-text search (both index layers).

Run after deploying changes to `update_search_vector` or after (re)installing
Meilisearch, so existing rows and the mirror are rebuilt from scratch:

    uv run python scripts/reindex_search.py
"""

import logging

from sqlmodel import Session, select

from app.core.db import engine
from app.models import Invoice
from app.services import invoices as invoice_service
from app.services import search_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    with Session(engine) as session:
        ids = session.exec(select(Invoice.id)).all()
        # rebuild the Meilisearch index from scratch first: drops ghost
        # documents of invoices deleted while the mirror was unreachable
        indexed = search_index.reindex_all(session)
        # rebuild the Postgres tsvector column
        for invoice_id in ids:
            invoice = session.get(Invoice, invoice_id)
            if invoice is not None:
                invoice_service.update_search_vector(session, invoice)
    logger.info("Reindexed %d invoices (%d mirrored to Meilisearch)", len(ids), indexed)


if __name__ == "__main__":
    main()
