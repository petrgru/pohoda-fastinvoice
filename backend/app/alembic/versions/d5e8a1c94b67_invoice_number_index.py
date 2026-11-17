"""invoice number index for duplicate detection

Revision ID: d5e8a1c94b67
Revises: c4a9f0b27e53
Create Date: 2026-11-17 19:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d5e8a1c94b67"
down_revision = "c4a9f0b27e53"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        op.f("ix_invoice_invoice_number"), "invoice", ["invoice_number"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_invoice_invoice_number"), table_name="invoice")
