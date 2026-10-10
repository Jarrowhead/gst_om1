"""Review + confirm flow for extracted drafts (API_SPECIFICATION.md §5).

Implements:
  - review queue
  - draft GET/PUT with validator re-run
  - confirm -> invoices/invoice_lines
  - reject -> FAILED

Money is integer paise everywhere; no float crosses boundaries.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.api.errors import ServiceError
from app.core.access import GstinAccess, audit
from app.db.models.core import GstAccount
from app.db.models.extraction import (
    Document,
    ExtractionJob,
    InvoiceDraft,
    JobStatus,
)
from app.db.models.gst import (
    FilingPeriod,
    FilingStatus,
    Invoice,
    InvoiceDirection,
    InvoiceLine,
    InvoiceStatus,
    InvType,
    SupplyType,
)
from app.extraction.models import ExtractedDocument, ExtractedLine, FieldConfidence
from app.extraction.validate import validate_extraction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MANDATORY_FIELDS: tuple[str, ...] = (
    "supplier_gstin",
    "buyer_gstin",
    "invoice_no",
    "invoice_date",
    "place_of_supply",
    "is_inter_state",
    "rchrg",
    "inv_typ",
    "taxable_value_paise",
    "total_value_paise",
    "cgst_paise",
    "sgst_paise",
    "igst_paise",
    "cess_paise",
)


def _normalise_gstin(value: str | None) -> str | None:
    return value.strip().upper() if value else None


async def _load_doc_with_draft(
    session: AsyncSession, doc_id: uuid.UUID, access: GstinAccess
) -> tuple[Document, ExtractionJob, InvoiceDraft]:
    """Fetch doc + job + draft, enforcing tenant isolation."""
    row = (
        await session.execute(
            select(Document, ExtractionJob, InvoiceDraft)
            .join(ExtractionJob, ExtractionJob.document_id == Document.id)
            .outerjoin(InvoiceDraft, InvoiceDraft.extraction_job_id == ExtractionJob.id)
            .where(
                Document.id == doc_id,
                Document.gstin == access.gstin,
            )
        )
    ).one_or_none()
    if row is None or row[0] is None or row[1] is None:
        raise ServiceError("document not found", 404, "DOCUMENT_NOT_FOUND")
    if row[2] is None:
        raise ServiceError("draft not found for document", 404, "DRAFT_NOT_FOUND")
    return row[0], row[1], row[2]


async def _ensure_open_period(
    session: AsyncSession, gstin: str, fp: str
) -> FilingPeriod:
    """Return the filing period, raising 423 if it is already FILED."""
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        raise ServiceError("filing period not found", 404, "PERIOD_NOT_FOUND")
    if period.status == FilingStatus.FILED:
        raise ServiceError("period is locked after filing", 423, "PERIOD_LOCKED")
    return period


async def _validation_context(
    session: AsyncSession,
    doc: Document,
    draft: InvoiceDraft,
) -> tuple[GstAccount | None, str, set[str]]:
    """Load the GSTIN's PAN + existing invoice numbers for duplicate checks."""
    account = await session.get(GstAccount, doc.gstin)
    if account is None:
        raise ServiceError("GSTIN not found", 404, "GSTIN_NOT_FOUND")

    existing_rows = await session.execute(
        select(Invoice.invoice_no).where(
            Invoice.gstin == doc.gstin,
            Invoice.fp == doc.fp,
        )
    )
    existing = {str(n) for n in existing_rows.scalars().all() if n}
    return account, account.gstin, existing


def _extracted_document_from_draft(
    doc: Document,
    draft: InvoiceDraft,
) -> ExtractedDocument:
    """Rebuild the canonical extracted-document model from the persisted draft."""
    payload = draft.payload or {}
    # The capture source is authoritative from the immutable Document row;
    # never let a stale/stored payload override it.
    payload["capture_source"] = doc.capture_source.value
    if "lines" not in payload:
        payload["lines"] = []
    doc_model = ExtractedDocument.model_validate(payload)
    doc_model.confidence = FieldConfidence.model_validate(draft.field_confidence or {})
    return doc_model


async def _run_validation(
    session: AsyncSession,
    doc: Document,
    draft: InvoiceDraft,
) -> Any:
    """Re-run EXTRACTION_SPEC §6 validator against the current draft."""
    account, _gstin, existing = await _validation_context(session, doc, draft)
    doc_model = _extracted_document_from_draft(doc, draft)
    return validate_extraction(
        doc_model,
        registration_pan=account.pan if account else None,
        ocr_text="",
        existing_invoice_nos=existing,
        period_fp=doc.fp,
    )


def _direction_for(fields: Any, reg_gstin: str) -> InvoiceDirection:
    """Infer SALES/PURCHASE by matching the registration GSTIN to a party."""
    supplier = _normalise_gstin(fields.supplier_gstin)
    buyer = _normalise_gstin(fields.buyer_gstin)
    me = reg_gstin.strip().upper()
    if supplier == me:
        return InvoiceDirection.SALES
    if buyer == me:
        return InvoiceDirection.PURCHASE
    # B2C edge: supplier is the registration, buyer absent.
    if supplier is not None:
        return InvoiceDirection.PURCHASE
    raise ServiceError(
        "invoice does not reference the registration GSTIN", 422, "VALIDATION_ERROR"
    )


def _compute_line_taxes(
    line: ExtractedLine | dict[str, Any], is_inter: bool
) -> tuple[int, int, int]:
    """Return (cgst, sgst, igst) in paise for one line, HALF_UP."""
    if isinstance(line, ExtractedLine):
        taxable = line.taxable_value_paise or 0
        rate = Decimal(str(line.gst_rate or 0))
    else:
        taxable = int(line.get("taxable_value_paise") or 0)
        rate = Decimal(str(line.get("gst_rate") or 0))
    tax = int(
        (Decimal(taxable) * rate / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if is_inter:
        return 0, 0, tax
    half = int((Decimal(tax) / Decimal(2)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return half, tax - half, 0


def _draft_envelope(
    doc: Document,
    job: ExtractionJob,
    draft: InvoiceDraft,
    validation: Any,
) -> dict[str, Any]:
    """API shape for GET /documents/{id}/draft and PUT /documents/{id}/draft."""
    return {
        "document_id": str(doc.id),
        "job_id": str(job.id),
        "job_status": job.status.value,
        "fp": doc.fp,
        "capture_source": doc.capture_source.value,
        "fields": draft.payload.get("fields", {}),
        "lines": draft.payload.get("lines", []),
        "confidence": draft.field_confidence or {},
        "flags": [f.model_dump() for f in validation.flags],
        "auto_confirm": validation.auto_confirm.value,
        "confidence_avg": validation.confidence_avg,
        "derived": validation.derived,
    }


async def get_review_queue(
    session: AsyncSession, access: GstinAccess, fp: str
) -> list[dict[str, Any]]:
    """NEEDS_REVIEW documents for the period, ordered by confidence ascending."""
    rows = (
        await session.execute(
            select(Document, ExtractionJob)
            .join(ExtractionJob, ExtractionJob.document_id == Document.id)
            .where(
                Document.gstin == access.gstin,
                Document.fp == fp,
                ExtractionJob.status == JobStatus.NEEDS_REVIEW,
            )
            .order_by(ExtractionJob.confidence_avg.asc().nullslast(), Document.uploaded_at.asc())
        )
    ).all()

    result: list[dict[str, Any]] = []
    for doc, job in rows:
        avg = (
            float(job.confidence_avg)
            if job.confidence_avg is not None
            else None
        )
        result.append(
            {
                "document_id": str(doc.id),
                "job_id": str(job.id),
                "job_status": job.status.value,
                "confidence_avg": avg,
                "uploaded_at": (
                    doc.uploaded_at.isoformat() if doc.uploaded_at else None
                ),
                "capture_source": doc.capture_source.value,
                "doc_type": doc.doc_type,
            }
        )
    return result


async def get_draft(
    session: AsyncSession, doc_id: uuid.UUID, access: GstinAccess
) -> dict[str, Any]:
    """GET /documents/{docId}/draft — fields + confidence + current flags."""
    doc, job, draft = await _load_doc_with_draft(session, doc_id, access)
    validation = await _run_validation(session, doc, draft)
    return _draft_envelope(doc, job, draft, validation)


async def update_draft(
    session: AsyncSession,
    doc_id: uuid.UUID,
    access: GstinAccess,
    user_id: uuid.UUID,
    fields_patch: dict[str, Any],
) -> dict[str, Any]:
    """PUT /documents/{docId}/draft — apply edits, re-run validator, save."""
    doc, job, draft = await _load_doc_with_draft(session, doc_id, access)

    payload = dict(draft.payload or {})
    fields: dict[str, Any] = dict(payload.get("fields", {}))
    confidence: dict[str, Any] = dict(draft.field_confidence or {})

    edited = False
    for key, value in fields_patch.items():
        if key in _MANDATORY_FIELDS or key in {
            "supplier_name",
            "supplier_address",
            "buyer_name",
            "buyer_address",
        }:
            fields[key] = value
            # Human-edited fields are trusted: confidence becomes 1.0.
            confidence[key] = 1.0
            edited = True

    if not edited:
        raise ServiceError("no editable fields supplied", 422, "VALIDATION_ERROR")

    payload["fields"] = fields
    draft.payload = payload
    draft.field_confidence = confidence

    # Once a human touches the draft it is no longer a pure machine auto-confirm.
    if job.status == JobStatus.EXTRACTED:
        job.status = JobStatus.NEEDS_REVIEW

    validation = await _run_validation(session, doc, draft)

    await audit(
        session,
        action="DRAFT_UPDATED",
        entity="invoice_draft",
        entity_id=str(draft.id),
        actor_user_id=user_id,
        gstin=access.gstin,
        payload_diff={"edited_fields": list(fields_patch.keys())},
    )
    await session.commit()
    await session.refresh(job)
    await session.refresh(draft)
    return _draft_envelope(doc, job, draft, validation)


async def _persist_invoice(
    session: AsyncSession,
    *,
    validation: Any,
    gstin: str,
    fp: str,
    source_doc_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> Invoice:
    """Create the Invoice + InvoiceLines from a validator-passing draft.

    Shared by per-draft confirm (review flow) and batch confirm
    (services/batch_confirm.py, task 11.2) so both write the ledger with the
    IDENTICAL direction/total/line-tax arithmetic. Raises 409
    DUPLICATE_INVOICE on a colliding invoice_no — the caller owns job status,
    audit rows and the commit.
    """
    fields = validation.document.fields
    direction = _direction_for(fields, gstin)

    supply_type_str = validation.derived.get("supply_type")
    if not supply_type_str:
        raise ServiceError(
            "unable to derive supply type", 422, "VALIDATION_ERROR"
        )
    is_inter = supply_type_str == "INTER"

    invoice_no = (fields.invoice_no or "").strip()
    if not invoice_no:
        raise ServiceError("invoice_no is required", 422, "VALIDATION_ERROR")

    duplicate = await session.execute(
        select(Invoice.id).where(
            Invoice.gstin == gstin,
            Invoice.fp == fp,
            Invoice.invoice_no == invoice_no,
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ServiceError(
            "duplicate invoice_no in this registration/filing period",
            409,
            "DUPLICATE_INVOICE",
        )

    invoice = Invoice(
        gstin=gstin,
        fp=fp,
        direction=direction,
        supplier_gstin=_normalise_gstin(fields.supplier_gstin),
        buyer_gstin=_normalise_gstin(fields.buyer_gstin),
        invoice_no=invoice_no,
        invoice_date=date.fromisoformat(str(fields.invoice_date)),
        place_of_supply=str(fields.place_of_supply or ""),
        supply_type=SupplyType(supply_type_str),
        rchrg=bool(fields.rchrg),
        inv_typ=InvType(fields.inv_typ or "R"),
        total_value_minor=int(fields.total_value_paise or 0),
        source_doc_id=source_doc_id,
        status=InvoiceStatus.CONFIRMED,
        confirmed_by=user_id,
        confirmed_at=datetime.now(tz=datetime.now().astimezone().tzinfo),
    )
    session.add(invoice)
    await session.flush()

    line_payloads = validation.document.lines
    for idx, raw_line in enumerate(line_payloads):
        line_no = idx + 1
        line_dict = raw_line.model_dump() if isinstance(raw_line, ExtractedLine) else dict(raw_line)
        cgst, sgst, igst = _compute_line_taxes(line_dict, is_inter)
        taxable = int(line_dict.get("taxable_value_paise") or 0)
        rate = Decimal(str(line_dict.get("gst_rate") or 0))
        qty = line_dict.get("qty")
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_no=line_no,
                description=line_dict.get("desc"),
                hsn_sac=line_dict.get("hsn_sac"),
                uqc=line_dict.get("uqc"),
                qty=Decimal(str(qty)) if qty is not None else None,
                unit_price_minor=(
                    int(up)
                    if (up := line_dict.get("unit_price_paise")) is not None
                    else None
                ),
                gst_rate=rate,
                taxable_value_minor=taxable,
                cgst_minor=cgst,
                sgst_minor=sgst,
                igst_minor=igst,
                cess_minor=int(line_dict.get("cess_paise") or 0),
            )
        )
    return invoice


async def confirm_draft(
    session: AsyncSession,
    doc_id: uuid.UUID,
    access: GstinAccess,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """POST /documents/{docId}/confirm — write invoices + invoice_lines if clean."""
    doc, job, draft = await _load_doc_with_draft(session, doc_id, access)

    if job.status == JobStatus.CONFIRMED:
        raise ServiceError("document already confirmed", 409, "ALREADY_CONFIRMED")
    if job.status == JobStatus.FAILED:
        raise ServiceError("cannot confirm a rejected document", 422, "VALIDATION_ERROR")

    await _ensure_open_period(session, doc.gstin, doc.fp)
    validation = await _run_validation(session, doc, draft)

    blocking_flags = [f for f in validation.flags if f.severity == "BLOCK"]
    if blocking_flags:
        messages = "; ".join(
            f"{f.rule}({f.severity}): {f.message}" for f in blocking_flags
        )
        raise ServiceError(
            f"draft has unresolved blocking flags: {messages}",
            422,
            "VALIDATION_DIRTY",
        )

    invoice = await _persist_invoice(
        session,
        validation=validation,
        gstin=doc.gstin,
        fp=doc.fp,
        source_doc_id=doc.id,
        user_id=user_id,
    )

    job.status = JobStatus.CONFIRMED

    await audit(
        session,
        action="DOCUMENT_CONFIRMED",
        entity="invoice",
        entity_id=str(invoice.id),
        actor_user_id=user_id,
        gstin=access.gstin,
        payload_diff={"source_doc_id": str(doc.id), "invoice_no": invoice.invoice_no},
    )
    await session.commit()
    await session.refresh(invoice)
    return {"invoice_id": str(invoice.id)}


async def reject_document(
    session: AsyncSession,
    doc_id: uuid.UUID,
    access: GstinAccess,
    user_id: uuid.UUID,
    reason: str | None = None,
) -> dict[str, Any]:
    """POST /documents/{docId}/reject — mark extraction job FAILED."""
    doc, job, draft = await _load_doc_with_draft(session, doc_id, access)
    if job.status == JobStatus.CONFIRMED:
        raise ServiceError("confirmed documents cannot be rejected", 409, "ALREADY_CONFIRMED")
    job.status = JobStatus.FAILED
    job.error = reason or "rejected by user"

    await audit(
        session,
        action="DOCUMENT_REJECTED",
        entity="document",
        entity_id=str(doc.id),
        actor_user_id=user_id,
        gstin=access.gstin,
    )
    await session.commit()
    await session.refresh(job)
    return {"document_id": str(doc.id), "status": job.status.value}
