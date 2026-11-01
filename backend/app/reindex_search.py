"""Rebuild the Meilisearch invoice index from the database.

Usage: uv run python app/reindex_search.py
"""

import logging

from sqlmodel import Session

from app.core.db import engine
from app.services import search_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    with Session(engine) as session:
        count = search_index.reindex_all(session)
    logger.info("indexed %d invoices into %s", count, search_index.INDEX)


if __name__ == "__main__":
    main()
