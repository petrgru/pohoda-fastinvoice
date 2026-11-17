"""supplier email, payment method, KH date, company email, user column prefs

Revision ID: b7f2c9a41d68
Revises: 8c4d1e7f0a35
Create Date: 2026-11-16 18:40:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7f2c9a41d68"
down_revision = "8c4d1e7f0a35"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "supplier_email",
                sqlmodel.sql.sqltypes.AutoString(length=255),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "payment_method",
                sqlmodel.sql.sqltypes.AutoString(length=20),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("kh_date", sa.Date(), nullable=True))

    with op.batch_alter_table("recipientcompany", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "email",
                sqlmodel.sql.sqltypes.AutoString(length=255),
                nullable=True,
            )
        )

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("invoice_columns", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("invoice_columns")

    with op.batch_alter_table("recipientcompany", schema=None) as batch_op:
        batch_op.drop_column("email")

    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.drop_column("kh_date")
        batch_op.drop_column("payment_method")
        batch_op.drop_column("supplier_email")
