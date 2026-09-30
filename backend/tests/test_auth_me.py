"""Auth me/stepup/firm integration tests for task 0.7A."""

from __future__ import annotations

import json as _json
import random
import uuid
from typing import Any

import fakeredis.aioredis
import pyotp
import pytest
from app.core.auth import tokens as token_svc
from app.core.auth.dependencies import require_stepup
from app.core.auth.errors import StepUpRequired, TokenInvalid
from app.db.models.core import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import _register_and_login
from tests.gstin_fixtures import make_pan

pytestmark = pytest.mark.asyncio

SessionMaker = async_sessionmaker[Any]

_rng = random.SystemRandom()


def _mobile() -> str:
    """Unique mobile so this test does not share an OTP key with another test.

    Flow:
        1. Return a 10-digit mobile that starts with 9.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    return "9" + "".join(_rng.choice("0123456789") for _ in range(9))


async def _register_totp_firm(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
    pan: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, uuid.UUID]:
    """Full CA onboarding: register -> totp setup+verify -> create firm.

    Returns (token_data, firm_out, mobile, user_id).

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        4. Expect HTTP 200.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["data"]["secret"]

    code = pyotp.TOTP(secret).now()
    verify = await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"code": code},
    )
    assert verify.status_code == 200, verify.text

    firm_pan = pan or make_pan()
    firm_resp = await client.post(
        "/api/v1/firm",
        headers=headers,
        json={"firm_name": "Test Firm", "pan": firm_pan},
    )
    assert firm_resp.status_code == 200, firm_resp.text

    user_id = token_svc.verify_access_token(data["access_token"])
    return data, firm_resp.json()["data"], mobile, user_id


async def test_me_after_firm_onboarding(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """4(a): REGISTER -> TOTP -> POST /firm -> GET /auth/me = 200, firm present.

    Flow:
        1. Await _register_totp_firm and keep the result.
        2. Call GET /api/v1/auth/me and read the JSON envelope.
        3. Expect HTTP 200.
        4. Call me.json.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    data, firm, mobile, user_id = await _register_totp_firm(
        client, api_sessionmaker
    )
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["success"] is True
    assert body["data"]["firm"] == firm["id"]
    assert body["data"]["businesses"] == []
    assert body["data"]["user"]["totp_enabled"] is True


async def test_me_second_firm_returns_primary_earliest_firm(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """4(b): second POST /firm + /me still 200 with the FIRST firm id.

    Flow:
        1. Await _register_totp_firm and keep the result.
        2. Call POST /api/v1/firm and read the JSON envelope.
        3. Expect HTTP 200.
        4. Call GET /api/v1/auth/me and read the JSON envelope.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
    data, firm1, _mobile1, _uid = await _register_totp_firm(
        client, api_sessionmaker
    )
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    firm2_resp = await client.post(
        "/api/v1/firm",
        headers=headers,
        json={"firm_name": "Second Firm", "pan": make_pan()},
    )
    assert firm2_resp.status_code == 200, firm2_resp.text
    firm2 = firm2_resp.json()["data"]

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["data"]["firm"] == firm1["id"]
    assert firm2["id"] != firm1["id"]


async def test_require_stepup_contract(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """4(c): no header -> 403 STEP_UP_REQUIRED; own stepup -> ok; other -> 401.

    Flow:
        1. Await _register_totp_firm and keep the result.
        2. Call Request.
        3. Open the database session.
        4. Await require_stepup.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _firm, _mobile, user_id = await _register_totp_firm(
        client, api_sessionmaker
    )

    from starlette.requests import Request

    # no step-up header
    bare_request = Request({"type": "http", "headers": []})
    with pytest.raises(StepUpRequired) as step_exc_info:
        await require_stepup(bare_request, None, user_id)  # type: ignore[arg-type]
    assert step_exc_info.value.status_code == 403
    assert step_exc_info.value.code == "STEP_UP_REQUIRED"

    # valid own token
    own_token = token_svc.create_stepup_token(user_id)
    good_request = Request({
        "type": "http",
        "headers": [(b"x-stepup-token", own_token.encode())],
    })
    assert await require_stepup(good_request, None, user_id) == user_id  # type: ignore[arg-type]

    # another user's token
    other_id = uuid.uuid4()
    bad_token = token_svc.create_stepup_token(other_id)
    bad_request = Request({
        "type": "http",
        "headers": [(b"x-stepup-token", bad_token.encode())],
    })
    with pytest.raises(TokenInvalid) as token_exc_info:
        await require_stepup(bad_request, None, user_id)  # type: ignore[arg-type]
    assert token_exc_info.value.code == "TOKEN_INVALID"


async def test_firm_create_shape_and_totp_guard(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """4(d): POST /firm 200 shape and 403 TOTP_REQUIRED for a TOTP-less user.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Call POST /api/v1/firm and read the JSON envelope.
        4. Expect HTTP 403.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    # TOTP-less user cannot create a firm
    guard = await client.post(
        "/api/v1/firm",
        headers=headers,
        json={"firm_name": "Too Early", "pan": make_pan()},
    )
    assert guard.status_code == 403
    assert guard.json()["error"]["code"] == "TOTP_REQUIRED"

    # TOTP setup + verify
    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    secret = setup.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )

    firm_resp = await client.post(
        "/api/v1/firm",
        headers=headers,
        json={"firm_name": "Real Firm", "pan": make_pan()},
    )
    assert firm_resp.status_code == 200, firm_resp.text
    firm = firm_resp.json()["data"]
    assert set(firm.keys()) == {"id", "firm_name", "pan", "ca_code"}
    assert firm["ca_code"].startswith("CAF-")


async def test_stepup_falls_back_mobile_when_email_otp_requested(
    client: AsyncClient, api_sessionmaker: SessionMaker, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """4(e): user with email AND mobile, OTP requested for MOBILE -> stepup 200.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Call token_svc.verify_access_token.
        4. Open the database session.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    access = data["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    user_id = token_svc.verify_access_token(access)
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        email = f"test+{uuid.uuid4().hex}@example.com"
        user.email = email
        await session.commit()

    # Request OTP against the mobile identifier (the fallback target)
    req = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": mobile, "purpose": "LOGIN"},
    )
    assert req.status_code == 200, req.text
    otp = req.json()["data"]["dev_otp"]

    resp = await client.post(
        "/api/v1/auth/stepup",
        headers=headers,
        json={"otp": otp},
    )
    assert resp.status_code == 200, resp.text
    assert "stepup_token" in resp.json()["data"]


async def test_stepup_mobile_with_both_live(
    client: AsyncClient, api_sessionmaker: SessionMaker, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """4(f): both email and mobile hold live OTPs; stepup with mobile OTP succeeds
    and the email OTP remains live (second stepup with email OTP succeeds).

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Call token_svc.verify_access_token.
        4. Open the database session.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    access = data["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    user_id = token_svc.verify_access_token(access)
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        email = f"test+{uuid.uuid4().hex}@example.com"
        user.email = email
        await session.commit()

    # Request OTPs for both identifiers and read both dev_otp values.
    req_mobile = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": mobile, "purpose": "LOGIN"},
    )
    assert req_mobile.status_code == 200, req_mobile.text
    mobile_otp = req_mobile.json()["data"]["dev_otp"]

    req_email = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": email, "purpose": "LOGIN"},
    )
    assert req_email.status_code == 200, req_email.text
    email_otp = req_email.json()["data"]["dev_otp"]

    # Step-up with mobile OTP.
    resp1 = await client.post(
        "/api/v1/auth/stepup",
        headers=headers,
        json={"otp": mobile_otp},
    )
    assert resp1.status_code == 200, resp1.text
    assert "stepup_token" in resp1.json()["data"]

    # Email OTP is still live.
    assert await fake_redis.exists(f"otp:{email}") == 1

    # Step-up with email OTP still succeeds.
    resp2 = await client.post(
        "/api/v1/auth/stepup",
        headers=headers,
        json={"otp": email_otp},
    )
    assert resp2.status_code == 200, resp2.text
    assert "stepup_token" in resp2.json()["data"]


async def test_stepup_wrong_otp_does_not_burn_attempts(
    client: AsyncClient, api_sessionmaker: SessionMaker, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """4(g): wrong OTP when both are live -> 401 OTP_INVALID and neither
    identifier's attempts counter advanced beyond the single recorded attempt.

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Await _register_and_login and keep the result.
        3. Call token_svc.verify_access_token.
        4. Open the database session.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    access = data["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    user_id = token_svc.verify_access_token(access)
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        email = f"test+{uuid.uuid4().hex}@example.com"
        user.email = email
        await session.commit()

    # Request OTPs for both identifiers and read both dev_otp values.
    req_mobile = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": mobile, "purpose": "LOGIN"},
    )
    assert req_mobile.status_code == 200, req_mobile.text
    mobile_otp = req_mobile.json()["data"]["dev_otp"]

    req_email = await client.post(
        "/api/v1/auth/otp/request",
        json={"identifier": email, "purpose": "LOGIN"},
    )
    assert req_email.status_code == 200, req_email.text
    email_otp = req_email.json()["data"]["dev_otp"]

    wrong = "000000" if mobile_otp != "000000" and email_otp != "000000" else "111111"
    resp = await client.post(
        "/api/v1/auth/stepup",
        headers=headers,
        json={"otp": wrong},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "OTP_INVALID"

    for ident in (email, mobile):
        raw = await fake_redis.get(f"otp:{ident}")
        assert raw is not None, f"otp:{ident} should still exist"
        rec = _json.loads(raw)
        assert rec["attempts"] == 0, f"otp:{ident} attempts advanced to {rec['attempts']}"
