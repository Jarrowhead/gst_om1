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
    """Insert a business and one registration using a checksum-valid fixture GSTIN.

    Flow:
        Assert checksum and PAN embedding, insert Business, flush, insert GstRegistration, commit.

    Debug:
        Assertion here means make_gstin and make_pan were not paired.
    """
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
    """Insert a business_users row so the user is a client of that business.

    Debug:
        Access tests that expect 404 should not call this for the denied actor.

    Flow:
        1. Open the database session.
        2. Call session.add.

    """
    async with sessionmaker() as session:
        session.add(BusinessUser(business_id=business_id, user_id=user_id, role=role))
        await session.commit()


async def _seed_firm(sessionmaker: SessionMaker, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a CA firm and a PARTNER membership. Returns (firm_id, member_id).

    Flow:
        Random firm_name and ca_code so unique constraints do not collide across tests.

    Debug:
        Firm access still needs an ACTIVE ca_client_links row; membership alone is not enough.
    """
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
    """Insert a firm-to-business link with the given status.

    Flow:
        1. Open the database session.
        2. Call session.add.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
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

    Flow:
        1. Return a 10-digit mobile that starts with 9.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    import random

    return "9" + "".join(random.SystemRandom().choice("0123456789") for _ in range(9))


def _row(label: str, token: str, kind: str, ident: Any, ok: bool) -> dict[str, Any]:
    """One cross-tenant matrix row (label, bearer token, probe kind, target, expect).

    Flow:
        1. Return one matrix row: label, token, kind, ident, and the expected result.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    return {"label": label, "token": token, "kind": kind, "ident": ident, "expect": ok}


# Probe app: guard-protected routes wired exactly as production routers will
# wire them (dependency factories, no ad-hoc checks).


def _guard_app(sessionmaker: SessionMaker) -> Any:
    """App with probe routes that use the production access guards.

    Flow:
        1. Call create_app.
        2. Call APIRouter.
        3. Call access_mod.require_business_access.
        4. Call access_mod.require_registration_access.

    Debug:
        This helper feeds the tests below. A bad fixture fails before the route is called.
    """
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    async def _override_session() -> AsyncGenerator[Any, None]:
        """Yield one session from the test session factory.

        Flow:
            1. Open the database session.

        Debug:
            This helper feeds the tests below. A bad fixture fails before the route is called.
        """
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
        """business probe.

        Flow:
            1. Return the business id, role, and how access was granted.

        Debug:
            Trace this function from its caller. API errors use the {success, error} envelope.
        """
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
        """restricted probe.

        Flow:
            1. Raise when the check fails.
            2. Return success once can_revoke is true.

        Debug:
            Trace this function from its caller. API errors use the {success, error} envelope.
        """
        if not access.can_revoke:
            raise access_mod.PermissionDenied("can_revoke required")
        return {"success": True, "data": {"revoked": True}}

    @router.get("/registration/{registration_id}")
    async def registration_probe(
        racc: Annotated[Any, Depends(reg_guard)],
    ) -> dict[str, object]:
        """registration probe.

        Flow:
            1. Return the GSTIN and how registration access was granted.

        Debug:
            Trace this function from its caller. API errors use the {success, error} envelope.
        """
        return {"success": True, "data": {"gstin": racc.gstin, "via": racc.access.via}}

    app.include_router(router)
    return app


# -------------------------------------------------- fixture GSTIN sanity


async def test_fixture_gstin_passes_mod36_and_pan_embedding() -> None:
    """test fixture gstin passes mod36 and pan embedding.

    Flow:
        1. For each case: call make_pan.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
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


# ------------------------------------------------------- cross-tenant MATRIX
# Actor classes: client-OWNER, client-CLERK, outsider (no grants),
# firm-PARTNER(ACTIVE link), firm-member(REVOKED link), firm-CLERK (no perms).
# Targets: own business, other business, nonexistent uuid.
# Grant -> 200 with data; everything else -> 404/403 envelope, zero data.
async def test_cross_tenant_matrix(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    """test cross tenant matrix.

    Flow:
        1. Await _seed_business and keep the result.
        2. Await _register_and_login and keep the result.
        3. Await _grant_client.
        4. Await _seed_firm and keep the result.

    Debug:
        Another tenant's id is 404, the same shape as a missing row.
    """
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

    # Build rows list for parameterized testing (same data across all parameters)
    tk_o = owner["access_token"]
    tk_c = clerk["access_token"]
    tk_f = firm_user["access_token"]
    tk_r = revoked_firm_user["access_token"]
    tk_x = outsider["access_token"]
    rows = [
        _row("owner->biz_a", tk_o, "business", biz_a.id, True),
        _row("clerk->biz_a", tk_c, "business", biz_a.id, True),
        _row("firm_active->biz_a", tk_f, "business", biz_a.id, True),
        _row("firm_revoked->biz_a", tk_r, "business", biz_a.id, False),
        _row("owner->biz_b", tk_o, "business", biz_b_id, False),
        _row("clerk->biz_b", tk_c, "business", biz_b_id, False),
        _row("firm_active->biz_b", tk_f, "business", biz_b_id, False),
        _row("firm_revoked->biz_b", tk_r, "business", biz_b_id, False),
        _row("owner->nonexistent", tk_o, "business", fake, False),
        _row("firm_active->nonexistent", tk_f, "business", fake, False),
        _row("firm_revoked->nonexistent", tk_r, "business", fake, False),
        _row("owner->reg_a", tk_o, "registration", reg_a.id, True),
        _row("firm_active->reg_a", tk_f, "registration", reg_a.id, True),
        _row("owner->reg_b", tk_o, "registration", reg_b.id, False),
        _row("firm_active->reg_b", tk_f, "registration", reg_b.id, False),
        _row("firm_revoked->reg_a", tk_r, "registration", reg_a.id, False),
        _row("outsider->biz_a", tk_x, "business", biz_a.id, False),
        _row("outsider->biz_b", tk_x, "business", biz_b_id, False),
        _row("outsider->nonexistent", tk_x, "business", fake, False),
    ]

    checked = 0
    app = _guard_app(api_sessionmaker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        for row in rows:
            label = row["label"]
            token = row["token"]
            kind = row["kind"]
            ident = row["ident"]
            expect_data = row["expect"]
            url = f"/api/v1/probe/{kind}/{ident}"
            resp = await ac.get(url, headers={"Authorization": f"Bearer {token}"})
            body: dict[str, Any] = {}
            try:
                body = resp.json()
            except Exception:
                body = {}
            checked += 1
            dumped = str(body)
            if expect_data:
                assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text}"
                assert body.get("success") is True, f"{label}: envelope success != True"
                assert body.get("data"), f"{label}: must carry data"
            else:
                assert resp.status_code in (403, 404), f"{label}: {resp.status_code} {resp.text}"
                # envelope carries zero business data
                assert pan_a not in dumped and pan_b not in dumped, f"{label}: PAN leak"
                assert "legal_name" not in dumped, f"{label}: name leak"
                assert pan_a not in resp.text and pan_b not in resp.text, f"{label}: PAN leak raw"
                assert "Tenant A" not in dumped and "Tenant B" not in dumped, f"{label}: name leak"
                err = body.get("error", {})
                assert err.get("code") in {
                    "BUSINESS_NOT_FOUND",
                    "FORBIDDEN",
                    "REGISTRATION_NOT_FOUND",
                }, f"{label}: {err}"
    assert checked == len(rows) == 19, f"matrix must exercise all 19 rows, ran {checked}"


async def test_guard_denies_revoked_link_immediately(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Revoke = instant access death (TESTING_STRATEGY §6 #5, no cache).

    Flow:
        1. Call make_pan.
        2. Await _seed_business and keep the result.
        3. Await _register_and_login and keep the result.
        4. Call token_svc.verify_access_token.

    Debug:
        Failure text is the assertion message. API errors use {success:false, error:{code,message}}.
    """
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
    """test audit writer appends rows.

    Flow:
        1. Open the database session.
        2. Call User.

    Debug:
        audit.audit_logs accepts INSERT only. UPDATE and DELETE must fail.
    """
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

    Flow:
        1. Call os.environ.get.
        2. Open the database session.
        3. Await audit.
        4. Call cur.execute.

    Debug:
        audit.audit_logs accepts INSERT only. UPDATE and DELETE must fail.
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
