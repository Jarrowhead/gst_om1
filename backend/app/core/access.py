"""Access guard + audit writer (SECURITY_AND_ACCESS.md §2/§5).

require_business_access is the SINGLE FastAPI dependency behind every
business/registration-scoped route (SECURITY §2 — never per-route ad-hoc
checks). It resolves user → business (via business_users) OR user → firm →
ACTIVE ca_client_link → business, and returns the tenant context plus the
firm member's granular permissions (None for client users).

Denial contract (API_SPECIFICATION.md non-negotiable #1/#2):
  unknown business id      -> 404 BUSINESS_NOT_FOUND (404 over 403 —
                              existence is data, so wrong-tenant reads and
                              nonexistent reads are indistinguishable)
  no grant / link missing
  or not ACTIVE            -> 404 as above, never 403 with a body that
                              reveals data
  authenticated but firm
  permission missing       -> 403 FORBIDDEN (route was reachable; the guard
                              can say so without leaking business data)
  unauthenticated          -> 401 from require_user before this dependency

Audit rows are append-only: the writer INSERTs only and the DB role is
granted INSERT-only on core.audit_logs (SECURITY §5); no update/delete path
exists in code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import require_user
from app.core.auth.errors import AuthError
from app.db.models.core import (
    BusinessRole,
    BusinessUser,
    CaClientLink,
    CaFirmMember,
    FirmRole,
    GstRegistration,
    LinkStatus,
)
from app.db.session import get_session


class AccessDenied(AuthError):
    """Wrong tenant or unknown object — 404, existence must not leak."""

    status_code = 404
    code = "BUSINESS_NOT_FOUND"


class PermissionDenied(AuthError):
    """Authenticated + linked, but the route's permission bit is off — 403."""

    status_code = 403
    code = "FORBIDDEN"


@dataclass(frozen=True, slots=True)
class BusinessAccess:
    """Resolved tenant context for one business."""

    business_id: uuid.UUID
    role: str  # "OWNER" / "CLERK" (client) or "PARTNER"/"CA"/"CLERK" (firm)
    via: str  # "client" | "firm"
    firm_id: uuid.UUID | None
    member_id: uuid.UUID | None
    can_export: bool
    can_revoke: bool
    can_invite_members: bool


@dataclass(frozen=True, slots=True)
class RegistrationAccess:
    """Tenant context for a registration (business chain pre-resolved)."""

    registration_id: uuid.UUID
    business_id: uuid.UUID
    gstin: str
    access: BusinessAccess


async def resolve_business_access(
    session: AsyncSession, user_id: uuid.UUID, business_id: uuid.UUID
) -> BusinessAccess:
    """User -> business access chain (SECURITY §2). Raises AccessDenied.

    Chain 1 (client): business_users row with role OWNER/CLERK.
    Chain 2 (firm):   ca_firm_member -> ACTIVE ca_client_link -> business.
    """
    # --- client chain -----------------------------------------------------
    bu = (
        await session.execute(
            select(BusinessUser).where(
                BusinessUser.business_id == business_id,
                BusinessUser.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if bu is not None:
        return BusinessAccess(
            business_id=business_id,
            role=bu.role.value if isinstance(bu.role, BusinessRole) else str(bu.role),
            via="client",
            firm_id=None,
            member_id=None,
            can_export=True,
            can_revoke=True,
            can_invite_members=True,
        )

    # --- CA firm chain ----------------------------------------------------
    member_rows = (
        await session.execute(select(CaFirmMember).where(CaFirmMember.user_id == user_id))
    ).scalars()
    for member in member_rows:
        link = (
            await session.execute(
                select(CaClientLink).where(
                    CaClientLink.ca_firm_id == member.firm_id,
                    CaClientLink.business_id == business_id,
                    CaClientLink.status == LinkStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if link is not None:
            return BusinessAccess(
                business_id=business_id,
                role=member.role.value if isinstance(member.role, FirmRole) else str(member.role),
                via="firm",
                firm_id=member.firm_id,
                member_id=member.id,
                can_export=member.can_export,
                can_revoke=member.can_revoke,
                can_invite_members=member.can_invite_members,
            )

    raise AccessDenied("no access to this business")


async def resolve_registration_access(
    session: AsyncSession, user_id: uuid.UUID, registration_id: uuid.UUID
) -> RegistrationAccess:
    """Every filing object resolves through registration -> business (§2)."""
    reg = await session.get(GstRegistration, registration_id)
    if reg is None:
        raise AccessDenied("no access to this registration")
    access = await resolve_business_access(session, user_id, reg.business_id)
    return RegistrationAccess(
        registration_id=reg.id,
        business_id=reg.business_id,
        gstin=reg.gstin,
        access=access,
    )


def require_business_access(
    business_id_param: str = "business_id",
) -> Any:
    """Dependency factory: guard a route by business_id (path param name)."""

    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> BusinessAccess:
        raw = request.path_params.get(business_id_param)
        try:
            bid = uuid.UUID(str(raw))
        except ValueError as exc:
            raise AccessDenied("no access to this business") from exc
        return await resolve_business_access(session, user_id, bid)

    return _guard


def require_registration_access(
    registration_id_param: str = "registration_id",
) -> Any:
    """Dependency factory: guard a route by registration_id (path param name)."""

    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> RegistrationAccess:
        raw = request.path_params.get(registration_id_param)
        try:
            rid = uuid.UUID(str(raw))
        except ValueError as exc:
            raise AccessDenied("no access to this registration") from exc
        return await resolve_registration_access(session, user_id, rid)

    return _guard


# ------------------------------------------------------------------- audit


class AuditWriter:
    """Append-only audit writer (SECURITY §5).

    INSERT only — no update/delete methods exist on this class, matching the
    doc's "no update/delete paths exist in code" rule. Rows are flushed with
    the caller's session so audit + business action commit atomically.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        action: str,
        entity: str,
        entity_id: str | None,
        actor_user_id: uuid.UUID | None,
        ca_firm_id: uuid.UUID | None = None,
        business_id: uuid.UUID | None = None,
        registration_id: uuid.UUID | None = None,
        payload_diff: dict[str, Any] | None = None,
    ) -> None:
        """Insert one audit row (never updates)."""
        from app.db.models.core import AuditLog

        self._session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                ca_firm_id=ca_firm_id,
                business_id=business_id,
                registration_id=registration_id,
                action=action,
                entity=entity,
                entity_id=entity_id,
                payload_diff=payload_diff,
            )
        )
        await self._session.flush()


async def audit(
    session: AsyncSession,
    *,
    action: str,
    entity: str,
    entity_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    ca_firm_id: uuid.UUID | None = None,
    business_id: uuid.UUID | None = None,
    registration_id: uuid.UUID | None = None,
    payload_diff: dict[str, Any] | None = None,
) -> None:
    """One-call append-only audit insert bound to the request session."""
    writer = AuditWriter(session)
    await writer.log(
        action=action,
        entity=entity,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        ca_firm_id=ca_firm_id,
        business_id=business_id,
        registration_id=registration_id,
        payload_diff=payload_diff,
    )
