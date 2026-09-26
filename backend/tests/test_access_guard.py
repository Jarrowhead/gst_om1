"""Task 0.5 — cross-tenant access matrix + audit logging (SECURITY §7).

The matrix (parameterized rows inside one live run): every actor class x
every target must yield either clean access or a 404/403 envelope that
carries ZERO business data. 404 over 403 — existence is data (API_SPEC
non-negotiable #2). Audit rows are append-only, proven by live
UPDATE/DELETE failures.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import pytest
from app.core import access as access_mod
from app.core.auth import tokens as token_svc
from app.db.models.core import (
    AuditLog,
    Business,
    BusinessRole,
    BusinessUser,
    CaClientLink,
    CaFirm,
    CaFirmMember,
    FilingScheme,
    FirmRole,
    GstRegistration,
    LinkInitiatedBy,
    LinkStatus,
)
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import _register_and_login
from tests.gstin_fixtures import gstin_checksum_valid, make_gstin, make_pan

pytestmark = pytest.mark.asyncio


SessionMaker = async_sessionmaker[Any]


# ------------------------------------------------------------------ helpers


async def _seed_business(
    sessionmaker: SessionMaker,
    pan: str,
    legal_name: str,
    gstin: str,
    state_code: str = "27",
) -> tuple[Business, GstRegistration]:
    """Business + one registration (checksum-valid fixture GSTIN)."""
    assert gstin_checksum_valid(gstin), "fixture GSTIN must pass mod-36"
    assert gstin[2:12] == pan, "PAN must equal GSTIN[2:12] (API cross-check)"
    async with sessionmaker() as session:
        biz = Business(pan=pan, legal_name=legal_name)
        session.add(biz)
        await session.flush()
        reg = GstRegistration(
            business_id=biz.id,
            gstin=gstin,
            state_code=state_code,
            filing_scheme=FilingScheme.REGULAR_MONTHLY,
        )
        session.add(reg)
        await session.commit()
        await session.refresh(biz)
        await session.refresh(reg)
        return biz, reg


async def _grant_client(
    sessionmaker: SessionMaker,
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    role: BusinessRole,
) -> None:
    async with sessionmaker() as session:
        session.add(BusinessUser(business_id=business_id, user_id=user_id, role=role))
        await session.commit()


async def _seed_firm(sessionmaker: SessionMaker, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Firm + PARTNER membership for the user."""
    async with sessionmaker() as session:
        firm = CaFirm(
            firm_name=f"Firm {uuid.uuid4().hex[:6]}",
            ca_code=f"CA{uuid.uuid4().hex[:8].upper()}",
            pan=make_pan(),
        )
        session.add(firm)
        await session.flush()
        member = CaFirmMember(
            firm_id=firm.id,
            user_id=user_id,
            role=FirmRole.PARTNER,
            can_export=True,
            can_revoke=True,
            can_invite_members=True,
        )
        session.add(member)
        await session.commit()
        return firm.id, member.id


async def _link_firm_business(
    sessionmaker: SessionMaker,
    firm_id: uuid.UUID,
    business_id: uuid.UUID,
    status: LinkStatus = LinkStatus.ACTIVE,
) -> None:
    async with sessionmaker() as session:
        session.add(
            CaClientLink(
                ca_firm_id=firm_id,
                business_id=business_id,
                status=status,
                initiated_by=LinkInitiatedBy.CLIENT_INVITE,
            )
        )
        await session.commit()


def _mobile() -> str:
    """Unique per-call mobile — the suite shares the live dev DB.

    Digit-only (identifier validator requires isdigit, 10-13 chars); 10^9
    space with a live-DB retry-free design matches test_auth_totp's approach.
    """
    import random

    return "9" + "".join(random.SystemRandom().choice("0123456789") for _ in range(9))


# Probe app: guard-protected routes wired exactly as production routers will
# wire them (dependency factories, no ad-hoc checks).


def _guard_app(sessionmaker: SessionMaker) -> Any:
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override_session() -> AsyncGenerator[Any, None]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    router = APIRouter(prefix="/api/v1/probe", tags=["probe"])

    biz_guard = access_mod.require_business_access("business_id")
    reg_guard = access_mod.require_registration_access("registration_id")

    @router.get("/business/{business_id}")
    async def business_probe(
        access: Annotated[Any, Depends(biz_guard)],
    ) -> dict[str, object]:
        return {
            "success": True,
            "data": {
                "business_id": str(access.business_id),
                "role": access.role,
                "via": access.via,
            },
        }

    @router.get("/business/{business_id}/restricted")
    async def restricted_probe(
        access: Annotated[Any, Depends(biz_guard)],
    ) -> dict[str, object]:
        if not access.can_revoke:
            raise access_mod.PermissionDenied("can_revoke required")
        return {"success": True, "data": {"revoked": True}}

    @router.get("/registration/{registration_id}")
    async def registration_probe(
        racc: Annotated[Any, Depends(reg_guard)],
    ) -> dict[str, object]:
        return {"success": True, "data": {"gstin": racc.gstin, "via": racc.access.via}}

    app.include_router(router)
    return app


# -------------------------------------------------- fixture GSTIN sanity


async def test_fixture_gstin_passes_mod36_and_pan_embedding() -> None:
    for _ in range(25):
        pan = make_pan()
        g = make_gstin(pan=pan)
        assert gstin_checksum_valid(g)
        assert g[2:12] == pan


# ------------------------------------------------------- cross-tenant MATRIX
# Actor classes: client-OWNER, client-CLERK, outsider (no grants),
# firm-PARTNER(ACTIVE link), firm-member(REVOKED link), firm-CLERK (no perms).
# Targets: own business, other business, nonexistent uuid.
# Grant -> 200 with data; everything else -> 404/403 envelope, zero data.


async def test_cross_tenant_matrix(client: AsyncClient, api_sessionmaker: SessionMaker) -> None:
    pan_a, pan_b = make_pan(), make_pan()
    biz_a, reg_a = await _seed_business(
        api_sessionmaker, pan_a, "Tenant A Pvt Ltd", make_gstin(pan=pan_a)
    )
    _biz_b, reg_b = await _seed_business(
        api_sessionmaker, pan_b, "Tenant B Pvt Ltd", make_gstin(pan=pan_b)
    )

    owner = await _register_and_login(client, _mobile())
    clerk = await _register_and_login(client, _mobile())
    outsider = await _register_and_login(client, _mobile())
    firm_user = await _register_and_login(client, _mobile())
    revoked_firm_user = await _register_and_login(client, _mobile())

    await _grant_client(
        api_sessionmaker,
        biz_a.id,
        token_svc.verify_access_token(owner["access_token"]),
        BusinessRole.OWNER,
    )
    await _grant_client(
        api_sessionmaker,
        biz_a.id,
        token_svc.verify_access_token(clerk["access_token"]),
        BusinessRole.CLERK,
    )
    firm_id, _member_id = await _seed_firm(
        api_sessionmaker, token_svc.verify_access_token(firm_user["access_token"])
    )
    await _link_firm_business(api_sessionmaker, firm_id, biz_a.id, LinkStatus.ACTIVE)
    rev_firm_id, _ = await _seed_firm(
        api_sessionmaker, token_svc.verify_access_token(revoked_firm_user["access_token"])
    )
    await _link_firm_business(api_sessionmaker, rev_firm_id, biz_a.id, LinkStatus.REVOKED)

    async with api_sessionmaker() as s:
        biz_b = (await s.execute(select(Business).where(Business.pan == pan_b))).scalar_one()
        biz_b_id = biz_b.id
    fake = uuid.uuid4()

    # (label, token, url, expect_data) — expect_data=False rows assert 404/403
    # envelope with zero business data (no PAN, no legal_name).
    def _tok(d: dict[str, Any]) -> str:
        return d["access_token"]

    def _row(label: str, tok: str, path: str, ident: Any, ok: bool) -> tuple[str, str, str, bool]:
        url = f"/api/v1/probe/{path}/{ident}"
        return (label, tok, url, ok)

    rows: list[tuple[str, str, str, bool]] = [
        _row("owner->biz_a", _tok(owner), "business", biz_a.id, True),
        _row("clerk->biz_a", _tok(clerk), "business", biz_a.id, True),
        _row("firm_active->biz_a", _tok(firm_user), "business", biz_a.id, True),
        _row("firm_revoked->biz_a", _tok(revoked_firm_user), "business", biz_a.id, False),
        _row("owner->biz_b", _tok(owner), "business", biz_b_id, False),
        _row("clerk->biz_b", _tok(clerk), "business", biz_b_id, False),
        _row("firm_active->biz_b", _tok(firm_user), "business", biz_b_id, False),
        _row("firm_revoked->biz_b", _tok(revoked_firm_user), "business", biz_b_id, False),
        _row("owner->nonexistent", _tok(owner), "business", fake, False),
        _row("firm_active->nonexistent", _tok(firm_user), "business", fake, False),
        _row("firm_revoked->nonexistent", _tok(revoked_firm_user), "business", fake, False),
        # reg-scoped guard: same tenant rules through the reg -> biz chain
        _row("owner->reg_a", _tok(owner), "registration", reg_a.id, True),
        _row("firm_active->reg_a", _tok(firm_user), "registration", reg_a.id, True),
        _row("owner->reg_b", _tok(owner), "registration", reg_b.id, False),
        _row("firm_active->reg_b", _tok(firm_user), "registration", reg_b.id, False),
        _row("firm_revoked->reg_a", _tok(revoked_firm_user), "registration", reg_a.id, False),
        # outsider: no grants at all -> 404 on every target
        _row("outsider->biz_a", _tok(outsider), "business", biz_a.id, False),
        _row("outsider->biz_b", _tok(outsider), "business", biz_b_id, False),
        _row("outsider->nonexistent", _tok(outsider), "business", fake, False),
    ]

    app = _guard_app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        for label, token, url, expect_data in rows:
            resp = await ac.get(url, headers={"Authorization": f"Bearer {token}"})
            body: dict[str, Any] = {}
            try:
                body = resp.json()
            except Exception:
                body = {}
            dumped = str(body)
            if expect_data:
                assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text}"
                assert body.get("success") is True
                assert body["data"], f"{label}: must carry data"
            else:
                assert resp.status_code in (403, 404), f"{label}: {resp.status_code} {resp.text}"
                # envelope carries zero business data
                assert pan_a not in dumped and pan_b not in dumped, f"{label}: PAN leak"
                assert "legal_name" not in dumped, f"{label}: name leak"
                assert pan_a not in resp.text and pan_b not in resp.text, f"{label}: PAN leak raw"
                assert "Tenant A" not in dumped and "Tenant B" not in dumped, f"{label}: name leak"
                err = body.get("error", {})
                assert err.get("code") in {"BUSINESS_NOT_FOUND", "FORBIDDEN"}, f"{label}: {err}"

        # malformed uuid on a guarded path -> 404 envelope, not 500
        resp = await ac.get(
            "/api/v1/probe/business/not-a-uuid",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "BUSINESS_NOT_FOUND"

        # unauthenticated -> 401 (guard never answers 200 without a bearer)
        resp = await ac.get(f"/api/v1/probe/business/{biz_a.id}")
        assert resp.status_code == 401

        # granular firm permission: CLERK-role firm member, can_revoke=False
        # -> route reachable (guard passes) but 403 on the permission gate
        clerk_firm_user = await _register_and_login(client, _mobile())
        cf_uid = token_svc.verify_access_token(clerk_firm_user["access_token"])
        cf_firm_id, _ = await _seed_firm(api_sessionmaker, cf_uid)
        async with api_sessionmaker() as s:
            m = (
                await s.execute(
                    select(CaFirmMember).where(
                        CaFirmMember.firm_id == cf_firm_id, CaFirmMember.user_id == cf_uid
                    )
                )
            ).scalar_one()
            m.role = FirmRole.CLERK
            m.can_revoke = False
            m.can_export = False
            await s.commit()
        await _link_firm_business(api_sessionmaker, cf_firm_id, biz_a.id, LinkStatus.ACTIVE)
        resp = await ac.get(
            f"/api/v1/probe/business/{biz_a.id}/restricted",
            headers={"Authorization": f"Bearer {clerk_firm_user['access_token']}"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_guard_denies_revoked_link_immediately(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Revoke = instant access death (TESTING_STRATEGY §6 #5, no cache)."""
    pan = make_pan()
    biz, _reg = await _seed_business(api_sessionmaker, pan, "Revoke Co", make_gstin(pan=pan))
    fu = await _register_and_login(client, _mobile())
    uid = token_svc.verify_access_token(fu["access_token"])
    firm_id, _ = await _seed_firm(api_sessionmaker, uid)
    await _link_firm_business(api_sessionmaker, firm_id, biz.id, LinkStatus.ACTIVE)

    app = _guard_app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        ok = await ac.get(
            f"/api/v1/probe/business/{biz.id}",
            headers={"Authorization": f"Bearer {fu['access_token']}"},
        )
        assert ok.status_code == 200

        async with api_sessionmaker() as s:
            link = (
                await s.execute(select(CaClientLink).where(CaClientLink.ca_firm_id == firm_id))
            ).scalar_one()
            link.status = LinkStatus.REVOKED
            await s.commit()

        dead = await ac.get(
            f"/api/v1/probe/business/{biz.id}",
            headers={"Authorization": f"Bearer {fu['access_token']}"},
        )
        assert dead.status_code == 404
        assert dead.json()["error"]["code"] == "BUSINESS_NOT_FOUND"


# ----------------------------------------------------------------- audit


async def test_audit_writer_appends_rows(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    from app.core.access import audit
    from app.db.models.core import User

    bid = rid = fid = None  # FK columns stay NULL — no real rows needed
    view_id = uuid.uuid4().hex  # unique entity_id isolates this run's row
    async with api_sessionmaker() as session:
        # actor row must exist (audit_logs.actor_user_id is a real FK)
        actor = User(mobile=_mobile(), full_name="Audit Actor")
        session.add(actor)
        await session.flush()

        await audit(
            session,
            action="BUSINESS_VIEWED",
            entity="business",
            entity_id=view_id,
            actor_user_id=actor.id,
            business_id=bid,
            registration_id=rid,
            ca_firm_id=fid,
            payload_diff={"before": None, "after": {"status": "ACTIVE"}},
        )
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "BUSINESS_VIEWED",
                        AuditLog.entity_id == view_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"expected 1 audit row, found {len(rows)}"
        row = rows[0]
        assert row.actor_user_id == actor.id
        assert row.payload_diff == {"before": None, "after": {"status": "ACTIVE"}}


async def test_audit_logs_table_is_append_only(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """UPDATE/DELETE on core.audit_logs must fail (SECURITY §5 DB-grant rule).

    Proven against the dedicated restricted role `gst_app` (migration
    a41c7e2d90f5): INSERT allowed, UPDATE/DELETE raise. The dev superuser
    `gst` bypasses grants, so the tamper attempts MUST run as gst_app.
    """
    import os

    import psycopg
    from app.core.access import audit

    password = os.environ.get("GST_APP_ROLE_PASSWORD", "gst_app_dev_pass")
    app_url = f"postgresql://gst_app:{password}@127.0.0.1:5436/gst_filing_db"

    async with api_sessionmaker() as session:
        probe_id = uuid.uuid4().hex
        await audit(
            session,
            action="PROBE_EVENT",
            entity="probe",
            entity_id=probe_id,
            actor_user_id=None,
        )
        await session.commit()

    with psycopg.connect(app_url) as conn, conn.cursor() as cur:
        # INSERT works for the app role (with FK-nullables set to NULL)
        cur.execute(
            "INSERT INTO core.audit_logs (id, action, entity, entity_id) "
            "VALUES (gen_random_uuid(), 'PROBE_EVENT2', 'probe', %s)",
            (probe_id,),
        )
        conn.commit()

        # UPDATE must raise
        try:
            cur.execute(
                "UPDATE core.audit_logs SET action = 'TAMPERED' WHERE entity_id = %s",
                (probe_id,),
            )
            conn.commit()
            tamper_update = False
        except psycopg.Error:
            conn.rollback()
            tamper_update = True
        assert tamper_update, "gst_app must NOT be able to UPDATE audit_logs"

        # DELETE must raise
        try:
            cur.execute(
                "DELETE FROM core.audit_logs WHERE entity_id = %s",
                (probe_id,),
            )
            conn.commit()
            tamper_delete = False
        except psycopg.Error:
            conn.rollback()
            tamper_delete = True
        assert tamper_delete, "gst_app must NOT be able to DELETE audit_logs"

    # row survives unchanged
    async with api_sessionmaker() as session:
        remaining = (
            (await session.execute(select(AuditLog).where(AuditLog.entity_id == probe_id)))
            .scalars()
            .all()
        )
        actions = sorted(r.action for r in remaining)
        assert actions == ["PROBE_EVENT", "PROBE_EVENT2"], actions
