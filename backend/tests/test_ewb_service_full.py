"""Direct service tests for E-Way Bill sandbox generation (ewb_service.py).

Fills the gate branches (not found / wrong gstin / non-SALES / draft /
threshold) and the retrieval path that the router-level tests leave uncovered.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from app.api.errors import ServiceError
from app.db.models.core import GstAccount
from app.db.models.gst import (
    Invoice,
    InvoiceDirection,
    InvoiceStatus,
    InvType,
    SupplyType,
)
from app.services.ewb_service import generate_sandbox_ewb, get_ewb_by_invoice
from sqlalchemy.ext.asyncio import async_sessionmaker

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
                legal_name="EWB Test Co",
                state_code="27",
            )
        )
        await session.commit()
    return gstin


def _invoice(**overrides: Any) -> Invoice:
    kwargs: dict[str, Any] = dict(
        id=uuid.uuid4(),
        gstin="",
        fp=FP,
        direction=InvoiceDirection.SALES,
        supplier_gstin="",
        buyer_gstin=None,
        invoice_no="INV-EWB-100",
        invoice_date=date(2026, 4, 10),
        place_of_supply="27",
        supply_type=SupplyType.INTRA,
        total_value_minor=6_500_000,
        status=InvoiceStatus.CONFIRMED,
        inv_typ=InvType.R,
    )
    kwargs.update(overrides)
    return Invoice(**kwargs)


async def test_generate_ewb_not_found(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    async with api_sessionmaker() as session:
        with pytest.raises(ServiceError) as exc:
            await generate_sandbox_ewb(session, uuid.uuid4(), gstin)
        assert exc.value.code == "NOT_FOUND"


async def test_generate_ewb_wrong_gstin(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    other = make_gstin()
    inv = _invoice(gstin=gstin, supplier_gstin=gstin)
    async with api_sessionmaker() as session:
        session.add(inv)
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await generate_sandbox_ewb(session, inv.id, other)
        assert exc.value.code == "FORBIDDEN"


async def test_generate_ewb_purchase_invoice_rejected(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    inv = _invoice(
        gstin=gstin,
        direction=InvoiceDirection.PURCHASE,
        supplier_gstin=make_gstin(),
        buyer_gstin=gstin,
    )
    async with api_sessionmaker() as session:
        session.add(inv)
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await generate_sandbox_ewb(session, inv.id, gstin)
        assert exc.value.code == "INVALID_OPERATION"


async def test_generate_ewb_draft_invoice_rejected(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    inv = _invoice(gstin=gstin, supplier_gstin=gstin, status=InvoiceStatus.DRAFT)
    async with api_sessionmaker() as session:
        session.add(inv)
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await generate_sandbox_ewb(session, inv.id, gstin)
        assert exc.value.code == "INVALID_OPERATION"


async def test_generate_ewb_below_threshold_rejected(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin = await _seed_account(api_sessionmaker)
    inv = _invoice(gstin=gstin, supplier_gstin=gstin, total_value_minor=4_000_000)
    async with api_sessionmaker() as session:
        session.add(inv)
        await session.commit()
        with pytest.raises(ServiceError) as exc:
            await generate_sandbox_ewb(session, inv.id, gstin)
        assert exc.value.code == "EWB_NOT_REQUIRED"


async def test_generate_ewb_no_buyer_gstin_and_default_distance(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """B2C (no buyer GSTIN) with no explicit distance still generates."""
    gstin = await _seed_account(api_sessionmaker)
    inv = _invoice(
        gstin=gstin,
        supplier_gstin=gstin,
        buyer_gstin=None,
        place_of_supply="",  # empty triggers the default-POS fallback path
    )
    async with api_sessionmaker() as session:
        session.add(inv)
        await session.commit()
        data = await generate_sandbox_ewb(session, inv.id, gstin)
    assert data["ewb_no"].startswith("1010")
    assert data["part_a"]["to_gstin"].startswith("27")  # default POS fallback


async def test_get_ewb_by_invoice_not_found(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    async with api_sessionmaker() as session:
        assert await get_ewb_by_invoice(session, uuid.uuid4()) is None
