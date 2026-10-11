from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ServiceError
from app.core.access import GstinAccess, require_gstin_access
from app.core.auth.dependencies import require_user
from app.db.models.gst import Invoice, InvoiceDirection, InvoiceStatus
from app.db.session import get_session
from app.services import ewb_service, irp_service

router = APIRouter(tags=["einvoice"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]


class EwbGeneratePayload(BaseModel):
    transporter_id: str | None = None
    vehicle_no: str | None = None
    distance_km: int | None = None


@router.post("/invoices/{invId}/irn")
async def generate_irn(
    invId: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    invoice = await session.get(Invoice, invId)
    if not invoice:
        raise ServiceError("invoice not found", 404, "NOT_FOUND")

    from app.core.access import resolve_gstin_access
    access = await resolve_gstin_access(session, user_id, invoice.gstin)
    access.require_write()

    e_invoice = await irp_service.generate_sandbox_irn(session, invId, user_id)

    return {
        "success": True,
        "data": {
            "irn": e_invoice.irn,
            "ack_no": e_invoice.ack_no,
            "ack_date": e_invoice.ack_date,
        }
    }


@router.post("/einvoices/{irnId}/cancel")
async def cancel_irn(
    irnId: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    invoice = await session.get(Invoice, irnId)
    if not invoice:
        raise ServiceError("invoice not found", 404, "NOT_FOUND")

    from app.core.access import resolve_gstin_access
    access = await resolve_gstin_access(session, user_id, invoice.gstin)
    access.require_write()

    e_invoice = await irp_service.cancel_sandbox_irn(session, irnId, user_id)

    return {
        "success": True,
        "data": {
            "irn": e_invoice.irn,
            "cancelled_at": e_invoice.cancelled_at.isoformat() if e_invoice.cancelled_at else None,
        }
    }


@router.get("/gst-accounts/{gstin}/months/{fp}/einvoices")
async def get_einvoices(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    einvoices = await irp_service.get_einvoices(session, access.gstin, fp)
    return {
        "success": True,
        "data": [
            {
                "irn": e.irn,
                "ack_no": e.ack_no,
                "ack_date": e.ack_date,
                "cancelled_at": e.cancelled_at,
            }
            for e in einvoices
        ]
    }


@router.get("/gst-accounts/{gstin}/months/{fp}/invoices")
async def get_invoices_for_irn(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """Confirmed outward (SALES) invoices eligible for IRN and E-Way Bill generation."""
    result = await session.execute(
        select(Invoice)
        .options(selectinload(Invoice.e_invoice))
        .where(Invoice.gstin == access.gstin)
        .where(Invoice.fp == fp)
        .where(Invoice.direction == InvoiceDirection.SALES)
        .where(Invoice.status == InvoiceStatus.CONFIRMED)
        .order_by(Invoice.invoice_date.desc(), Invoice.invoice_no.asc())
    )
    invoices = result.scalars().all()

    # Enrich rows with E-Way Bill status if present
    rows = []
    for inv in invoices:
        ewb = await ewb_service.get_ewb_by_invoice(session, inv.id)
        irn_status = (
            "CANCELLED"
            if inv.e_invoice and inv.e_invoice.cancelled_at
            else ("GENERATED" if inv.e_invoice else "PENDING")
        )
        if ewb:
            ewb_status = "GENERATED"
            ewb_no = ewb.get("ewb_no")
        elif inv.total_value_minor <= ewb_service.EWB_MIN_THRESHOLD_PAISE:
            ewb_status = "NOT_REQUIRED"
            ewb_no = None
        else:
            ewb_status = "PENDING"
            ewb_no = None

        rows.append(
            {
                "id": str(inv.id),
                "invoice_no": inv.invoice_no,
                "invoice_date": inv.invoice_date.isoformat(),
                "buyer_gstin": inv.buyer_gstin,
                "total_value_minor": inv.total_value_minor,
                "supply_type": inv.supply_type.value,
                "irn": inv.e_invoice.irn if inv.e_invoice else None,
                "irn_status": irn_status,
                "ewb_no": ewb_no,
                "ewb_status": ewb_status,
            }
        )

    return {
        "success": True,
        "data": rows,
    }


@router.post("/gst-accounts/{gstin}/months/{fp}/einvoices/{invoice_id}/ewb")
async def generate_ewb(
    gstin: str,
    fp: str,
    invoice_id: uuid.UUID,
    payload: EwbGeneratePayload,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    """Generate sandbox E-Way Bill for an outward confirmed invoice (Phase 13, Rec 6)."""
    access.require_write()
    _ = fp
    _ = user_id
    ewb_data = await ewb_service.generate_sandbox_ewb(
        session=session,
        invoice_id=invoice_id,
        gstin=access.gstin,
        transporter_id=payload.transporter_id,
        vehicle_no=payload.vehicle_no,
        distance_km=payload.distance_km,
    )
    return {
        "success": True,
        "data": ewb_data,
    }


@router.get("/gst-accounts/{gstin}/months/{fp}/einvoices/{invoice_id}/ewb")
async def get_ewb(
    gstin: str,
    fp: str,
    invoice_id: uuid.UUID,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """Retrieve existing E-Way Bill for an invoice."""
    _ = fp
    _ = access
    ewb_data = await ewb_service.get_ewb_by_invoice(session, invoice_id)
    if not ewb_data:
        raise ServiceError("E-Way Bill not found for this invoice", 404, "NOT_FOUND")
    return {
        "success": True,
        "data": ewb_data,
    }
