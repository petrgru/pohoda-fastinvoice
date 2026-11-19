"""Supplier registration during parsing - the concurrent-creation race.

Two documents from one supplier can be parsed side by side (inline mode runs
the parse in each upload request's background task). Both sessions look the
supplier up, see nothing, and insert - the loser must adopt the winner's row
instead of poisoning its transaction with a UniqueViolation.
"""

import pytest
from sqlmodel import Session

from app.models import Supplier
from app.services import account_integrity
from tests.utils.invoice import create_invoice

RACE_ICO = "86846904"


def test_upsert_adopts_a_supplier_created_in_parallel(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = Supplier(name="Race Winner s.r.o.", ico=RACE_ICO, bank_accounts=[])
    db.add(winner)
    db.commit()

    invoice = create_invoice(
        db,
        supplier_ico=RACE_ICO,
        supplier_name="Race Winner s.r.o.",
        supplier_bank_account="123456789/0100",
    )

    # first lookup behaves as if the winner had not committed yet - exactly
    # the interleaving that produced the stuck PARSING document
    real = account_integrity.find_supplier
    lookups = 0

    def blind_once(session: Session, inv: object) -> Supplier | None:
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return real(session, inv)  # type: ignore[arg-type]

    monkeypatch.setattr(account_integrity, "find_supplier", blind_once)

    supplier = account_integrity.upsert_from_invoice(db, invoice)
    db.commit()

    assert supplier is not None
    assert supplier.id == winner.id

    # the loser's accounts are merged into the adopted row
    db.expire_all()
    stored = db.get(Supplier, winner.id)
    assert stored is not None
    accounts = {row.account for row in account_integrity._known_rows(stored)}
    assert accounts == {"123456789/0100"}
