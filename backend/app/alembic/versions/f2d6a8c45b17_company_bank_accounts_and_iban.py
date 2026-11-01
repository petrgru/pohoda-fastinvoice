"""company bank accounts (multiple account/IBAN rows) + invoice.supplier_iban

Revision ID: f2d6a8c45b17
Revises: c4a9e7d31f82
Create Date: 2026-10-31 15:00:00.000000

"""
import uuid

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision = 'f2d6a8c45b17'
down_revision = 'c4a9e7d31f82'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'company_bank_account',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('account', sqlmodel.sql.sqltypes.AutoString(length=40), nullable=True),
        sa.Column('iban', sqlmodel.sql.sqltypes.AutoString(length=34), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_company_bank_account_company_id'),
        'company_bank_account',
        ['company_id'],
        unique=False,
    )
    op.add_column(
        'invoice',
        sa.Column(
            'supplier_iban',
            sqlmodel.sql.sqltypes.AutoString(length=34),
            nullable=True,
        ),
    )
    # keep the existing single account reachable as the primary row
    company_table = sa.table(
        'company',
        sa.column('id', sa.Uuid),
        sa.column('bank_account', sa.String),
    )
    account_table = sa.table(
        'company_bank_account',
        sa.column('id', sa.Uuid),
        sa.column('company_id', sa.Uuid),
        sa.column('position', sa.Integer),
        sa.column('account', sa.String),
        sa.column('iban', sa.String),
    )
    rows = sa.select(
        company_table.c.id, company_table.c.bank_account
    ).where(company_table.c.bank_account.is_not(None))
    existing = op.get_bind().execute(rows).all()
    if existing:
        op.get_bind().execute(
            account_table.insert(),
            [
                {
                    'id': uuid.uuid4(),
                    'company_id': company_id,
                    'position': 0,
                    'account': bank_account,
                    'iban': None,
                }
                for company_id, bank_account in existing
            ],
        )


def downgrade():
    op.drop_column('invoice', 'supplier_iban')
    op.drop_index(
        op.f('ix_company_bank_account_company_id'), table_name='company_bank_account'
    )
    op.drop_table('company_bank_account')
