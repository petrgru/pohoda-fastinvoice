"""drop item table (feature removed)

Revision ID: b7d4f2a19c3e
Revises: 8ca830e0e596
Create Date: 2026-10-31 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7d4f2a19c3e'
down_revision = '8ca830e0e596'
branch_labels = None
depends_on = None


def upgrade():
    # The item feature was removed; drop the table (its FK to "user" and the
    # associated indexes/index conventions go with it in Postgres).
    op.drop_table('item')


def downgrade():
    op.create_table(
        'item',
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
