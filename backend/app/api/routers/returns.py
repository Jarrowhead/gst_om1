from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceError
from app.core.access import GstinAccess, audit, require_gstin_access
from app.core.auth.dependencies import require_user
from app.db.models.gst import ExportType, FilingPeriod, FilingStatus, Gstr1Export
from app.db.session import get_session
from app.services.returns.gsp_adapter import file_gstr1_gsp, file_gstr3b_gsp
from app.services.returns.gstr1a import create_gstr1a_amendments
from app.services.returns.pipeline import (
    RETURN_FORMS,
    build_hsn_summary_from_payload,
    hsn_summary_totals,
    load_gstr1_payload,
    load_gstr3b_payload,
    load_return_payload,
)
from app.services.returns.setoff import compute_rule88a_setoff
from app.services.returns.validation import validate_period_for_filing
from app.services.returns.xlsx_export import render_return_xlsx

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]

router = APIRouter(prefix="/gst-accounts/{gstin}/months/{fp}", tags=["returns"])

@router.get("/gstr1/export.json")
async def export_gstr1_json(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    payload = await load_gstr1_payload(session, gstin, fp)
    return {"success": True, "data": payload}


@router.get("/gstr3b/export.json")
async def export_gstr3b_json(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    payload = await load_gstr3b_payload(session, gstin, fp)
    return {"success": True, "data": payload}


@router.get("/gstr1/export.xlsx")
async def export_gstr1_xlsx(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> Response:
    payload = await load_gstr1_payload(session, gstin, fp)
    content = render_return_xlsx("gstr1", payload)
    return _xlsx_response(content, f"GSTR1-{gstin}-{fp}.xlsx")


@router.get("/gstr3b/export.xlsx")
async def export_gstr3b_xlsx(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> Response:
    payload = await load_gstr3b_payload(session, gstin, fp)
    content = render_return_xlsx("gstr3b", payload)
    return _xlsx_response(content, f"GSTR3B-{gstin}-{fp}.xlsx")


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/gstr1/hsn-summary")
async def gstr1_hsn_summary(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    payload = await load_gstr1_payload(session, gstin, fp)
    rows = build_hsn_summary_from_payload(payload)
    return {
        "success": True,
        "data": {"rows": rows, "totals": hsn_summary_totals(rows)},
    }


@router.get("/gstr3b/setoff")
async def gstr3b_setoff(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """Task 11.3: Rule 88A set-off matrix + PMT-06 challan summary.

    A pure read over the single-source GSTR-3B payload: the statutory
    set-off order (IGST first, remainder split CGST/SGST, then own-head
    CGST/SGST/Cess), per-head net cash payable, and the challan preview
    card. No CPIN is ever fabricated — a CPIN is generated on the GST
    portal and the payload states that explicitly.
    """
    payload = await load_gstr3b_payload(session, gstin, fp)
    result = compute_rule88a_setoff(payload, gstin=gstin, fp=fp)
    return {"success": True, "data": result}


@router.get("/gstr3b/challan")
async def gstr3b_challan(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """Task 11.3: the PMT-06 challan summary alone (same payload, challan
    subsection) — convenient for the payment card without the full matrix."""
    payload = await load_gstr3b_payload(session, gstin, fp)
    result = compute_rule88a_setoff(payload, gstin=gstin, fp=fp)
    return {"success": True, "data": result["challan"]}


@router.get("/returns/validation")
async def returns_validation(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    result = await validate_period_for_filing(session, gstin, fp)
    return {
        "success": True,
        "data": {"blocking": result["blocking"], "warnings": result["warnings"]},
    }


@router.post("/returns/generate")
async def returns_generate(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    """Idempotent orchestration: prepare→generate GSTR-1 and GSTR-3B.

    Loads both payloads (single source of truth), writes one Gstr1Export
    row per call (the existing /gstr1/generate contract), and returns a
    per-return status envelope. Safe to re-run.
    """
    statuses: dict[str, dict[str, Any]] = {}

    for form in RETURN_FORMS:
        try:
            payload = await load_return_payload(session, gstin, fp, form)
        except ServiceError as exc:
            statuses[form] = {"status": "FAILED", "error": exc.message}
            continue
        statuses[form] = {
            "status": "READY",
            "invoice_count": _invoice_count(payload),
            "totals": _payload_totals(payload),
        }

    gstr1_ready = statuses.get("gstr1", {}).get("status") == "READY"

    export_id: str | None = None
    if gstr1_ready:
        payload = await load_gstr1_payload(session, gstin, fp)
        export = Gstr1Export(
            gstin=gstin,
            fp=fp,
            generated_by=user_id,
            json_minio_key=f"gstr1/{gstin}/{fp}/export.json",
            invoice_count=statuses["gstr1"]["invoice_count"],
            totals=statuses["gstr1"]["totals"],
            schema_version="1.0",
            export_type=ExportType.ORIGINAL,
        )
        session.add(export)
        await audit(
            session,
            action="RETURNS_GENERATE",
            entity="return",
            entity_id=f"{gstin}-{fp}",
            actor_user_id=user_id,
            gstin=gstin,
        )
        await session.commit()
        export_id = str(export.id)

    return {
        "success": True,
        "data": {
            "gstin": gstin,
            "fp": fp,
            "returns": statuses,
            "gstr1_export_id": export_id,
        },
    }


def _invoice_count(payload: dict[str, Any]) -> int:
    if "b2b" in payload or "cdnr" in payload or "cdnur" in payload:
        count = 0
        for entry in payload.get("b2b", []):
            count += len(entry.get("inv", []))
        for entry in payload.get("cdnr", []):
            count += len(entry.get("nt", []))
        for entry in payload.get("cdnur", []):
            count += len(entry.get("nt", []))
        return count
    return 0


def _payload_totals(payload: dict[str, Any]) -> dict[str, int]:
    if "b2b" in payload:
        txval = 0
        iamt = 0
        camt = 0
        samt = 0
        csamt = 0
        for entry in payload.get("b2b", []):
            for inv in entry.get("inv", []):
                for item in inv.get("itms", []):
                    txval += int(item.get("txval_paise") or 0)
                    iamt += int(item.get("iamt_paise") or 0)
                    camt += int(item.get("camt_paise") or 0)
                    samt += int(item.get("samt_paise") or 0)
                    csamt += int(item.get("csamt_paise") or 0)
        for entry in payload.get("cdnr", []):
            for note in entry.get("nt", []):
                for item in note.get("itms", []):
                    txval += int(item.get("txval_paise") or 0)
                    iamt += int(item.get("iamt_paise") or 0)
                    camt += int(item.get("camt_paise") or 0)
                    samt += int(item.get("samt_paise") or 0)
                    csamt += int(item.get("csamt_paise") or 0)
        for entry in payload.get("cdnur", []):
            for note in entry.get("nt", []):
                for item in note.get("itms", []):
                    txval += int(item.get("txval_paise") or 0)
                    iamt += int(item.get("iamt_paise") or 0)
                    camt += int(item.get("camt_paise") or 0)
                    samt += int(item.get("samt_paise") or 0)
                    csamt += int(item.get("csamt_paise") or 0)
        return {
            "txval_paise": txval,
            "iamt_paise": iamt,
            "camt_paise": camt,
            "samt_paise": samt,
            "csamt_paise": csamt,
        }
    if "sup_details" in payload:
        osup = payload["sup_details"].get("osup_det", {})
        return {
            "txval_paise": int(osup.get("txval_paise") or 0),
            "iamt_paise": int(osup.get("iamt_paise") or 0),
            "camt_paise": int(osup.get("camt_paise") or 0),
            "samt_paise": int(osup.get("samt_paise") or 0),
            "csamt_paise": int(osup.get("csamt_paise") or 0),
        }
    return {}


@router.post("/gstr1/prepare")
async def prepare_gstr1(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    await audit(
        session,
        action="GSTR1_PREPARE",
        entity="return",
        entity_id=f"{gstin}-{fp}",
        actor_user_id=user_id,
        gstin=gstin,
    )
    return {"success": True, "data": {"summary": "OK"}}

@router.post("/gstr1/generate")
async def generate_gstr1(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    export = Gstr1Export(
        gstin=gstin,
        fp=fp,
        generated_by=user_id,
        json_minio_key=f"gstr1/{gstin}/{fp}/export.json",
        invoice_count=0,
        totals={},
        schema_version="1.0",
        export_type=ExportType.ORIGINAL,
    )
    session.add(export)
    await audit(
        session,
        action="GSTR1_GENERATE",
        entity="return",
        entity_id=str(export.id),
        actor_user_id=user_id,
        gstin=gstin,
    )
    await session.commit()
    return {"success": True, "data": {"export_id": str(export.id)}}


@router.post("/filed")
async def file_return(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        period = FilingPeriod(
            gstin=gstin,
            fp=fp,
            scheme_snapshot="REGULAR_MONTHLY",
            status=FilingStatus.FILED,
            filed_at=datetime.now(UTC),
            filed_by=user_id,
        )
        session.add(period)
    else:
        period.status = FilingStatus.FILED
        period.filed_at = datetime.now(UTC)
        period.filed_by = user_id
    await audit(
        session,
        action="RETURN_FILED",
        entity="return",
        entity_id=f"{gstin}-{fp}",
        actor_user_id=user_id,
        gstin=gstin,
    )
    await session.commit()
    return {"success": True, "data": {"status": "FILED"}}


@router.post("/gstr1a")
async def gstr1a_amend(
    gstin: str,
    fp: str,
    body: dict[str, Any],
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    created = await create_gstr1a_amendments(session, gstin, fp, body)
    await audit(
        session,
        action="GSTR1A_AMEND",
        entity="gstr1a",
        entity_id=f"{gstin}-{fp}",
        actor_user_id=user_id,
        gstin=gstin,
    )
    return {"success": True, "data": {"amendments": created}}


@router.post("/gstr4/prepare")
async def prepare_gstr4(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    return {"success": True, "data": {"summary": "OK"}}

@router.post("/gstr4/filed")
async def file_gstr4(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        period = FilingPeriod(
            gstin=gstin,
            fp=fp,
            scheme_snapshot="COMPOSITION",
            status=FilingStatus.FILED,
            filed_at=datetime.now(UTC),
            filed_by=user_id,
        )
        session.add(period)
    else:
        period.status = FilingStatus.FILED
        period.filed_at = datetime.now(UTC)
        period.filed_by = user_id
    await session.commit()
    return {"success": True, "data": {"status": "FILED"}}


@router.post("/gsp/gstr1/file")
async def file_gstr1_via_gsp(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    res = await file_gstr1_gsp(session, gstin, fp, user_id)
    return {"success": True, "data": res}


@router.post("/gsp/gstr3b/file")
async def file_gstr3b_via_gsp(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    res = await file_gstr3b_gsp(session, gstin, fp, user_id)
    return {"success": True, "data": res}


