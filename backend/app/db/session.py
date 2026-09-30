"""Database engine/session configuration.

URL comes from GST_DATABASE_URL (or composes from discrete GST_PG_* vars).
Local dev default matches scripts/bootstrap_stack.py: PG :5436 gst/gst_dev_pass.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PG_HOST = os.environ.get("GST_PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("GST_PG_PORT", "5436")
PG_DB = os.environ.get("GST_PG_DB", "gst_filing_db")
PG_USER = os.environ.get("GST_PG_USER", "gst")
PG_PASSWORD = os.environ.get("GST_PG_PASSWORD", "gst_dev_pass")

SYNC_URL = os.environ.get(
    "GST_DATABASE_URL",
    f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}",
)
ASYNC_URL = os.environ.get(
    "GST_DATABASE_ASYNC_URL",
    f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}",
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazily create the process-wide async engine.

    Flow:
        First call: create_async_engine(ASYNC_URL, pool_pre_ping) and bind sessionmaker.
        Later calls return the same engine.

    Debug:
        Connection refused → GST_DATABASE_ASYNC_URL or bootstrap PG :5436. Tests set env
        before import via conftest.
    """
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(ASYNC_URL, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the process-wide engine.

    Flow:
        get_engine() then return _sessionmaker (asserted non-None).

    Debug:
        expire_on_commit=False so route handlers can read attributes after commit.
    """
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Flow:
        async with sessionmaker() as session: yield. Closes when the request ends.

    Debug:
        Uncommitted work disappears at request end. Services that commit internally
        (create_business) do not rely on the dependency to commit.
    """
    async with get_sessionmaker()() as session:
        yield session
