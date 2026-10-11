"""Filing period freeze / unlock service (Task 12.1, rec 4).

Maker-checker sign-off workflow:
- ADMIN approves / freezes a period to prevent further edits or draft confirmations.
- FILER prepares the return, but cannot freeze/unfreeze (guarded by require_admin).
- When a period is FROZEN (or FILED), mutating operations raise 423 PERIOD_LOCKED.
- Freezing and unfreezing are audited append-only via core.access.audit.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.api.errors import ServiceError
from app.core.access import audit
from app.db.models.gst import FilingPeriod, FilingStatus
from sqlalchemy.ext.asyncio import AsyncSession


async def get_period_status(
    session: AsyncSession,
    gstin: str,
    fp: str,
) -> dict[str, Any]:
    """Return the current lifecycle state of a filing period."""
    period = await session.get(FilingPeriod, (gstin, fp))
    status = period.status if period is not None else FilingStatus.OPEN
    return {
        "status": status.value,
        "gstin": gstin,
        "fp": fp,
        "is_frozen": status == FilingStatus.FROZEN,
        "is_filed": status == FilingStatus.FILED,
    }


async def freeze_period(
    session: AsyncSession,
    gstin: str,
    fp: str,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Freeze a filing period. Requires ADMIN role."""
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        period = FilingPeriod(
            gstin=gstin,
            fp=fp,
            scheme_snapshot="REGULAR_MONTHLY",
            status=FilingStatus.OPEN,
        )
        session.add(period)
        await session.flush()

    if period.status == FilingStatus.FILED:
        raise ServiceError("cannot freeze an already filed period", 400, "CANNOT_FREEZE_FILED")

    prev = period.status.value
    period.status = FilingStatus.FROZEN
    await audit(
        session,
        action="FREEZE_PERIOD",
        entity="filing_period",
        entity_id=f"{gstin}:{fp}",
        actor_user_id=user_id,
        gstin=gstin,
        payload_diff={"previous_status": prev, "status": "FROZEN"},
    )
    await session.commit()
    return {"status": "FROZEN", "gstin": gstin, "fp": fp}


async def unfreeze_period(
    session: AsyncSession,
    gstin: str,
    fp: str,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Unfreeze a filing period. Requires ADMIN role."""
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        raise ServiceError("filing period not found", 404, "PERIOD_NOT_FOUND")

    if period.status == FilingStatus.FILED:
        raise ServiceError("cannot unfreeze an already filed period", 400, "CANNOT_UNFREEZE_FILED")

    if period.status != FilingStatus.FROZEN:
        return {"status": period.status.value, "gstin": gstin, "fp": fp}

    prev = period.status.value
    period.status = FilingStatus.OPEN
    await audit(
        session,
        action="UNLOCK_PERIOD",
        entity="filing_period",
        entity_id=f"{gstin}:{fp}",
        actor_user_id=user_id,
        gstin=gstin,
        payload_diff={"previous_status": prev, "status": "OPEN"},
    )
    await session.commit()
    return {"status": "OPEN", "gstin": gstin, "fp": fp}
