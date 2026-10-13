"""Direct tests for GSTR-2B vendor discrepancy notice (discrepancy_notice.py)."""

from __future__ import annotations

from typing import Any

import pytest
from app.api.errors import ServiceError
from app.services.discrepancy_notice import (
    build_notice_texts,
    generate_discrepancy_notice,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.gstin_fixtures import make_gstin


def test_build_notice_texts() -> None:
    formal, wa, url = build_notice_texts(
        recipient_gstin="27ABCDE1234F1Z5",
        fp="042026",
        supplier_gstin="29ABCDE1234F1Z5",
        supplier_name="Supplier Co",
        invoice_no="INV-1",
        invoice_date="2026-04-10",
        taxable_paise=100000,
        tax_paise=18000,
    )
    assert "Section 16(2)(aa)" in formal
    assert "INV-1" in wa
    assert url.startswith("https://wa.me/?text=")


def test_format_rupees() -> None:
    from app.services.discrepancy_notice import _format_rupees

    assert _format_rupees(118000) == "1,180.00"


async def test_generate_notice_with_explicit_params(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    supplier = make_gstin(state_code="29")
    async with api_sessionmaker() as session:
        data = await generate_discrepancy_notice(
            session,
            "27ABCDE1234F1Z5",
            "042026",
            supplier_gstin=supplier,
            supplier_name="Vendor",
            invoice_no="INV-1",
            invoice_date="2026-04-10",
            taxable_paise=100000,
            tax_paise=18000,
        )
    assert data["invoice_no"] == "INV-1"
    assert data["supplier_gstin"] == supplier
    assert data["total_paise"] == 118000


async def test_generate_notice_recon_not_found(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    import uuid

    async with api_sessionmaker() as session:
        with pytest.raises(ServiceError) as exc:
            await generate_discrepancy_notice(
                session, "27ABCDE1234F1Z5", "042026", recon_id=uuid.uuid4()
            )
        assert exc.value.code == "DISCREPANCY_NOT_FOUND"


async def test_generate_notice_invalid_supplier_gstin(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    async with api_sessionmaker() as session:
        with pytest.raises(ServiceError) as exc:
            await generate_discrepancy_notice(
                session,
                "27ABCDE1234F1Z5",
                "042026",
                supplier_gstin="INVALIDGSTIN123",
            )
        assert exc.value.code == "INVALID_GSTIN"
