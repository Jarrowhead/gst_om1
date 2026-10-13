"""High-level extraction pipeline: preprocess → OCR → LLM → validate.

`run_pipeline` is the entrypoint used by both the worker and tests.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings, get_settings
from app.extraction.llm import LLMClient, LLMExtractionError
from app.extraction.models import ExtractionResult
from app.extraction.ocr import OCRError, OCRProvider, extract_text, extract_text_many
from app.extraction.preprocess import preprocess, preprocess_many
from app.extraction.validate import validate_extraction


class PipelineError(Exception):
    """Pipeline stage failure surfaced as FAILED result."""


async def run_pipeline(
    data: bytes,
    mime_type: str,
    capture_source: str,
    *,
    doc_id: str | None = None,
    ocr_provider: OCRProvider | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
    registration_pan: str | None = None,
    existing_invoice_nos: set[str] | None = None,
    period_fp: str | None = None,
) -> ExtractionResult:
    """Run full extraction pipeline on a single document payload.

    Args:
        data: document bytes after storage-layer merge (single PDF/image).
        mime_type: final MIME type of data.
        capture_source: PDF_SCAN | DIGITAL | PHOTO | WHATSAPP.
        doc_id: optional document id to include in output.
        ocr_provider: injectable OCR function (defaults to tesseract).
        llm_client: injectable LLM client (defaults to FreeLLMAPI client).
        settings: runtime settings.
        registration_pan: business PAN for supplier cross-check.
        existing_invoice_nos: set of invoice numbers already in period.
        period_fp: MMYYYY filing period, for advisory date-window rules.
    """
    settings = settings or get_settings()
    source = capture_source.upper()

    # 1. Preprocess
    report = preprocess(
        data,
        mime_type,
        source,
        dpi=settings.extraction_ocr_dpi,
        photo_blur_threshold=settings.extraction_blur_threshold_photo,
        whatsapp_blur_threshold=settings.extraction_blur_threshold_whatsapp,
    )

    if report.blur_rejected:
        return ExtractionResult(
            error="re-upload a clearer image",
            preproc_report=report,
        )

    # 2. OCR / Native PDF extraction
    try:
        from app.extraction.ocr import _extract_pdf_text
        if mime_type == "application/pdf":
            native_text = _extract_pdf_text(data)
            if len("".join(native_text.split())) >= 15:
                ocr_text = native_text
            else:
                ocr_text = extract_text(
                    report.output_data if report.output_data is not None else data,
                    report.output_mime_type if report.output_data is not None else mime_type,
                    source,
                    ocr_provider=ocr_provider,
                    dpi=settings.extraction_ocr_dpi,
                )
        else:
            ocr_text = extract_text(
                report.output_data if report.output_data is not None else data,
                report.output_mime_type if report.output_data is not None else mime_type,
                source,
                ocr_provider=ocr_provider,
                dpi=settings.extraction_ocr_dpi,
            )
    except OCRError as exc:
        return ExtractionResult(
            error=str(exc),
        )

    if not ocr_text.strip():
        return ExtractionResult(
            error="OCR returned empty text",
        )

    # 3. LLM extraction
    from app.extraction.llm import get_llm_client
    client = llm_client or get_llm_client()
    try:
        raw = await client.extract(ocr_text)
        raw["doc_id"] = doc_id
        raw["capture_source"] = source
        document = client.parse_document(raw)
    except LLMExtractionError as exc:
        return ExtractionResult(
            ocr_text=ocr_text,
            llm_model=client.model,
            error=f"LLM extraction failed: {exc}",
        )

    # 4. Validate
    validation = validate_extraction(
        document,
        registration_pan=registration_pan,
        ocr_text=ocr_text,
        existing_invoice_nos=existing_invoice_nos,
        period_fp=period_fp,
    )

    return ExtractionResult(
        document=document,
        validation=validation,
        ocr_text=ocr_text,
        llm_model=raw.get("_llm_meta", {}).get("model"),
        llm_tokens_in=raw.get("_llm_meta", {}).get("tokens_in"),
        llm_tokens_out=raw.get("_llm_meta", {}).get("tokens_out"),
        preproc_report=report,
        error=None,
    )


async def run_pipeline_many(
    parts: Sequence[tuple[str, bytes]],
    capture_source: str,
    *,
    doc_id: str | None = None,
    ocr_provider: OCRProvider | None = None,
    llm_client: LLMClient | None = None,
    settings: Settings | None = None,
    registration_pan: str | None = None,
    existing_invoice_nos: set[str] | None = None,
    period_fp: str | None = None,
) -> ExtractionResult:
    """Run pipeline for photo bursts: preprocess images + assemble, then OCR/LLM."""
    settings = settings or get_settings()
    source = capture_source.upper()

    report = preprocess_many(
        list(parts),
        source,
        dpi=settings.extraction_ocr_dpi,
        photo_blur_threshold=settings.extraction_blur_threshold_photo,
        whatsapp_blur_threshold=settings.extraction_blur_threshold_whatsapp,
    )

    if report.blur_rejected:
        return ExtractionResult(
            error="re-upload a clearer image",
            preproc_report=report,
        )

    # Carry preprocessed output into the result for the OCR call.
    if report.output_data is not None:
        ocr_data: Sequence[tuple[str, bytes]] = [(report.output_mime_type, report.output_data)]
    else:
        ocr_data = parts

    try:
        ocr_text = extract_text_many(
            ocr_data,
            source,
            ocr_provider=ocr_provider,
            dpi=settings.extraction_ocr_dpi,
        )
    except OCRError as exc:
        return ExtractionResult(
            error=str(exc),
        )

    from app.extraction.llm import get_llm_client
    client = llm_client or get_llm_client()
    try:
        raw = await client.extract(ocr_text)
        raw["doc_id"] = doc_id
        raw["capture_source"] = source
        document = client.parse_document(raw)
    except LLMExtractionError as exc:
        return ExtractionResult(
            ocr_text=ocr_text,
            llm_model=client.model,
            error=f"LLM extraction failed: {exc}",
        )

    validation = validate_extraction(
        document,
        registration_pan=registration_pan,
        ocr_text=ocr_text,
        existing_invoice_nos=existing_invoice_nos,
        period_fp=period_fp,
    )

    return ExtractionResult(
        document=document,
        validation=validation,
        ocr_text=ocr_text,
        llm_model=raw.get("_llm_meta", {}).get("model"),
        llm_tokens_in=raw.get("_llm_meta", {}).get("tokens_in"),
        llm_tokens_out=raw.get("_llm_meta", {}).get("tokens_out"),
        preproc_report=report,
        error=None,
    )
