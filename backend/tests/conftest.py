"""Shared test fixtures.

Requires a live PG at 127.0.0.1:5436 (scripts/bootstrap_stack.py). The DB is
reset to head schema per session; tests never fabricate GSTINs/PANs.
"""

from __future__ import annotations

import os

import pytest

# Local dev DB — bootstrap_stack.py owns creation/seeding; tests only migrate.
os.environ.setdefault("GST_PG_HOST", "127.0.0.1")
os.environ.setdefault("GST_PG_PORT", "5436")
os.environ.setdefault("GST_PG_DB", "gst_filing_db")
os.environ.setdefault("GST_PG_USER", "gst")
os.environ.setdefault("GST_PG_PASSWORD", "gst_dev_pass")
os.environ.setdefault(
    "GST_DATABASE_URL",
    "postgresql+psycopg://gst:gst_dev_pass@127.0.0.1:5436/gst_filing_db",
)
os.environ.setdefault(
    "GST_DATABASE_ASYNC_URL",
    "postgresql+asyncpg://gst:gst_dev_pass@127.0.0.1:5436/gst_filing_db",
)

from app.db import base as app_db_base  # noqa: E402

app_db_base.all_models()


def _pg_available() -> bool:
    from sqlalchemy import create_engine, text

    url = os.environ["GST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available()

requires_pg = pytest.mark.skipif(not PG_AVAILABLE, reason="PG:5436 not reachable")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Reap gst_mig_test_* scratch DBs stranded by crashed runs (tester MUST-FIX)."""
    try:
        from sqlalchemy import create_engine, text

        url = os.environ["GST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
        engine = create_engine(url.replace("/gst_filing_db", "/postgres"))
        try:
            with engine.connect() as conn:
                conn.execute(text("COMMIT"))
                rows = conn.execute(
                    text(
                        "SELECT datname FROM pg_database "
                        "WHERE datname LIKE 'gst_mig_test_%'"
                    )
                ).fetchall()
                for (db_name,) in rows:
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :db AND pid <> pg_backend_pid()"
                        ).bindparams(db=db_name)
                    )
                    conn.execute(text(f'DROP DATABASE "{db_name}"'))
                conn.commit()
        finally:
            engine.dispose()
    except Exception:  # noqa: S110  best-effort hygiene; never block the suite
        pass


# --- auth fixtures (defined in auth_helpers.py; exposed via conftest) ---------
# Fixture re-exports are intentionally "unused" here — pytest discovers them
# through conftest's namespace, so the F401 must be silenced explicitly.
from tests import auth_helpers as _auth_helpers  # noqa: E402, F401

api_sessionmaker = _auth_helpers.api_sessionmaker
client = _auth_helpers.client
fake_redis = _auth_helpers.fake_redis
