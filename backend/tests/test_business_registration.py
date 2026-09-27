"""Task 0.6 — Businesses/registrations CRUD + GSTIN validation tests.

Exit criteria: GSTIN mod-36 checksum + PAN==GSTIN[2..12] tests pass;
all business/registration routes go through the access guard.
"""

from __future__ import annotations

import random
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from app.core.businesses import gstin
from app.core.businesses.service import (
    create_business,
    create_registration,
    get_business_detail,
    list_my_businesses,
    update_business,
    update_registration,
)
from app.db.models.core import FilingScheme
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import register_and_login
from tests.gstin_fixtures import gstin_checksum_valid, make_gstin, make_pan

pytestmark = pytest.mark.asyncio

SessionMaker = async_sessionmaker[Any]


def _mobile() -> str:
    return "9" + "".join(random.SystemRandom().choice("0123456789") for _ in range(9))


# ------------------------------------------------------------------ GSTIN unit


async def test_gstin_checksum_valid_real_vector() -> None:
    """Known-good real GSTINs from published GSTN-spec worked examples."""
    assert gstin_checksum_valid("27AAPFU0939F1ZV")
    assert gstin_checksum_valid("27AAACR5055K1Z7")
    assert gstin_checksum_valid("00AAACR5055K1ZN")


async def test_gstin_rejects_bad_checksum() -> None:
    base = "33AAACC1206D1Z"
    for tail in "ABCDEFGHIJKLMOPQRSTUVWXYZ0123456789":
        if tail != "N":
            assert not gstin_checksum_valid(base + tail)


async def test_gstin_pan_embedding() -> None:
    for _ in range(25):
        pan = make_pan()
        g = make_gstin(pan=pan)
        assert gstin_checksum_valid(g)
        assert g[2:12] == pan


async def test_validate_gstin_enforces_pan_match() -> None:
    pan_a, pan_b = make_pan(), make_pan()
    g_a = make_gstin(pan=pan_a)
    with pytest.raises(ValueError, match="PAN does not match GSTIN"):
        gstin.validate_gstin(g_a, pan=pan_b)


async def test_validate_gstin_accepts_matching_pan() -> None:
    pan = make_pan()
    g = make_gstin(pan=pan)
    assert gstin.validate_gstin(g, pan=pan) == g


async def test_pan_validation_rejects_bad_format() -> None:
    for bad in ["12345", "ABCD1234", "", "ABCD12345F", "ABCDEf1234"]:
        with pytest.raises(ValueError, match="invalid PAN format"):
            gstin.validate_pan(bad)


async def test_pan_validation_uppercases() -> None:
    pan = make_pan()
    assert gstin.validate_pan(pan.lower()) == pan.upper()


# ----------------------------------------------------------- service CRUD


async def _seed_owner_business(
    sessionmaker: SessionMaker,
    client: AsyncClient,
) -> tuple[Any, Any, Any]:
    mobile = _mobile()
    tokens = await register_and_login(client, mobile)
    from app.core.auth.tokens import verify_access_token

    user_id = verify_access_token(tokens["access_token"])
    pan = make_pan()
    async with sessionmaker() as session:
        business = await create_business(
            session,
            legal_name="Test Enterprise",
            pan=pan,
            trade_name="Test Ent",
            created_by=user_id,
        )
        return tokens, business, user_id


async def test_create_business_then_list(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _user_id = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    async with api_sessionmaker() as session:
        rows = await list_my_businesses(session, _user_id)
    ids = [r["id"] for r in rows]
    assert str(business.id) in ids


async def test_create_registration_with_checksum_and_pan_match(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    pan = business.pan
    gstin_str = make_gstin(pan=pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
            registered_address="123 Street, City",
            aato_minor=5_000_000_001,
        )
    assert reg.gstin == gstin_str
    assert reg.gstin[2:12] == pan
    assert reg.state_code == gstin_str[:2]
    assert reg.irn_applicable is True
    assert reg.aato_latest_minor == 5_000_000_001


async def test_irn_threshold_false_below_5cr(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
            aato_minor=4_999_999_999,
        )
    assert reg.irn_applicable is False
    assert reg.aato_latest_minor == 4_999_999_999


async def test_irn_threshold_false_at_exactly_5cr(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
            aato_minor=5_000_000_000,
        )
    assert reg.irn_applicable is False
    assert reg.aato_latest_minor == 5_000_000_000


async def test_irn_threshold_true_above_5cr(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
            aato_minor=5_000_000_001,
        )
    assert reg.irn_applicable is True
    assert reg.aato_latest_minor == 5_000_000_001


async def test_update_registration_irn_threshold_boundary(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
            aato_minor=4_999_999_999,
        )
    async with api_sessionmaker() as session:
        updated = await update_registration(
            session,
            reg.id,
            aato_minor=5_000_000_001,
        )
    assert updated.aato_latest_minor == 5_000_000_001
    assert updated.irn_applicable is True
    # bring it back down
    async with api_sessionmaker() as session:
        updated = await update_registration(
            session,
            reg.id,
            aato_minor=5_000_000_000,
        )
    assert updated.irn_applicable is False


async def test_duplicate_gstin_raises_409(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
        )
    # Same GSTIN under same PAN must be rejected as 409.
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/v1/businesses/{business.id}/registrations",
            json={"gstin": gstin_str},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_update_business_patches_fields(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    _, business, _ = await _seed_owner_business(client=client, sessionmaker=api_sessionmaker)
    async with api_sessionmaker() as session:
        updated = await update_business(
            session,
            business.id,
            legal_name="New Legal Name",
            trade_name="New Trade Name",
        )
    assert updated.legal_name == "New Legal Name"
    assert updated.trade_name == "New Trade Name"


async def test_update_registration_patches_aato_and_scheme(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    _, business, _ = await _seed_owner_business(client=client, sessionmaker=api_sessionmaker)
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
        )
    async with api_sessionmaker() as session:
        updated = await update_registration(
            session,
            reg.id,
            aato_minor=5_000_000_001,
            filing_scheme=FilingScheme.QRMP,
        )
    assert updated.aato_latest_minor == 5_000_000_001
    assert updated.irn_applicable is True
    assert updated.filing_scheme == FilingScheme.QRMP


async def test_get_business_detail_includes_registrations(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    _, business, _ = await _seed_owner_business(client=client, sessionmaker=api_sessionmaker)
    gstin_str = make_gstin(pan=business.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business.id,
            gstin=gstin_str,
        )
    async with api_sessionmaker() as session:
        detail = await get_business_detail(session, business.id)
    assert detail["id"] == str(business.id)
    assert len(detail["registrations"]) == 1
    assert detail["registrations"][0]["gstin"] == reg.gstin


# ------------------------------------------------------------- HTTP / guard


def _app(sessionmaker: SessionMaker) -> Any:
    from app.db.session import get_session

    app = create_app()

    async def _override_session() -> AsyncGenerator[Any, None]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return app


async def test_api_create_business_returns_envelope(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens = await register_and_login(client, _mobile())
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/businesses",
            json={"legal_name": "API Biz", "pan": make_pan()},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["legal_name"] == "API Biz"
    assert body["data"]["pan"] == body["data"]["pan"].upper()


async def test_api_create_registration_goes_through_guard(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business.pan)
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/v1/businesses/{business.id}/registrations",
            json={"gstin": gstin_str, "aato_minor": 5_000_000_001},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["gstin"] == gstin_str
    assert body["data"]["irn_applicable"] is True
    assert body["data"]["gstin"][2:12] == business.pan


async def test_api_get_registration_cross_tenant_404(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens_a, business_a, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    tokens_b, _business_b, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    gstin_str = make_gstin(pan=business_a.pan)
    async with api_sessionmaker() as session:
        reg = await create_registration(
            session,
            business_id=business_a.id,
            gstin=gstin_str,
        )
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get(
            f"/api/v1/registrations/{reg.id}",
            headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
        )
    # Guard resolves registration -> business -> access; 404 is expected and safe.
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] in {"REGISTRATION_NOT_FOUND", "BUSINESS_NOT_FOUND"}


async def test_api_create_registration_rejects_invalid_gstin(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    base_gstin = make_gstin(pan=business.pan)
    bad_gstin = base_gstin[:14] + ("0" if base_gstin[14] != "0" else "1")
    assert len(bad_gstin) == 15
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/v1/businesses/{business.id}/registrations",
            json={"gstin": bad_gstin},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert resp.status_code == 422


async def test_api_create_registration_rejects_pan_mismatch(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens, business, _ = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    other_pan = make_pan()
    while other_pan == business.pan:
        other_pan = make_pan()
    other_gstin = make_gstin(pan=other_pan)
    assert other_gstin[2:12] != business.pan
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            f"/api/v1/businesses/{business.id}/registrations",
            json={"gstin": other_gstin},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert resp.status_code == 422
    assert "PAN does not match GSTIN" in resp.text or "does not match" in resp.text


async def test_api_business_routes_401_without_token(
    api_sessionmaker: SessionMaker,
) -> None:
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        for method, url in [
            ("post", "/api/v1/businesses"),
            ("get", "/api/v1/businesses"),
            ("get", f"/api/v1/businesses/{'00000000-0000-0000-0000-000000000000'}"),
        ]:
            resp = await getattr(ac, method)(url)
            assert resp.status_code == 401


async def test_duplicate_pan_returns_409(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens = await register_and_login(client, _mobile())
    pan = make_pan()
    app = _app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        first = await ac.post(
            "/api/v1/businesses",
            json={"legal_name": "First", "pan": pan},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert first.status_code == 200
        second = await ac.post(
            "/api/v1/businesses",
            json={"legal_name": "Second", "pan": pan},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert second.status_code == 409


async def test_concurrent_duplicate_pan_returns_409(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    """Two sessions racing for the same PAN must produce exactly one 409."""
    import asyncio

    tokens_a = await register_and_login(client, _mobile())
    tokens_b = await register_and_login(client, _mobile())
    pan = make_pan()
    app = _app(api_sessionmaker)

    async def _create(tok: str) -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/api/v1/businesses",
                json={"legal_name": "Race", "pan": pan},
                headers={"Authorization": f"Bearer {tok}"},
            )
        return resp.status_code

    codes = await asyncio.gather(
        _create(tokens_a["access_token"]),
        _create(tokens_b["access_token"]),
    )
    assert sorted(codes) == [200, 409]


async def test_business_list_only_returns_memberships(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    tokens_a, business_a, uid_a = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    tokens_b, _business_b, uid_b = await _seed_owner_business(
        client=client, sessionmaker=api_sessionmaker
    )
    # business_b should not appear in A's list
    async with api_sessionmaker() as session:
        a_rows = await list_my_businesses(session, uid_a)
        b_rows = await list_my_businesses(session, uid_b)
    a_ids = {r["id"] for r in a_rows}
    b_ids = {r["id"] for r in b_rows}
    assert str(business_a.id) in a_ids
    assert str(business_a.id) not in b_ids
