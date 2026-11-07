"""rename company tables to recipientcompany

The Company entity became the document recipient (the user's own company),
so it gets its own clearly-named tables and a recipient_company_id column
everywhere it was called company_id.

Revision ID: 5e1c4f9a2b7d
Revises: 3f2a6c81b5d4
Create Date: 2026-11-02 09:15:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = '5e1c4f9a2b7d'
down_revision = '3f2a6c81b5d4'
branch_labels = None
depends_on = None


RENAMES = [
    ("company", "recipientcompany"),
    ("companymembership", "recipientmembership"),
    ("companyapikey", "recipientapikey"),
    ("company_bank_account", "recipient_bank_account"),
]

TABLES = [
    "invoice",
    "auditlog",
    "recipientmembership",
    "recipientapikey",
    "recipient_bank_account",
]


def upgrade():
    for old, new in RENAMES:
        op.rename_table(old, new)
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "company_id",
                new_column_name="recipient_company_id",
            )


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "recipient_company_id",
                new_column_name="company_id",
            )
    for old, new in reversed(RENAMES):
        op.rename_table(new, old)
