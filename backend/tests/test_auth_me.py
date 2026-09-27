"""Auth me/stepup/firm integration tests for task 0.7A."""

from __future__ import annotations

import random
import uuid
from typing import Any

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
    return "9" + "".join(_rng.choice("0123456789") for _ in range(9))


async def _register_totp_firm(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
    pan: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, uuid.UUID]:
    """Full CA onboarding: register -> totp setup+verify -> create firm.

    Returns (token_data, firm_out, mobile, user_id).
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
    """4(a): REGISTER -> TOTP -> POST /firm -> GET /auth/me = 200, firm present."""
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
    """4(b): second POST /firm + /me still 200 with the FIRST firm id."""
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
    """4(c): no header -> 403 STEP_UP_REQUIRED; own stepup -> ok; other -> 401."""
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
    """4(d): POST /firm 200 shape and 403 TOTP_REQUIRED for a TOTP-less user."""
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
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """4(e): user with email AND mobile, OTP requested for MOBILE -> stepup 200."""
    mobile = _mobile()
    data = await _register_and_login(client, mobile)
    access = data["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    user_id = token_svc.verify_access_token(access)
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.email = f"test+{uuid.uuid4().hex}@example.com"
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
