"""Task 0.3 verification: migration cycle + v2 table inventory.

Proves the task's done_when against the live DB:
  alembic upgrade -> downgrade -> upgrade clean;
  all 25 v2 tables present in core/gst/extraction schemas.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from app.db import base as app_db_base
from app.db.base import Base

from tests.conftest import PG_AVAILABLE, requires_pg

app_db_base.all_models()

BACKEND_DIR = Path(__file__).resolve().parent.parent

EXPECTED_TABLES: dict[str, set[str]] = {
    "core": {
        "users",
        "ca_firms",
        "ca_firm_members",
        "businesses",
        "gst_registrations",
        "business_users",
        "ca_client_links",
        "consent_records",
        "audit_logs",
    },
    "gst": {
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
    },
    "extraction": {
        "documents",
        "extraction_jobs",
        "invoice_drafts",
    },
}


UV_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "bin" / "uv.exe"


def _alembic(*args: str) -> int:
    """Run alembic CLI via uv in a subprocess (fresh interpreter, no event loop).

    Flow:
        uv run alembic <args> with cwd backend. Non-zero → pytest.fail with output.

    Debug:
        Missing uv.exe under LOCALAPPDATA/hermes/bin fails before Alembic starts.
    """
    proc = subprocess.run(
        [str(UV_EXE), "run", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.returncode


def _table_counts_by_schema() -> dict[str, int]:
    """Count tables per schema from pg_catalog on the live dev database.

    Debug:
        Zero counts mean Alembic has not been applied to gst_filing_db.
    """
    import os

    from sqlalchemy import create_engine, text

    url = os.environ["GST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT schemaname, count(*) FROM pg_tables "
                    "WHERE schemaname IN ('core','gst','extraction') "
                    "GROUP BY schemaname"
                )
            ).fetchall()
            return {schema: count for schema, count in rows}
    finally:
        engine.dispose()


def _tables_in_schema(schema: str) -> set[str]:
    """Set of table names in one schema (core, gst, or extraction).

    Debug:
        Compare against EXPECTED sets in this file when a model is added.
    """
    import os

    from sqlalchemy import create_engine, text

    url = os.environ["GST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = :s"
                ),
                {"s": schema},
            ).fetchall()
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_model_metadata_registers_all_25_tables() -> None:
    """The ORM registers exactly the doc's 25 tables across 3 schemas."""

    app_db_base.all_models()
    by_schema: dict[str, set[str]] = {}
    for table in Base.metadata.sorted_tables:
        assert table.schema in EXPECTED_TABLES, f"unexpected schema {table.schema}"
        by_schema.setdefault(table.schema, set()).add(table.name)
    assert by_schema == EXPECTED_TABLES


def test_money_columns_are_integer_paise() -> None:
    """No float money anywhere: *_minor columns must be Integer/BigInteger-typed."""
    from decimal import Decimal

    from sqlalchemy import BigInteger, Integer, Numeric

    app_db_base.all_models()
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.name.endswith("_minor"):
                assert isinstance(column.type, (Integer, BigInteger)), (
                    f"{table.schema}.{table.name}.{column.name} is "
                    f"{column.type!r}, expected Integer/BigInteger (paise rule)"
                )
            if isinstance(column.type, Numeric) and not isinstance(
                column.type, (Integer, BigInteger)
            ):
                assert isinstance(
                    getattr(column.type, "_type_affinity", None), type
                )
                # Non-money numerics (qty/gst_rate/confidence) are Decimal in
                # Python — a float would be `float` in the annotation; Numeric
                # guarantees no binary float at the DB boundary.
                assert column.type.python_type is Decimal


@requires_pg
def test_migration_up_down_up_cycle_clean() -> None:
    """The done_when: upgrade -> downgrade -> upgrade, exit 0 each step."""
    assert PG_AVAILABLE
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")


@requires_pg
def test_all_v2_tables_present_after_upgrade() -> None:
    """Post-upgrade, all 25 tables exist in their doc-specified schemas."""
    assert PG_AVAILABLE
    for schema, expected in EXPECTED_TABLES.items():
        present = _tables_in_schema(schema)
        missing = expected - present
        assert not missing, f"{schema} schema missing tables: {sorted(missing)}"
        assert expected == present, (
            f"{schema} schema drift: extra={sorted(present - expected)}"
        )


@requires_pg
def test_filing_period_composite_key_and_invoice_lock_columns() -> None:
    """Spot-checks locking/GST semantics that later tasks build on."""
    import os

    from sqlalchemy import create_engine, text

    url = os.environ["GST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            pk_cols = conn.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE c.relname='filing_periods' AND i.indisprimary "
                    "ORDER BY a.attname"
                )
            ).fetchall()
            assert {r[0] for r in pk_cols} == {"registration_id", "fp"}
            invoice_status = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema='gst' AND table_name='invoices' "
                    "AND column_name='status'"
                )
            ).scalar()
            assert invoice_status == "USER-DEFINED"  # enum with LOCKED
            enum_values = conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid=e.enumtypid "
                    "WHERE t.typname='invoice_status' ORDER BY e.enumsortorder"
                )
            ).fetchall()
            assert [r[0] for r in enum_values] == ["DRAFT", "CONFIRMED", "LOCKED"]
    finally:
        engine.dispose()
