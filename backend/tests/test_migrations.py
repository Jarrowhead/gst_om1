"""Task 0.3 acceptance: alembic up -> down -> up clean; all v2 tables present.

Runs the real migration chain against a throwaway database on the dev PG
server (:5436), asserting every TECHNICAL_ARCHITECTURE §3 table lands in the
right schema, and that downgrade leaves no tables/enum types behind.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent.parent
ADMIN_URL = "postgresql+asyncpg://gst:gst_dev_pass@127.0.0.1:5436/postgres"
BASE_URL = "postgresql+asyncpg://gst:gst_dev_pass@127.0.0.1:5436"

# TECHNICAL_ARCHITECTURE.md §3 — the v2 table contract, 25 tables.
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
    "extraction": {
        "documents",
        "extraction_jobs",
        "invoice_drafts",
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
}


def _alembic_config(db_name: str) -> Config:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.set_main_option(
        "script_location", str(BACKEND_DIR / "alembic")
    )
    cfg.set_main_option("sqlalchemy.url", f"{BASE_URL}/{db_name}")
    return cfg


@pytest.fixture()
async def scratch_db() -> AsyncGenerator[str, None]:
    """Create an empty throwaway database; drop it (with leftovers) after."""
    db_name = f"gst_mig_test_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await conn.close()
    finally:
        await admin.dispose()
    # Seed schemas INSIDE the new database (a separate connection — the admin
    # connection above is attached to the 'postgres' database, where CREATE
    # SCHEMA would seed the wrong DB).
    seeder = create_async_engine(f"{BASE_URL}/{db_name}", isolation_level="AUTOCOMMIT")
    try:
        async with seeder.connect() as conn:
            for schema in ("core", "gst", "extraction"):
                await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    finally:
        await seeder.dispose()
    yield db_name
    # Revoke + drop terminates any lingering connections.
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ).bindparams(db=db_name)
            )
            await conn.execute(text(f'DROP DATABASE "{db_name}"'))
    finally:
        await admin.dispose()


async def _tables_and_enums(db_name: str) -> tuple[set[str], set[str]]:
    engine = create_async_engine(f"{BASE_URL}/{db_name}")
    try:
        async with engine.connect() as conn:
            tables = {
                f"{row[0]}.{row[1]}"
                for row in await conn.execute(
                    text(
                        "SELECT table_schema, table_name FROM information_schema.tables "
                        "WHERE table_schema IN ('core','gst','extraction')"
                    )
                )
            }
            enums = {
                f"{row[0]}.{row[1]}"
                for row in await conn.execute(
                    text(
                        "SELECT n.nspname, t.typname FROM pg_type t "
                        "JOIN pg_namespace n ON n.oid = t.typnamespace "
                        "WHERE t.typtype = 'e' AND n.nspname IN "
                        "('core','gst','extraction')"
                    )
                )
            }
    finally:
        await engine.dispose()
    return tables, enums


def _alembic(db_name: str, op_name: str, target: str) -> None:
    """Run an alembic command in a worker thread.

    Alembic's env.py calls asyncio.run(), which requires no running loop;
    pytest-asyncio owns the test's loop, so hop threads.
    """
    import concurrent.futures

    cfg = _alembic_config(db_name)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            command.upgrade if op_name == "upgrade" else command.downgrade,
            cfg,
            target,
        )
        future.result(timeout=120)


@pytest.mark.asyncio()
async def test_upgrade_downgrade_upgrade_cycle_clean(scratch_db: str) -> None:
    """up -> down -> up: the exact acceptance criterion for task 0.3."""
    _alembic(scratch_db, "upgrade", "head")
    tables_1, _ = await _tables_and_enums(scratch_db)

    _alembic(scratch_db, "downgrade", "base")
    tables_2, enums_2 = await _tables_and_enums(scratch_db)
    assert tables_2 == set(), f"downgrade left tables: {sorted(tables_2)}"
    assert enums_2 == set(), f"downgrade left enum types: {sorted(enums_2)}"

    _alembic(scratch_db, "upgrade", "head")
    tables_3, _ = await _tables_and_enums(scratch_db)
    assert tables_3 == tables_1, "second upgrade produced a different table set"


@pytest.mark.asyncio()
async def test_all_v2_tables_present_in_correct_schemas(scratch_db: str) -> None:
    """All 25 §3 tables exist in core/gst/extraction after upgrade head."""
    _alembic(scratch_db, "upgrade", "head")
    tables, _ = await _tables_and_enums(scratch_db)

    for schema, expected in EXPECTED_TABLES.items():
        actual = {t.split(".", 1)[1] for t in tables if t.startswith(f"{schema}.")}
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"{schema}: missing {sorted(missing)}"
        assert not extra, f"{schema}: unexpected {sorted(extra)}"
    assert len(tables) == 25


@pytest.mark.asyncio()
async def test_money_columns_are_integer_paise(scratch_db: str) -> None:
    """Hard rule 2: every *_minor money column is integer — no float/numeric."""
    _alembic(scratch_db, "upgrade", "head")
    engine = create_async_engine(f"{BASE_URL}/{scratch_db}")
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT table_schema, table_name, column_name, data_type "
                        "FROM information_schema.columns "
                        "WHERE column_name LIKE '%_minor' "
                        "AND table_schema IN ('core','gst','extraction') "
                        "ORDER BY 1, 2, 3"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    assert rows, "no *_minor columns found — migration did not apply?"
    for _schema, _table, _column, data_type in rows:
        assert data_type == "integer", (
            f"{_schema}.{_table}.{_column} is {_schema and data_type}, must be integer"
        )
    assert len(rows) == 18  # exact: 7 invoice_lines + 5 cdns + 5 2b + aato + total
