"""Tests for Task 12.2: GSTR-2B Vendor Discrepancy Notice Generator."""

from __future__ import annotations

import urllib.parse
import uuid
from datetime import date
from typing import Any

import pytest
from app.db.models.gst import (
    Invoice,
    InvoiceDirection,
    InvoiceLine,
    InvoiceStatus,
    InvType,
    ItcReconciliation,
    MatchStatus,
    SupplyType,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_email, register_and_login
from tests.gstin_fixtures import make_gstin

pytestmark = pytest.mark.asyncio

SessionMaker = async_sessionmaker[Any]


async def test_discrepancy_notice_explicit_params(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    tokens = await register_and_login(client, make_email())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    gstin = make_gstin()
    supplier_gstin = make_gstin()
    fp = "042026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Notice Recipient Co"},
    )

    req_body = {
        "invoice_no": "INV/2026/0091",
        "supplier_gstin": supplier_gstin,
        "supplier_name": "Acme Supplier Pvt Ltd",
        "taxable_paise": 10000000,  # Rs 1,00,000.00
        "tax_paise": 1800000,       # Rs 18,000.00
        "invoice_date": "2026-04-12",
    }

    res = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/itc/discrepancy-notice",
        headers=headers,
        json=req_body,
    )
    assert res.status_code == 200
    data = res.json()["data"]

    # Verify statutory citation
    assert "Section 16(2)(aa)" in data["statutory_citation"]
    assert "Section 16(2)(aa)" in data["formal_notice"]
    assert "Section 16(2)(aa)" in data["whatsapp_draft"]

    # Verify formatting
    assert "INV/2026/0091" in data["formal_notice"]
    assert "100,000.00" in data["formal_notice"]
    assert "18,000.00" in data["formal_notice"]
    assert data["supplier_gstin"] == supplier_gstin

    # Verify WhatsApp link
    assert data["whatsapp_url"].startswith("https://wa.me/?text=")
    encoded_text = data["whatsapp_url"].replace("https://wa.me/?text=", "")
    decoded_text = urllib.parse.unquote(encoded_text)
    assert decoded_text == data["whatsapp_draft"]


async def test_discrepancy_notice_invalid_gstin_rejected(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    tokens = await register_and_login(client, make_email())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    gstin = make_gstin()
    fp = "042026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Notice Buyer"},
    )

    res = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/itc/discrepancy-notice",
        headers=headers,
        json={
            "invoice_no": "INV/001",
            "supplier_gstin": "27AAPFU0939F1Z9",  # Invalid checksum
            "taxable_paise": 500000,
            "tax_paise": 90000,
        },
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_GSTIN"


async def test_discrepancy_notice_from_db_recon(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    tokens = await register_and_login(client, make_email())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    gstin = make_gstin()
    supplier_gstin = make_gstin()
    fp = "042026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Buyer Ltd"},
    )

    inv_id = uuid.uuid4()
    recon_id = uuid.uuid4()

    async with api_sessionmaker() as session:
        inv = Invoice(
            id=inv_id,
            gstin=gstin,
            fp=fp,
            direction=InvoiceDirection.PURCHASE,
            supplier_gstin=supplier_gstin,
            buyer_gstin=gstin,
            invoice_no="PUR-9988",
            invoice_date=date(2026, 4, 15),
            place_of_supply=gstin[:2],
            supply_type=SupplyType.INTRA,
            inv_typ=InvType.R,
            total_value_minor=1180000,
            status=InvoiceStatus.CONFIRMED,
        )
        session.add(inv)
        session.add(
            InvoiceLine(
                invoice_id=inv_id,
                line_no=1,
                gst_rate=18,
                taxable_value_minor=1000000,
                cgst_minor=90000,
                sgst_minor=90000,
                igst_minor=0,
                cess_minor=0,
            )
        )
        recon = ItcReconciliation(
            id=recon_id,
            gstin=gstin,
            fp=fp,
            purchase_invoice_id=inv_id,
            match_status=MatchStatus.MISSING_IN_2B,
        )
        session.add(recon)
        await session.commit()

    # Call endpoint with recon_id
    res = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/itc/discrepancy-notice",
        headers=headers,
        json={"recon_id": str(recon_id)},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["invoice_no"] == "PUR-9988"
    assert data["supplier_gstin"] == supplier_gstin
    assert data["taxable_paise"] == 1000000
    assert data["tax_paise"] == 180000
    assert "PUR-9988" in data["formal_notice"]
    assert "10,000.00" in data["formal_notice"]
    assert "1,800.00" in data["formal_notice"]

    # Unknown recon_id -> 404
    res404 = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/itc/discrepancy-notice",
        headers=headers,
        json={"recon_id": str(uuid.uuid4())},
    )
    assert res404.status_code == 404
    assert res404.json()["error"]["code"] == "DISCREPANCY_NOT_FOUND"
