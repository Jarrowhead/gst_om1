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

# Dev default only; prod sets GST_APP_ROLE_PASSWORD via env (env.py injects it
# as `app_role_password` into the alembic config when present).
DEFAULT_APP_ROLE_PASSWORD = "gst_app_dev_pass"  # noqa: S105


def _role_password() -> str:
    import os

    return os.environ.get("GST_APP_ROLE_PASSWORD", DEFAULT_APP_ROLE_PASSWORD)


_ = _role_password  # kept for reference; upgrade() reads the env directly


def _grant_all(schema: str, table: str) -> str:
    return f"GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.{table} TO gst_app"


def _grant_insert_only(schema: str, table: str) -> str:
    return f"GRANT INSERT ON {schema}.{table} TO gst_app"


def upgrade() -> None:
    import os

    conn = op.get_bind()
    password = os.environ.get("GST_APP_ROLE_PASSWORD", DEFAULT_APP_ROLE_PASSWORD)
    # Create/refresh the role (cluster-level; DO block keeps it idempotent).
    conn.exec_driver_sql(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gst_app') THEN
                CREATE ROLE gst_app LOGIN PASSWORD '{password}';
            ELSE
                ALTER ROLE gst_app LOGIN PASSWORD '{password}';
            END IF;
        END $$;
        """
    )
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
