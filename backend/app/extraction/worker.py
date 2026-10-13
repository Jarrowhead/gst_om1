"""Extraction worker CLI.

Usage:
    python -m app.extraction.worker --job-id <uuid>
    python -m app.extraction.worker --poll

Durable state lives in extraction.extraction_jobs; Redis holds only job IDs.
The worker fetches the job, loads the document from MinIO, runs the pipeline,
and writes the result back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.core import GstAccount
from app.db.models.extraction import Document, ExtractionJob, InvoiceDraft, JobStatus
from app.db.session import get_sessionmaker
from app.extraction.llm import LLMClient
from app.extraction.ocr import OCRProvider
from app.extraction.pipeline import run_pipeline, run_pipeline_many
from app.services.storage import _BUCKET, _minio_client

_LOGGER = logging.getLogger(__name__)


async def _fetch_document(
    session: AsyncSession, job_id: uuid.UUID
) -> tuple[ExtractionJob, Document, str, str | None] | None:
    job = await session.get(ExtractionJob, job_id)
    if job is None:
        _LOGGER.error("job %s not found", job_id)
        return None
    doc = await session.get(Document, job.document_id)
    if doc is None:
        _LOGGER.error("document %s for job %s not found", job.document_id, job_id)
        return None
    account = await session.get(GstAccount, doc.gstin)
    if account is None:
        _LOGGER.error("gst account %s for doc %s not found", doc.gstin, doc.id)
        return None
    return job, doc, account.gstin, account.pan


async def _load_bytes(minio_key: str) -> bytes:
    client = _minio_client()
    response = await asyncio.to_thread(client.get_object, _BUCKET, minio_key)
    try:
        return await asyncio.to_thread(response.read)
    finally:
        await asyncio.to_thread(response.close)
        await asyncio.to_thread(response.release_conn)


async def _persist_result(
    session: AsyncSession,
    job: ExtractionJob,
    doc: Document,
    result: Any,
) -> None:
    from decimal import Decimal

    from app.extraction.models import AutoConfirmStatus, ExtractionResult

    if not isinstance(result, ExtractionResult):
        raise TypeError(f"expected ExtractionResult, got {type(result).__name__}")
    job.raw_llm_output = result.document.model_dump(mode="json") if result.document else None
    job.llm_model = result.llm_model
    job.llm_tokens_in = result.llm_tokens_in
    job.llm_tokens_out = result.llm_tokens_out
    if result.validation and result.validation.confidence_avg is not None:
        job.confidence_avg = Decimal(f"{result.validation.confidence_avg:.3f}")
    else:
        job.confidence_avg = None

    if result.error:
        job.status = JobStatus.FAILED
        job.error = result.error
        _LOGGER.info("job %s FAILED: %s", job.id, result.error)
    elif result.validation and result.validation.auto_confirm == AutoConfirmStatus.AUTO_CONFIRMED:
        job.status = JobStatus.EXTRACTED
        _LOGGER.info("job %s EXTRACTED (auto-confirm eligible)", job.id)
    else:
        job.status = JobStatus.NEEDS_REVIEW
        _LOGGER.info("job %s NEEDS_REVIEW", job.id)

    # For failures at preprocess stage we still want the preproc report persisted.
    if result.error == "re-upload a clearer image":
        pr = result.preproc_report
        job.preproc_report = (job.preproc_report or {}) | {
            "pipeline_status": job.status.value,
            "capture_source": pr.capture_source if pr else None,
            "blur_score": pr.blur_score if pr else None,
            "blur_threshold": pr.blur_threshold if pr else None,
            "blur_rejected": pr.blur_rejected if pr else None,
            "applied": pr.applied if pr else [],
            "pages": pr.pages if pr else None,
            "validation_flags": [],
        }

    if result.document:
        # One draft per job: re-running an extraction (e.g. the dev-mode
        # synchronous trigger after the UI already ran it) must refresh the
        # existing draft, never insert a second row — a duplicate draft makes
        # every subsequent read raise MultipleResultsFound (500).
        existing_draft = (
            await session.execute(
                select(InvoiceDraft).where(InvoiceDraft.extraction_job_id == job.id)
            )
        ).scalars().first()
        payload = result.document.model_dump(mode="json")
        confidence = result.document.confidence.model_dump(mode="json")
        if existing_draft is None:
            session.add(
                InvoiceDraft(
                    extraction_job_id=job.id,
                    gstin=doc.gstin,
                    fp=doc.fp,
                    payload=payload,
                    field_confidence=confidence,
                )
            )
        else:
            existing_draft.gstin = doc.gstin
            existing_draft.fp = doc.fp
            existing_draft.payload = payload
            existing_draft.field_confidence = confidence

    pr = result.preproc_report
    job.preproc_report = (job.preproc_report or {}) | {
        "pipeline_status": job.status.value,
        "capture_source": pr.capture_source if pr else None,
        "blur_score": pr.blur_score if pr else None,
        "blur_threshold": pr.blur_threshold if pr else None,
        "blur_rejected": pr.blur_rejected if pr else None,
        "applied": pr.applied if pr else [],
        "pages": pr.pages if pr else None,
        "validation_flags": (
            [f.model_dump() for f in result.validation.flags]
            if result.validation else []
        ),
    }
    await session.commit()


async def run_job(
    job_id: uuid.UUID,
    *,
    ocr_provider: OCRProvider | None = None,
    llm_client: LLMClient | None = None,
) -> Any:
    """Run extraction pipeline for a single job id and persist the result."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        fetched = await _fetch_document(session, job_id)
        if fetched is None:
            return None
        job, doc, gstin, pan = fetched

        job.status = JobStatus.PREPROCESS
        await session.commit()

        data = await _load_bytes(doc.minio_key)
        mime_type = _detect_mime(data)

        # Query existing invoice numbers for duplicate rule.
        from app.db.models.gst import Invoice

        existing = (
            await session.execute(
                select(Invoice.invoice_no).where(
                    Invoice.gstin == gstin, Invoice.fp == doc.fp
                )
            )
        ).scalars().all()
        existing_set = {str(n) for n in existing if n}

        if mime_type == "application/pdf":
            result = await run_pipeline(
                data,
                mime_type,
                doc.capture_source.value,
                doc_id=str(doc.id),
                ocr_provider=ocr_provider,
                llm_client=llm_client,
                settings=settings,
                registration_pan=pan,
                existing_invoice_nos=existing_set,
                period_fp=doc.fp,
            )
        else:
            result = await run_pipeline_many(
                [(mime_type, data)],
                doc.capture_source.value,
                doc_id=str(doc.id),
                ocr_provider=ocr_provider,
                llm_client=llm_client,
                settings=settings,
                registration_pan=pan,
                existing_invoice_nos=existing_set,
                period_fp=doc.fp,
            )

        await _persist_result(session, job, doc, result)
        return result


def _detect_mime(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    raise ValueError("unsupported document bytes")


async def _poll_once(redis_client: redis.Redis) -> str | None:
    """Pop one job id from the extraction queue with timeout resilience."""
    try:
        item = await redis_client.blpop("extraction:queue", timeout=2)
        if item is None:
            return None
        return item[1].decode() if isinstance(item[1], bytes) else str(item[1])
    except (TimeoutError, Exception):
        return None


async def poll_loop(
    *,
    ocr_provider: OCRProvider | None = None,
    llm_client: LLMClient | None = None,
    concurrency: int = 4,
) -> None:
    """Long-running poll: bounded parallel workers via asyncio.Semaphore."""
    import asyncio
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, protocol=2)
    sem = asyncio.Semaphore(concurrency)
    active_tasks: set[asyncio.Task] = set()

    async def _handle_job(jid: uuid.UUID):
        async with sem:
            try:
                await run_job(jid, ocr_provider=ocr_provider, llm_client=llm_client)
            except Exception:
                _LOGGER.exception("job %s failed in worker", jid)

    try:
        while True:
            if sem.locked():
                await asyncio.sleep(0.1)
                continue
            raw = await _poll_once(redis_client)
            if raw is None:
                continue
            try:
                job_id = uuid.UUID(raw)
            except ValueError:
                _LOGGER.error("invalid job id in queue: %s", raw)
                continue
            task = asyncio.create_task(_handle_job(job_id))
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)
    finally:
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        await redis_client.aclose()


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="GST extraction worker")
    ap.add_argument("--job-id", type=str, help="single job UUID to process")
    ap.add_argument("--poll", action="store_true", help="poll Redis queue continuously")
    ap.add_argument("--verbose", action="store_true", help="debug logging")
    return ap


async def main(argv: list[str] | None = None) -> int:
    ap = _build_arg_parser()
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.job_id:
        job_id = uuid.UUID(args.job_id)
        result = await run_job(job_id)
        if result is None:
            return 1
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return 0
    if args.poll:
        await poll_loop()
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
