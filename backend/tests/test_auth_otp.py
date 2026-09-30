"""L2 auth suite — OTP request/verify + rate limit + expiry/attempts.

Covers SECURITY_AND_ACCESS.md §1/§7 and the task 0.4 done_when "incl. rate
limit". Redis = fakeredis (fresh per test); PG = live :5436; every test gets a
unique identifier so the shared dev DB never couples tests.
"""

from __future__ import annotations

import json
import random
import uuid

import fakeredis.aioredis
import pytest
from app.config import get_settings
from app.core.auth import otp as otp_svc
from app.core.auth.errors import OtpExpired, OtpRateLimited
from httpx import AsyncClient

from tests.auth_helpers import _register_and_login

pytestmark = pytest.mark.asyncio

_rng = random.SystemRandom()


def _mobile() -> str:
    """Unique per-call mobile — the suite shares the live dev DB (isolation).

    Flow:
        1. Return a 10-digit mobile that starts with 9.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    return "9" + "".join(_rng.choice("0123456789") for _ in range(9))


def _email() -> str:
    """Unique email so OTP rate-limit keys do not collide across tests.

    Flow:
        1. Return a unique email address.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    return f"{uuid.uuid4().hex[:12]}@test.example"


async def test_otp_request_returns_dev_otp_in_dev_mode(client: AsyncClient) -> None:
    """test otp request returns dev otp in dev mode.

    Flow:
        1. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        2. Expect HTTP 200.
        3. Call resp.json.
        4. Assert body['success'] is True.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    resp = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": _mobile(), "purpose": "LOGIN"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["otp_sent"] is True
    assert body["data"]["dev_otp"] is not None
    assert len(body["data"]["dev_otp"]) == get_settings().otp_length


async def test_otp_request_accepts_email_identifier(client: AsyncClient) -> None:
    """test otp request accepts email identifier.

    Flow:
        1. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        2. Expect HTTP 200.
        3. Assert resp.json()['data']['otp_sent'] is True.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    resp = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": _email(), "purpose": "REGISTER"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["otp_sent"] is True


async def test_otp_verify_new_user_auto_created_on_register(client: AsyncClient) -> None:
    """test otp verify new user auto created on register.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Assert data['access_token'].
        4. Assert data['refresh_token'].

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["user"]["mobile"] == mobile
    # /auth/me works with the fresh access token
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["data"]["user"]["mobile"] == mobile


async def test_otp_verify_login_purpose_rejects_unknown_user(client: AsyncClient) -> None:
    """test otp verify login purpose rejects unknown user.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. Call POST /api/v1/auth/otp/verify and read the JSON envelope.
        4. Expect HTTP 401.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()  # never registered
    resp = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "LOGIN"}
    )
    otp = resp.json()["data"]["dev_otp"]
    ver = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": otp}
    )
    assert ver.status_code == 401
    assert ver.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_otp_verify_wrong_code_fails_and_counts_attempts(
    client: AsyncClient, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """test otp verify wrong code fails and counts attempts.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. Call POST /api/v1/auth/otp/verify and read the JSON envelope.
        4. Expect HTTP 401.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()
    await client.post(
        "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "REGISTER"}
    )
    resp = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": "000000"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "OTP_INVALID"
    # attempt counter persisted in Redis
    raw = await fake_redis.get(f"otp:{mobile}")
    assert raw is not None and isinstance(raw, str)
    record = json.loads(raw)
    assert record["attempts"] == 1


async def test_otp_verify_exhausts_attempts_then_kills_otp(
    client: AsyncClient, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """test otp verify exhausts attempts then kills otp.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. For each case: call POST /api/v1/auth/otp/verify and read the JSON envelope.
        4. Assert last is not None and last.status_code == 429.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()
    await client.post(
        "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "LOGIN"}
    )
    max_attempts = get_settings().otp_max_verify_attempts
    last = None
    for _ in range(max_attempts):
        last = await client.post(
            "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": "000000"}
        )
    assert last is not None and last.status_code == 429
    assert last.json()["error"]["code"] == "OTP_TOO_MANY_ATTEMPTS"
    # even the CORRECT code no longer works — the OTP is dead
    assert await fake_redis.get(f"otp:{mobile}") is None


async def test_otp_unit_expiry_and_single_use(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Unit-level: verify_otp raises OtpExpired when nothing stored; single-use.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await otp_svc.request_otp.
        3. Await fake_redis.get and keep the result.
        4. Assert code_record is not None and isinstance(code_record, str).

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()
    await otp_svc.request_otp(fake_redis, mobile, "LOGIN")
    code_record = await fake_redis.get(f"otp:{mobile}")
    assert code_record is not None and isinstance(code_record, str)
    code = json.loads(code_record)["code"]
    purpose = await otp_svc.verify_otp(fake_redis, mobile, code)
    assert purpose == "LOGIN"
    with pytest.raises(OtpExpired):
        await otp_svc.verify_otp(fake_redis, mobile, code)  # replay = expired


async def test_otp_rate_limit_5_per_hour_per_identifier(
    client: AsyncClient,
) -> None:
    """THE done_when rate-limit test: 5 requests ok, 6th is 429; other ids unaffected.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. For each case: call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. Assert statuses[:limit] == [(200, None)] * limit.
        4. Assert statuses[limit] == (429, 'OTP_RATE_LIMITED').

    Debug:
        The sixth OTP request for one identifier in an hour is 429. The key is otp:rate:{identifier}.
    """
    mobile = _mobile()
    other = _mobile()
    limit = get_settings().otp_request_limit_per_hour
    statuses = []
    for _ in range(limit + 1):
        resp = await client.post(
            "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "LOGIN"}
        )
        statuses.append((resp.status_code, resp.json().get("error", {}).get("code")))
    assert statuses[:limit] == [(200, None)] * limit
    assert statuses[limit] == (429, "OTP_RATE_LIMITED")
    # an unrelated identifier still gets OTPs (per-identifier, not global)
    other_resp = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": other, "purpose": "LOGIN"}
    )
    assert other_resp.status_code == 200


async def test_otp_rate_limit_unit_direct(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Service-level mirror: OtpRateLimited raised past the 5th request.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. For each case: await otp_svc.request_otp.
        3. Open the database session.
        4. Await otp_svc.request_otp.

    Debug:
        The sixth OTP request for one identifier in an hour is 429. The key is otp:rate:{identifier}.
    """
    mobile = _mobile()
    for _ in range(5):
        await otp_svc.request_otp(fake_redis, mobile, "LOGIN")
    with pytest.raises(OtpRateLimited):
        await otp_svc.request_otp(fake_redis, mobile, "LOGIN")


async def test_otp_invalid_identifier_422(client: AsyncClient) -> None:
    """test otp invalid identifier 422.

    Flow:
        1. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        2. Expect HTTP 422.
        3. Check code against the expected value.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    resp = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": "not-an-id", "purpose": "LOGIN"}
    )
    assert resp.status_code == 422
    code = resp.json()["error"]["code"]
    assert code in {"VALIDATION_ERROR", "IDENTIFIER_INVALID"}


async def test_otp_wrong_code_then_correct_code_still_works(client: AsyncClient) -> None:
    """Correct code works after failed attempts (attempts < max).

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. Call POST /api/v1/auth/otp/verify and read the JSON envelope.
        4. Expect HTTP 200.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    mobile = _mobile()
    req = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "REGISTER"}
    )
    dev_otp = req.json()["data"]["dev_otp"]
    await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": "111111"}
    )
    ver = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": dev_otp}
    )
    assert ver.status_code == 200
    assert ver.json()["data"]["access_token"]


async def test_otp_verify_is_single_use_across_logins(client: AsyncClient) -> None:
    """The same verified code cannot log in twice (OTP deleted on success).

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Call POST /api/v1/auth/otp/request and read the JSON envelope.
        3. Call POST /api/v1/auth/otp/verify and read the JSON envelope.
        4. Expect HTTP 200.

    Debug:
        Another tenant's id is 404, the same shape as a missing row.
    """
    mobile = _mobile()
    req = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": mobile, "purpose": "REGISTER"}
    )
    dev_otp = req.json()["data"]["dev_otp"]
    first = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": dev_otp}
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/otp/verify", json={"identifier": mobile, "otp": dev_otp}
    )
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "OTP_EXPIRED"
