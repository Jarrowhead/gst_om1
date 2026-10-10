"""Batch Confirm (task 11.2, PHASE11_13 rec 2) — API + service regression.

Contract under test (PHASE11_13_RECOMMENDATION_BUILD.md rec 2, task 11.2):

  * the exact 0.95 boundary: a mandatory-field average of exactly 0.95 is
    confirmed, 0.9499 is quarantined (Decimal gate, no float drift);
  * a BLOCK-flagged draft is excluded (quarantined, job untouched)
    regardless of its confidence;
  * one append-only BATCH_CONFIRM audit row per real call, keyed by gstin;
  * confirmed drafts land in the ledger through the SAME writer the single
    confirm uses (paise-exact lines, NEEDS_REVIEW -> CONFIRMED);
  * dry_run=true writes nothing (no ledger rows, no job flips, no audit row)
    and reports exactly what a real call then confirms;
  * BULK_IMPORT drafts (no document/job — 11.1) are confirmed too, and a
    second call is a no-op for them (idempotency via the ledger);
  * a locked period returns 423 before anything is written; VIEWER 403;
    another user's / unknown GSTIN 404.

GSTINs are mod-36 checksum-valid synthetic fixtures (tests/gstin_fixtures) —
never hand-typed literals. Money is integer paise everywhere.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from app.db.models.core import AccessRole, AuditLog, User, UserGstAccess
from app.db.models.extraction import InvoiceDraft
from app.db.models.gst import Invoice
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_mobile, register_and_login
from tests.gstin_fixtures import make_gstin
from tests.v4_helpers import seed_gstin_for_user, seed_open_period

pytestmark = pytest.mark.asyncio

FP = "092026"
URL_T = "/api/v1/gst-accounts/{gstin}/months/{fp}/review-queue/batch-confirm"


def _clean_fields(reg_gstin: str, supplier_gstin: str, invoice_no: str) -> dict[str, Any]:
    """Validator-clean intra-state purchase field set (integer paise)."""
    taxable = 100000
    cgst = sgst = 9000
    return {
        "supplier_gstin": supplier_gstin,
        "buyer_gstin": reg_gstin,
        "invoice_no": invoice_no,
        "invoice_date": "2026-09-05",
        "place_of_supply": "27",
        "is_inter_state": False,
        "rchrg": False,
        "inv_typ": "R",
        "taxable_value_paise": taxable,
        "total_value_paise": taxable + cgst + sgst,
        "cgst_paise": cgst,
        "sgst_paise": sgst,
        "igst_paise": 0,
        "cess_paise": 0,
    }


def _draft(
    fields: dict[str, Any],
    *,
    confidence: float,
    source: str = "EXTRACTION",
    job_id: uuid.UUID | None = None,
    gstin: str | None = None,
) -> InvoiceDraft:
    return InvoiceDraft(
        extraction_job_id=job_id,
        gstin=gstin or str(fields["buyer_gstin"]),
        fp=FP,
        source=source,
        payload={"fields": dict(fields), "lines": []},
        field_confidence={k: confidence for k in fields},
    )


async def _seed_extraction_draft(
    sessionmaker: async_sessionmaker[Any],
    fields: dict[str, Any],
    *,
    confidence: float,
    job_status: str = "NEEDS_REVIEW",
    gstin: str | None = None,
) -> uuid.UUID:
    """Document + job + draft (the OCR population). Returns the document id.

    `gstin` is the REGISTRATION the document belongs to (the ledger party);
    it defaults to the buyer for the common purchase drafts. Pass it
    explicitly when the field parties are deliberately foreign (the
    PAN-mismatch BLOCK test).
    """
    from app.db.models.extraction import (
        CaptureSource,
        Document,
        ExtractionJob,
        JobStatus,
    )

    gstin = gstin or str(fields["buyer_gstin"])
    async with sessionmaker() as session:
        doc = Document(
            id=uuid.uuid4(),
            gstin=gstin,
            fp=FP,
            capture_source=CaptureSource.PHOTO,
            doc_type="INV",
            minio_key=f"{gstin}/{FP}/{uuid.uuid4().hex}.png",
            sha256=uuid.uuid4().hex,
            bytes=100,
            page_count=1,
        )
        job = ExtractionJob(
            document_id=doc.id,
            status=getattr(JobStatus, job_status),
        )
        session.add(doc)
        session.add(job)
        await session.flush()
        session.add(_draft(fields, confidence=confidence, job_id=job.id, gstin=gstin))
        await session.commit()
        return doc.id


async def _seed_import_draft(
    sessionmaker: async_sessionmaker[Any],
    fields: dict[str, Any],
    *,
    confidence: float = 0.95,
) -> None:
    """Bare jobless BULK_IMPORT draft (the spreadsheet population from 11.1)."""
    from app.db.models.extraction import InvoiceDraft as ID

    async with sessionmaker() as session:
        session.add(
            ID(
                extraction_job_id=None,
                gstin=str(fields["buyer_gstin"]),
                fp=FP,
                source="BULK_IMPORT",
                payload={"fields": dict(fields), "lines": []},
                field_confidence={k: confidence for k in fields},
            )
        )
        await session.commit()


async def _tenant(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[Any],
) -> tuple[dict[str, str], str, str]:
    """Register a user, attach one GSTIN + open period; (headers, gstin, uid)."""
    from app.core.auth.tokens import verify_access_token

    tokens = await register_and_login(client, make_mobile())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = verify_access_token(tokens["access_token"])
    account = await seed_gstin_for_user(sessionmaker, user_id, state_code="27")
    await seed_open_period(sessionmaker, account.gstin, FP)
    return headers, account.gstin, str(user_id)


async def _grant_role(
    sessionmaker: async_sessionmaker[Any], gstin: str, user_id: str, role: str
) -> None:
    async with sessionmaker() as session:
        session.add(
            UserGstAccess(
                gstin=gstin, user_id=uuid.UUID(user_id), role=AccessRole(role)
            )
        )
        await session.commit()


async def _ledger(
    sessionmaker: async_sessionmaker[Any], gstin: str
) -> list[Invoice]:
    async with sessionmaker() as session:
        return cast(
            "list[Invoice]",
            (
                await session.execute(
                    select(Invoice).where(Invoice.gstin == gstin, Invoice.fp == FP)
                )
            )
            .scalars()
            .all(),
        )


async def _batch_audit_rows(
    sessionmaker: async_sessionmaker[Any], gstin: str
) -> list[AuditLog]:
    async with sessionmaker() as session:
        return cast(
            "list[AuditLog]",
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.gstin == gstin, AuditLog.action == "BATCH_CONFIRM"
                    )
                )
            )
            .scalars()
            .all(),
        )


# ---------------------------------------------------------------------------
# The 0.95 boundary (Decimal-exact)
# ---------------------------------------------------------------------------


async def test_batch_confirm_threshold_boundary_095_in_09499_out(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """Exactly 0.95 averages confirm; 0.9499 stays quarantined — one call."""
    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-095"),
        confidence=0.95,
    )
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-09499"),
        confidence=0.9499,
    )

    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["dry_run"] is False
    assert data["confirmed"] == 1
    assert data["quarantined"] == 1
    assert data["quarantine"][0]["reason"] == "BELOW_THRESHOLD"
    assert data["quarantine"][0]["confidence_avg"] == pytest.approx(0.9499)

    ledger = await _ledger(api_sessionmaker, gstin)
    assert [inv.invoice_no for inv in ledger] == ["INV-BC-095"]


async def test_batch_confirm_every_draft_below_boundary_quarantined(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-LOW"),
        confidence=0.9499,
    )
    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["confirmed"] == 0
    assert data["quarantined"] == 1
    assert await _ledger(api_sessionmaker, gstin) == []


# ---------------------------------------------------------------------------
# Blocking flags + audit
# ---------------------------------------------------------------------------


async def test_batch_confirm_excludes_blocked_draft_and_writes_audit_row(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """A BLOCK-flagged draft stays pending at ANY confidence; audit row written."""
    from app.db.models.extraction import Document, ExtractionJob

    headers, gstin, user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    # A supplier whose PAN never matches the registration PAN -> the
    # validator's BLOCK PAN_CONSISTENCY flag (regardless of confidence). The
    # document still belongs to the registration; both PARTIES are foreign.
    outsider_supplier = make_gstin(state_code="29")
    fields = _clean_fields(gstin, outsider_supplier, "INV-BC-BLK")
    fields["buyer_gstin"] = make_gstin(state_code="27")  # neither party = reg PAN
    await _seed_extraction_draft(
        api_sessionmaker, fields, confidence=0.99, gstin=gstin
    )
    # a clean 0.95 draft in the same period still confirms
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-OK"),
        confidence=0.95,
    )

    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["confirmed"] == 1
    assert data["quarantined"] == 1
    blocked = [q for q in data["quarantine"] if q["reason"] == "BLOCKING_FLAGS"]
    assert len(blocked) == 1
    assert "PAN_CONSISTENCY" in blocked[0]["rules"]

    async with api_sessionmaker() as session:
        # the blocked job is untouched: quarantined, never deleted (its doc's
        # job is still NEEDS_REVIEW); the clean one's job moved to CONFIRMED.
        job_rows = (
            (
                await session.execute(
                    select(ExtractionJob)
                    .join(
                        Document, Document.id == ExtractionJob.document_id
                    )
                    .where(Document.gstin == gstin, Document.fp == FP)
                )
            )
            .scalars()
            .all()
        )
        assert sorted(j.status.value for j in job_rows) == ["CONFIRMED", "NEEDS_REVIEW"]

    audit_rows = await _batch_audit_rows(api_sessionmaker, gstin)
    assert len(audit_rows) == 1
    assert audit_rows[0].actor_user_id == uuid.UUID(user_id)
    assert audit_rows[0].entity_id == gstin  # keyed by gstin
    payload = audit_rows[0].payload_diff or {}
    assert payload["confirmed"] == 1
    assert payload["quarantined"] == 1

    # one append-only row per real call
    second = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["confirmed"] == 0  # nothing new pending
    assert len(await _batch_audit_rows(api_sessionmaker, gstin)) == 2


# ---------------------------------------------------------------------------
# Ledger writes through the shared writer
# ---------------------------------------------------------------------------


async def test_batch_confirm_confirmed_rows_are_paise_exact(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from app.db.models.extraction import ExtractionJob, JobStatus

    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    doc_id = await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-PAISE"),
        confidence=0.95,
    )
    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_paise"] == 118000  # 100000 + 9000 + 9000, integer paise

    async with api_sessionmaker() as session:
        invoice = (
            (
                await session.execute(
                    select(Invoice).where(Invoice.gstin == gstin, Invoice.fp == FP)
                )
            )
            .scalars()
            .one()
        )
        assert invoice.direction.value == "PURCHASE"
        assert invoice.total_value_minor == 118000
        assert invoice.supplier_gstin == supplier
        assert invoice.buyer_gstin == gstin
        assert invoice.status.value == "CONFIRMED"
        assert invoice.source_doc_id == doc_id
        job = (
            (
                await session.execute(
                    select(ExtractionJob).where(
                        ExtractionJob.document_id == doc_id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert job.status == JobStatus.CONFIRMED


# ---------------------------------------------------------------------------
# dry_run: the button's enable/disable signal
# ---------------------------------------------------------------------------


async def test_batch_confirm_dry_run_writes_nothing(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-DRY"),
        confidence=0.95,
    )
    resp = await client.post(
        URL_T.format(gstin=gstin, fp=FP), headers=headers, params={"dry_run": "true"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["dry_run"] is True
    assert data["confirmed"] == 1

    # nothing written: no invoice, no audit row — and the draft still confirms
    # in a following real call, returning exactly what the dry run predicted.
    assert await _ledger(api_sessionmaker, gstin) == []
    assert await _batch_audit_rows(api_sessionmaker, gstin) == []
    real = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert real.status_code == 200, real.text
    assert real.json()["data"]["confirmed"] == 1


# ---------------------------------------------------------------------------
# BULK_IMPORT population (11.1) + idempotency
# ---------------------------------------------------------------------------


async def test_batch_confirm_covers_bulk_import_drafts_idempotently(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    await _seed_import_draft(
        api_sessionmaker, _clean_fields(gstin, supplier, "INV-BC-BULK")
    )

    first = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert first.status_code == 200, first.text
    fdata = first.json()["data"]
    assert fdata["confirmed"] == 1

    async with api_sessionmaker() as session:
        invoice = (
            (await session.execute(select(Invoice).where(Invoice.gstin == gstin)))
            .scalars()
            .one()
        )
        assert invoice.source_doc_id is None  # no document behind a spreadsheet row

    # a re-run finds the draft's invoice_no already in the ledger: no-op
    second = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["confirmed"] == 0
    assert len(await _ledger(api_sessionmaker, gstin)) == 1


# ---------------------------------------------------------------------------
# Empty queue + guards
# ---------------------------------------------------------------------------


async def test_batch_confirm_empty_queue_returns_zeros(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["confirmed"] == 0 and data["quarantined"] == 0


async def test_batch_confirm_locked_period_423(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from app.db.models.gst import FilingPeriod, FilingStatus

    headers, gstin, _uid = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    await _seed_extraction_draft(
        api_sessionmaker,
        _clean_fields(gstin, supplier, "INV-BC-LOCK"),
        confidence=0.95,
    )
    async with api_sessionmaker() as session:
        period = await session.get(FilingPeriod, (gstin, FP))
        period.status = FilingStatus.FILED
        session.add(period)
        await session.commit()

    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=headers)
    assert resp.status_code == 423
    assert resp.json()["error"]["code"] == "PERIOD_LOCKED"
    assert await _ledger(api_sessionmaker, gstin) == []


async def test_batch_confirm_viewer_403(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from app.core.auth.tokens import verify_access_token

    _headers, gstin, _uid = await _tenant(client, api_sessionmaker)

    viewer_tokens = await register_and_login(client, make_mobile())
    viewer_headers = {"Authorization": f"Bearer {viewer_tokens['access_token']}"}
    viewer_id = verify_access_token(viewer_tokens["access_token"])
    await _grant_role(api_sessionmaker, gstin, str(viewer_id), "VIEWER")

    resp = await client.post(URL_T.format(gstin=gstin, fp=FP), headers=viewer_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_batch_confirm_unknown_or_unlinked_gstin_404(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, _gstin, _uid = await _tenant(client, api_sessionmaker)
    other = make_gstin(state_code="27")  # valid checksum, no grant -> 404
    resp = await client.post(URL_T.format(gstin=other, fp=FP), headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "GSTIN_NOT_FOUND"


# ---------------------------------------------------------------------------
# Service-layer direct call (same coverage policy as test_review_service.py)
# ---------------------------------------------------------------------------


async def test_service_batch_confirm_quarantines_below_threshold(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from app.core.access import GstinAccess
    from app.db.models.core import FilingScheme
    from app.db.models.core import GstAccount as GA
    from app.db.models.gst import FilingPeriod, FilingStatus
    from app.services.batch_confirm import batch_confirm

    gstin = make_gstin(state_code="27")
    user = User(email=f"{uuid.uuid4().hex[:9]}@test.local", full_name="BC Co")
    async with api_sessionmaker() as session:
        session.add(user)
        await session.flush()
        user_id = user.id
        session.add(
            GA(gstin=gstin, legal_name="BC Co", pan=gstin[2:12], state_code="27")
        )
        session.add(
            UserGstAccess(gstin=gstin, user_id=user_id, role=AccessRole.ADMIN)
        )
        session.add(
            FilingPeriod(
                gstin=gstin,
                fp=FP,
                scheme_snapshot=FilingScheme.REGULAR_MONTHLY.value,
                status=FilingStatus.OPEN,
            )
        )
        session.add(
            _draft(
                _clean_fields(gstin, make_gstin(state_code="27"), "SVC-1"),
                confidence=0.9499,
                source="BULK_IMPORT",
            )
        )
        await session.commit()

    access = GstinAccess(
        gstin=gstin, role=AccessRole.ADMIN, user_id=user_id,
        legal_name="BC Co", pan=gstin[2:12],
    )
    async with api_sessionmaker() as session:
        result = await batch_confirm(session, access, FP, user_id)
        assert result["confirmed"] == 0
        assert result["quarantined"] == 1
        assert result["quarantine"][0]["confidence_avg"] == pytest.approx(0.9499)


async def test_service_batch_confidence_avg_decimal_exact(
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """The gate's arithmetic: 13 mandatory fields' avg in exact Decimal."""
    from app.services.batch_confirm import _batch_confidence_avg

    conf = {k: 0.95 for k in (
        "supplier_gstin", "buyer_gstin", "invoice_no", "invoice_date",
        "place_of_supply", "is_inter_state", "rchrg", "inv_typ",
        "taxable_value_paise", "total_value_paise", "cgst_paise",
        "sgst_paise", "igst_paise", "cess_paise",
    )}
    draft = _draft(_clean_fields("x", "y", "z"), confidence=0.95)
    draft.field_confidence = conf  # type: ignore[assignment]
    avg = _batch_confidence_avg(draft)
    assert avg == Decimal("0.95")
    # a non-mandatory extra key must never dilute the gate
    conf2 = dict(conf)
    conf2["buyer_name"] = 0.10
    draft2 = _draft(_clean_fields("x", "y", "z"), confidence=0.95)
    draft2.field_confidence = conf2  # type: ignore[assignment]
    assert _batch_confidence_avg(draft2) == Decimal("0.95")
