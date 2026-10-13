"""Document service: upload, list, detail, presigned URL, dedupe checks.

Follows API_SPECIFICATION.md §5 + SECURITY_AND_ACCESS.md §3 (v4: every document
is keyed directly by GSTIN + filing period, never by a surrogate registration id).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.api.errors import ServiceError
from app.core.access import GstinAccess, audit
from app.db.models.extraction import CaptureSource, Document, ExtractionJob, JobStatus
from app.db.models.gst import FilingPeriod, FilingStatus
from app.services.storage import _BUCKET, build_document, remove_object, upload_document
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

_LOGGER = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_EXTRACTION_QUEUE = "extraction:queue"


async def _enqueue_extraction_job(job_id: uuid.UUID) -> None:
    """Push a job id onto the Redis extraction queue via the shared client.

    Uses the process-wide client (``app.core.auth.redis_client.get_redis``) so
    tests can inject fakeredis and workers share one connection pool — never a
    fresh ``from_url`` connection per job (which would bypass test fakes and
    race with any live worker).
    """
    from app.core.auth.redis_client import get_redis

    await get_redis().rpush(_EXTRACTION_QUEUE, str(job_id))


async def _ensure_open_period(session: AsyncSession, gstin: str, fp: str) -> None:
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is not None:
        if period.status in (FilingStatus.FILED, FilingStatus.FROZEN):
            raise ServiceError("period is locked after filing", 423, "PERIOD_LOCKED")
        return

    # Race-safe insert using PostgreSQL ON CONFLICT DO NOTHING.
    try:
        await session.execute(
            pg_insert(FilingPeriod)
            .values(
                gstin=gstin,
                fp=fp,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.OPEN,
            )
            .on_conflict_do_nothing(index_elements=["gstin", "fp"])
        )
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ServiceError("period concurrently created", 409, "CONFLICT") from None

    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        # Should never happen after a successful insert, but guard anyway.
        raise ServiceError("period concurrently created", 409, "CONFLICT")
    if period.status in (FilingStatus.FILED, FilingStatus.FROZEN):
        raise ServiceError("period is locked after filing", 423, "PERIOD_LOCKED")


async def store_documents(
    session: AsyncSession,
    access: GstinAccess,
    fp: str,
    files: list[UploadFile],
    capture_source: str,
    doc_type: str,
    uploaded_by: uuid.UUID,
) -> dict[str, Any]:
    """Store a multi-file upload as one document + spawn extraction job."""
    await _ensure_open_period(session, access.gstin, fp)

    try:
        source = CaptureSource(capture_source)
    except ValueError as exc:
        raise ServiceError("invalid capture_source", 422, "VALIDATION_ERROR") from exc

    if not files:
        raise ServiceError("no files uploaded", 422, "VALIDATION_ERROR")

    # Size cap check before reading into storage layer.
    for file in files:
        data = await file.read()
        await file.seek(0)
        if len(data) > _MAX_UPLOAD_BYTES:
            raise ServiceError("file exceeds 25 MB limit", 422, "VALIDATION_ERROR")

    built = await build_document(files=files, capture_source=capture_source)

    # Dedupe within {gstin}/{fp} scope using the post-process sha256.
    dup_stmt = (
        select(Document.id)
        .where(
            Document.gstin == access.gstin,
            Document.fp == fp,
            Document.sha256 == built["sha256"],
        )
        .limit(1)
    )
    dup_result = await session.execute(dup_stmt)
    if dup_result.scalar_one_or_none() is not None:
        raise ServiceError("duplicate document upload detected", 409, "CONFLICT")

    doc_id = uuid.uuid4()
    meta = await upload_document(
        gstin=access.gstin,
        fp=fp,
        doc_id=doc_id,
        built=built,
        uploaded_by=uploaded_by,
    )

    document = Document(
        id=doc_id,
        gstin=access.gstin,
        fp=fp,
        capture_source=source,
        doc_type=doc_type,
        minio_key=meta["minio_key"],
        sha256=meta["sha256"],
        bytes=meta["bytes"],
        page_count=meta["page_count"],
        uploaded_by=uploaded_by,
    )

    job = ExtractionJob(document_id=doc_id, status=JobStatus.QUEUED)

    try:
        session.add(document)
        session.add(job)

        await audit(
            session,
            action="DOCUMENT_UPLOADED",
            entity="document",
            entity_id=str(doc_id),
            actor_user_id=uploaded_by,
            gstin=access.gstin,
        )

        await session.commit()

        # Enqueue job into Redis extraction queue (best-effort: a queue hiccup
        # must never fail the upload — the document + job are already durable).
        try:
            await _enqueue_extraction_job(job.id)
        except Exception as q_exc:  # noqa: BLE001
            _LOGGER.warning("failed to enqueue extraction job %s: %s", job.id, q_exc)
    except IntegrityError as exc:
        await session.rollback()
        remove_object(_BUCKET, meta["minio_key"])
        raise ServiceError("duplicate document upload detected", 409, "CONFLICT") from exc
    except Exception:
        await session.rollback()
        remove_object(_BUCKET, meta["minio_key"])
        raise

    await session.refresh(document)
    await session.refresh(job)

    return _document_envelope(document, job)


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    """Fetch a document by id; raise 404 DOCUMENT_NOT_FOUND if absent."""
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise ServiceError("document not found", 404, "DOCUMENT_NOT_FOUND")
    return doc


def _document_envelope(document: Document, job: ExtractionJob | None = None) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "gstin": document.gstin,
        "fp": document.fp,
        "capture_source": document.capture_source.value,
        "doc_type": document.doc_type,
        "sha256": document.sha256,
        "bytes": document.bytes,
        "page_count": document.page_count,
        "minio_key": document.minio_key,
        "uploaded_by": str(document.uploaded_by) if document.uploaded_by else None,
        "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
        "job": {
            "id": str(job.id),
            "status": job.status.value,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        if job
        else None,
    }


async def list_documents(
    session: AsyncSession, access: GstinAccess, fp: str, *, page: int = 0, size: int = 20
) -> tuple[list[dict[str, Any]], int]:
    stmt = (
        select(Document, ExtractionJob)
        .outerjoin(ExtractionJob, ExtractionJob.document_id == Document.id)
        .where(Document.gstin == access.gstin, Document.fp == fp)
    )
    count_stmt = select(func.count()).select_from(Document).where(
        Document.gstin == access.gstin, Document.fp == fp
    )
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Document.uploaded_at.desc()).offset(page * size).limit(size)
        )
    ).all()
    return [_document_envelope(doc, job) for doc, job in rows], total


async def get_document_detail(
    session: AsyncSession, doc_id: uuid.UUID, access: GstinAccess
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(Document, ExtractionJob).outerjoin(
                ExtractionJob, ExtractionJob.document_id == Document.id
            )
            .where(
                Document.id == doc_id,
                Document.gstin == access.gstin,
            )
        )
    ).one_or_none()
    if row is None:
        raise ServiceError("document not found", 404, "NOT_FOUND")
    return _document_envelope(row[0], row[1])


async def build_document_from_bytes(
    data: bytes,
    filename: str,
    capture_source: str,
) -> dict[str, Any]:
    import hashlib
    sha256 = hashlib.sha256(data).hexdigest()
    page_count = 1
    if filename.lower().endswith(".pdf"):
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            page_count = len(doc)
            doc.close()
        except Exception:
            page_count = 1

    mime = "application/pdf" if filename.lower().endswith(".pdf") else "image/jpeg"
    return {
        "bytes": len(data),
        "sha256": sha256,
        "page_count": page_count,
        "payload": data,
        "mime_type": mime,
    }



_INVOICE_NUMBER_RE = re.compile(
    r"(?:INVOICE|BILL|DOC)\s*(?:NO\.?|NUMBER|#)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-\/]*)",
    re.IGNORECASE,
)


def _first_invoice_number(text: str) -> str | None:
    """Return the first invoice-number token on a page, or None."""
    m = _INVOICE_NUMBER_RE.search(text or "")
    if m is None:
        return None
    return m.group(1).strip().upper()


async def split_pdf_if_multi_invoice(data: bytes) -> list[bytes]:
    """Detect if a PDF contains multiple distinct invoices across pages.

    A page begins a new invoice only when it carries an invoice number that
    DIFFERS from the running invoice number (or it is page 0). A continuation
    page that repeats the same number — e.g. "INVOICE NO: 123 · page 2 of 3" —
    never triggers a split, so a multi-page single invoice is never over-split.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if len(doc) <= 1:
                return [data]

            starts: list[int] = []
            running_no: str | None = None
            for p_idx in range(len(doc)):
                text = doc[p_idx].get_text() or ""
                inv_no = _first_invoice_number(text)
                if p_idx == 0:
                    starts.append(p_idx)
                    running_no = inv_no
                elif inv_no is not None and running_no is not None and inv_no != running_no:
                    starts.append(p_idx)
                    running_no = inv_no

            # One start (or none resolved) => a single invoice, keep as one.
            if len(starts) <= 1:
                return [data]

            split_pdfs: list[bytes] = []
            for i, start_p in enumerate(starts):
                end_p = starts[i + 1] if i + 1 < len(starts) else len(doc)
                sub_doc = fitz.open()
                sub_doc.insert_pdf(doc, from_page=start_p, to_page=end_p - 1)
                split_pdfs.append(sub_doc.tobytes())
                sub_doc.close()
            return split_pdfs
        finally:
            doc.close()
    except Exception as e:
        _LOGGER.warning("PDF split analysis failed, falling back to original: %s", e)
        return [data]


async def store_batch_documents(
    session: AsyncSession,
    access: GstinAccess,
    fp: str,
    files: list[UploadFile],
    capture_source: str,
    doc_type: str,
    uploaded_by: uuid.UUID,
) -> dict[str, Any]:
    """Store multiple files (or split multi-invoice PDFs) as individual documents.

    Returns ``{"created": [envelopes], "skipped": [{filename, reason}]}`` — an
    oversized or duplicate file is reported, never silently dropped.
    """
    await _ensure_open_period(session, access.gstin, fp)
    source = CaptureSource(capture_source)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for file in files:
        raw_bytes = await file.read()
        await file.seek(0)
        filename = file.filename or "<unnamed>"
        if len(raw_bytes) > _MAX_UPLOAD_BYTES:
            skipped.append({"filename": filename, "reason": "exceeds 25 MB limit"})
            continue

        is_pdf = (
            (file.filename and file.filename.lower().endswith(".pdf"))
            or file.content_type == "application/pdf"
        )
        sub_items = await split_pdf_if_multi_invoice(raw_bytes) if is_pdf else [raw_bytes]

        for sub_data in sub_items:
            built = await build_document_from_bytes(
                data=sub_data,
                filename=filename,
                capture_source=capture_source,
            )

            # Check dedupe
            dup = await session.execute(
                select(Document.id).where(
                    Document.gstin == access.gstin,
                    Document.fp == fp,
                    Document.sha256 == built["sha256"],
                ).limit(1)
            )
            if dup.scalar_one_or_none() is not None:
                skipped.append(
                    {"filename": filename, "reason": "duplicate document (sha256)"}
                )
                continue

            doc_id = uuid.uuid4()
            meta = await upload_document(
                gstin=access.gstin,
                fp=fp,
                doc_id=doc_id,
                built=built,
                uploaded_by=uploaded_by,
            )

            document = Document(
                id=doc_id,
                gstin=access.gstin,
                fp=fp,
                capture_source=source,
                doc_type=doc_type,
                minio_key=meta["minio_key"],
                sha256=meta["sha256"],
                bytes=meta["bytes"],
                page_count=meta["page_count"],
                uploaded_by=uploaded_by,
            )
            job = ExtractionJob(document_id=doc_id, status=JobStatus.QUEUED)

            session.add(document)
            session.add(job)
            await session.commit()
            await session.refresh(document)
            await session.refresh(job)

            # Enqueue to Redis (best-effort)
            try:
                await _enqueue_extraction_job(job.id)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("failed to enqueue: %s", e)

            created.append(_document_envelope(document, job))

    return {"created": created, "skipped": skipped}
