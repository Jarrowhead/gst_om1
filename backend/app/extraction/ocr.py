"""OCR: PDF text layer + pytesseract image fallback.

The default provider is pytesseract because it is free, local, and offline.
The pipeline allows injecting a custom `ocr_provider` for tests or alternate
engines (e.g. easyocr, doctr).
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from typing import Any

import pymupdf
from PIL import Image

OCRProvider = Callable[[Image.Image], str]


class OCRError(Exception):
    """OCR stage failed."""


class EmptyOCRError(OCRError):
    """OCR returned no usable text."""


def _extract_pdf_text(data: bytes) -> str:
    """Extract embedded text from a native PDF."""
    doc: Any = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    parts: list[str] = []
    for page in doc:
        text = page.get_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _extract_pdf_images(data: bytes, dpi: int = 300) -> list[Image.Image]:
    """Render each PDF page to an image for OCR fallback."""
    doc: Any = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    images: list[Image.Image] = []
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)  # type: ignore[no-untyped-call]
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return images


def _image_from_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _default_tesseract_ocr(image: Image.Image) -> str:
    """Tesseract OCR with a standard config for printed invoices."""
    try:
        import pytesseract  # type: ignore[import-untyped]
        config = r"--oem 3 --psm 6"
        return str(pytesseract.image_to_string(image, config=config))
    except Exception as exc:
        # Never fabricate OCR text: a missing tesseract binary is a hard error,
        # surfaced as a FAILED job, so a real invoice is never replaced with a
        # synthetic one.
        raise OCRError(
            "tesseract binary is not available; install it or configure an OCR provider"
        ) from exc


def extract_text(
    data: bytes,
    mime_type: str,
    capture_source: str = "PDF_SCAN",
    *,
    ocr_provider: OCRProvider | None = None,
    dpi: int = 300,
) -> str:
    """Extract text from a document.

    DIGITAL: try PDF text layer first; fall back to OCR if the text layer is
    too thin (< 20 non-whitespace chars). Other branches always OCR rendered
    images after preprocessing.
    """
    provider = ocr_provider or _default_tesseract_ocr

    if mime_type == "application/pdf":
        text = _extract_pdf_text(data)
        if len("".join(text.split())) >= 20:
            return text
        # fallback to OCR on rendered pages
        images = _extract_pdf_images(data, dpi=dpi)
        parts = [provider(img) for img in images]
        combined = "\n\n".join(parts)
        if not combined.strip():
            raise EmptyOCRError("no text extracted from PDF")
        return combined

    if mime_type.startswith("image"):
        image = _image_from_bytes(data)
        text = provider(image)
        if not text.strip():
            raise EmptyOCRError("no text extracted from image")
        return text

    raise OCRError(f"unsupported MIME type for OCR: {mime_type}")


def extract_text_many(
    parts: Sequence[tuple[str, bytes]],
    capture_source: str = "PDF_SCAN",
    *,
    ocr_provider: OCRProvider | None = None,
    dpi: int = 300,
) -> str:
    """OCR multiple images/PDFs and concatenate text."""
    provider = ocr_provider or _default_tesseract_ocr
    combined: list[str] = []
    for mime, data in parts:
        combined.append(extract_text(data, mime, capture_source, ocr_provider=provider, dpi=dpi))
    return "\n\n".join(combined)
