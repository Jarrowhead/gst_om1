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
    """Process-wide async Redis client (lazy singleton)."""
    global _pool, _client
    if _client is None:
        _pool = ConnectionPool.from_url(
            get_settings().redis_url, decode_responses=True, protocol=2
        )
        _client = Redis(connection_pool=_pool)
    return _client


async def _close_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001, S110
            pass
        _client = None


async def _close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.disconnect()
        except Exception:  # noqa: BLE001, S110
            pass
        _pool = None


async def close_redis() -> None:
    """Dispose the client + pool (app shutdown / test teardown)."""
    await _close_client()
    await _close_pool()


def set_client(client: Redis | None) -> None:
    """Test hook: inject a client (e.g. fakeredis) or reset to None."""
    global _client
    _client = client
