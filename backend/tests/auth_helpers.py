"""L2 auth fixtures: fakeredis-backed app client + scratch user rows.

The API app runs fully in-process (httpx ASGITransport) against the live PG
(:5436) via the app's async session; Redis is faked (fakeredis) per test so
OTP/rate-limit/family state never leaks between tests.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.auth import redis_client as redis_client_mod
from app.db.models.core import User
from app.main import create_app

TEST_DB_URL = "postgresql+asyncpg://gst:gst_dev_pass@127.0.0.1:5436/gst_filing_db"
os.environ.setdefault("GST_DATABASE_ASYNC_URL", TEST_DB_URL)

SessionMaker = async_sessionmaker[Any]


@pytest.fixture()
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """Fresh fakeredis per test; injected as the app's Redis client.

    Flow:
        FakeRedis(decode_responses=True) then redis_client.set_client.

    Debug:
        OTP/refresh state leaking across tests means this fixture was not requested.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_client_mod.set_client(client)
    return client


@pytest_asyncio.fixture()
async def api_sessionmaker() -> AsyncGenerator[SessionMaker, None]:
    """Async session factory on the live gst_filing_db. Disposes the engine after the test.

    Debug:
        Connection errors → bootstrap_stack.py and PG :5436.

    Flow:
        1. Call create_async_engine.
        2. Await engine.dispose.

    """
    engine = create_async_engine(TEST_DB_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(
    fake_redis: fakeredis.aioredis.FakeRedis,
    api_sessionmaker: SessionMaker,
) -> AsyncGenerator[AsyncClient, None]:
    """ASGI client wired to the real app + live PG + fakeredis.

    Flow:
        1. create_app and override get_session with api_sessionmaker.
        2. Yield httpx AsyncClient on ASGITransport.
        3. Clear the redis client so the next test does not keep fakeredis.

    Debug:
        500s here are real app bugs. Routes still need PG tables from Alembic.
    """
    from app.db.session import get_session

    app = create_app()

    async def _override_session() -> AsyncGenerator[Any, None]:
        """Yield one session from the test session factory.

        Flow:
            1. Open the database session.

        Debug:
            This helper feeds the tests below. A bad fixture fails before the route is called.
        """
        async with api_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    redis_client_mod.set_client(None)


async def make_user(
    sessionmaker: SessionMaker, mobile: str | None = None, email: str | None = None
) -> User:
    """Seed a user row directly (no OTP round-trip).

    Flow:
        Insert User with the given mobile/email, commit, refresh, return the row.

    Debug:
        mobile default is 9 + 9 hex chars so it passes the 10-13 digit identifier rule.
    """
    async with sessionmaker() as session:
        user = User(
            mobile=mobile or "9" + uuid4hex(),
            email=email,
            full_name="Test User",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def uuid4hex() -> str:
    """Nine hex chars from uuid4, used as a unique mobile suffix.

    Debug:
        Not a full UUID. Collisions are unlikely but possible if sliced too short in a loop.

    Flow:
        1. Return the first 9 hex characters of a new UUID.

    """
    import uuid

    return uuid.uuid4().hex[:9]


async def register_and_login(client: AsyncClient, mobile: str) -> dict[str, Any]:
    """Full OTP journey: request (dev echo) → verify → token pair in the envelope data.

    Flow:
        1. POST otp/request purpose REGISTER.
        2. Assert dev_otp is present (GST dev mode).
        3. POST otp/verify and return data (user, access_token, refresh_token).

    Debug:
        Missing dev_otp → settings.dev_mode is false. 401 on verify → OTP key was consumed.
    """
    req = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": mobile, "purpose": "REGISTER"},
    )
    assert req.status_code == 200, req.text
    dev_otp = req.json()["data"]["dev_otp"]
    assert dev_otp, "dev mode must echo dev_otp for tests"
    ver = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": dev_otp}
    )
    assert ver.status_code == 200, ver.text
    envelope: dict[str, Any] = ver.json()
    data: dict[str, Any] = envelope["data"]
    return data


def redis_view(fake_redis: fakeredis.aioredis.FakeRedis) -> Redis:
    """Typing shim so tests can pass fakeredis where Redis is annotated.

    Debug:
        No runtime conversion. If a method is missing, the fakeredis version is too old.

    Flow:
        1. Return the fakeredis client unchanged.

    """
    return fake_redis


# Back-compat aliases for test modules imported before the rename.
_register_and_login = register_and_login
_make_user = make_user
_redis_sync_view = redis_view
