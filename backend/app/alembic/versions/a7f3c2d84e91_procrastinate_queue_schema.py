"""procrastinate task queue schema

Revision ID: a7f3c2d84e91
Revises: d5e8a1c94b67
Create Date: 2026-11-18 10:00:00.000000

"""

from pathlib import Path

from alembic import op
from procrastinate import schema as procrastinate_schema

# revision identifiers, used by Alembic.
revision = "a7f3c2d84e91"
down_revision = "d5e8a1c94b67"
branch_labels = None
depends_on = None

_SQL_DIR = Path(procrastinate_schema.migrations_path).parent


def _execute_script(sql: str) -> None:
    # the schema contains literal % characters (format() calls in plpgsql):
    # run it on the raw driver connection so psycopg does not scan it for
    # placeholders (and the simple protocol executes the whole multi-statement
    # script in one go)
    conn = op.get_bind()
    raw = getattr(conn, "dbapi_connection", conn.connection)
    with raw.cursor() as cursor:
        cursor.execute(sql)


def upgrade():
    # the queue tables, procedures and enums shipped with the pinned
    # procrastinate version; when upgrading the library later, apply the SQL
    # scripts from <_SQL_DIR>/migrations on top (see procrastinate docs,
    # "Migrate the Procrastinate schema")
    _execute_script((_SQL_DIR / "schema.sql").read_text())


def downgrade():
    # remove everything the schema.sql above created (tables take their
    # trigger functions with them, the type/function loops sweep enums,
    # composites and helpers regardless of the library's naming scheme)
    _execute_script(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
            END LOOP;
            FOR r IN
                SELECT typname FROM pg_type
                WHERE typname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
            END LOOP;
            FOR r IN
                SELECT oid::regprocedure AS sig FROM pg_proc
                WHERE proname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP FUNCTION %s CASCADE', r.sig);
            END LOOP;
        END $$;
        """
    )
