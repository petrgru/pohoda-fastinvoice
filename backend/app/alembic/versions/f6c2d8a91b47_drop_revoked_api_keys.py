"""drop revoked API keys – revoked keys are deleted, not kept

Revision ID: f6c2d8a91b47
Revises: e5a1b3d74c09
Create Date: 2026-11-17 08:20:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f6c2d8a91b47"
down_revision = "e5a1b3d74c09"
branch_labels = None
depends_on = None


def upgrade():
    # revocation now deletes the row; clean up the historical leftovers
    op.execute("DELETE FROM recipientapikey WHERE revoked_at IS NOT NULL")


def downgrade():
    # deleted keys cannot be restored (only the audit log remembers them)
    pass
