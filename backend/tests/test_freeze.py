"""Tests for Task 12.1: Period Freeze/Unlock & Maker-Checker workflow."""

from __future__ import annotations

from typing import Any

import pytest
from app.api.errors import ServiceError
from app.core.auth.tokens import verify_access_token
from app.db.models.core import AccessRole, AuditLog, UserGstAccess
from app.db.models.gst import FilingPeriod, FilingStatus
from app.services.review import _ensure_open_period
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_email, register_and_login
from tests.gstin_fixtures import make_gstin

pytestmark = pytest.mark.asyncio

SessionMaker = async_sessionmaker[Any]


async def test_freeze_and_unfreeze_flow(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    # 1. Register owner (ADMIN) and create GST account
    owner_tokens = await register_and_login(client, make_email())
    owner_hdr = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    gstin = make_gstin()
    fp = "042026"

    res_acc = await client.post(
        "/api/v1/gst-accounts",
        headers=owner_hdr,
        json={"gstin": gstin, "legal_name": "Freeze Corp"},
    )
    assert res_acc.status_code in (200, 201)

    # 2. Register collaborator with FILER role directly in UserGstAccess
    filer_email = make_email()
    filer_tokens = await register_and_login(client, filer_email)
    filer_id = verify_access_token(filer_tokens["access_token"])
    filer_hdr = {"Authorization": f"Bearer {filer_tokens['access_token']}"}

    async with api_sessionmaker() as session:
        session.add(UserGstAccess(user_id=filer_id, gstin=gstin, role=AccessRole.FILER))
        await session.commit()

    # 3. Initial status check
    res = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/freeze/status",
        headers=owner_hdr,
    )
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "OPEN"
    assert res.json()["data"]["can_freeze"] is True
    assert res.json()["data"]["can_unfreeze"] is False

    # 4. FILER tries to freeze -> 403 Forbidden
    res_filer = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/freeze",
        headers=filer_hdr,
    )
    assert res_filer.status_code == 403
    assert res_filer.json()["error"]["code"] == "FORBIDDEN"

    # 5. ADMIN freezes -> 200 OK
    res_admin = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/freeze",
        headers=owner_hdr,
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["data"]["status"] == "FROZEN"

    # Verify status endpoint reflects FROZEN
    res_st = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/freeze/status",
        headers=owner_hdr,
    )
    assert res_st.status_code == 200
    assert res_st.json()["data"]["is_frozen"] is True
    assert res_st.json()["data"]["can_unfreeze"] is True

    # 6. While period is FROZEN, _ensure_open_period raises 423 PERIOD_LOCKED
    async with api_sessionmaker() as session:
        with pytest.raises(ServiceError) as exc_info:
            await _ensure_open_period(session, gstin, fp)
        assert exc_info.value.status_code == 423
        assert exc_info.value.code == "PERIOD_LOCKED"

    # 7. FILER tries to unfreeze -> 403 Forbidden
    res_unfreeze_f = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/unfreeze",
        headers=filer_hdr,
    )
    assert res_unfreeze_f.status_code == 403

    # 8. ADMIN unfreezes -> 200 OK
    res_unfreeze_a = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/unfreeze",
        headers=owner_hdr,
    )
    assert res_unfreeze_a.status_code == 200
    assert res_unfreeze_a.json()["data"]["status"] == "OPEN"

    # 9. After unfreezing, _ensure_open_period succeeds
    async with api_sessionmaker() as session:
        period = await _ensure_open_period(session, gstin, fp)
        assert period.status == FilingStatus.OPEN

    # 10. Verify append-only audit entries
    async with api_sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.gstin == gstin).order_by(AuditLog.at)
            )
        ).scalars().all()
        actions = [a.action for a in audits]
        assert "FREEZE_PERIOD" in actions
        assert "UNLOCK_PERIOD" in actions


async def test_cannot_freeze_or_unfreeze_filed_period(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    owner_tokens = await register_and_login(client, make_email())
    owner_hdr = {"Authorization": f"Bearer {owner_tokens['access_token']}"}
    gstin = make_gstin()
    fp = "052026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=owner_hdr,
        json={"gstin": gstin, "legal_name": "Filed Test Corp"},
    )

    # Mark period as FILED in DB
    async with api_sessionmaker() as session:
        p = FilingPeriod(
            gstin=gstin,
            fp=fp,
            scheme_snapshot="REGULAR_MONTHLY",
            status=FilingStatus.FILED,
        )
        session.add(p)
        await session.commit()

    # Attempt freeze on FILED period -> 400
    res1 = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/freeze",
        headers=owner_hdr,
    )
    assert res1.status_code == 400
    assert res1.json()["error"]["code"] == "CANNOT_FREEZE_FILED"

    # Attempt unfreeze on FILED period -> 400
    res2 = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/unfreeze",
        headers=owner_hdr,
    )
    assert res2.status_code == 400
    assert res2.json()["error"]["code"] == "CANNOT_UNFREEZE_FILED"
