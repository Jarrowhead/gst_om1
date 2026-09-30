"""L2 auth suite — TOTP setup/verify + step-up token flow (SECURITY §1).

TOTP is mandatory for every ca_firm_member; the enforcement point is the
firm-join guard (task 0.5) — here we prove the setup/verify lifecycle and the
step-up token contract.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from typing import Any

import pyotp
import pytest
from app.core.auth import tokens as token_svc
from app.core.auth import totp as totp_svc
from app.core.auth.errors import TotpAlreadyEnabled, TotpInvalid
from app.db.models.core import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import _register_and_login

pytestmark = pytest.mark.asyncio

SessionMaker = async_sessionmaker[Any]

_rng = random.SystemRandom()


def _mobile() -> str:
    """Unique per-call mobile — the suite shares the live dev DB (isolation).

    Flow:
        1. Return a 10-digit mobile that starts with 9.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    return "9" + "".join(_rng.choice("0123456789") for _ in range(9))


async def _login(client: AsyncClient) -> tuple[dict[str, Any], str]:
    """Register+login a FRESH user per call; returns (tokens, mobile).

    Flow:
        1. Build a fresh identifier with _mobile().
        2. Return (await _register_and_login(client, mobile), mobile).

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    mobile = _mobile()
    return await _register_and_login(client, mobile), mobile


async def _user_id_from_access(sessionmaker: SessionMaker, access_token: str) -> uuid.UUID:
    """User id embedded in a verified access token.

    Flow:
        1. Return token_svc.verify_access_token(access_token).

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    return token_svc.verify_access_token(access_token)


async def test_totp_setup_returns_secret_and_qr_uri(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """test totp setup returns secret and qr uri.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        3. Expect HTTP 200.
        4. Assert body['secret'].

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    resp = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["secret"]
    assert body["qr_uri"].startswith("otpauth://totp/")
    # secret persisted on the user row
    user_id = await _user_id_from_access(api_sessionmaker, data["access_token"])
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None and user.totp_secret == body["secret"]
        assert user.totp_enabled_at is None  # NOT enabled until verify


async def test_totp_verify_wrong_code_rejected_then_correct_enables(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """test totp verify wrong code rejected then correct enables.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        3. Call POST /api/v1/auth/totp/verify and read the JSON envelope.
        4. Expect HTTP 401.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    secret = setup.json()["data"]["secret"]

    bad = await client.post("/api/v1/auth/totp/verify", headers=headers, json={"code": "000000"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "TOTP_INVALID"

    code = pyotp.TOTP(secret).now()
    good = await client.post("/api/v1/auth/totp/verify", headers=headers, json={"code": code})
    assert good.status_code == 200
    assert good.json()["data"]["enabled"] is True

    user_id = await _user_id_from_access(api_sessionmaker, data["access_token"])
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None and user.totp_enabled_at is not None


async def test_totp_setup_twice_conflicts_after_enable(client: AsyncClient) -> None:
    """test totp setup twice conflicts after enable.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        3. Call now.
        4. Call POST /api/v1/auth/totp/verify and read the JSON envelope.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    secret = setup.json()["data"]["secret"]
    code = pyotp.TOTP(secret).now()
    await client.post("/api/v1/auth/totp/verify", headers=headers, json={"code": code})
    again = await client.post("/api/v1/auth/totp/setup", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "TOTP_ALREADY_ENABLED"


async def test_totp_unit_enable_requires_valid_code() -> None:
    """test totp unit enable requires valid code.

    Flow:
        1. Call totp_svc.generate_secret.
        2. Open the database session.
        3. Await totp_svc.enable_totp.
        4. Assert await totp_svc.enable_totp(secret, pyotp.TOTP(secret).now()) is True.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    secret = totp_svc.generate_secret()
    with pytest.raises(TotpInvalid):
        await totp_svc.enable_totp(secret, "000000")
    assert await totp_svc.enable_totp(secret, pyotp.TOTP(secret).now()) is True


async def test_totp_already_enabled_guard_unit() -> None:
    """test totp already enabled guard unit.

    Flow:
        1. Call totp_svc.generate_secret.
        2. Open the database session.
        3. Call totp_svc.assert_not_enabled.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    secret = totp_svc.generate_secret()
    with pytest.raises(TotpAlreadyEnabled):
        totp_svc.assert_not_enabled(secret, object())


async def test_totp_window_tolerance_unit() -> None:
    """valid_window=1 accepts the previous 30s step (clock drift).

    Flow:
        1. Call totp_svc.generate_secret.
        2. Call at.
        3. Assert totp_svc.verify_code(secret, prev_code, valid_window=1) is True.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    secret = totp_svc.generate_secret()
    t0 = dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=30)
    prev_code = pyotp.TOTP(secret).at(t0)
    assert totp_svc.verify_code(secret, prev_code, valid_window=1) is True


async def test_totp_requires_authentication(client: AsyncClient) -> None:
    """test totp requires authentication.

    Flow:
        1. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        2. Expect HTTP 401.
        3. Assert resp.json()['error']['code'] in {'TOKEN_INVALID', 'MISSING_CREDENTIALS'}.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    resp = await client.post("/api/v1/auth/totp/setup")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] in {"TOKEN_INVALID", "MISSING_CREDENTIALS"}


async def test_me_reports_totp_enabled_flag(client: AsyncClient) -> None:
    """test me reports totp enabled flag.

    Flow:
        1. Await _login and keep the result.
        2. Call POST /api/v1/auth/totp/setup and read the JSON envelope.
        3. Call POST /api/v1/auth/totp/verify and read the JSON envelope.
        4. Call GET /api/v1/auth/me and read the JSON envelope.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    setup = await client.post("/api/v1/auth/totp/setup", headers=headers)
    secret = setup.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/totp/verify",
        headers=headers,
        json={"code": pyotp.TOTP(secret).now()},
    )
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["data"]["user"]["totp_enabled"] is True


async def test_stepup_issues_verifiable_token(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """POST /auth/stepup with a fresh OTP -> stepup_token verifiable as JWT.

    Flow:
        1. Await _login and keep the result.
        2. Await _user_id_from_access and keep the result.
        3. Open the database session.
        4. Await session.get and keep the result.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    user_id = await _user_id_from_access(api_sessionmaker, data["access_token"])
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        identifier = user.mobile

    req = await client.post(
        "/api/v1/auth/otp/request", json={"identifier": identifier, "purpose": "LOGIN"}
    )
    otp = req.json()["data"]["dev_otp"]
    resp = await client.post("/api/v1/auth/stepup", headers=headers, json={"otp": otp})
    assert resp.status_code == 200, resp.text
    stepup_token = resp.json()["data"]["stepup_token"]
    assert token_svc.verify_stepup_token(stepup_token) == user_id


async def test_stepup_rejects_wrong_otp(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """test stepup rejects wrong otp.

    Flow:
        1. Await _login and keep the result.
        2. Await _user_id_from_access and keep the result.
        3. Open the database session.
        4. Await session.get and keep the result.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    data, _mobile_used = await _login(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    user_id = await _user_id_from_access(api_sessionmaker, data["access_token"])
    async with api_sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        await client.post(
            "/api/v1/auth/otp/request",
            json={"identifier": user.mobile, "purpose": "LOGIN"},
        )
    resp = await client.post("/api/v1/auth/stepup", headers=headers, json={"otp": "999999"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "OTP_INVALID"


async def test_totp_helper_generates_unique_secrets() -> None:
    """test totp helper generates unique secrets.

    Flow:
        1. Assert len(secrets) == 20.

    Debug:
        A wrong TOTP or OTP is 401. Codes in the previous 30-second window still pass.
    """
    secrets = {totp_svc.generate_secret() for _ in range(20)}
    assert len(secrets) == 20
