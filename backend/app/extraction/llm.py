"""LLM extraction client.

Pinned model via FreeLLMAPI (NOT flm/auto), temperature 0, strict JSON schema.
Follows EXTRACTION_SPEC.md §9 prompt contract.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.extraction.models import ExtractedDocument

_LOGGER = logging.getLogger(__name__)

LLM_SYSTEM_PROMPT = (  # noqa: E501
    "You are an Indian GST invoice extractor. "
    "You receive OCR text from a bill and return strict JSON."
)

SYSTEM_RULES = """\
Rules:
1. Extract only facts visible on the page. Do not invent GSTINs, PANs, invoice
   numbers, dates, or amounts.
2. Missing value => null and confidence 0.0. Do not fill gaps from context.
3. Money must be in integer PAUSE (100 paise = 1 rupee). 1500000 paise = INR
   15,000.
4. Dates as "YYYY-MM-DD".
5. GSTIN must match format ^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$. If
   invalid or absent, null.
6. inv_typ is one of: R, SEWP, SEWOP, DE. rchrg is boolean. is_inter_state is
   boolean.
7. Do not compute tax totals; extract what is printed. Lines are secondary.

Return ONLY a JSON object matching this schema:
{
  "doc_id": string or null,
  "capture_source": "PDF_SCAN" | "DIGITAL" | "PHOTO" | "WHATSAPP",
  "fields": {
    "supplier_gstin": "29AABCU9603R1ZM" or null,
    "buyer_gstin": null,
    "invoice_no": "INV-2025-0042" or null,
    "invoice_date": "2025-05-04" or null,
    "place_of_supply": "29" or null,
    "is_inter_state": false or null,
    "rchrg": false or null,
    "inv_typ": "R" or null,
    "taxable_value_paise": 1500000 or null,
    "total_value_paise": 1770000 or null,
    "cgst_paise": 135000 or null,
    "sgst_paise": 135000 or null,
    "igst_paise": 0 or null,
    "cess_paise": 0 or null
  },
  "lines": [
    {"hsn_sac": "73269099", "desc": "Steel bracket", "uqc": "NOS", "qty": 100,
     "unit_price_paise": 15000, "gst_rate": 18.0,
     "taxable_value_paise": 1500000, "cgst_paise": 135000, "sgst_paise": 135000}
  ],
  "confidence": {
    "supplier_gstin": 0.98, "invoice_no": 0.95, "total_value_paise": 0.91
  }
}
"""

LLM_SYSTEM_PROMPT = LLM_SYSTEM_PROMPT + "\n\n" + SYSTEM_RULES

FEW_SHOT_1_USER = (
    "OCR text:\nM/s ABC Enterprises\nGSTIN: 27AABCU9603R1ZM\n"
    "Invoice No: INV-2025-0042\nDate: 04/05/2025\nBuyer: Walk-in Customer\n"
    "Taxable: 15,000.00\nCGST 9%: 1,350.00\nSGST 9%: 1,350.00\n"
    "Total: 17,700.00\nPlace of Supply: Maharashtra (27)"
)
FEW_SHOT_2_USER = (
    "OCR text:\nXYZ Supplies, Karnataka 29GGGGG1314R9Z6\n"
    "Inv # PI-88 dt 12-08-2025\nBuyer: 07AAAAA0000A1Z5 (Delhi)\n"
    "Taxable 20,000\nIGST 18% 3,600\nBill total 23,600"
)

FEW_SHOT = [
    {"role": "user", "content": FEW_SHOT_1_USER},
    {
        "role": "assistant",
        "content": json.dumps({
            "doc_id": "INV-2025-0042",
            "capture_source": "PDF_SCAN",
            "fields": {
                "supplier_gstin": "27AABCU9603R1ZM",
                "buyer_gstin": None,
                "invoice_no": "INV-2025-0042",
                "invoice_date": "2025-05-04",
                "place_of_supply": "27",
                "is_inter_state": False,
                "rchrg": False,
                "inv_typ": "R",
                "taxable_value_paise": 1500000,
                "total_value_paise": 1770000,
                "cgst_paise": 135000,
                "sgst_paise": 135000,
                "igst_paise": 0,
                "cess_paise": 0,
            },
            "lines": [],
            "confidence": {
                "supplier_gstin": 0.98,
                "invoice_no": 0.98,
                "invoice_date": 0.95,
                "place_of_supply": 0.95,
                "taxable_value_paise": 0.96,
                "total_value_paise": 0.96,
                "cgst_paise": 0.95,
                "sgst_paise": 0.95,
                "igst_paise": 0.99,
                "cess_paise": 0.99,
            },
        }),
    },
    {"role": "user", "content": FEW_SHOT_2_USER},
    {
        "role": "assistant",
        "content": json.dumps({
            "doc_id": "PI-88",
            "capture_source": "PHOTO",
            "fields": {
                "supplier_gstin": "29GGGGG1314R9Z6",
                "buyer_gstin": "07AAAAA0000A1Z5",
                "invoice_no": "PI-88",
                "invoice_date": "2025-08-12",
                "place_of_supply": "07",
                "is_inter_state": True,
                "rchrg": False,
                "inv_typ": "R",
                "taxable_value_paise": 2000000,
                "total_value_paise": 2360000,
                "cgst_paise": 0,
                "sgst_paise": 0,
                "igst_paise": 360000,
                "cess_paise": 0,
            },
            "lines": [],
            "confidence": {
                "supplier_gstin": 0.95,
                "buyer_gstin": 0.92,
                "invoice_no": 0.94,
                "invoice_date": 0.93,
                "place_of_supply": 0.90,
                "is_inter_state": 0.85,
                "taxable_value_paise": 0.92,
                "total_value_paise": 0.92,
                "igst_paise": 0.91,
            },
        }),
    },
]


class LLMExtractionError(Exception):
    """LLM call or parse failed."""


class LLMClient:
    """Thin OpenAI-compatible client pointing at FreeLLMAPI :3001."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.extraction_llm_base_url.rstrip("/")
        self.model = self.settings.extraction_llm_model
        self.timeout = self.settings.extraction_llm_timeout_seconds

    async def extract(self, ocr_text: str) -> dict[str, Any]:
        """Call the pinned model with temperature 0 and parse JSON output."""
        messages = [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            *FEW_SHOT,
            {"role": "user", "content": f"OCR text:\n{ocr_text}"},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": self.settings.extraction_llm_max_tokens_per_doc,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMExtractionError(f"LLM HTTP {exc.response.status_code}") from exc
            except httpx.RequestError as exc:
                raise LLMExtractionError(f"LLM request failed: {exc}") from exc

        data = response.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            parsed: dict[str, Any] = json.loads(content)
            parsed["_llm_meta"] = {
                "model": self.model,
                "tokens_in": usage.get("prompt_tokens"),
                "tokens_out": usage.get("completion_tokens"),
            }
            return parsed
        except (KeyError, json.JSONDecodeError) as exc:
            raise LLMExtractionError(f"LLM output not parseable: {exc}") from exc

    def parse_document(self, raw: dict[str, Any]) -> ExtractedDocument:
        """Validate the LLM JSON against the canonical schema."""
        try:
            return ExtractedDocument.model_validate(raw)
        except ValidationError as exc:
            raise LLMExtractionError(f"LLM JSON schema invalid: {exc}") from exc




class GeminiFlashLLMClient(LLMClient):
    """Google Gemini Flash LLM extraction client with free-tier structured JSON.

    Raises ``LLMExtractionError`` when the API key is missing — extraction is
    never fabricated offline.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.api_key = self.settings.gemini_api_key
        self.gemini_model = getattr(self.settings, "gemini_model", "gemini-2.0-flash")

    async def extract(self, ocr_text: str) -> dict[str, Any]:
        if not self.api_key:
            raise LLMExtractionError(
                "Gemini API key not configured — set GST_GEMINI_API_KEY"
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.api_key}"
        prompt_content = f"{LLM_SYSTEM_PROMPT}\n\nExtract strict JSON from OCR text:\n{ocr_text}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMExtractionError(
                    f"Gemini HTTP {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise LLMExtractionError(f"Gemini request failed: {exc}") from exc

        data = response.json()
        try:
            cand = data["candidates"][0]
            part = cand["content"]["parts"][0]
            raw_text = part["text"]
            parsed = json.loads(raw_text)
            parsed["_llm_meta"] = {
                "model": self.gemini_model,
                "provider": "google-gemini-flash",
            }
            return parsed
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LLMExtractionError(f"Gemini output parsing failed: {exc}") from exc


def get_llm_client() -> LLMClient:
    settings = get_settings()
    if getattr(settings, "gemini_api_key", None):
        return GeminiFlashLLMClient(settings)
    return LLMClient(settings)
