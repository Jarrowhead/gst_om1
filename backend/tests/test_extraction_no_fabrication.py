"""Tests that extraction never fabricates data (no fake OCR / no fake LLM).

Regression tests for the three "no fabricated data" fixes:
  - ``GeminiFlashLLMClient`` raises when the API key is missing (no offline mock).
  - ``get_llm_client()`` falls back to the base LLM client, not a mock client.
  - ``_default_tesseract_ocr`` raises ``OCRError`` when tesseract is unavailable.
  - ``run_pipeline`` turns a base ``OCRError`` into a FAILED result, not a crash.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.config import Settings
from app.extraction import llm as llm_mod
from app.extraction import ocr as ocr_mod
from app.extraction.llm import LLMClient, LLMExtractionError
from app.extraction.pipeline import run_pipeline
from PIL import Image


def _image_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# LLM — no fabricated fallback
# --------------------------------------------------------------------------- #


async def test_gemini_client_raises_without_api_key() -> None:
    client = llm_mod.GeminiFlashLLMClient(Settings(gemini_api_key=""))
    with pytest.raises(LLMExtractionError):
        await client.extract("TAX INVOICE ...")


async def test_gemini_client_no_key_error_names_the_fix() -> None:
    client = llm_mod.GeminiFlashLLMClient(Settings(gemini_api_key=""))
    with pytest.raises(LLMExtractionError, match="GST_GEMINI_API_KEY"):
        await client.extract("text")


def test_get_llm_client_returns_base_client_without_key(monkeypatch: Any) -> None:
    monkeypatch.setattr(llm_mod, "get_settings", lambda: Settings(gemini_api_key=""))
    client = llm_mod.get_llm_client()
    assert isinstance(client, LLMClient)
    assert not isinstance(client, llm_mod.GeminiFlashLLMClient)


def test_get_llm_client_returns_gemini_with_key(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        llm_mod, "get_settings", lambda: Settings(gemini_api_key="sk-test")
    )
    client = llm_mod.get_llm_client()
    assert isinstance(client, llm_mod.GeminiFlashLLMClient)


# --------------------------------------------------------------------------- #
# OCR — no fabricated fallback
# --------------------------------------------------------------------------- #


async def test_default_tesseract_ocr_raises_when_tesseract_missing(
    monkeypatch: Any,
) -> None:
    import pytesseract

    def _boom(_image: Any, **_: Any) -> str:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", _boom)

    img = Image.new("RGB", (2, 2))
    with pytest.raises(ocr_mod.OCRError):
        ocr_mod._default_tesseract_ocr(img)


# --------------------------------------------------------------------------- #
# Pipeline — a base OCRError is a FAILED result, not a crash
# --------------------------------------------------------------------------- #


async def test_pipeline_base_ocerror_becomes_failed_result() -> None:
    settings = Settings()
    settings.extraction_blur_threshold_photo = 10.0
    settings.extraction_blur_threshold_whatsapp = 5.0

    img = Image.new("RGB", (400, 200), "white")
    data = _image_bytes(img)

    def _failing_ocr(_image: Image.Image) -> str:
        raise ocr_mod.OCRError("tesseract binary is not available")

    result = await run_pipeline(
        data,
        "image/png",
        "PDF_SCAN",
        ocr_provider=_failing_ocr,
        settings=settings,
    )
    assert result.error is not None
    assert "tesseract binary" in result.error
    assert result.document is None
    assert result.validation is None
