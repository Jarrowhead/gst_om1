"""HTTP-path tests for the LLM extraction clients (llm.py).

Covers LLMClient.extract (success / HTTP error / request error / parse error),
LLMClient.parse_document, and GeminiFlashLLMClient.extract (success / HTTP
error / parse error) using a mocked httpx.AsyncClient — no network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.config import Settings
from app.extraction import llm as llm_mod
from app.extraction.llm import (
    GeminiFlashLLMClient,
    LLMClient,
    LLMExtractionError,
)


class _Resp:
    def __init__(self, data: Any, status: int = 200) -> None:
        self._data = data
        self.status_code = status
        self._req = httpx.Request("POST", "http://mock")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"err {self.status_code}",
                request=self._req,
                response=httpx.Response(self.status_code),
            )

    def json(self) -> Any:
        return self._data


class _Client:
    def __init__(self, resp: Any, *, fail_post: bool = False) -> None:
        self._resp = resp
        self._fail_post = fail_post

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, url: str, json: Any = None) -> Any:
        if self._fail_post:
            raise httpx.RequestError("conn refused", request=httpx.Request("POST", url))
        return self._resp


def _patch_httpx(monkeypatch: Any, resp: Any, *, fail_post: bool = False) -> None:
    monkeypatch.setattr(
        llm_mod.httpx, "AsyncClient", lambda *a, **k: _Client(resp, fail_post=fail_post)
    )


def _choices(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 6},
    }


async def test_llm_client_extract_success(monkeypatch: Any) -> None:
    content = json.dumps({"fields": {"invoice_no": "INV-1"}, "lines": [], "confidence": {}})
    _patch_httpx(monkeypatch, _Resp(_choices(content)))
    out = await LLMClient(Settings()).extract("ocr text")
    assert out["fields"]["invoice_no"] == "INV-1"
    assert out["_llm_meta"]["tokens_in"] == 5


async def test_llm_client_extract_http_error(monkeypatch: Any) -> None:
    _patch_httpx(monkeypatch, _Resp({}, status=500))
    with pytest.raises(LLMExtractionError):
        await LLMClient(Settings()).extract("ocr")


async def test_llm_client_extract_request_error(monkeypatch: Any) -> None:
    _patch_httpx(monkeypatch, _Resp({}), fail_post=True)
    with pytest.raises(LLMExtractionError):
        await LLMClient(Settings()).extract("ocr")


async def test_llm_client_extract_parse_error(monkeypatch: Any) -> None:
    _patch_httpx(monkeypatch, _Resp(_choices("not json")))
    with pytest.raises(LLMExtractionError):
        await LLMClient(Settings()).extract("ocr")


def test_llm_client_parse_document_valid() -> None:
    raw = {
        "doc_id": "INV-1",
        "capture_source": "PDF_SCAN",
        "fields": {},
        "lines": [],
        "confidence": {},
    }
    doc = LLMClient(Settings()).parse_document(raw)
    assert doc.capture_source.value == "PDF_SCAN"


def test_llm_client_parse_document_invalid() -> None:
    with pytest.raises(LLMExtractionError):
        LLMClient(Settings()).parse_document({"fields": "not a dict"})


async def test_gemini_extract_success(monkeypatch: Any) -> None:
    content = json.dumps({"fields": {}, "lines": [], "confidence": {}})
    _patch_httpx(monkeypatch, _Resp({"candidates": [{"content": {"parts": [{"text": content}]}}]}))
    out = await GeminiFlashLLMClient(Settings(gemini_api_key="sk-x")).extract("ocr")
    assert out["_llm_meta"]["provider"] == "google-gemini-flash"


async def test_gemini_extract_http_error(monkeypatch: Any) -> None:
    _patch_httpx(monkeypatch, _Resp({}, status=403))
    with pytest.raises(LLMExtractionError):
        await GeminiFlashLLMClient(Settings(gemini_api_key="sk-x")).extract("ocr")


async def test_gemini_extract_parse_error(monkeypatch: Any) -> None:
    _patch_httpx(monkeypatch, _Resp({"candidates": []}))
    with pytest.raises(LLMExtractionError):
        await GeminiFlashLLMClient(Settings(gemini_api_key="sk-x")).extract("ocr")
