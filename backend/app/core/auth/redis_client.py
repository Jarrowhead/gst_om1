"""Redis connection factory (OTP store, refresh families, rate limits).

Async redis on the bootstrap_stack.py port :6380. Tests swap in fakeredis via
`app.core.auth.redis_client.set_client`.
"""

from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from app.config import get_settings

_pool: ConnectionPool | None = None
_client: Redis | None = None


def get_redis() -> Redis:
    """Process-wide async Redis client (lazy singleton).

    Flow:
        If _client is None, build a pool from settings.redis_url and cache it.

    Debug:
        Tests call set_client(fakeredis) before routes. Real :6380 comes from bootstrap.
    """
    global _pool, _client
    if _client is None:
        _pool = ConnectionPool.from_url(
            get_settings().redis_url, decode_responses=True, protocol=2
        )
        _client = Redis(connection_pool=_pool)
    return _client


async def _close_client() -> None:
    """Close the cached Redis client and clear the singleton. Errors are swallowed.

    Debug:
        Pool may still be open until _close_pool runs via close_redis.

    Flow:
        1. If a client exists, await aclose and ignore errors.
        2. Set the module client to None so the next get_redis builds a new one.
    """
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001, S110
            pass
        _client = None


async def _close_pool() -> None:
    """Disconnect the connection pool and clear it. Errors are swallowed.

    Debug:
        Call after _close_client so in-flight commands are not left on a dead pool.

    Flow:
        1. If a pool exists, disconnect it and ignore errors.
        2. Set the module pool to None.
    """
    global _pool
    if _pool is not None:
        try:
            await _pool.disconnect()
        except Exception:  # noqa: BLE001, S110
            pass
        _pool = None


async def close_redis() -> None:
    """Dispose the client + pool (app shutdown / test teardown).

    Flow:
        _close_client then _close_pool.

    Debug:
        Next get_redis() builds a new pool. set_client(None) does not close the old one.
    """
    await _close_client()
    await _close_pool()


def set_client(client: Redis | None) -> None:
    """Test hook: inject a client (e.g. fakeredis) or reset to None.

    Flow:
        Replace module _client. Does not close the previous client.

    Debug:
        Forgetting set_client in a test leaks OTP state into the next test or hits real Redis.
    """
    global _client
    _client = client
