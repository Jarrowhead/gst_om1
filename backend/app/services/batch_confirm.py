"""1-click Batch Confirm (task 11.2, PHASE11_13 rec 2).

Confirms in one call every pending draft of a registration + filing period
that is (a) validator-clean — no BLOCK-severity flag, the exact gate the
single confirm enforces — and (b) high-confidence: the confidence average of
its MANDATORY fields is >= 0.95, compared in `Decimal` so the 0.95 boundary
is exact (a field set averaging 0.9499 stays quarantined; a float running
average is never trusted — the no-float rule applied to the gate itself).

Drafts failing either test stay pending and are returned in the response's
`quarantine` rows with the reason — the endpoint is "batch confirm the clean
set", never "force through the rest".

Two pending populations are covered (PHASE11_13 rec 2 gap note; 11.1's module
doc leaves their wiring to this task):

  * EXTRACTION drafts (Document + ExtractionJob): the job flips
    NEEDS_REVIEW -> CONFIRMED and the Invoice carries `source_doc_id`.
  * BULK_IMPORT drafts (no document, no job): only the presence of their
    invoice_no in the (gstin, fp) ledger marks them confirmed — so they are
    picked as pending while that invoice_no is absent from the ledger, which
    makes batch confirm idempotent for them (their own ledger UNIQUE
    (gstin, fp, invoice_no) is the double-write backstop at write time).

Both populations are validated with the SAME `validate_extraction` rules the
single confirm runs (registration PAN + current ledger + the period attached)
— never a weaker bulk-path validator.

One append-only `BATCH_CONFIRM` audit row per successful (non-dry-run) call,
keyed by gstin (`entity_id=gstin`) with confirmed/quarantined counts — the
same pattern as 11.1's `BULK_IMPORT` audit row. A dry run writes nothing and
therefore writes no audit row: an append-only audit records a write, not a
look.

Lock rule (HARD RULE 4): `_ensure_open_period` (the shipped helper) raises
423 PERIOD_LOCKED on a FILED period before anything is evaluated or written.

Money is integer paise everywhere: the invoice writer is the shared
`review._persist_invoice`, so batch and single confirm write the ledger with
identical arithmetic.

`dry_run=true` runs the full evaluation and writes nothing — the review
queue's Batch Confirm button uses it to enable/disable itself on real counts.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from app.api.errors import ServiceError
from app.core.access import GstinAccess, audit
from app.db.models.core import GstAccount
from app.db.models.extraction import (
    DRAFT_SOURCE_BULK_IMPORT,
    Document,
    ExtractionJob,
    InvoiceDraft,
    JobStatus,
)
from app.db.models.gst import Invoice
from app.extraction.models import ExtractedDocument
from app.extraction.validate import MANDATORY_FIELDS, validate_extraction
from app.services.review import (
    _ensure_open_period,
    _persist_invoice,
    _run_validation,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# A draft confirms in bulk only when its mandatory-field confidence average
# (the same population the validator averages) reaches this floor.
BATCH_CONFIRM_THRESHOLD = Decimal("0.95")


def _confidence_decimal(value: Any) -> Decimal | None:
    """Coerce a persisted confidence to Decimal — never via a float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _batch_confidence_avg(draft: InvoiceDraft) -> Decimal | None:
    """Sum of the mandatory-field confidences / count, in exact Decimal.

    Mirrors the validator's confidence_avg population (MANDATORY_FIELDS only —
    name/address strings never dilute the gate), read from the draft's stored
    `field_confidence`, the authoritative per-confirm values. The
    ExtractionJob's Numeric(4,3) row does not even exist for BULK_IMPORT
    drafts, so the draft column is the single source for both populations.
    """
    conf = draft.field_confidence or {}
    values: list[Decimal] = []
    for key in MANDATORY_FIELDS:
        dec = _confidence_decimal(conf.get(key))
        if dec is not None:
            values.append(dec)
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _draft_invoice_no(draft: InvoiceDraft) -> str:
    fields = (draft.payload or {}).get("fields") or {}
    value = fields.get("invoice_no")
    return str(value).strip().upper() if value else ""


async def _ledger_numbers(session: AsyncSession, gstin: str, fp: str) -> set[str]:
    """Confirmed invoice_nos of the (gstin, fp) ledger, upper-normalised."""
    rows = (
        await session.execute(
            select(Invoice.invoice_no).where(
                Invoice.gstin == gstin, Invoice.fp == fp
            )
        )
    ).scalars()
    return {str(n).strip().upper() for n in rows if n}


def _import_document(draft: InvoiceDraft) -> ExtractedDocument:
    """Rebuild the ExtractedDocument for a BULK_IMPORT draft — the same shape
    bulk_import.py validated at ingest time."""
    payload = dict(draft.payload or {})
    return ExtractedDocument.model_validate(
        {
            "doc_id": None,
            "capture_source": payload.get("capture_source", "DIGITAL"),
            "fields": payload.get("fields", {}),
            "lines": payload.get("lines", []),
            "confidence": dict(draft.field_confidence or {}),
        }
    )


def _quarantine_detail(
    draft: InvoiceDraft,
    validation: Any,
    *,
    avg: Decimal | None,
    reason: str,
) -> dict[str, Any]:
    """One quarantine row (the reason the draft stays pending)."""
    kind = "BULK_IMPORT" if draft.source == DRAFT_SOURCE_BULK_IMPORT else "EXTRACTION"
    blocking = [f.rule for f in validation.flags if f.severity == "BLOCK"]
    return {
        "kind": kind,
        # document_id is filled in by the caller for extraction drafts.
        "document_id": None,
        "draft_id": str(draft.id),
        "invoice_no": _draft_invoice_no(draft) or None,
        "confidence_avg": None if avg is None else float(avg),
        "reason": reason,
        "rules": blocking if reason == "BLOCKING_FLAGS" else [],
    }


def _confidence_avg_float(draft: InvoiceDraft) -> float | None:
    """API representation of the draft's confidence average (display only)."""
    avg = _batch_confidence_avg(draft)
    return None if avg is None else float(avg)


async def batch_confirm(
    session: AsyncSession,
    access: GstinAccess,
    fp: str,
    user_id: uuid.UUID,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Confirm every clean, >= 0.95 pending draft; quarantine the rest.

    Returns `{gstin, fp, dry_run, confirmed, total_paise, confirmed_invoice_ids,
    quarantined, quarantine}`. `dry_run=true` evaluates everything and writes
    nothing (the button's enable/disable signal).
    """
    await _ensure_open_period(session, access.gstin, fp)
    account = await session.get(GstAccount, access.gstin)
    if account is None:
        raise ServiceError("GSTIN not found", 404, "GSTIN_NOT_FOUND")
    known = await _ledger_numbers(session, access.gstin, fp)

    candidates: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []

    # ---- population 1: document-backed extraction drafts in review ----------
    review_rows = (
        await session.execute(
            select(Document, ExtractionJob, InvoiceDraft)
            .join(ExtractionJob, ExtractionJob.document_id == Document.id)
            .join(InvoiceDraft, InvoiceDraft.extraction_job_id == ExtractionJob.id)
            .where(
                Document.gstin == access.gstin,
                Document.fp == fp,
                ExtractionJob.status == JobStatus.NEEDS_REVIEW,
            )
            .order_by(ExtractionJob.confidence_avg.asc().nullslast())
        )
    ).all()
    for doc, job, draft in review_rows:
        validation = await _run_validation(session, doc, draft)
        avg = _batch_confidence_avg(draft)
        if any(f.severity == "BLOCK" for f in validation.flags):
            detail = _quarantine_detail(
                draft, validation, avg=avg, reason="BLOCKING_FLAGS"
            )
            detail["document_id"] = str(doc.id)
            quarantine.append(detail)
            continue
        if avg is None or avg < BATCH_CONFIRM_THRESHOLD:
            detail = _quarantine_detail(
                draft, validation, avg=avg, reason="BELOW_THRESHOLD"
            )
            detail["document_id"] = str(doc.id)
            quarantine.append(detail)
            continue
        candidates.append(
            {
                "job": job,
                "draft": draft,
                "validation": validation,
                "doc_id": doc.id,
            }
        )

    # ---- population 2: spreadsheet drafts (no job, not yet in the ledger) ---
    import_drafts = (
        (
            await session.execute(
                select(InvoiceDraft)
                .where(
                    InvoiceDraft.gstin == access.gstin,
                    InvoiceDraft.fp == fp,
                    InvoiceDraft.extraction_job_id.is_(None),
                    InvoiceDraft.source == DRAFT_SOURCE_BULK_IMPORT,
                )
                .order_by(InvoiceDraft.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for draft in import_drafts:
        if _draft_invoice_no(draft) in known:
            continue  # already confirmed — batch confirm stays idempotent
        validation = validate_extraction(
            _import_document(draft),
            registration_pan=account.pan,
            ocr_text="",
            existing_invoice_nos=known,
            period_fp=fp,
        )
        avg = _batch_confidence_avg(draft)
        if any(f.severity == "BLOCK" for f in validation.flags):
            quarantine.append(
                _quarantine_detail(draft, validation, avg=avg, reason="BLOCKING_FLAGS")
            )
            continue
        if avg is None or avg < BATCH_CONFIRM_THRESHOLD:
            quarantine.append(
                _quarantine_detail(draft, validation, avg=avg, reason="BELOW_THRESHOLD")
            )
            continue
        candidates.append(
            {"job": None, "draft": draft, "validation": validation, "doc_id": None}
        )

    if dry_run:
        return {
            "gstin": access.gstin,
            "fp": fp,
            "dry_run": True,
            "confirmed": len(candidates),
            "total_paise": 0,
            "confirmed_invoice_ids": [],
            "quarantined": len(quarantine),
            "quarantine": quarantine,
        }

    confirmed_invoice_ids: list[str] = []
    total_paise = 0
    for item in candidates:
        try:
            invoice = await _persist_invoice(
                session,
                validation=item["validation"],
                gstin=access.gstin,
                fp=fp,
                source_doc_id=item["doc_id"],
                user_id=user_id,
            )
        except ServiceError as exc:
            # Per-item conflicts never abort the batch (the dominant case is
            # the ledger's duplicate 409: two pending drafts with the same
            # invoice_no — exactly the single-confirm's 409, per-item here).
            quarantine.append(
                {
                    "kind": "BULK_IMPORT" if item["job"] is None else "EXTRACTION",
                    "document_id": (
                        None if item["doc_id"] is None else str(item["doc_id"])
                    ),
                    "draft_id": str(item["draft"].id),
                    "invoice_no": _draft_invoice_no(item["draft"]) or None,
                    "confidence_avg": _confidence_avg_float(item["draft"]),
                    "reason": f"WRITE_{exc.code}",
                    "rules": [],
                }
            )
            continue
        if item["job"] is not None:
            item["job"].status = JobStatus.CONFIRMED
        confirmed_invoice_ids.append(str(invoice.id))
        total_paise += int(invoice.total_value_minor)

    await audit(
        session,
        action="BATCH_CONFIRM",
        entity="invoice_draft",
        entity_id=access.gstin,
        actor_user_id=user_id,
        gstin=access.gstin,
        payload_diff={
            "confirmed": len(confirmed_invoice_ids),
            "quarantined": len(quarantine),
        },
    )
    await session.commit()

    return {
        "gstin": access.gstin,
        "fp": fp,
        "dry_run": False,
        "confirmed": len(confirmed_invoice_ids),
        "total_paise": total_paise,
        "confirmed_invoice_ids": confirmed_invoice_ids,
        "quarantined": len(quarantine),
        "quarantine": quarantine,
    }
