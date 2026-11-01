"""invoice.supplier_bank_accounts (all payment accounts parsed from the document)

Revision ID: a5c8e9f37d24
Revises: f2d6a8c45b17
Create Date: 2026-10-31 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = 'a5c8e9f37d24'
down_revision = 'f2d6a8c45b17'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'invoice',
        sa.Column('supplier_bank_accounts', sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column('invoice', 'supplier_bank_accounts')
