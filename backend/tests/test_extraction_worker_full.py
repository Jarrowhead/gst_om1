"""Full extraction-worker tests: run_job, _persist_result, _fetch_document,
_detect_mime, and the Redis poll helper.

The CLI smoke tests in test_extraction_worker.py only cover `main`; these
cover the worker's real job-processing path with injected fake OCR/LLM
providers against the live test PG + MinIO.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import httpx
import pytest
from app.db.models.extraction import ExtractionJob, InvoiceDraft, JobStatus
from app.extraction import worker
from app.extraction.llm import LLMClient
from app.extraction.models import (
    AutoConfirmStatus,
    ExtractedDocument,
    ExtractionResult,
    ValidationResult,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_email, register_and_login
from tests.gstin_fixtures import make_gstin, make_pan
from tests.v4_helpers import seed_gstin_for_user, seed_open_period

FP = "092026"


class _FakeLLM(LLMClient):
    model = "test-model"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def extract(self, ocr_text: str) -> dict[str, Any]:
        return self.payload


def _llm_payload(supplier_gstin: str) -> dict[str, Any]:
    return {
        "doc_id": "INV-W-001",
        "capture_source": "PHOTO",
        "fields": {
            "supplier_gstin": supplier_gstin,
            "buyer_gstin": None,
            "invoice_no": "INV-W-001",
            "invoice_date": "2026-09-15",
            "place_of_supply": supplier_gstin[:2],
            "is_inter_state": False,
            "rchrg": False,
            "inv_typ": "R",
            "taxable_value_paise": 100000,
            "total_value_paise": 118000,
            "cgst_paise": 9000,
            "sgst_paise": 9000,
            "igst_paise": 0,
            "cess_paise": 0,
        },
        "lines": [],
        "confidence": {
            "supplier_gstin": 0.99, "invoice_no": 0.99, "invoice_date": 0.99,
            "place_of_supply": 0.99, "is_inter_state": 0.99, "rchrg": 0.99,
            "inv_typ": 0.99, "taxable_value_paise": 0.99, "total_value_paise": 0.99,
            "cgst_paise": 0.99, "sgst_paise": 0.99, "igst_paise": 0.99,
            "cess_paise": 0.99,
        },
        "_llm_meta": {"model": "test-model", "tokens_in": 10, "tokens_out": 20},
    }


def _fake_ocr(text: str = "TAX INVOICE Invoice INV-W-001 Date 2026-09-15"):
    def _ocr(_image: Any) -> str:
        return text

    return _ocr


async def _seed_and_upload(
    client: httpx.AsyncClient, api_sessionmaker: async_sessionmaker[Any]
) -> tuple[str, str]:
    """Register a user, seed a GSTIN + open period, upload one PNG, return (gstin, doc_id)."""
    import numpy as np
    from app.core.auth.tokens import verify_access_token
    from PIL import Image

    tokens = await register_and_login(client, make_email())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = verify_access_token(tokens["access_token"])
    account = await seed_gstin_for_user(api_sessionmaker, user_id, state_code="27")
    gstin = account.gstin
    await seed_open_period(api_sessionmaker, gstin, FP)

    # Noisy image: enough detail to pass the pipeline's blur gate.
    arr = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{FP}/documents",
        headers=headers,
        data={"capture_source": "PHOTO"},
        files={"files": ("w.png", buf.getvalue(), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["data"]["id"]
    return gstin, doc_id


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_detect_mime_pdf() -> None:
    assert worker._detect_mime(b"%PDF-1.7") == "application/pdf"


def test_detect_mime_jpeg() -> None:
    assert worker._detect_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_detect_mime_png() -> None:
    assert worker._detect_mime(b"\x89PNG\r\n\x1a\n") == "image/png"


def test_detect_mime_unsupported_raises() -> None:
    with pytest.raises(ValueError):
        worker._detect_mime(b"GIF89a")


async def test_poll_once_pops_job(fake_redis: Any) -> None:
    jid = str(uuid.uuid4())
    await fake_redis.rpush("extraction:queue", jid)
    assert await worker._poll_once(fake_redis) == jid


async def test_poll_once_empty_returns_none(fake_redis: Any) -> None:
    assert await worker._poll_once(fake_redis) is None


# --------------------------------------------------------------------------- #
# run_job / _persist_result
# --------------------------------------------------------------------------- #


async def test_run_job_persists_draft_and_terminal_status(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: api_sessionmaker)
    gstin, doc_id = await _seed_and_upload(client, api_sessionmaker)

    async with api_sessionmaker() as session:
        job = (
            await session.execute(
                select(ExtractionJob).where(
                    ExtractionJob.document_id == uuid.UUID(doc_id)
                )
            )
        ).scalar_one()

    supplier_gstin = make_gstin(pan=make_pan(), state_code="27")
    result = await worker.run_job(
        job.id,
        ocr_provider=_fake_ocr(),
        llm_client=_FakeLLM(_llm_payload(supplier_gstin)),
    )
    assert result is not None

    async with api_sessionmaker() as session:
        refreshed = await session.get(ExtractionJob, job.id)
        assert refreshed.status in {JobStatus.EXTRACTED, JobStatus.NEEDS_REVIEW}
        draft = (
            await session.execute(
                select(InvoiceDraft).where(
                    InvoiceDraft.extraction_job_id == job.id
                )
            )
        ).scalar_one()
        assert draft.payload["fields"]["invoice_no"] == "INV-W-001"


async def test_run_job_missing_job_returns_none(
    api_sessionmaker: async_sessionmaker[Any],
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: api_sessionmaker)
    result = await worker.run_job(uuid.uuid4())
    assert result is None


async def test_persist_result_failure_marks_job_failed(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin, doc_id = await _seed_and_upload(client, api_sessionmaker)

    async with api_sessionmaker() as session:
        job = (
            await session.execute(
                select(ExtractionJob).where(
                    ExtractionJob.document_id == uuid.UUID(doc_id)
                )
            )
        ).scalar_one()
        doc = await session.get(
            __import__("app.db.models.extraction", fromlist=["Document"]).Document,
            uuid.UUID(doc_id),
        )
        failed = ExtractionResult(error="re-upload a clearer image")
        await worker._persist_result(session, job, doc, failed)

    async with api_sessionmaker() as session:
        refreshed = await session.get(ExtractionJob, job.id)
        assert refreshed.status == JobStatus.FAILED
        assert refreshed.error == "re-upload a clearer image"


async def test_persist_result_auto_confirmed_marks_extracted(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    gstin, doc_id = await _seed_and_upload(client, api_sessionmaker)

    from app.db.models.extraction import Document

    supplier_gstin = make_gstin(pan=make_pan(), state_code="27")
    document = ExtractedDocument.model_validate(_llm_payload(supplier_gstin))
    validation = ValidationResult(
        document=document,
        flags=[],
        auto_corrected={},
        derived={},
        auto_confirm=AutoConfirmStatus.AUTO_CONFIRMED,
        confidence_avg=0.99,
    )
    result = ExtractionResult(
        document=document,
        validation=validation,
        llm_model="test-model",
        llm_tokens_in=10,
        llm_tokens_out=20,
    )

    async with api_sessionmaker() as session:
        job = (
            await session.execute(
                select(ExtractionJob).where(
                    ExtractionJob.document_id == uuid.UUID(doc_id)
                )
            )
        ).scalar_one()
        doc = await session.get(Document, uuid.UUID(doc_id))
        await worker._persist_result(session, job, doc, result)

    async with api_sessionmaker() as session:
        refreshed = await session.get(ExtractionJob, job.id)
        assert refreshed.status == JobStatus.EXTRACTED
        assert refreshed.llm_model == "test-model"
        assert refreshed.llm_tokens_in == 10
