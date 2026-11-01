"""invoice recipient fields

Revision ID: 3f2a6c81b5d4
Revises: 7d3b91ae4c02
Create Date: 2026-11-01 21:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = '3f2a6c81b5d4'
down_revision = '7d3b91ae4c02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('invoice', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recipient_name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True))
        batch_op.add_column(sa.Column('recipient_ico', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=True))
        batch_op.add_column(sa.Column('recipient_dic', sqlmodel.sql.sqltypes.AutoString(length=15), nullable=True))

    # Company now means the document recipient (the user's own company);
    # assignments made under the old supplier meaning are dropped so every
    # non-exported invoice goes through the review screen again.
    op.execute(
        "UPDATE invoice SET company_id = NULL, state = 'needs_review' "
        "WHERE state <> 'exported'"
    )


def downgrade():
    with op.batch_alter_table('invoice', schema=None) as batch_op:
        batch_op.drop_column('recipient_dic')
        batch_op.drop_column('recipient_ico')
        batch_op.drop_column('recipient_name')
