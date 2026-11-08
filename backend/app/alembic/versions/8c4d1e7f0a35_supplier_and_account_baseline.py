"""supplier registry and invoice account baselines

Adds the Supplier table (with a backfill from existing invoices), the
per-invoice parsed_snapshot document baseline, and account_confirmed_at.

Revision ID: 8c4d1e7f0a35
Revises: 5e1c4f9a2b7d
Create Date: 2026-11-07 21:30:00.000000

"""

import sqlalchemy as sa
from sqlmodel.sql.sqltypes import AutoString
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "8c4d1e7f0a35"
down_revision = "5e1c4f9a2b7d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "invoice",
        sa.Column("parsed_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "invoice",
        sa.Column("account_confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "supplier",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", AutoString(length=255), nullable=False),
        sa.Column("ico", AutoString(length=8), nullable=True),
        sa.Column("dic", AutoString(length=15), nullable=True),
        sa.Column("bank_accounts", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ico"),
    )
    op.create_index(op.f("ix_supplier_name"), "supplier", ["name"], unique=False)
    op.create_index(op.f("ix_supplier_ico"), "supplier", ["ico"], unique=True)

    # backfill the registry from the invoices already in the database
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT supplier_ico, supplier_name, supplier_dic, supplier_bank_accounts "
            "FROM invoice WHERE supplier_ico IS NOT NULL OR supplier_name IS NOT NULL"
        )
    ).fetchall()
    seen: dict[str, dict] = {}
    for ico, name, dic, accounts in rows:
        key = ico or name
        if not key:
            continue
        entry = seen.setdefault(
            key, {"ico": ico, "name": name or ico, "dic": dic, "accounts": {}}
        )
        for row in accounts or []:
            value = row.get("account") or row.get("iban")
            if value:
                entry["accounts"][value] = False
    import json

    import uuid as uuid_module

    for entry in seen.values():
        conn.execute(
            sa.text(
                "INSERT INTO supplier (id, name, ico, dic, bank_accounts, created_at, updated_at) "
                "VALUES (:id, :name, :ico, :dic, CAST(:accounts AS json), now(), now())"
            ),
            {
                "id": uuid_module.uuid4(),
                "name": entry["name"][:255],
                "ico": entry["ico"],
                "dic": entry["dic"],
                "accounts": json.dumps(
                    [
                        {"account": value, "iban": None, "confirmed": confirmed}
                        for value, confirmed in entry["accounts"].items()
                    ]
                ),
            },
        )


def downgrade():
    op.drop_index(op.f("ix_supplier_ico"), table_name="supplier")
    op.drop_index(op.f("ix_supplier_name"), table_name="supplier")
    op.drop_table("supplier")
    op.drop_column("invoice", "account_confirmed_at")
    op.drop_column("invoice", "parsed_snapshot")
