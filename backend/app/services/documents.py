"""Document service: upload, list, detail, presigned URL, dedupe checks.

Follows API_SPECIFICATION.md §5 + SECURITY_AND_ACCESS.md §3 (v4: every document
is keyed directly by GSTIN + filing period, never by a surrogate registration id).
"""

from __future__ import annotations

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

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


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
