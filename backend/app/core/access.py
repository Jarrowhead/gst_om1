"""Access guard + audit writer (SECURITY_AND_ACCESS.md §2/§5, v4 unified model).

`require_gstin_access` is the SINGLE FastAPI dependency behind every
GSTIN-scoped route (SECURITY §2 — never per-route ad-hoc checks). It resolves
user → core.user_gst_access → core.gst_accounts and returns the GSTIN context
plus the role (ADMIN / FILER / VIEWER) the user holds on it.

Denial contract (API_SPECIFICATION.md non-negotiable #1/#2):
  unknown / not-granted
  GSTIN                    -> 404 GSTIN_NOT_FOUND (404 over 403 — existence is
                              data, so wrong-tenant reads and nonexistent reads
                              are indistinguishable)
  authenticated but the
  route's role is missing  -> 403 FORBIDDEN (the guard can say so without
                              leaking GSTIN data)
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
from app.db.models.core import AccessRole, GstAccount, UserGstAccess
from app.db.session import get_session

_WRITE_ROLES = (AccessRole.ADMIN, AccessRole.FILER)


class AccessDenied(AuthError):
    """Unknown GSTIN or no grant — 404, existence must not leak."""

    status_code = 404
    code = "GSTIN_NOT_FOUND"


class PermissionDenied(AuthError):
    """Authenticated + granted, but this route needs a role the user lacks — 403."""

    status_code = 403
    code = "FORBIDDEN"


@dataclass(frozen=True, slots=True)
class GstinAccess:
    """Resolved tenant context for one GSTIN."""

    gstin: str
    role: AccessRole
    user_id: uuid.UUID
    legal_name: str
    pan: str

    @property
    def can_write(self) -> bool:
        """ADMIN and FILER may mutate filing data; VIEWER may only read."""
        return self.role in _WRITE_ROLES

    @property
    def can_export(self) -> bool:
        return self.role in _WRITE_ROLES

    @property
    def can_revoke(self) -> bool:
        return self.role is AccessRole.ADMIN

    @property
    def can_invite_members(self) -> bool:
        return self.role is AccessRole.ADMIN

    @property
    def is_admin(self) -> bool:
        return self.role is AccessRole.ADMIN

    def require_write(self) -> None:
        """Raise 403 unless the route's role requirement is met."""
        if not self.can_write:
            raise PermissionDenied("FILER or ADMIN role required on this GSTIN")

    def require_export(self) -> None:
        if not self.can_export:
            raise PermissionDenied("FILER or ADMIN role required to export")

    def require_revoke(self) -> None:
        if not self.can_revoke:
            raise PermissionDenied("ADMIN role required to revoke")

    def require_invite(self) -> None:
        if not self.can_invite_members:
            raise PermissionDenied("ADMIN role required to invite members")

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PermissionDenied("ADMIN role required on this GSTIN")


async def resolve_gstin_access(
    session: AsyncSession, user_id: uuid.UUID, gstin: str
) -> GstinAccess:
    """User → GSTIN access chain (SECURITY §2). Raises AccessDenied.

    A missing GSTIN row and a missing grant are both 404: the guard never
    reveals whether a GSTIN exists on the platform.
    """
    normalised = (gstin or "").strip().upper()
    if not normalised:
        raise AccessDenied("no access to this GSTIN")

    row = (
        await session.execute(
            select(GstAccount, UserGstAccess.role)
            .join(UserGstAccess, UserGstAccess.gstin == GstAccount.gstin)
            .where(
                GstAccount.gstin == normalised,
                UserGstAccess.user_id == user_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise AccessDenied("no access to this GSTIN")

    account, role = row
    return GstinAccess(
        gstin=account.gstin,
        role=role,
        user_id=user_id,
        legal_name=account.legal_name,
        pan=account.pan,
    )


def require_gstin_access(gstin_param: str = "gstin") -> Any:
    """Dependency factory: guard a route by the GSTIN path param."""

    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> GstinAccess:
        raw = request.path_params.get(gstin_param)
        if not raw:
            raise AccessDenied("no access to this GSTIN")
        return await resolve_gstin_access(session, user_id, str(raw))

    return _guard


def require_gstin_write(gstin_param: str = "gstin") -> Any:
    """Same guard plus a FILER/ADMIN role requirement (SECURITY §2).

    Deliberately re-resolves rather than depending on require_gstin_access():
    with `from __future__ import annotations` a self-referential
    `Annotated[..., Depends(closure_local)]` cannot be resolved by FastAPI and
    silently degrades into a required *query* parameter named `access`.
    """

    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> GstinAccess:
        raw = request.path_params.get(gstin_param)
        if not raw:
            raise AccessDenied("no access to this GSTIN")
        access = await resolve_gstin_access(session, user_id, str(raw))
        access.require_write()
        return access

    return _guard


def require_gstin_export(gstin_param: str = "gstin") -> Any:
    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> GstinAccess:
        raw = request.path_params.get(gstin_param)
        if not raw:
            raise AccessDenied("no access to this GSTIN")
        access = await resolve_gstin_access(session, user_id, str(raw))
        access.require_export()
        return access

    return _guard


def require_gstin_revoke(gstin_param: str = "gstin") -> Any:
    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> GstinAccess:
        raw = request.path_params.get(gstin_param)
        if not raw:
            raise AccessDenied("no access to this GSTIN")
        access = await resolve_gstin_access(session, user_id, str(raw))
        access.require_revoke()
        return access

    return _guard


def require_gstin_invite(gstin_param: str = "gstin") -> Any:
    async def _guard(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user_id: Annotated[uuid.UUID, Depends(require_user)],
    ) -> GstinAccess:
        raw = request.path_params.get(gstin_param)
        if not raw:
            raise AccessDenied("no access to this GSTIN")
        access = await resolve_gstin_access(session, user_id, str(raw))
        access.require_invite()
        return access

    return _guard


# ------------------------------------------------------------------- audit


class AuditWriter:
    """Append-only audit writer (SECURITY §5).

    INSERT only — no update/delete methods exist on this class, matching the
    doc's "no update/delete paths exist in code" rule. Rows are flushed with
    the caller's session so audit + data action commit atomically.
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
        gstin: str | None = None,
        payload_diff: dict[str, Any] | None = None,
    ) -> None:
        """Insert one audit row (never updates)."""
        from app.db.models.core import AuditLog

        self._session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                gstin=gstin,
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
    gstin: str | None = None,
    payload_diff: dict[str, Any] | None = None,
) -> None:
    """One-call append-only audit insert bound to the request session."""
    writer = AuditWriter(session)
    await writer.log(
        action=action,
        entity=entity,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        gstin=gstin,
        payload_diff=payload_diff,
    )
