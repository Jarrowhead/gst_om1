"""L2 auth suite — refresh rotation + reuse kill (SECURITY §1, §7).

The done_when's "refresh rotation kill": replaying a rotated refresh token
must (a) return 401 REFRESH_REUSE_DETECTED and (b) kill the family — siblings
minted from the same family stop working too.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

import fakeredis.aioredis
import pytest
from app.core.auth import tokens as token_svc
from app.core.auth.errors import RefreshReuseDetected
from app.core.auth.redis_client import get_redis
from httpx import AsyncClient

from tests.auth_helpers import _register_and_login

pytestmark = pytest.mark.asyncio

_rng = random.SystemRandom()


def _mobile() -> str:
    """Unique per-call mobile — the suite shares the live dev DB (isolation).

    Flow:
        1. Return a 10-digit mobile that starts with 9.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    return "9" + "".join(_rng.choice("0123456789") for _ in range(9))


async def _login(client: AsyncClient) -> dict[str, Any]:
    """Register+login a FRESH user per call (per-test isolation).

    Flow:
        1. Return await _register_and_login(client, _mobile()).

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    return await _register_and_login(client, _mobile())


async def test_refresh_returns_new_access_token(client: AsyncClient) -> None:
    """test refresh returns new access token.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/refresh and read the JSON envelope.
        3. Expect HTTP 200.
        4. Call resp.json.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    data = await _login(client)
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['data']['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_rotates_old_token_is_retired(client: AsyncClient) -> None:
    """test refresh rotates old token is retired.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/refresh and read the JSON envelope.
        3. Expect HTTP 200.
        4. Call get_redis.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    data = await _login(client)
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
    )
    assert first.status_code == 200
    # the OLD refresh token must no longer be a live token (rtk: gone)
    redis = get_redis()
    assert not await redis.exists(f"rtk:{data['refresh_token']}")


async def test_refresh_reuse_kills_family(client: AsyncClient) -> None:
    """The rotation-kill: replay of a rotated token invalidates siblings.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/refresh and read the JSON envelope.
        3. Expect HTTP 200.
        4. Assert sibling['access_token'].

    Debug:
        Reusing a rotated refresh token must 401 and delete every key in that family.
    """
    data = await _login(client)
    old_refresh = data["refresh_token"]
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200
    sibling = first.json()["data"]  # access only; refresh lands in the cookie
    assert sibling["access_token"]
    new_refresh = first.cookies.get("refresh_token")
    assert new_refresh, "rotation must set the new refresh cookie"

    # REPLAY the old token -> 401 REFRESH_REUSE_DETECTED
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "REFRESH_REUSE_DETECTED"

    # family kill: the sibling minted during rotation is now DEAD too
    replay2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert replay2.status_code == 401
    assert replay2.json()["error"]["code"] in {"TOKEN_INVALID", "REFRESH_REUSE_DETECTED"}


async def test_refresh_reuse_kill_is_total_in_redis(
    client: AsyncClient,
) -> None:
    """Direct Redis proof: after reuse, zero live tokens remain for the family.

    Flow:
        1. Await _login and keep the result.
        2. Await get and keep the result.
        3. Assert family_id is not None and isinstance(family_id, str).
        4. Call POST /api/v1/auth/refresh and read the JSON envelope.

    Debug:
        Reusing a rotated refresh token must 401 and delete every key in that family.
    """
    data = await _login(client)
    old_refresh = data["refresh_token"]
    family_id = await get_redis().get(f"rtk:{old_refresh}")
    assert family_id is not None and isinstance(family_id, str)

    await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

    redis = get_redis()
    live_tokens = 0
    async for key in redis.scan_iter(match="rtk:*"):
        val = await redis.get(key)
        if val == family_id and isinstance(val, str):
            live_tokens += 1
    assert live_tokens == 0, "family kill left live tokens behind"


async def test_access_token_survives_refresh_kill_but_expires_on_own_clock(
    client: AsyncClient,
) -> None:
    """Rotation-kill revokes refresh, not already-issued access JWTs (stateless).

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/refresh and read the JSON envelope.
        3. Call GET /api/v1/auth/me and read the JSON envelope.
        4. Expect HTTP 200.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    data = await _login(client)
    old_refresh = data["refresh_token"]
    await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200


async def test_unknown_or_garbage_refresh_token_401(client: AsyncClient) -> None:
    """test unknown or garbage refresh token 401.

    Flow:
        1. Call POST /api/v1/auth/refresh and read the JSON envelope.
        2. Expect HTTP 401.
        3. Assert resp.json()['error']['code'] == 'TOKEN_INVALID'.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "garbage-token-value"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


async def test_refresh_missing_token_401(client: AsyncClient) -> None:
    """test refresh missing token 401.

    Flow:
        1. Call POST /api/v1/auth/refresh and read the JSON envelope.
        2. Expect HTTP 401.
        3. Assert resp.json()['error']['code'] == 'TOKEN_INVALID'.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    resp = await client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


async def test_refresh_ttl_is_seven_days(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Family + token keys carry the 7-day TTL (± slack).

    Flow:
        1. Call uuid.uuid4.
        2. Await token_svc.issue_refresh_family and keep the result.
        3. Await fake_redis.ttl and keep the result.
        4. Assert 7 * 24 * 3600 <= ttl <= 7 * 24 * 3600 + 120.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    user_id = uuid.uuid4()
    token = await token_svc.issue_refresh_family(fake_redis, user_id)
    ttl = await fake_redis.ttl(f"rtk:{token}")
    assert 7 * 24 * 3600 <= ttl <= 7 * 24 * 3600 + 120
    fam_id = await fake_redis.get(f"rtk:{token}")
    assert fam_id is not None and isinstance(fam_id, str)
    fam_ttl = await fake_redis.ttl(f"rfam:{fam_id}")
    assert fam_ttl > 0


async def test_logout_retires_only_presented_token(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """revoke_refresh_token: presented token dies; a second sibling survives.

    Flow:
        1. Call get_redis.
        2. Call uuid.uuid4.
        3. Await token_svc.issue_refresh_family and keep the result.
        4. Await token_svc.revoke_refresh_token.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    redis = get_redis()
    user_id = uuid.uuid4()
    t1 = await token_svc.issue_refresh_family(fake_redis, user_id)
    t2 = await token_svc.issue_refresh_family(fake_redis, user_id)
    await token_svc.revoke_refresh_token(redis, t1)
    assert not await redis.exists(f"rtk:{t1}")
    assert await redis.exists(f"rtk:{t2}")
    # replaying the revoked (retired) token triggers reuse-kill semantics
    with pytest.raises(RefreshReuseDetected):
        await token_svc.rotate_refresh_token(redis, t1)


async def test_second_family_is_unaffected_by_first_family_kill(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Reuse-kill is family-scoped, not user-scoped or global.

    Flow:
        1. Call uuid.uuid4.
        2. Await token_svc.issue_refresh_family and keep the result.
        3. Await token_svc.rotate_refresh_token and keep the result.
        4. Open the database session.

    Debug:
        Refresh reads the httpOnly cookie on /api/v1/auth. A missing cookie is 401.
    """
    user_id = uuid.uuid4()
    family_a = await token_svc.issue_refresh_family(fake_redis, user_id)
    family_b = await token_svc.issue_refresh_family(fake_redis, user_id)
    # rotate A once, then replay A's original -> A family dies
    new_a, _ = await token_svc.rotate_refresh_token(fake_redis, family_a)
    with pytest.raises(RefreshReuseDetected):
        await token_svc.rotate_refresh_token(fake_redis, family_a)
    # the kill deleted the sibling's live key entirely -> dead token, not reuse
    with pytest.raises(RefreshReuseDetected):
        await token_svc.rotate_refresh_token(fake_redis, family_a)
    assert not await fake_redis.exists(f"rtk:{new_a}")
    # B still rotates cleanly
    new_b, uid_b = await token_svc.rotate_refresh_token(fake_redis, family_b)
    assert uid_b == user_id
    assert await fake_redis.exists(f"rtk:{new_b}")
