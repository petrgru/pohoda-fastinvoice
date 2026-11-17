"""one active API key per company

Revision ID: e5a1b3d74c09
Revises: d9e4a7c25f81
Create Date: 2026-11-16 22:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e5a1b3d74c09"
down_revision = "d9e4a7c25f81"
branch_labels = None
depends_on = None


def upgrade():
    # keep only the newest active key of each company active
    op.execute(
        """
        UPDATE recipientapikey
        SET revoked_at = COALESCE(created_at, CURRENT_TIMESTAMP)
        WHERE revoked_at IS NULL
          AND id IN (
            SELECT older.id
            FROM recipientapikey older
            WHERE older.revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM recipientapikey newer
                WHERE newer.recipient_company_id = older.recipient_company_id
                  AND newer.revoked_at IS NULL
                  AND (
                    newer.created_at > older.created_at
                    OR (
                      newer.created_at = older.created_at
                      AND newer.id > older.id
                    )
                  )
              )
          )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_key_active_per_company "
        "ON recipientapikey (recipient_company_id) WHERE revoked_at IS NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_api_key_active_per_company")
