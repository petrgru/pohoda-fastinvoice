"""external document number and API key suffix

Revision ID: d9e4a7c25f81
Revises: b7f2c9a41d68
Create Date: 2026-11-16 21:10:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "d9e4a7c25f81"
down_revision = "b7f2c9a41d68"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "external_number",
                sqlmodel.sql.sqltypes.AutoString(length=64),
                nullable=True,
            )
        )

    with op.batch_alter_table("recipientapikey", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "key_suffix",
                sqlmodel.sql.sqltypes.AutoString(length=12),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("recipientapikey", schema=None) as batch_op:
        batch_op.drop_column("key_suffix")

    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.drop_column("external_number")
