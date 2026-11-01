"""add company.address (filled from VAT registry lookup)

Revision ID: c4a9e7d31f82
Revises: b7d4f2a19c3e
Create Date: 2026-10-31 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = 'c4a9e7d31f82'
down_revision = 'b7d4f2a19c3e'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'company',
        sa.Column(
            'address',
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column('company', 'address')
