import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas_gst import Gstr2bImportIn, Gstr2bStatementOut, ItcReconciliationOut
from app.core.access import GstinAccess, require_gstin_access
from app.core.auth.dependencies import require_user
from app.db.session import get_session
from app.services.discrepancy_notice import generate_discrepancy_notice
from app.services.returns.gsp_adapter import fetch_gstr2b_gsp
from app.services.returns.gstr2b import (
    build_itc_report,
    import_gstr2b,
    reconcile_itc,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]

router = APIRouter(prefix="/gst-accounts/{gstin}/months/{fp}", tags=["itc"])

@router.post("/gstr2b/import")
async def import_2b_statement(
    gstin: str,
    fp: str,
    body: Gstr2bImportIn,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    statement = await import_gstr2b(session, gstin, fp, body.payload, user_id)
    return {"success": True, "data": Gstr2bStatementOut.model_validate(statement)}


@router.post("/gstr2b/fetch")
async def fetch_2b_via_gsp(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    statement = await fetch_gstr2b_gsp(session, gstin, fp, user_id)
    return {"success": True, "data": Gstr2bStatementOut.model_validate(statement)}


@router.post("/itc/reconcile")
async def reconcile_itc_data(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    results = await reconcile_itc(session, gstin, fp)
    return {"success": True, "data": [ItcReconciliationOut.model_validate(r) for r in results]}


@router.get("/itc/report")
async def get_itc_report(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    status: Annotated[str | None, Query(description="MatchStatus filter")] = None,
) -> dict[str, Any]:
    """Books-vs-2B reconciliation report (task 9.4).

    Same (gstin, fp) scoped report the reconciliation dashboard renders: the
    5-status rows with both sides' paise figures, the per-status counts, and
    the GSTR-3B ITC prefill totals. Guarded by `require_gstin_access` like
    every other route on this router (API_SPEC non-negotiable #1).
    """
    data = await build_itc_report(session, gstin, fp, status)
    return {"success": True, "data": data}

class DiscrepancyNoticeIn(BaseModel):
    recon_id: uuid.UUID | None = None
    invoice_no: str | None = None
    supplier_gstin: str | None = None
    supplier_name: str | None = None
    taxable_paise: int | None = None
    tax_paise: int | None = None
    invoice_date: str | None = None


@router.post("/itc/discrepancy-notice")
async def create_discrepancy_notice(
    gstin: str,
    fp: str,
    body: DiscrepancyNoticeIn,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """1-click GSTR-2B vendor discrepancy notice generator (Task 12.2)."""
    data = await generate_discrepancy_notice(
        session,
        gstin,
        fp,
        recon_id=body.recon_id,
        invoice_no=body.invoice_no,
        supplier_gstin=body.supplier_gstin,
        supplier_name=body.supplier_name,
        taxable_paise=body.taxable_paise,
        tax_paise=body.tax_paise,
        invoice_date=body.invoice_date,
    )
    return {"success": True, "data": data}
