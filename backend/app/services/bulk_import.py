"""Bulk Excel/CSV import + column auto-mapper (task 11.1, rec 1).

Ingests a Tally/Busy/Zoho spreadsheet of invoices into
`extraction.invoice_drafts` — the same draft table the OCR pipeline writes to,
with the same payload shape and the same EXTRACTION_SPEC §6 validator applied,
so an imported row carries the same flags, derived fields and duplicate
detection as a scanned one.

Downstream reachability (honest status): the shipped review/confirm path is
*document*-scoped (`/documents/{docId}/{draft,confirm}` → Document →
ExtractionJob → InvoiceDraft), and a spreadsheet row has no document and no
extraction job — so an imported draft is not yet listed by the review queue and
not yet confirmable through it. This module only ingests; wiring imported drafts
into review/batch-confirm is the next task's surface (PHASE11_13 rec 2, 11.2).

Why `extraction_job_id` is NULL for these rows: a spreadsheet is *already*
structured data. There is no document to preprocess, no OCR text and no LLM
call, so there is no extraction job to hang the draft off. `source =
'BULK_IMPORT'` records that provenance (see db/models/extraction.py).

Contract points this module must hold (AI_BUILD_PLAYBOOK hard rules 1–2,
EXTRACTION_SPEC §3/§6):

* **Money is integer paise.** Every rupee value in the file crosses exactly one
  ``Decimal`` boundary — never a float — with ``ROUND_HALF_UP`` to the paise.
  (openpyxl hands numeric cells over as ``float``; it is converted to
  ``Decimal`` immediately in ``_to_paise`` and never used as a float.)
* **GSTINs are never trusted from the file.** Each party GSTIN is re-validated
  with the mod-36 checksum in ``app.core.gstin``; a bad one is a per-row error.
* **A bad row never aborts the file.** Every row is reported individually
  (row number, reasons, flags) and the valid rows still land.
* **The GST contract owns the arithmetic.** Each row is put through the exact
  EXTRACTION_SPEC §6 validator (``validate_extraction``) that gates OCR drafts,
  so an imported draft carries the same flags, the same derived supply type and
  the same duplicate detection as a scanned one.

Derived (never taken from the file, per EXTRACTION_SPEC §3):
  * ``fp`` — derived from ``invoice_date``; a row whose date does not belong to
    the period being imported into is rejected (that is the "valid fp" check).
  * ``place_of_supply`` — when the file has no POS column, the *recipient's*
    state code (buyer GSTIN if present, else supplier GSTIN), i.e. GST's
    default POS rule for this determination.
  * ``is_inter_state`` — supplier state vs place of supply.
  * per-line CGST/SGST/IGST — recomputed from the rate by the validator.

Ambiguity is never guessed: a column header that cannot be matched to exactly
one canonical field is left unmapped and reported, and the import refuses to
run while a required column is missing (``required_missing``). The caller may
always send an explicit ``mapping`` override.

Safety bounds (documented policy, not GST law): 10 MB/file, 5000 data rows.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.api.errors import ServiceError
from app.core.access import GstinAccess, audit
from app.core.gstin import gstin_state_code, validate_gstin
from app.db.models.core import GstAccount
from app.db.models.extraction import (
    DRAFT_SOURCE_BULK_IMPORT,
    InvoiceDraft,
)
from app.db.models.gst import FilingPeriod, FilingStatus, Invoice
from app.extraction.models import (
    CaptureSource,
    ExtractedDocument,
    ExtractedFields,
    ExtractedLine,
    FieldConfidence,
)
from app.extraction.validate import (
    MANDATORY_FIELDS,
    VALID_STATE_CODES,
    validate_extraction,
)
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MAX_BULK_BYTES = 10 * 1024 * 1024
MAX_BULK_ROWS = 5000
PREVIEW_SAMPLE_ROWS = 5

# A spreadsheet row is a digital document (EXTRACTION_SPEC §2 DIGITAL is
# literally "native PDF (Tally/Busy/Zoho export)").
BULK_CAPTURE_SOURCE = CaptureSource.DIGITAL
DEFAULT_INV_TYP = "R"

# ---------------------------------------------------------------------------
# Column auto-mapper
# ---------------------------------------------------------------------------

# canonical field -> accepted header spellings (normalised, alias order wins).
# Deliberately absent: a bare "gstin" (that column could be either party — the
# mapper must not guess), a bare "rate" (GST rate vs item rate), a bare "value".
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_no": (
        "invoice no",
        "invoice number",
        "invoice no.",
        "inv no",
        "inv no.",
        "invno",
        "invoice",
        "bill no",
        "bill number",
        "voucher no",
        "voucher number",
        "document no",
        "document number",
        "doc no",
        "invoice id",
    ),
    "invoice_date": (
        "invoice date",
        "inv date",
        "bill date",
        "voucher date",
        "document date",
        "doc date",
        "date",
    ),
    "supplier_gstin": (
        "supplier gstin",
        "supplier gstin/uin",
        "gstin of supplier",
        "seller gstin",
        "vendor gstin",
        "party gstin",
        "suppliergstin",
        "gstin no of supplier",
    ),
    "buyer_gstin": (
        "buyer gstin",
        "buyer gstin/uin",
        "recipient gstin",
        "gstin of recipient",
        "customer gstin",
        "client gstin",
        "buyergstin",
        "gstin no of recipient",
    ),
    "taxable_value": (
        "taxable value",
        "taxable amount",
        "taxable val",
        "assessable value",
        "basic amount",
        "net amount",
        "taxablevalue",
    ),
    "total_value": (
        "invoice value",
        "invoice total",
        "total value",
        "total amount",
        "grand total",
        "bill amount",
        "invoice amount",
    ),
    "gst_rate": (
        "gst rate",
        "tax rate",
        "gst rate %",
        "tax rate %",
        "rate of tax",
        "gst %",
        "tax %",
        "gst percent",
    ),
    "cgst": ("cgst", "cgst amount", "central tax", "central gst", "cgst value"),
    "sgst": (
        "sgst",
        "sgst amount",
        "state tax",
        "state gst",
        "utgst",
        "sgst value",
    ),
    "igst": ("igst", "igst amount", "integrated tax", "integrated gst", "igst value"),
    "cess": ("cess", "cess amount", "compensation cess"),
    "hsn_sac": (
        "hsn",
        "hsn sac",
        "hsn/sac",
        "hsn code",
        "sac",
        "sac code",
        "hsn sac code",
    ),
    "place_of_supply": (
        "place of supply",
        "place of supply state",
        "pos",
        "pos state code",
    ),
    "inv_typ": ("invoice type", "inv type", "document type", "doc type"),
    "rchrg": ("reverse charge", "reverse charge applicable", "rcm", "rchrg"),
    "qty": ("quantity", "qty", "quantity nos"),
    "unit_price": ("unit price", "item rate", "rate per item", "price per item"),
    "description": (
        "description",
        "item description",
        "goods service description",
        "particulars",
        "narration",
        "item name",
        "product name",
    ),
    "supplier_name": ("supplier name", "seller name", "vendor name", "party name"),
    "buyer_name": ("buyer name", "recipient name", "customer name", "client name"),
}

# Required to ingest a row: at least ONE of the members of each group must be
# mapped. Order defines the reported order.
REQUIRED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("invoice_no",),
    ("invoice_date",),
    ("taxable_value",),
    ("supplier_gstin", "buyer_gstin"),
    ("gst_rate", "cgst", "sgst", "igst"),
)

_NON_HEADER = re.compile(r"[^a-z0-9%/#]+")
_WS = re.compile(r"\s+")


def _normalise_header(value: object) -> str:
    """lower-case, punctuation-folded, whitespace-collapsed header text."""
    text = str(value or "").strip().lower()
    text = _NON_HEADER.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _alias_index() -> dict[str, frozenset[str]]:
    return {
        canonical: frozenset(_normalise_header(alias) for alias in aliases)
        for canonical, aliases in COLUMN_ALIASES.items()
    }


def detect_columns(headers: list[str]) -> dict[str, str | None]:
    """Auto-map canonical fields to headers in the file.

    Pass 1: exact normalised alias match. Pass 2: token-subset match, accepted
    only when exactly one canonical field is a candidate (otherwise the header
    stays unmapped — a guess in the GST domain is a defect).
    Returns ``{canonical: header | None}`` for every known canonical field.
    """
    index = _alias_index()
    normalised: dict[str, str] = {
        header: _normalise_header(header) for header in headers if str(header).strip()
    }
    mapping: dict[str, str | None] = {canonical: None for canonical in COLUMN_ALIASES}
    taken: set[str] = set()

    for canonical, aliases in index.items():
        for header, norm in normalised.items():
            if header in taken or not norm:
                continue
            if norm in aliases:
                mapping[canonical] = header
                taken.add(header)
                break

    for header, norm in normalised.items():
        if header in taken or not norm:
            continue
        tokens = set(norm.split())
        candidates: list[str] = []
        for canonical, aliases in index.items():
            if mapping[canonical] is not None:
                continue
            for alias in aliases:
                alias_tokens = set(alias.split())
                if alias_tokens and (
                    alias_tokens <= tokens or tokens <= alias_tokens
                ):
                    candidates.append(canonical)
                    break
        if len(candidates) == 1:
            mapping[candidates[0]] = header
            taken.add(header)

    return mapping


def apply_mapping_override(
    mapping: dict[str, str | None], override: dict[str, Any]
) -> dict[str, str | None]:
    """Merge an explicit ``{canonical: header}`` override over detection."""
    merged = dict(mapping)
    for canonical, header in override.items():
        if canonical not in COLUMN_ALIASES:
            raise ServiceError(
                f"unknown mapping target '{canonical}'", 422, "VALIDATION_ERROR"
            )
        if header is None or str(header).strip() == "":
            merged[canonical] = None
            continue
        merged[canonical] = str(header)
    return merged


def missing_required(mapping: dict[str, str | None]) -> list[str]:
    """Canonical groups that have no mapped column, in REQUIRED_GROUPS order."""
    missing: list[str] = []
    for group in REQUIRED_GROUPS:
        if not any(mapping.get(field) for field in group):
            missing.append(" or ".join(group))
    return missing


def unmapped_headers(headers: list[str], mapping: dict[str, str | None]) -> list[str]:
    """Headers the mapper could not place (reported back to the user)."""
    used = {header for header in mapping.values() if header}
    return [h for h in headers if str(h).strip() and h not in used]


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

RowValues = tuple[int, dict[str, Any]]  # (source row number, header -> cell)


@dataclass(frozen=True, slots=True)
class SheetTable:
    """One worksheet/CSV file flattened to header-keyed rows."""

    headers: list[str]
    rows: list[RowValues]
    file_format: str


def _format_of(data: bytes, filename: str) -> str:
    """XLSX | CSV, decided by zip magic bytes first, then the extension."""
    if data[:2] == b"PK":
        return "XLSX"
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        return "CSV"
    if lowered.endswith(".xlsx") or lowered.endswith(".xlsm"):
        return "XLSX"
    raise ServiceError(
        "unsupported file type: upload a .xlsx or .csv", 422, "VALIDATION_ERROR"
    )


def _parse_csv(data: bytes) -> SheetTable:
    text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise ServiceError("file is empty", 422, "VALIDATION_ERROR")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    raw_rows = list(reader)
    if not raw_rows:
        raise ServiceError("file is empty", 422, "VALIDATION_ERROR")
    header_row = raw_rows[0]
    headers = [str(cell).strip() for cell in header_row]
    if not any(headers):
        raise ServiceError("file has no header row", 422, "VALIDATION_ERROR")
    rows: list[RowValues] = []
    for line_no, raw in enumerate(raw_rows[1:], start=2):
        if not any(str(cell).strip() for cell in raw):
            continue
        rows.append((line_no, _row_dict(headers, raw)))
    return SheetTable(headers=headers, rows=rows, file_format="CSV")


def _parse_xlsx(data: bytes) -> SheetTable:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of exceptions on bad zips
        raise ServiceError(
            f"cannot read the .xlsx file: {exc}", 422, "VALIDATION_ERROR"
        ) from exc
    try:
        sheet = book.worksheets[0]
        header_cells: list[str] | None = None
        rows: list[RowValues] = []
        for row_no, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            if header_cells is None:
                if not any(v is not None and str(v).strip() for v in values):
                    continue
                header_cells = [
                    "" if v is None else str(v).strip() for v in values
                ]
                continue
            if not any(v is not None and str(v).strip() for v in values):
                continue
            rows.append((row_no, _row_dict(header_cells, list(values))))
        if header_cells is None:
            raise ServiceError("file has no header row", 422, "VALIDATION_ERROR")
        return SheetTable(headers=header_cells, rows=rows, file_format="XLSX")
    finally:
        book.close()


def _row_dict(headers: list[str], raw: list[Any]) -> dict[str, Any]:
    """Index a physical row by header; first occurrence of a duplicate wins."""
    values: dict[str, Any] = {}
    for idx, header in enumerate(headers):
        if not header or header in values:
            continue
        values[header] = raw[idx] if idx < len(raw) else None
    return values


def parse_spreadsheet(data: bytes, filename: str) -> SheetTable:
    """Parse a .xlsx/.csv payload into a header-keyed table."""
    if len(data) > MAX_BULK_BYTES:
        raise ServiceError(
            f"file is larger than {MAX_BULK_BYTES // (1024 * 1024)} MB",
            422,
            "VALIDATION_ERROR",
        )
    if not data:
        raise ServiceError("file is empty", 422, "VALIDATION_ERROR")
    fmt = _format_of(data, filename)
    table = _parse_xlsx(data) if fmt == "XLSX" else _parse_csv(data)
    if len(table.rows) > MAX_BULK_ROWS:
        raise ServiceError(
            f"file has more than {MAX_BULK_ROWS} data rows; split it",
            422,
            "VALIDATION_ERROR",
        )
    return table


# ---------------------------------------------------------------------------
# Value conversion (money crosses exactly one Decimal boundary)
# ---------------------------------------------------------------------------

EXCEL_EPOCH = date(1899, 12, 30)
_EXCEL_SERIAL_MIN = 20000  # 1954-09-18
_EXCEL_SERIAL_MAX = 60000  # 2064-04-09

_MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_MONEY_STRIP = re.compile(r"[,\s\u20b9$]")
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def _text(value: object) -> str | None:
    """Trimmed text for a cell, or None when the cell is blank."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _to_paise(value: object) -> int:
    """Rupee cell -> integer paise, ROUND_HALF_UP. Raises ValueError if absent.

    Accepts ``₹1,234.56``, ``(1234.56)`` (accounting negative), ints, and the
    ``float`` openpyxl yields for a numeric cell (converted through its decimal
    repr immediately — the value is never arithmetic'd as a float).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("amount is blank")
    if isinstance(value, bool):
        raise ValueError("amount is not a number")
    negative = False
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        dec = Decimal(repr(value))
    elif isinstance(value, Decimal):
        dec = value
    else:
        text = str(value).strip()
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1]
        text = _MONEY_STRIP.sub("", text)
        if not text:
            raise ValueError("amount is blank")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"amount '{value}' is not a number") from exc
    if negative and dec > 0:
        dec = -dec
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_rate(value: object) -> float:
    """GST rate cell -> float in [0, 100]. Raises ValueError when out of range."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("gst rate is blank")
    if isinstance(value, bool):
        raise ValueError("gst rate is not a number")
    if isinstance(value, float):
        dec = Decimal(repr(value))
    elif isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    else:
        text = _MONEY_STRIP.sub("", str(value).strip().rstrip("%"))
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"gst rate '{value}' is not a number") from exc
    if dec < 0 or dec > 100:
        raise ValueError(f"gst rate '{value}' is outside 0-100")
    return float(dec)


def _to_qty(value: object) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("quantity is not a number")
    if isinstance(value, int):
        return value
    dec = Decimal(repr(value)) if isinstance(value, float) else Decimal(
        _MONEY_STRIP.sub("", str(value).strip())
    )
    if dec < 0:
        raise ValueError("quantity is negative")
    return int(dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_bool(value: object) -> bool:
    """Reverse-charge cell -> bool. Unknown text is False (GSTN default)."""
    text = (_text(value) or "").lower()
    return text in {"y", "yes", "true", "1", "1.0", "t", "applicable"}


def parse_date(value: object) -> date | None:
    """Parse a spreadsheet date cell, or None when it is not a date.

    Accepts datetime/date cells, Excel serial numbers (within the documented
    bounds), ``YYYY-MM-DD``, ``DD-MM-YYYY``, ``DD/MM/YYYY``, ``DD.MM.YYYY`` and
    ``DD-MMM-YYYY``. Indian day-first order is used when both a day and month
    candidate are <= 12 (documented, deterministic — never a guess at runtime).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if _EXCEL_SERIAL_MIN <= value <= _EXCEL_SERIAL_MAX:
            return EXCEL_EPOCH + timedelta(days=value)
        return None
    parts = [p for p in _TOKEN_SPLIT.split(str(value).strip()) if p]
    if len(parts) < 3:
        return None
    day_s, month_s, year_s = parts[0], parts[1], parts[2]
    if len(day_s) == 4 and day_s.isdigit():
        year_s, month_s, day_s = parts[0], parts[1], parts[2]
    if not year_s.isdigit():
        return None
    year = int(year_s)
    if len(year_s) == 2:
        year += 2000
    if month_s.isdigit():
        month = int(month_s)
    else:
        month = _MONTHS.get(month_s.lower()[:4], 0) or _MONTHS.get(
            month_s.lower()[:3], 0
        )
    if not day_s.isdigit():
        return None
    try:
        return date(year, month, int(day_s))
    except ValueError:
        return None


def derive_fp(value: date) -> str:
    """MMYYYY filing period of an invoice date (never the upload date)."""
    return f"{value.month:02d}{value.year}"


def _state_code_of(gstin: str) -> str | None:
    try:
        return gstin_state_code(gstin)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-row build + pre-validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowBuild:
    """A parsed, pre-validated spreadsheet row."""

    row_no: int
    invoice_no: str | None
    errors: list[str]
    payload: dict[str, Any] | None
    confidence: dict[str, float]


def _cell(row: dict[str, Any], mapping: dict[str, str | None], field: str) -> Any:
    header = mapping.get(field)
    if header is None:
        return None
    return row.get(header)


def build_row(
    row_no: int,
    row: dict[str, Any],
    mapping: dict[str, str | None],
    *,
    filename: str,
    period_fp: str,
    registration_gstin: str,
) -> RowBuild:
    """Turn one spreadsheet row into a draft payload, or into per-row errors."""
    errors: list[str] = []
    confidence: dict[str, float] = {}

    invoice_no = _text(_cell(row, mapping, "invoice_no"))

    invoice_dt = parse_date(_cell(row, mapping, "invoice_date"))
    if invoice_dt is None:
        errors.append(
            "invoice_date is missing or not a date "
            f"('{_text(_cell(row, mapping, 'invoice_date')) or ''}')"
        )
    else:
        derived = derive_fp(invoice_dt)
        if derived != period_fp:
            errors.append(
                f"invoice_date {invoice_dt.isoformat()} belongs to filing period "
                f"{derived}, not {period_fp}"
            )

    supplier_gstin: str | None = None
    supplier_raw = _text(_cell(row, mapping, "supplier_gstin"))
    if supplier_raw:
        try:
            supplier_gstin = validate_gstin(supplier_raw)
        except ValueError as exc:
            errors.append(f"supplier_gstin '{supplier_raw}' rejected: {exc}")
    buyer_gstin: str | None = None
    buyer_raw = _text(_cell(row, mapping, "buyer_gstin"))
    if buyer_raw:
        try:
            buyer_gstin = validate_gstin(buyer_raw)
        except ValueError as exc:
            errors.append(f"buyer_gstin '{buyer_raw}' rejected: {exc}")

    me = registration_gstin.strip().upper()
    supplier_is_me = supplier_gstin == me
    buyer_is_me = buyer_gstin == me
    if not supplier_is_me and not buyer_is_me:
        errors.append(
            "row does not reference the registration GSTIN "
            f"{me} as supplier or buyer"
        )

    taxable_paise: int | None = None
    try:
        taxable_paise = _to_paise(_cell(row, mapping, "taxable_value"))
        if taxable_paise < 0:
            errors.append("taxable_value is negative")
    except ValueError as exc:
        errors.append(f"taxable_value: {exc}")

    rate: float | None = None
    try:
        rate = _to_rate(_cell(row, mapping, "gst_rate"))
    except ValueError as exc:
        if _cell(row, mapping, "gst_rate") is not None:
            errors.append(f"gst_rate: {exc}")

    def _tax(field: str) -> int | None:
        try:
            value = _to_paise(_cell(row, mapping, field))
        except ValueError:
            return None
        return value

    cgst = _tax("cgst")
    sgst = _tax("sgst")
    igst = _tax("igst")
    cess = _tax("cess")
    total = _tax("total_value")
    if total is not None and total < 0:
        errors.append("total_value is negative")

    if cgst is None and sgst is None and igst is None and rate is None:
        errors.append(
            "no tax on the row: map a GST rate column or cgst/sgst/igst columns"
        )

    # --- derived: recipient state is the place of supply --------------------
    recipient_gstin = buyer_gstin or supplier_gstin
    place_of_supply: str | None = None
    pos_raw = _text(_cell(row, mapping, "place_of_supply"))
    if pos_raw:
        candidate = pos_raw.strip()
        if candidate.isdigit():
            candidate = f"{int(candidate):02d}"
        if candidate not in VALID_STATE_CODES:
            errors.append(f"place_of_supply '{pos_raw}' is not a valid state code")
        else:
            place_of_supply = candidate
    elif recipient_gstin:
        place_of_supply = _state_code_of(recipient_gstin)

    supplier_state = _state_code_of(supplier_gstin) if supplier_gstin else None
    is_inter = bool(
        supplier_state and place_of_supply and supplier_state != place_of_supply
    )

    inv_typ = DEFAULT_INV_TYP
    inv_raw = _text(_cell(row, mapping, "inv_typ"))
    if inv_raw:
        upper = inv_raw.upper().replace(" ", "")
        if upper in {"R", "SEWP", "SEWOP", "DE"}:
            inv_typ = upper
    rchrg = _to_bool(_cell(row, mapping, "rchrg"))

    hsn_sac = _text(_cell(row, mapping, "hsn_sac"))
    description = _text(_cell(row, mapping, "description"))
    try:
        qty = _to_qty(_cell(row, mapping, "qty"))
    except (ValueError, InvalidOperation):
        qty = None
    unit_price_paise: int | None = None
    try:
        unit_price_paise = _to_paise(_cell(row, mapping, "unit_price"))
    except ValueError:
        unit_price_paise = None

    supplier_name = _text(_cell(row, mapping, "supplier_name"))
    buyer_name = _text(_cell(row, mapping, "buyer_name"))

    if errors:
        return RowBuild(
            row_no=row_no,
            invoice_no=invoice_no,
            errors=errors,
            payload=None,
            confidence={},
        )

    assert taxable_paise is not None  # guarded: a parse error returns above

    # --- money: line tax. Rate column wins; otherwise the printed tax defines
    # the implied rate (kept to 6 dp) so the validator's per-line recompute
    # reproduces the printed tax instead of wiping it to zero.
    printed_tax = (cgst or 0) + (sgst or 0) + (igst or 0)
    effective_rate: float
    if rate is not None:
        effective_rate = rate
    elif taxable_paise > 0 and printed_tax:
        effective_rate = float(
            (Decimal(printed_tax) * 100 / Decimal(taxable_paise)).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        )
    else:
        effective_rate = 0.0

    computed = (
        int(
            (Decimal(taxable_paise) * Decimal(str(effective_rate)) / Decimal(100)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if rate is not None
        else printed_tax
    )

    if cgst is None and sgst is None and igst is None:
        if is_inter:
            igst, cgst, sgst = computed, 0, 0
        else:
            half = int(
                (Decimal(computed) / Decimal(2)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            cgst, sgst, igst = half, computed - half, 0
    else:
        cgst = cgst or 0
        sgst = sgst or 0
        igst = igst or 0
    cess = cess or 0

    if total is None:
        total = taxable_paise + cgst + sgst + igst + cess

    lines: list[dict[str, Any]] = [
        {
            "hsn_sac": hsn_sac,
            "desc": description,
            "uqc": None,
            "qty": qty,
            "unit_price_paise": unit_price_paise,
            "gst_rate": effective_rate,
            "taxable_value_paise": taxable_paise,
            "cgst_paise": cgst,
            "sgst_paise": sgst,
            "igst_paise": igst,
        }
    ]

    fields: dict[str, Any] = {
        "supplier_gstin": supplier_gstin,
        "buyer_gstin": buyer_gstin,
        "invoice_no": invoice_no,
        "invoice_date": invoice_dt.isoformat() if invoice_dt else None,
        "place_of_supply": place_of_supply,
        "is_inter_state": is_inter,
        "rchrg": rchrg,
        "inv_typ": inv_typ,
        "taxable_value_paise": taxable_paise,
        "total_value_paise": total,
        "cgst_paise": cgst,
        "sgst_paise": sgst,
        "igst_paise": igst,
        "cess_paise": cess,
        "supplier_name": supplier_name,
        "buyer_name": buyer_name,
    }
    # A value read straight out of a structured file is not an inference: every
    # field the file supplied (and every field derived from it) is 1.0.
    for key in MANDATORY_FIELDS:
        if fields.get(key) is not None:
            confidence[key] = 1.0

    payload: dict[str, Any] = {
        "doc_id": None,
        "capture_source": BULK_CAPTURE_SOURCE.value,
        "fields": fields,
        "lines": lines,
        "confidence": confidence,
        "bulk_import": {
            "source_file": filename,
            "source_row": row_no,
            "mapping": {k: v for k, v in mapping.items() if v},
        },
    }
    return RowBuild(
        row_no=row_no,
        invoice_no=invoice_no,
        errors=[],
        payload=payload,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _ensure_open_period(
    session: AsyncSession, gstin: str, fp: str
) -> FilingPeriod:
    """Return an OPEN filing period, creating one when it does not exist.

    Product parity with the single-upload path (``documents.py``): a first
    bulk import into a month that was never touched materialises the OPEN
    period rather than 404ing, so a fresh tenant's first spreadsheet works.
    The insert is race-safe (PostgreSQL ``ON CONFLICT DO NOTHING``). A FILED
    period still refuses every write with 423 PERIOD_LOCKED (HARD RULE 4).
    """
    period = await session.get(FilingPeriod, (gstin, fp))
    if period is not None:
        if period.status == FilingStatus.FILED:
            raise ServiceError("period is locked after filing", 423, "PERIOD_LOCKED")
        return period

    # Race-safe insert using PostgreSQL ON CONFLICT DO NOTHING (the same
    # pattern documents.py uses for a first upload).
    try:
        await session.execute(
            pg_insert(FilingPeriod)
            .values(
                gstin=gstin,
                fp=fp,
                scheme_snapshot="REGULAR_MONTHLY",
                status=FilingStatus.OPEN,
            )
            .on_conflict_do_nothing(index_elements=["gstin", "fp"])
        )
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ServiceError("period concurrently created", 409, "CONFLICT") from None

    period = await session.get(FilingPeriod, (gstin, fp))
    if period is None:
        # Should never happen after a successful insert, but guard anyway.
        raise ServiceError("period concurrently created", 409, "CONFLICT")
    if period.status == FilingStatus.FILED:
        raise ServiceError("period is locked after filing", 423, "PERIOD_LOCKED")
    return period


async def _known_invoice_numbers(
    session: AsyncSession, gstin: str, fp: str
) -> set[str]:
    """Invoice numbers already in the ledger or already drafted for the period."""
    confirmed = (
        await session.execute(
            select(Invoice.invoice_no).where(Invoice.gstin == gstin, Invoice.fp == fp)
        )
    ).scalars().all()
    known = {str(no) for no in confirmed if no}

    drafts: list[dict[str, Any]] = list(
        (
            await session.execute(
                select(InvoiceDraft.payload).where(
                    InvoiceDraft.gstin == gstin,
                    InvoiceDraft.fp == fp,
                    InvoiceDraft.source == DRAFT_SOURCE_BULK_IMPORT,
                )
            )
        ).scalars().all()
    )
    for payload in drafts:
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if isinstance(fields, dict):
            number = fields.get("invoice_no")
            if number:
                known.add(str(number))
    return known


def _sample_rows(
    table: SheetTable, mapping: dict[str, str | None]
) -> list[dict[str, Any]]:
    """First few rows as display strings so the user can eyeball the mapping."""
    wanted = [(field, header) for field, header in mapping.items() if header]
    sample: list[dict[str, Any]] = []
    for row_no, row in table.rows[:PREVIEW_SAMPLE_ROWS]:
        values = {field: _text(row.get(header)) for field, header in wanted}
        sample.append({"row": row_no, "values": values})
    return sample


def _base_report(
    table: SheetTable,
    mapping: dict[str, str | None],
    filename: str,
) -> dict[str, Any]:
    return {
        "filename": filename,
        "format": table.file_format,
        "headers": table.headers,
        "mapping": {k: v for k, v in mapping.items() if v},
        "unmapped_headers": unmapped_headers(table.headers, mapping),
        "required_missing": missing_required(mapping),
        "data_rows": len(table.rows),
        "capture_source": BULK_CAPTURE_SOURCE.value,
        "sample": _sample_rows(table, mapping),
    }


async def run_bulk_import(
    session: AsyncSession,
    access: GstinAccess,
    fp: str,
    *,
    data: bytes,
    filename: str,
    confirm: bool,
    mapping_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preview (``confirm=False``) or ingest (``confirm=True``) a spreadsheet.

    Preview: header detection + required-column check + sample rows. Always 200
    (even when columns are missing) so the UI can render what it found; ingest
    refuses with 422 while a required column is unmapped.
    """
    table = parse_spreadsheet(data, filename)
    mapping = detect_columns(table.headers)
    if mapping_override:
        mapping = apply_mapping_override(mapping, mapping_override)
    report = _base_report(table, mapping, filename)
    report["confirmed"] = False
    report["imported"] = 0
    report["invalid"] = 0
    report["rows"] = []
    report["error_report"] = []

    if not confirm:
        return report

    missing = report["required_missing"]
    if missing:
        raise ServiceError(
            "cannot import: no column mapped for " + ", ".join(missing),
            422,
            "VALIDATION_ERROR",
        )
    if not table.rows:
        raise ServiceError("the file has no data rows", 422, "VALIDATION_ERROR")

    await _ensure_open_period(session, access.gstin, fp)
    account = await session.get(GstAccount, access.gstin)
    if account is None:
        raise ServiceError("GSTIN not found", 404, "GSTIN_NOT_FOUND")
    known_numbers = await _known_invoice_numbers(session, access.gstin, fp)

    rows_out: list[dict[str, Any]] = []
    imported = 0
    for row_no, row in table.rows:
        built = build_row(
            row_no,
            row,
            mapping,
            filename=filename,
            period_fp=fp,
            registration_gstin=access.gstin,
        )
        if built.payload is None:
            rows_out.append(
                {
                    "row": row_no,
                    "status": "INVALID",
                    "invoice_no": built.invoice_no,
                    "draft_id": None,
                    "errors": built.errors,
                    "flags": [],
                }
            )
            continue

        fields = ExtractedFields.model_validate(built.payload["fields"])
        document = ExtractedDocument(
            doc_id=None,
            capture_source=BULK_CAPTURE_SOURCE,
            fields=fields,
            lines=[ExtractedLine.model_validate(line) for line in built.payload["lines"]],
            confidence=FieldConfidence.model_validate(built.confidence),
        )
        validation = validate_extraction(
            document,
            registration_pan=account.pan,
            ocr_text="",
            existing_invoice_nos=known_numbers,
            period_fp=fp,
        )
        flags = [flag.model_dump() for flag in validation.flags]
        blocking = [flag.message for flag in validation.flags if flag.severity == "BLOCK"]
        invoice_no = (fields.invoice_no or "").strip()
        if blocking or not invoice_no:
            rows_out.append(
                {
                    "row": row_no,
                    "status": "INVALID",
                    "invoice_no": built.invoice_no,
                    "draft_id": None,
                    "errors": blocking or ["invoice_no is required"],
                    "flags": flags,
                }
            )
            continue

        draft = InvoiceDraft(
            extraction_job_id=None,
            gstin=access.gstin,
            fp=fp,
            source=DRAFT_SOURCE_BULK_IMPORT,
            payload=built.payload,
            field_confidence=built.confidence,
        )
        session.add(draft)
        await session.flush()
        known_numbers.add(invoice_no)
        imported += 1
        rows_out.append(
            {
                "row": row_no,
                "status": "IMPORTED",
                "invoice_no": invoice_no,
                "draft_id": str(draft.id),
                "errors": [],
                "flags": flags,
            }
        )

    invalid = len(table.rows) - imported
    await audit(
        session,
        action="BULK_IMPORT",
        entity="invoice_draft",
        entity_id=access.gstin,
        actor_user_id=access.user_id,
        gstin=access.gstin,
        payload_diff={
            "filename": filename,
            "format": table.file_format,
            "fp": fp,
            "rows": len(table.rows),
            "imported": imported,
            "invalid": invalid,
        },
    )
    await session.commit()

    report["confirmed"] = True
    report["imported"] = imported
    report["invalid"] = invalid
    report["rows"] = rows_out
    report["error_report"] = [
        {"row": entry["row"], "errors": entry["errors"]}
        for entry in rows_out
        if entry["status"] == "INVALID"
    ]
    return report
