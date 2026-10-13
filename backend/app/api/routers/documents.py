"""Document router: upload, list, detail, presigned view (API_SPEC §5).

v4.0 (TECHNICAL_ARCHITECTURE §3): documents hang off the GSTIN, so the
collection routes are `/gst-accounts/{gstin}/months/{fp}/documents`. Routes
keyed by `doc_id` resolve the document first and then re-run the same guard
against that document's GSTIN — a cross-tenant doc id must resolve to 404.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceError
from app.api.schemas import DraftFieldUpdateIn, RejectIn
from app.config import get_settings
from app.core.access import (
    GstinAccess,
    require_gstin_access,
    require_gstin_write,
    resolve_gstin_access,
)
from app.core.auth.dependencies import require_user
from app.db.session import get_session
from app.extraction.seed import (
    SeedLLMClient,
    auto_invoice_no,
    seed_ocr_provider,
)
from app.extraction.worker import run_job
from app.services.batch_confirm import batch_confirm
from app.services.documents import (
    get_document,
    get_document_detail,
    list_documents,
    store_documents,
)
from app.services.review import (
    confirm_draft,
    get_draft,
    get_review_queue,
    reject_document,
    update_draft,
)
from app.services.storage import presigned_view_url

_LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]

_REVIEW_WRITE = "document review requires FILER or ADMIN on the GSTIN"
_ALLOWED_DOC_TYPES = {"UNCLASSIFIED", "INV", "CDN", "DBN", "OTHER"}
FP_REGEX = r"^(0[1-9]|1[0-2])(20\d{2})$"


async def _doc_access(
    session: AsyncSession, user_id: uuid.UUID, doc_id: uuid.UUID
) -> tuple[Any, GstinAccess]:
    """Resolve a document by id and guard it against its own GSTIN."""
    doc = await get_document(session, doc_id)
    access = await resolve_gstin_access(session, user_id, doc.gstin)
    return doc, access


async def _run_seeded_extraction(
    session: AsyncSession,
    doc_id: uuid.UUID,
    access: GstinAccess,
) -> Any:
    """Run the real pipeline with deterministic seed providers (dev mode only)."""
    from app.db.models.extraction import ExtractionJob
    from app.db.models.gst import Invoice

    doc = await get_document(session, doc_id)
    job = (
        await session.execute(
            select(ExtractionJob).where(ExtractionJob.document_id == doc_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise ServiceError("extraction job not found", 404, "NOT_FOUND")

    existing = (
        await session.execute(
            select(Invoice.invoice_no).where(
                Invoice.gstin == access.gstin, Invoice.fp == doc.fp
            )
        )
    ).scalars().all()
    existing_set = {str(n) for n in existing if n}

    invoice_no = auto_invoice_no(str(doc_id))
    if invoice_no in existing_set:
        invoice_no = f"{invoice_no}-{len(existing_set) + 1}"

    return await run_job(
        job.id,
        ocr_provider=seed_ocr_provider,
        llm_client=SeedLLMClient(access.gstin, invoice_no, "2026-09-15"),
    )


@router.post("/documents/{doc_id}/extract", include_in_schema=get_settings().dev_mode)
async def run_doc_extraction(
    doc_id: uuid.UUID,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Dev-only synchronous extraction trigger. Queued job is run in-request."""
    if not get_settings().dev_mode:
        raise ServiceError("not available", 404, "NOT_FOUND")
    doc, access = await _doc_access(session, user_id, doc_id)
    if not access.can_write:
        from app.core.access import PermissionDenied

        raise PermissionDenied("extraction requires FILER or ADMIN on the GSTIN")
    result = await _run_seeded_extraction(session, doc_id, access)
    if result is None:
        raise ServiceError("extraction job not found", 404, "NOT_FOUND")
    return {"success": True, "data": {"status": result.error or "ok"}}


@router.get("/gst-accounts/{gstin}/months/{fp}/review-queue")
async def review_queue(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    rows = await get_review_queue(session, access, fp)
    return {"success": True, "data": rows}


@router.post("/gst-accounts/{gstin}/months/{fp}/review-queue/batch-confirm")
async def batch_confirm_clean_invoices(
    gstin: str,
    fp: Annotated[str, Path(pattern=FP_REGEX, description="Filing period MMYYYY")],
    access: Annotated[GstinAccess, Depends(require_gstin_write("gstin"))],
    session: SessionDep,
    dry_run: Annotated[bool, Query(description="Evaluate + report, write nothing")] = False,
) -> dict[str, Any]:
    """Task 11.2: confirm every clean (no BLOCK flag), confidence-avg >= 0.95
    pending draft in one call; the rest stay pending, reported per-item."""
    data = await batch_confirm(session, access, fp, access.user_id, dry_run=dry_run)
    return {"success": True, "data": data}


@router.get("/documents/{doc_id}/draft")
async def get_doc_draft(
    doc_id: uuid.UUID,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    data = await get_draft(session, doc_id, access)
    return {"success": True, "data": data}


@router.put("/documents/{doc_id}/draft")
async def put_doc_draft(
    doc_id: uuid.UUID,
    body: DraftFieldUpdateIn,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    if not access.can_write:
        from app.core.access import PermissionDenied

        raise PermissionDenied(_REVIEW_WRITE)
    data = await update_draft(session, doc_id, access, user_id, body.model_dump(exclude_unset=True))
    return {"success": True, "data": data}


@router.post("/documents/{doc_id}/confirm")
async def confirm_doc(
    doc_id: uuid.UUID,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    if not access.can_write:
        from app.core.access import PermissionDenied

        raise PermissionDenied(_REVIEW_WRITE)
    data = await confirm_draft(session, doc_id, access, user_id)
    return {"success": True, "data": data}


@router.post("/documents/{doc_id}/reject")
async def reject_doc(
    doc_id: uuid.UUID,
    body: RejectIn,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    if not access.can_write:
        from app.core.access import PermissionDenied

        raise PermissionDenied(_REVIEW_WRITE)
    data = await reject_document(session, doc_id, access, user_id, body.reason)
    return {"success": True, "data": data}


@router.post("/gst-accounts/{gstin}/months/{fp}/documents")
async def upload_documents(
    gstin: str,
    fp: Annotated[str, Path(pattern=FP_REGEX, description="Filing period MMYYYY")],
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
    capture_source: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    doc_type: Annotated[str, Form()] = "UNCLASSIFIED",
) -> dict[str, Any]:
    if len(doc_type) > 16 or doc_type not in _ALLOWED_DOC_TYPES:
        from app.api.errors import ServiceError

        raise ServiceError("invalid doc_type", 422, "VALIDATION_ERROR")
    if not access.can_write:
        from app.core.access import PermissionDenied

        raise PermissionDenied("upload requires FILER or ADMIN on the GSTIN")
    result = await store_documents(
        session,
        access=access,
        fp=fp,
        files=files,
        capture_source=capture_source,
        doc_type=doc_type,
        uploaded_by=user_id,
    )
    return {"success": True, "data": result}


@router.get("/gst-accounts/{gstin}/months/{fp}/documents")
async def list_docs(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    rows, total = await list_documents(session, access, fp, page=page, size=size)
    return {
        "success": True,
        "data": {
            "content": rows,
            "page": page,
            "size": size,
            "totalElements": total,
            "last": (page + 1) * size >= total,
        },
    }


@router.get("/documents/{doc_id}")
async def get_doc(
    doc_id: uuid.UUID,
    user_id: UserDep,
    session: SessionDep,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    detail = await get_document_detail(session, doc_id, access)
    return {"success": True, "data": detail}


@router.get("/documents/{doc_id}/image")
async def get_doc_image(
    doc_id: uuid.UUID,
    user_id: UserDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    _doc, access = await _doc_access(session, user_id, doc_id)
    detail = await get_document_detail(session, doc_id, access)
    page_count = detail["page_count"] or 1
    if page > page_count:
        from app.api.errors import ServiceError

        raise ServiceError("page exceeds document page count", 422, "VALIDATION_ERROR")
    url = presigned_view_url(detail["minio_key"])
    return {
        "success": True,
        "data": {
            "presigned_url": url,
            "expires_in_minutes": 30,
            "page": page,
            "page_count": page_count,
        },
    }



@router.post("/gst-accounts/{gstin}/months/{fp}/documents/batch")
async def upload_documents_batch(
    gstin: str,
    fp: Annotated[str, Path(pattern=FP_REGEX, description="Filing period MMYYYY")],
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
    user_id: UserDep,
    capture_source: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    doc_type: Annotated[str, Form()] = "UNCLASSIFIED",
) -> dict[str, Any]:
    """Upload multiple documents in a single request. Each file/invoice gets its own queue job."""
    if not access.can_write:
        from app.core.access import PermissionDenied
        raise PermissionDenied("upload requires FILER or ADMIN on the GSTIN")

    from app.services.documents import store_batch_documents
    results = await store_batch_documents(
        session,
        access=access,
        fp=fp,
        files=files,
        capture_source=capture_source,
        doc_type=doc_type,
        uploaded_by=user_id,
    )
    return {"success": True, "data": results, "count": len(results["created"])}


@router.get("/gst-accounts/{gstin}/months/{fp}/extraction/queue-status")
async def get_extraction_queue_status(
    gstin: str,
    fp: str,
    access: Annotated[GstinAccess, Depends(require_gstin_access("gstin"))],
    session: SessionDep,
) -> dict[str, Any]:
    """Return Redis queue length and pending/processing job counts."""
    q_len = 0
    try:
        from app.core.auth.redis_client import get_redis

        q_len = await get_redis().llen("extraction:queue")
    except Exception as exc:  # noqa: BLE001  best-effort probe, never 500
        _LOGGER.warning("extraction queue-status probe failed: %s", exc)

    return {
        "success": True,
        "data": {
            "queue_depth": q_len,
            "gstin": gstin,
            "fp": fp,
        }
    }
