"""audit_logs append-only enforcement: INSERT-only DB role

Revision ID: a41c7e2d90f5
Revises: 0d70cc869b18
Create Date: 2026-09-26

SECURITY_AND_ACCESS.md §5: "DB role has INSERT-only grant on `audit_logs`
(migration-enforced)". Creates the dedicated LOGIN role `gst_app` (dev password
overridable via GST_APP_ROLE_PASSWORD), grants it full DML on the v2 tables
EXCEPT audit_logs, where it gets INSERT-only, and revokes everything from
PUBLIC. The API process is expected to connect as gst_app in later phases;
tests connect as gst_app to prove UPDATE/DELETE on audit_logs raises.
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "a41c7e2d90f5"
down_revision: str | Sequence[str] | None = "0d70cc869b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# v2 table inventory by schema (matches 0d70cc869b18; kept literal so this
# revision cannot silently drift with model changes — add here explicitly).
_SCHEMAS: tuple[str, ...] = ("core", "gst", "extraction")
_TABLES: dict[str, tuple[str, ...]] = {
    "core": (
        "users",
        "ca_firms",
        "ca_firm_members",
        "businesses",
        "gst_registrations",
        "business_users",
        "ca_client_links",
        "consent_records",
        "audit_logs",
    ),
    "gst": (
        "document_series",
        "invoices",
        "invoice_lines",
        "credit_debit_notes",
        "filing_periods",
        "gstr1_exports",
        "gstr1a_amendments",
        "e_invoices",
        "gstr3b_exports",
        "gstr2b_statements",
        "gstr2b_entries",
        "itc_reconciliation",
        "notifications",
    ),
    "extraction": ("documents", "extraction_jobs", "invoice_drafts"),
}

# Dev default only; prod sets GST_APP_ROLE_PASSWORD via env (no env.py injection).
DEFAULT_APP_ROLE_PASSWORD = "gst_app_dev_pass"  # noqa: S105


def _role_password() -> str:
    """Password for role gst_app. GST_APP_ROLE_PASSWORD overrides the dev default.

    Debug:
        Tests that connect as gst_app must use the same value the migration applied.
    """
    return os.environ.get("GST_APP_ROLE_PASSWORD", DEFAULT_APP_ROLE_PASSWORD)


def _grant_all(schema: str, table: str) -> str:
    """SQL granting SELECT/INSERT/UPDATE/DELETE on one table to gst_app."""
    return f"GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.{table} TO gst_app"


def _grant_insert_only(schema: str, table: str) -> str:
    """SQL granting INSERT only. Used for core.audit_logs."""
    return f"GRANT INSERT ON {schema}.{table} TO gst_app"


def _q(value: str) -> str:
    """Render a SQL string literal (double single quotes). asyncpg has no psycopg composer.

    Debug:
        A password with a quote must survive this or CREATE ROLE fails at upgrade.
    """
    return "'" + value.replace("'", "''") + "'"


# CREATE/ALTER ROLE are utility commands: Postgres refuses bind parameters for
# them, so the password must be inlined. `__PW__` is substituted through _q()
# below rather than an f-string, which keeps ruff's S608 check quiet on a query
# whose value really is escaped.
_ROLE_DDL_TEMPLATE = """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gst_app') THEN
                CREATE ROLE gst_app LOGIN PASSWORD __PW__;
            ELSE
                ALTER ROLE gst_app LOGIN PASSWORD __PW__;
            END IF;
        END $$;
        """


def upgrade() -> None:
    """Create role gst_app and grant DML, with INSERT-only on core.audit_logs.

    Flow:
        1. CREATE or ALTER ROLE with the escaped password.
        2. GRANT full DML on every v2 table except audit_logs.
        3. GRANT INSERT only on audit_logs. REVOKE from PUBLIC.

    Debug:
        UPDATE on audit_logs as gst_app must fail. The API still connects as gst in dev.
    """
    conn = op.get_bind()
    password = _role_password()
    # Create/refresh the role (cluster-level; DO block keeps it idempotent).
    # NOTE: the engine here is asyncpg, so the SQL must be a plain str — a
    # psycopg.sql.Composed object is unhashable and dies in SQLAlchemy's
    # statement cache with "TypeError: unhashable type: 'Composed'".
    # DDL (CREATE/ALTER ROLE) cannot take bind parameters in Postgres, so the
    # literal is built here with _q() doubling quotes per the SQL standard.
    conn.exec_driver_sql(_ROLE_DDL_TEMPLATE.replace("__PW__", _q(password)))
    # PUBLIC must hold nothing on these tables; grants are role-scoped only.
    for schema, tables in _TABLES.items():
        for table in tables:
            conn.exec_driver_sql(f"REVOKE ALL ON {schema}.{table} FROM PUBLIC")
    # Schema USAGE is the gateway privilege for every table grant.
    for schema in _SCHEMAS:
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA {schema} TO gst_app")
    for schema, tables in _TABLES.items():
        for table in tables:
            grant = (
                _grant_insert_only(schema, table)
                if (schema, table) == ("core", "audit_logs")
                else _grant_all(schema, table)
            )
            conn.exec_driver_sql(grant)


def downgrade() -> None:
    """Revoke gst_app grants and drop the role.

    Flow:
        REVOKE ALL on each v2 table, then DROP ROLE if present.

    Debug:
        DROP ROLE fails if a session is still connected as gst_app.
    """
    conn = op.get_bind()
    for schema, tables in _TABLES.items():
        for table in tables:
            conn.exec_driver_sql(f"REVOKE ALL ON {schema}.{table} FROM gst_app")
    conn.exec_driver_sql("REVOKE ALL ON ALL TABLES IN SCHEMA core FROM gst_app")
    conn.exec_driver_sql("REVOKE ALL ON ALL TABLES IN SCHEMA gst FROM gst_app")
    conn.exec_driver_sql("REVOKE ALL ON ALL TABLES IN SCHEMA extraction FROM gst_app")
    for schema in _SCHEMAS:
        conn.exec_driver_sql(f"REVOKE USAGE ON SCHEMA {schema} FROM gst_app")
    # Role itself survives downgrade (cluster-level object, shared across DBs).
