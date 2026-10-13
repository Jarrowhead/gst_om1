"""Direct service tests for filing period freeze/unfreeze (filing_freeze.py)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.api.errors import ServiceError
from app.db.models.core import GstAccount
from app.db.models.gst import FilingPeriod, FilingStatus
from app.services.filing_freeze import freeze_period, get_period_status, unfreeze_period
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_user
from tests.gstin_fixtures import make_gstin

pytestmark = pytest.mark.asyncio

FP = "042026"


async def _seed_account(api_sessionmaker: async_sessionmaker[Any]) -> str:
    gstin = make_gstin(state_code="27")
    async with api_sessionmaker() as session:
        session.add(
            GstAccount(
                gstin=gstin,
                pan=gstin[2:12],
                legal_name="Freeze Test Co",
                state_code="27",
            )
        )
        await session.commit()
    return gstin


async def test_get_period_status_returns_open_when_no_period(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    async with api_sessionmaker() as session:
        st = await get_period_status(session, gstin, FP)
    assert st["status"] == "OPEN"
    assert st["is_frozen"] is False
    assert st["is_filed"] is False


async def test_get_period_status_reflects_frozen(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    async with api_sessionmaker() as session:
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.FROZEN,
            )
        )
        await session.commit()
        st = await get_period_status(session, gstin, FP)
    assert st["is_frozen"] is True


async def test_freeze_period_creates_missing_period(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = (await make_user(api_sessionmaker)).id
    async with api_sessionmaker() as session:
        res = await freeze_period(session, gstin, FP, uid)
    assert res["status"] == "FROZEN"


async def test_freeze_period_on_filed_raises(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.FILED,
            )
        )
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await freeze_period(session, gstin, FP, uid)
        assert exc.value.code == "CANNOT_FREEZE_FILED"


async def test_unfreeze_period_not_found_raises(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = uuid.uuid4()
    async with api_sessionmaker() as session:
        with pytest.raises(ServiceError) as exc:
            await unfreeze_period(session, gstin, FP, uid)
        assert exc.value.code == "PERIOD_NOT_FOUND"


async def test_unfreeze_period_open_is_noop(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.OPEN,
            )
        )
        await session.commit()
        res = await unfreeze_period(session, gstin, FP, uid)
    assert res["status"] == "OPEN"


async def test_unfreeze_period_frozen_returns_open(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = (await make_user(api_sessionmaker)).id
    async with api_sessionmaker() as session:
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.FROZEN,
            )
        )
        await session.commit()
        res = await unfreeze_period(session, gstin, FP, uid)
    assert res["status"] == "OPEN"


async def test_unfreeze_period_filed_raises(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    uid = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.FILED,
            )
        )
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await unfreeze_period(session, gstin, FP, uid)
        assert exc.value.code == "CANNOT_UNFREEZE_FILED"
