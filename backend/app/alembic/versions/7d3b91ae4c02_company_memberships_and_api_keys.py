"""company memberships and api keys

Revision ID: 7d3b91ae4c02
Revises: a5c8e9f37d24
Create Date: 2026-11-01 19:55:00.000000

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = '7d3b91ae4c02'
down_revision = 'a5c8e9f37d24'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'companymembership',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('role', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'company_id'),
    )
    op.create_index(op.f('ix_companymembership_company_id'), 'companymembership', ['company_id'], unique=False)
    op.create_index(op.f('ix_companymembership_user_id'), 'companymembership', ['user_id'], unique=False)

    op.create_table(
        'companyapikey',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('label', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('key_prefix', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('key_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('created_by_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_companyapikey_company_id'), 'companyapikey', ['company_id'], unique=False)
    op.create_index(op.f('ix_companyapikey_key_hash'), 'companyapikey', ['key_hash'], unique=True)

    with op.batch_alter_table('invoice', schema=None) as batch_op:
        batch_op.add_column(sa.Column('uploaded_by_id', sa.Uuid(), nullable=True))
        batch_op.create_index(
            op.f('ix_invoice_uploaded_by_id'), ['uploaded_by_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_invoice_uploaded_by_id', 'user',
            ['uploaded_by_id'], ['id'], ondelete='SET NULL',
        )

    # attribute historical uploads to the user who recorded them
    op.execute(
        """
        UPDATE invoice
        SET uploaded_by_id = a.user_id
        FROM (
            SELECT DISTINCT ON (invoice_id) invoice_id, user_id
            FROM auditlog
            WHERE action = 'uploaded' AND user_id IS NOT NULL
            ORDER BY invoice_id, created_at
        ) a
        WHERE invoice.id = a.invoice_id
        """
    )


def downgrade():
    with op.batch_alter_table('invoice', schema=None) as batch_op:
        batch_op.drop_constraint('fk_invoice_uploaded_by_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_invoice_uploaded_by_id'))
        batch_op.drop_column('uploaded_by_id')

    op.drop_index(op.f('ix_companyapikey_key_hash'), table_name='companyapikey')
    op.drop_index(op.f('ix_companyapikey_company_id'), table_name='companyapikey')
    op.drop_table('companyapikey')
    op.drop_index(op.f('ix_companymembership_user_id'), table_name='companymembership')
    op.drop_index(op.f('ix_companymembership_company_id'), table_name='companymembership')
    op.drop_table('companymembership')
