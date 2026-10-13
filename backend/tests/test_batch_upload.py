"""Multi-PDF batch upload feature tests (uncommitted multi-PDF implementation).

Covers the pieces that had NO dedicated tests:
  - build_document_from_bytes (sha256 / page count / mime)
  - split_pdf_if_multi_invoice (single-page, multi-page single invoice, split)
  - store_batch_documents (N files -> N docs + N QUEUED jobs, dedupe, enqueue)
  - POST /gst-accounts/{gstin}/months/{fp}/documents/batch
  - GET  /gst-accounts/{gstin}/months/{fp}/extraction/queue-status

Money / identity rules: every fixture GSTIN is a mod-36-valid synthetic
(tests.gstin_fixtures.make_gstin); no fabricated taxpayer data.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

import httpx
import pytest
from app.core.access import resolve_gstin_access
from app.core.auth.tokens import verify_access_token
from app.db.models.extraction import Document, ExtractionJob
from app.services.documents import (
    build_document_from_bytes,
    split_pdf_if_multi_invoice,
    store_batch_documents,
)
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_email, register_and_login
from tests.v4_helpers import seed_gstin_for_user, seed_open_period

pytestmark = pytest.mark.asyncio

FP = "092026"


def _make_pdf(pages: list[str]) -> bytes:
    """Build a PDF with one page per string; text is drawn on each page."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_png(seed: int) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (2, 2), color=(seed % 256, 7, 42))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _seed_user_and_account(
    client: httpx.AsyncClient, api_sessionmaker: async_sessionmaker[Any]
) -> tuple[dict[str, str], str, uuid.UUID]:
    tokens = await register_and_login(client, make_email())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = verify_access_token(tokens["access_token"])
    account = await seed_gstin_for_user(api_sessionmaker, user_id, state_code="27")
    await seed_open_period(api_sessionmaker, account.gstin, FP)
    return headers, account.gstin, user_id


# --------------------------------------------------------------------------- #
# Pure byte helpers (no DB / MinIO)
# --------------------------------------------------------------------------- #


async def test_build_document_from_bytes_pdf_page_count_and_sha256():
    data = _make_pdf(["page one", "page two"])
    built = await build_document_from_bytes(data, "inv.pdf", "PDF_SCAN")
    assert built["page_count"] == 2
    assert built["mime_type"] == "application/pdf"
    assert built["bytes"] == len(data)
    assert len(built["sha256"]) == 64
    assert built["payload"] == data


async def test_build_document_from_bytes_image_mime():
    data = _make_png(1)
    built = await build_document_from_bytes(data, "inv.png", "PHOTO")
    assert built["mime_type"] == "image/jpeg"
    assert built["page_count"] == 1


async def test_split_pdf_single_page_is_untouched():
    data = _make_pdf(["TAX INVOICE INVOICE NO: INV-1"])
    parts = await split_pdf_if_multi_invoice(data)
    assert len(parts) == 1
    assert parts[0] == data


async def test_split_pdf_multi_page_single_invoice_is_untouched():
    # One header on page 0; page 1 is a continuation without a new header.
    data = _make_pdf(["TAX INVOICE INVOICE NO: INV-1", "line items continued"])
    parts = await split_pdf_if_multi_invoice(data)
    assert len(parts) == 1


async def test_split_pdf_splits_distinct_invoices():
    data = _make_pdf(
        [
            "TAX INVOICE INVOICE NO: INV-1",
            "more of INV-1",
            "TAX INVOICE INVOICE NO: INV-2",
            "more of INV-2",
        ]
    )
    parts = await split_pdf_if_multi_invoice(data)
    assert len(parts) == 2

    import fitz

    for part in parts:
        d = fitz.open(stream=part, filetype="pdf")
        assert len(d) >= 1
        d.close()


async def test_split_pdf_does_not_oversplit_on_repeated_invoice_number():
    # A continuation page that repeats the SAME invoice number in its header
    # must NOT be treated as a new invoice.
    data = _make_pdf(
        [
            "TAX INVOICE INVOICE NO: INV-1",
            "INVOICE NO: INV-1  (page 2 of 3)  line items continued",
        ]
    )
    parts = await split_pdf_if_multi_invoice(data)
    assert len(parts) == 1


async def test_first_invoice_number_extraction():
    from app.services.documents import _first_invoice_number

    assert _first_invoice_number("TAX INVOICE INVOICE NO: INV-1") == "INV-1"
    assert _first_invoice_number("INVOICE NUMBER: 12345") == "12345"
    assert _first_invoice_number("INVOICE NO:ABC/2026-004") == "ABC/2026-004"
    assert _first_invoice_number("line items only") is None


# --------------------------------------------------------------------------- #
# Service layer
# --------------------------------------------------------------------------- #


async def test_store_batch_documents_creates_queued_jobs_and_enqueues(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
):
    headers, gstin, user_id = await _seed_user_and_account(client, api_sessionmaker)

    async with api_sessionmaker() as session:
        access = await resolve_gstin_access(session, user_id, gstin)
        files = [
            UploadFile(filename=f"inv{i}.png", file=io.BytesIO(_make_png(i)))
            for i in range(2)
        ]
        results = await store_batch_documents(
            session,
            access=access,
            fp=FP,
            files=files,
            capture_source="PHOTO",
            doc_type="UNCLASSIFIED",
            uploaded_by=user_id,
        )

    created = results["created"]
    assert results["skipped"] == []
    assert len(created) == 2
    for r in created:
        assert r["gstin"] == gstin
        assert r["fp"] == FP
        assert r["job"]["status"] == "QUEUED"

    # 2 documents + 2 jobs persisted
    async with api_sessionmaker() as session:
        n_docs = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.gstin == gstin, Document.fp == FP)
            )
        ).scalar_one()
        n_jobs = (
            await session.execute(
                select(func.count())
                .select_from(ExtractionJob)
                .where(
                    ExtractionJob.document_id.in_(
                        [uuid.UUID(r["id"]) for r in created]
                    )
                )
            )
        ).scalar_one()
    assert n_docs == 2
    assert n_jobs == 2

    # both enqueued on the shared (fakeredis) client
    assert await fake_redis.llen("extraction:queue") == 2


async def test_store_batch_documents_dedupes_identical_file(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
):
    headers, gstin, user_id = await _seed_user_and_account(client, api_sessionmaker)

    async with api_sessionmaker() as session:
        access = await resolve_gstin_access(session, user_id, gstin)
        files = [
            UploadFile(filename="dup.png", file=io.BytesIO(_make_png(99))),
            UploadFile(filename="dup.png", file=io.BytesIO(_make_png(99))),
        ]
        results = await store_batch_documents(
            session,
            access=access,
            fp=FP,
            files=files,
            capture_source="PHOTO",
            doc_type="UNCLASSIFIED",
            uploaded_by=user_id,
        )

    assert len(results["created"]) == 1
    assert len(results["skipped"]) == 1
    assert results["skipped"][0]["reason"] == "duplicate document (sha256)"
    assert await fake_redis.llen("extraction:queue") == 1


async def test_store_batch_documents_reports_oversized_file(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
):
    headers, gstin, user_id = await _seed_user_and_account(client, api_sessionmaker)

    async with api_sessionmaker() as session:
        access = await resolve_gstin_access(session, user_id, gstin)
        # > 25 MB → must be reported as skipped, never silently dropped.
        files = [UploadFile(filename="big.png", file=io.BytesIO(b"x" * (25 * 1024 * 1024 + 1)))]
        results = await store_batch_documents(
            session,
            access=access,
            fp=FP,
            files=files,
            capture_source="PHOTO",
            doc_type="UNCLASSIFIED",
            uploaded_by=user_id,
        )

    assert results["created"] == []
    assert len(results["skipped"]) == 1
    assert results["skipped"][0]["filename"] == "big.png"
    assert results["skipped"][0]["reason"] == "exceeds 25 MB limit"
    assert await fake_redis.llen("extraction:queue") == 0


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #


async def test_batch_endpoint_creates_one_doc_per_file_and_reports_count(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
):
    headers, gstin, _ = await _seed_user_and_account(client, api_sessionmaker)

    files = [
        ("files", (f"inv{i}.png", _make_png(i), "image/png")) for i in range(3)
    ]
    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{FP}/documents/batch",
        headers=headers,
        data={"capture_source": "PHOTO", "doc_type": "UNCLASSIFIED"},
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3
    assert len(body["data"]["created"]) == 3

    ids = {uuid.UUID(d["id"]) for d in body["data"]["created"]}
    async with api_sessionmaker() as session:
        n_docs = (
            await session.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.gstin == gstin, Document.fp == FP)
            )
        ).scalar_one()
        n_jobs = (
            await session.execute(
                select(func.count()).select_from(ExtractionJob).where(
                    ExtractionJob.document_id.in_(ids)
                )
            )
        ).scalar_one()
    assert n_docs == 3
    assert n_jobs == 3
    assert await fake_redis.llen("extraction:queue") == 3


async def test_batch_endpoint_splits_multi_invoice_pdf(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
):
    headers, gstin, _ = await _seed_user_and_account(client, api_sessionmaker)

    pdf = _make_pdf(
        [
            "TAX INVOICE INVOICE NO: INV-1",
            "more of INV-1",
            "TAX INVOICE INVOICE NO: INV-2",
            "more of INV-2",
        ]
    )
    files = [("files", ("multi.pdf", pdf, "application/pdf"))]
    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{FP}/documents/batch",
        headers=headers,
        data={"capture_source": "DIGITAL", "doc_type": "UNCLASSIFIED"},
        files=files,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 2


async def test_batch_endpoint_requires_write_role(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
):
    # A VIEWER cannot upload: the batch route re-checks can_write.
    headers, gstin, user_id = await _seed_user_and_account(client, api_sessionmaker)

    # Demote the creator to VIEWER directly on the access row.
    from app.db.models.core import AccessRole, UserGstAccess
    from sqlalchemy import update

    async with api_sessionmaker() as session:
        await session.execute(
            update(UserGstAccess)
            .where(UserGstAccess.gstin == gstin, UserGstAccess.user_id == user_id)
            .values(role=AccessRole.VIEWER)
        )
        await session.commit()

    files = [("files", ("inv0.png", _make_png(0), "image/png"))]
    resp = await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{FP}/documents/batch",
        headers=headers,
        data={"capture_source": "PHOTO", "doc_type": "UNCLASSIFIED"},
        files=files,
    )
    assert resp.status_code == 403


async def test_queue_status_endpoint_reports_depth(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    fake_redis: Any,
):
    headers, gstin, _ = await _seed_user_and_account(client, api_sessionmaker)

    await fake_redis.rpush("extraction:queue", "job-a", "job-b")

    resp = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{FP}/extraction/queue-status",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["queue_depth"] >= 2
    assert data["gstin"] == gstin
    assert data["fp"] == FP
