"""supplier registry verification fields

Revision ID: c9e1f4a82b56
Revises: a7f3c2d84e91
Create Date: 2026-11-18 13:20:00.000000

"""

import sqlmodel.sql.sqltypes
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9e1f4a82b56"
down_revision = "a7f3c2d84e91"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supplier", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "registry_status",
                sqlmodel.sql.sqltypes.AutoString(length=20),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(
            sa.Column("in_insolvency", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("insolvency_info", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "unreliable_payer", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("registry_data", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("registry_checked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("registry_next_check_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "registry_check_attempts", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column(
                "export_confirmed_manually",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index(
            op.f("ix_supplier_registry_status"),
            ["registry_status"],
            unique=False,
        )

    # rows without an identifier can never be verified and must not gate
    # anything from day one
    op.execute("UPDATE supplier SET registry_status = 'not_checkable' WHERE ico IS NULL")


def downgrade():
    with op.batch_alter_table("supplier", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_supplier_registry_status"))
        batch_op.drop_column("export_confirmed_manually")
        batch_op.drop_column("registry_check_attempts")
        batch_op.drop_column("registry_next_check_at")
        batch_op.drop_column("registry_checked_at")
        batch_op.drop_column("registry_data")
        batch_op.drop_column("unreliable_payer")
        batch_op.drop_column("insolvency_info")
        batch_op.drop_column("in_insolvency")
        batch_op.drop_column("registry_status")
