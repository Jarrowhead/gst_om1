"""Bulk Excel/CSV import route (task 11.1, PHASE11_13 rec 1).

One route, two calls:

  preview  POST /gst-accounts/{gstin}/months/{fp}/bulk-import
               multipart file=<.xlsx|.csv>  [confirm=false]
           -> detected column mapping + required-column check + sample rows.
              Always 200 (even with columns missing) so the UI can show what
              it found; nothing is written.

  ingest   POST /gst-accounts/{gstin}/months/{fp}/bulk-import
               multipart file=<.xlsx|.csv>  confirm=true  [mapping=<json>]
           -> writes one `extraction.invoice_drafts` row per valid line and
              reports every rejected row individually; 422 while a required
              column is unmapped, 423 on a locked (FILED) period.

The route goes through the shipped write guard (`require_gstin_write`, i.e.
`resolve_gstin_access` + FILER/ADMIN) — no inline access checks.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceError
from app.core.access import GstinAccess, require_gstin_write
from app.db.session import get_session
from app.services.bulk_import import run_bulk_import

router = APIRouter(tags=["bulk-import"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccessDep = Annotated[GstinAccess, Depends(require_gstin_write("gstin"))]

FP_PATTERN = r"^(0[1-9]|1[0-2])(20\d{2})$"


def _mapping_override(raw: str | None) -> dict[str, Any] | None:
    """Parse the optional `mapping` form field (a JSON object)."""
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServiceError(
            f"mapping is not valid JSON: {exc}", 422, "VALIDATION_ERROR"
        ) from exc
    if not isinstance(parsed, dict):
        raise ServiceError(
            "mapping must be a JSON object of {canonical_field: header}",
            422,
            "VALIDATION_ERROR",
        )
    return parsed


@router.post("/gst-accounts/{gstin}/months/{fp}/bulk-import")
async def bulk_import(
    gstin: str,
    fp: Annotated[str, Path(pattern=FP_PATTERN, description="Filing period MMYYYY")],
    access: AccessDep,
    session: SessionDep,
    file: Annotated[UploadFile, File(description=".xlsx or .csv export")],
    confirm: Annotated[bool, Form()] = False,
    mapping: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    data = await file.read()
    report = await run_bulk_import(
        session,
        access,
        fp,
        data=data,
        filename=file.filename or "import",
        confirm=confirm,
        mapping_override=_mapping_override(mapping),
    )
    return {"success": True, "data": report}
