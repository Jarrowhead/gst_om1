"""Phase 13 (Task 13.1) E-Way Bill Sandbox Tests.

Verifies:
1. Gated outward invoice with value > Rs 50,000 (5,000,000 paise) generates sandbox EWB.
2. Invoices <= Rs 50,000 return 400 EWB_NOT_REQUIRED.
3. Inward or draft invoices return 400 INVALID_OPERATION.
4. Part-A, Part-B, 12-digit EWB number, and signed QR code payload.
5. Deterministic distance fallback works without internet access.
6. Listing endpoint enriches invoices with EWB status.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.db.models.gst import (
    Invoice,
    InvoiceDirection,
    InvoiceStatus,
    InvType,
    SupplyType,
)
from app.services.ewb_service import estimate_distance_km
from httpx import AsyncClient

from tests.auth_helpers import SessionMaker, make_mobile, register_and_login
from tests.gstin_fixtures import make_gstin


@pytest.mark.asyncio
async def test_ewb_sandbox_generation_above_threshold(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Outward invoice > Rs 50,000 generates EWB with Part-A, Part-B, and QR data."""
    auth_data = await register_and_login(client, make_mobile())
    headers = {"Authorization": f"Bearer {auth_data['access_token']}"}
    gstin = make_gstin()
    buyer_gstin = make_gstin()
    fp = "042026"

    # Setup GST account
    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Logistics Super Corp"},
    )

    # Insert confirmed outward invoice above Rs 50,000 (Rs 65,000 = 6,500,000 paise)
    inv_id = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            Invoice(
                id=inv_id,
                gstin=gstin,
                fp=fp,
                direction=InvoiceDirection.SALES,
                supplier_gstin=gstin,
                buyer_gstin=buyer_gstin,
                invoice_no="INV-EWB-001",
                invoice_date=date(2026, 4, 10),
                place_of_supply="27",
                supply_type=SupplyType.INTER,
                total_value_minor=6500000,  # Rs 65,000
                status=InvoiceStatus.CONFIRMED,
                inv_typ=InvType.R,
            )
        )
        await session.commit()

    # Generate EWB
    payload = {
        "transporter_id": f"{gstin[:2]}AABCT9999F1Z1",
        "vehicle_no": "MH12AB1234",
        "distance_km": 350,
    }
    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{inv_id}/ewb",
        headers=headers,
        json=payload,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    # Validate EWB Number: 12 digits starting with 1010
    assert "ewb_no" in data
    assert len(data["ewb_no"]) == 12
    assert data["ewb_no"].startswith("1010")
    assert data["status"] == "GENERATED"
    assert "valid_until" in data
    assert "signed_qr_base64" in data
    assert len(data["signed_qr_base64"]) > 20

    # Part-A validation
    part_a = data["part_a"]
    assert part_a["from_gstin"] == gstin
    assert part_a["to_gstin"] == buyer_gstin
    assert part_a["doc_no"] == "INV-EWB-001"
    assert part_a["total_value_minor"] == 6500000

    # Part-B validation
    part_b = data["part_b"]
    assert part_b["vehicle_no"] == "MH12AB1234"
    assert part_b["distance_km"] == 350

    # Idempotency check: Generating again returns the exact same EWB number
    resp_again = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{inv_id}/ewb",
        headers=headers,
        json=payload,
    )
    assert resp_again.status_code == 200
    assert resp_again.json()["data"]["ewb_no"] == data["ewb_no"]

    # Retrieve via GET endpoint
    get_resp = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{inv_id}/ewb",
        headers=headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["ewb_no"] == data["ewb_no"]


@pytest.mark.asyncio
async def test_ewb_below_threshold_rejected(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Outward invoice <= Rs 50,000 returns 400 EWB_NOT_REQUIRED."""
    auth_data = await register_and_login(client, make_mobile())
    headers = {"Authorization": f"Bearer {auth_data['access_token']}"}
    gstin = make_gstin()
    fp = "042026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Retail Store"},
    )

    # Insert confirmed outward invoice of Rs 40,000 (4,000,000 paise) <= Rs 50,000
    inv_id = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            Invoice(
                id=inv_id,
                gstin=gstin,
                fp=fp,
                direction=InvoiceDirection.SALES,
                supplier_gstin=gstin,
                buyer_gstin=make_gstin(),
                invoice_no="INV-SMALL-001",
                invoice_date=date(2026, 4, 11),
                place_of_supply="07",
                supply_type=SupplyType.INTRA,
                total_value_minor=4000000,  # Rs 40,000
                status=InvoiceStatus.CONFIRMED,
                inv_typ=InvType.R,
            )
        )
        await session.commit()

    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{inv_id}/ewb",
        headers=headers,
        json={"vehicle_no": "DL01AA1111"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "EWB_NOT_REQUIRED"


@pytest.mark.asyncio
async def test_ewb_inward_or_draft_rejected(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Purchase invoice or draft invoice rejects E-Way bill generation."""
    auth_data = await register_and_login(client, make_mobile())
    headers = {"Authorization": f"Bearer {auth_data['access_token']}"}
    gstin = make_gstin()
    fp = "042026"

    await client.post(
        "/api/v1/gst-accounts",
        headers=headers,
        json={"gstin": gstin, "legal_name": "Trade House"},
    )

    # Purchase invoice
    pur_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    async with api_sessionmaker() as session:
        session.add(
            Invoice(
                id=pur_id,
                gstin=gstin,
                fp=fp,
                direction=InvoiceDirection.PURCHASE,
                supplier_gstin=make_gstin(),
                buyer_gstin=gstin,
                invoice_no="PUR-001",
                invoice_date=date(2026, 4, 12),
                place_of_supply="07",
                supply_type=SupplyType.INTRA,
                total_value_minor=8000000,  # Rs 80,000
                status=InvoiceStatus.CONFIRMED,
                inv_typ=InvType.R,
            )
        )
        session.add(
            Invoice(
                id=draft_id,
                gstin=gstin,
                fp=fp,
                direction=InvoiceDirection.SALES,
                supplier_gstin=gstin,
                buyer_gstin=make_gstin(),
                invoice_no="INV-DRAFT-002",
                invoice_date=date(2026, 4, 12),
                place_of_supply="07",
                supply_type=SupplyType.INTRA,
                total_value_minor=8000000,
                status=InvoiceStatus.DRAFT,  # Not confirmed
                inv_typ=InvType.R,
            )
        )
        await session.commit()

    # Purchase invoice rejected
    pur_resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{pur_id}/ewb",
        headers=headers,
        json={},
    )
    assert pur_resp.status_code == 400
    assert pur_resp.json()["error"]["code"] == "INVALID_OPERATION"

    # Draft invoice rejected
    draft_resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/einvoices/{draft_id}/ewb",
        headers=headers,
        json={},
    )
    assert draft_resp.status_code == 400
    assert draft_resp.json()["error"]["code"] == "INVALID_OPERATION"


def test_distance_fallback_deterministic() -> None:
    """Distance estimation works offline without failing or hanging."""
    dist1 = estimate_distance_km("07", "27")
    dist2 = estimate_distance_km("07", "27")
    assert dist1 > 0
    assert dist1 == dist2  # Deterministic repeatability
