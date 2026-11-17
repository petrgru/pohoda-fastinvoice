"""erp connector sync fields and heartbeat table

Revision ID: c4a9f0b27e53
Revises: f6c2d8a91b47
Create Date: 2026-11-17 17:30:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c4a9f0b27e53"
down_revision = "f6c2d8a91b47"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "erp_remote_id",
                sqlmodel.sql.sqltypes.AutoString(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("erp_error_message", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "export_retry_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "export_claimed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )

    with op.batch_alter_table("supplier", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "erp_remote_id",
                sqlmodel.sql.sqltypes.AutoString(length=64),
                nullable=True,
            )
        )

    op.create_table(
        "erpconnection",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "system", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False
        ),
        sa.Column(
            "version", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True
        ),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("queue_size", sa.Integer(), nullable=True),
        sa.Column(
            "last_error", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["recipient_company_id"],
            ["recipientcompany.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_erpconnection_recipient_company_id"),
        "erpconnection",
        ["recipient_company_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        op.f("ix_erpconnection_recipient_company_id"), table_name="erpconnection"
    )
    op.drop_table("erpconnection")

    with op.batch_alter_table("supplier", schema=None) as batch_op:
        batch_op.drop_column("erp_remote_id")

    with op.batch_alter_table("invoice", schema=None) as batch_op:
        batch_op.drop_column("export_claimed_at")
        batch_op.drop_column("export_retry_count")
        batch_op.drop_column("erp_error_message")
        batch_op.drop_column("erp_remote_id")
