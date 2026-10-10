"""Bulk Excel/CSV import + column auto-mapper (task 11.1).

Contract under test (PHASE11_13_RECOMMENDATION_BUILD.md rec 1,
EXTRACTION_SPEC.md §3/§6, API_SPECIFICATION.md):

  * column auto-detection returns a mapping preview and refuses to guess on an
    ambiguous header;
  * `confirm=true` bulk-inserts one `extraction.invoice_drafts` row per valid
    line with `source='BULK_IMPORT'` and no extraction job;
  * money is integer paise on both sides of the boundary (the file is rupee
    text, the draft is paise ints) and the conversion is HALF_UP;
  * an invalid row is reported per row and never aborts the rest of the file;
  * a bad header file cannot be imported (422) but still previews (200);
  * the write guard and the period lock hold (403 / 423).

GSTINs are mod-36 checksum-valid synthetic fixtures (tests/gstin_fixtures) —
never hand-typed literals.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from app.db.models.core import AccessRole, AuditLog, User
from app.db.models.extraction import DRAFT_SOURCE_BULK_IMPORT, InvoiceDraft
from app.db.models.gst import FilingPeriod, FilingStatus
from app.services.bulk_import import _to_paise as to_paise
from app.services.bulk_import import detect_columns, parse_date, parse_spreadsheet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.auth_helpers import make_mobile, register_and_login
from tests.gstin_fixtures import make_gstin, validate_gstin
from tests.v4_helpers import seed_gstin_for_user, seed_open_period

FP = "092026"

CSV_HEADER = (
    "Invoice No,Invoice Date,Supplier GSTIN,Buyer GSTIN,"
    "Taxable Value,GST Rate,HSN"
)


def _csv(*rows: str) -> bytes:
    return ("\n".join([CSV_HEADER, *rows])).encode("utf-8")


def _bad_checksum_gstin() -> str:
    """Structurally valid but checksum-invalid GSTIN (mod-36 must reject it)."""
    good = make_gstin(state_code="27")
    for flip in ("A", "B", "C", "5", "9", "Z"):
        candidate = good[:14] + flip
        try:
            validate_gstin(candidate)
        except ValueError:
            return candidate
    raise AssertionError("could not build a checksum-invalid GSTIN")


async def _tenant(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
    *,
    state_code: str = "27",
) -> tuple[dict[str, str], str, str]:
    """Register a user, attach a GSTIN with an open period; return ctx."""
    from app.core.auth.tokens import verify_access_token

    tokens = await register_and_login(client, make_mobile())
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = verify_access_token(tokens["access_token"])
    account = await seed_gstin_for_user(api_sessionmaker, user_id, state_code=state_code)
    await seed_open_period(api_sessionmaker, account.gstin, FP)
    return headers, account.gstin, str(user_id)


async def _drafts(
    api_sessionmaker: async_sessionmaker[Any], gstin: str
) -> Sequence[InvoiceDraft]:
    async with api_sessionmaker() as session:
        return (
            await session.execute(
                select(InvoiceDraft).where(InvoiceDraft.gstin == gstin)
            )
        ).scalars().all()


async def _post(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    gstin: str,
    body: bytes,
    filename: str,
    *,
    confirm: bool = False,
    mapping: dict[str, str] | None = None,
    fp: str = FP,
) -> httpx.Response:
    data: dict[str, str] = {"confirm": "true" if confirm else "false"}
    if mapping is not None:
        import json

        data["mapping"] = json.dumps(mapping)
    return await client.post(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/bulk-import",
        headers=headers,
        data=data,
        files={"file": (filename, body, "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# unit: auto-mapper, date + paise conversion
# ---------------------------------------------------------------------------


def test_detect_columns_maps_the_canonical_headers() -> None:
    headers = [
        "Invoice No",
        "Invoice Date",
        "Supplier GSTIN",
        "Buyer GSTIN",
        "Taxable Value",
        "GST Rate (%)",
        "HSN/SAC",
        "Invoice Value",
    ]
    mapping = detect_columns(headers)
    assert mapping["invoice_no"] == "Invoice No"
    assert mapping["invoice_date"] == "Invoice Date"
    assert mapping["supplier_gstin"] == "Supplier GSTIN"
    assert mapping["buyer_gstin"] == "Buyer GSTIN"
    assert mapping["taxable_value"] == "Taxable Value"
    assert mapping["gst_rate"] == "GST Rate (%)"
    assert mapping["hsn_sac"] == "HSN/SAC"
    assert mapping["total_value"] == "Invoice Value"


def test_detect_columns_leaves_ambiguous_headers_unmapped() -> None:
    """A bare GSTIN / Rate could be either field — the mapper must not guess."""
    mapping = detect_columns(["Invoice No", "GSTIN", "Rate", "Amount"])
    assert mapping["invoice_no"] == "Invoice No"
    assert mapping["supplier_gstin"] is None
    assert mapping["buyer_gstin"] is None
    assert mapping["gst_rate"] is None


def test_detect_columns_token_subset_matches() -> None:
    mapping = detect_columns(["Taxable Value (Rs.)", "CGST Amount (Rs)"])
    assert mapping["taxable_value"] == "Taxable Value (Rs.)"
    assert mapping["cgst"] == "CGST Amount (Rs)"


def test_to_paise_is_integer_paise_half_up() -> None:
    assert to_paise("15,000.00") == 1500000
    assert to_paise("\u20b91,234.56") == 123456
    assert to_paise("2500") == 250000
    assert to_paise(1234.56) == 123456
    # HALF_UP at the paise boundary
    assert to_paise("1234.575") == 123458
    assert to_paise("0.005") == 1
    # accounting negative
    assert to_paise("(1,234.50)") == -123450
    assert to_paise("-10.5") == -1050
    assert isinstance(to_paise("999999.99"), int)


def test_to_paise_rejects_non_numbers() -> None:
    for bad in ("", None, "abc", "12.3.4"):
        with pytest.raises(ValueError):
            to_paise(bad)


def test_parse_date_accepts_indian_iso_and_excel_serial() -> None:
    from datetime import date

    assert parse_date("2026-09-15") == date(2026, 9, 15)
    assert parse_date("15-09-2026") == date(2026, 9, 15)
    assert parse_date("15/09/2026") == date(2026, 9, 15)
    assert parse_date("15-Sep-2026") == date(2026, 9, 15)
    assert parse_date("2026/09/15") == date(2026, 9, 15)
    # Excel serial (openpyxl general-formatted date cell): 46265 = 2026-08-31
    assert parse_date(46265) == date(2026, 8, 31)
    assert parse_date("not a date") is None
    assert parse_date("") is None


def test_parse_spreadsheet_reads_csv_and_xlsx() -> None:
    csv_table = parse_spreadsheet(_csv("A-1,2026-09-01,,,100,18,1234"), "b.csv")
    assert csv_table.file_format == "CSV"
    assert csv_table.headers[0] == "Invoice No"
    assert csv_table.rows[0][0] == 2  # header is line 1

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Invoice No", "Invoice Date", "Taxable Value"])
    sheet.append(["A-2", "2026-09-02", 100.5])
    buf = io.BytesIO()
    book.save(buf)
    xlsx_table = parse_spreadsheet(buf.getvalue(), "b.xlsx")
    assert xlsx_table.file_format == "XLSX"
    assert xlsx_table.rows[0][0] == 2
    assert xlsx_table.rows[0][1]["Taxable Value"] == 100.5


def test_parse_spreadsheet_rejects_unknown_type() -> None:
    from app.api.errors import ServiceError

    with pytest.raises(ServiceError):
        parse_spreadsheet(b"not a spreadsheet", "invoices.pdf")


# ---------------------------------------------------------------------------
# API: preview
# ---------------------------------------------------------------------------


async def test_bulk_import_preview_detects_columns_and_writes_nothing(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    buyer = gstin
    body = _csv(f'INV-P1,2026-09-03,{supplier},{buyer},"15,000.00",18,1234')

    resp = await _post(client, headers, gstin, body, "invoices.csv")

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["confirmed"] is False
    assert data["format"] == "CSV"
    assert data["required_missing"] == []
    assert data["mapping"]["invoice_no"] == "Invoice No"
    assert data["mapping"]["supplier_gstin"] == "Supplier GSTIN"
    assert data["mapping"]["taxable_value"] == "Taxable Value"
    assert data["mapping"]["gst_rate"] == "GST Rate"
    assert data["mapping"]["hsn_sac"] == "HSN"
    assert data["data_rows"] == 1
    assert data["sample"][0]["values"]["taxable_value"] == "15,000.00"
    assert data["imported"] == 0
    assert await _drafts(api_sessionmaker, gstin) == []


async def test_bulk_import_bad_header_file_previews_but_refuses_to_import(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    body = b"alpha,beta,gamma\n1,2,3\n"

    preview = await _post(client, headers, gstin, body, "wrong.csv")
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["mapping"] == {}
    assert preview.json()["data"]["unmapped_headers"] == ["alpha", "beta", "gamma"]
    assert len(preview.json()["data"]["required_missing"]) == 5

    confirm = await _post(client, headers, gstin, body, "wrong.csv", confirm=True)
    assert confirm.status_code == 422, confirm.text
    err = confirm.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert "no column mapped for invoice_no" in err["message"]
    assert await _drafts(api_sessionmaker, gstin) == []


async def test_bulk_import_reports_the_missing_required_group_only(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    body = b"Invoice No,Invoice Date,Taxable Value\nINV-1,2026-09-03,100\n"

    resp = await _post(client, headers, gstin, body, "partial.csv")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["required_missing"] == [
        "supplier_gstin or buyer_gstin",
        "gst_rate or cgst or sgst or igst",
    ]


# ---------------------------------------------------------------------------
# API: ingest
# ---------------------------------------------------------------------------


async def test_bulk_import_csv_inserts_drafts_with_integer_paise(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    body = _csv(
        f'INV-1001,2026-09-03,{supplier},{gstin},"15,000.00",18,73269099',
        f'INV-1002,2026-09-04,{supplier},{gstin},"2,500.75",18,73269099',
    )

    resp = await _post(client, headers, gstin, body, "sept.csv", confirm=True)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["confirmed"] is True
    assert data["imported"] == 2
    assert data["invalid"] == 0
    assert data["error_report"] == []
    assert all(row["status"] == "IMPORTED" for row in data["rows"])
    assert all(uuid.UUID(row["draft_id"]) for row in data["rows"])

    drafts = await _drafts(api_sessionmaker, gstin)
    assert len(drafts) == 2
    by_no = {d.payload["fields"]["invoice_no"]: d for d in drafts}
    assert set(by_no) == {"INV-1001", "INV-1002"}

    first = by_no["INV-1001"]
    assert first.source == DRAFT_SOURCE_BULK_IMPORT
    assert first.extraction_job_id is None
    assert first.fp == FP
    assert first.payload["capture_source"] == "DIGITAL"
    fields = first.payload["fields"]
    # 15,000.00 -> 1_500_000 paise; 18% intra -> 135_000 cgst + 135_000 sgst
    assert fields["taxable_value_paise"] == 1500000
    assert fields["cgst_paise"] == 135000
    assert fields["sgst_paise"] == 135000
    assert fields["igst_paise"] == 0
    assert fields["total_value_paise"] == 1770000
    assert isinstance(fields["taxable_value_paise"], int)
    assert fields["invoice_date"] == "2026-09-03"
    assert fields["buyer_gstin"] == gstin
    assert fields["place_of_supply"] == gstin[:2]

    second = by_no["INV-1002"]
    assert second.payload["fields"]["taxable_value_paise"] == 250075
    assert second.payload["fields"]["total_value_paise"] == 295089
    assert second.payload["lines"][0]["hsn_sac"] == "73269099"
    assert second.payload["bulk_import"]["source_row"] == 3


async def test_bulk_import_invalid_gstin_row_is_reported_without_aborting(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    bad = _bad_checksum_gstin()
    body = _csv(
        f"INV-2001,2026-09-03,{supplier},{gstin},1000.00,18,1234",
        f"INV-2002,2026-09-03,{bad},{gstin},2000.00,18,1234",
        f"INV-2003,2026-09-04,{supplier},{gstin},3000.00,18,1234",
    )

    resp = await _post(client, headers, gstin, body, "mixed.csv", confirm=True)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["imported"] == 2
    assert data["invalid"] == 1
    assert [entry["row"] for entry in data["error_report"]] == [3]
    assert "checksum" in data["error_report"][0]["errors"][0]
    statuses = {row["row"]: row["status"] for row in data["rows"]}
    assert statuses == {2: "IMPORTED", 3: "INVALID", 4: "IMPORTED"}

    drafts = await _drafts(api_sessionmaker, gstin)
    assert {d.payload["fields"]["invoice_no"] for d in drafts} == {
        "INV-2001",
        "INV-2003",
    }


async def test_bulk_import_rejects_row_from_another_filing_period(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    body = _csv(f"INV-3001,2026-08-05,{supplier},{gstin},1000.00,18,1234")

    resp = await _post(client, headers, gstin, body, "aug.csv", confirm=True)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["imported"] == 0
    errors = resp.json()["data"]["error_report"][0]["errors"]
    assert any("082026" in message for message in errors)
    assert await _drafts(api_sessionmaker, gstin) == []


async def test_bulk_import_row_without_registration_gstin_is_rejected(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    other_a = make_gstin(state_code="27")
    other_b = make_gstin(state_code="27")
    body = _csv(f"INV-3101,2026-09-03,{other_a},{other_b},1000.00,18,1234")

    resp = await _post(client, headers, gstin, body, "foreign.csv", confirm=True)

    assert resp.status_code == 200, resp.text
    errors = resp.json()["data"]["error_report"][0]["errors"]
    assert any("does not reference the registration GSTIN" in m for m in errors)
    assert await _drafts(api_sessionmaker, gstin) == []


async def test_bulk_import_xlsx_happy_path(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from openpyxl import Workbook

    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(
        [
            "Invoice No",
            "Invoice Date",
            "Supplier GSTIN",
            "Buyer GSTIN",
            "Taxable Value",
            "GST Rate",
            "HSN",
        ]
    )
    sheet.append(["XL-1", "2026-09-07", supplier, gstin, 1000.25, 18, "998314"])
    buf = io.BytesIO()
    book.save(buf)

    resp = await _post(
        client, headers, gstin, buf.getvalue(), "sept.xlsx", confirm=True
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["format"] == "XLSX"
    assert data["imported"] == 1
    drafts = await _drafts(api_sessionmaker, gstin)
    assert len(drafts) == 1
    assert drafts[0].payload["fields"]["taxable_value_paise"] == 100025


async def test_bulk_import_explicit_mapping_override(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    body = b"a,b,c,d,e,f\nINV-4001,2026-09-08," + supplier.encode() + b","
    body += gstin.encode() + b",1000.00,18\n"
    override = {
        "invoice_no": "a",
        "invoice_date": "b",
        "supplier_gstin": "c",
        "buyer_gstin": "d",
        "taxable_value": "e",
        "gst_rate": "f",
    }

    resp = await _post(
        client, headers, gstin, body, "opaque.csv", confirm=True, mapping=override
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["imported"] == 1
    drafts = await _drafts(api_sessionmaker, gstin)
    assert drafts[0].payload["fields"]["invoice_no"] == "INV-4001"


async def test_bulk_import_unknown_mapping_target_is_422(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    resp = await _post(
        client,
        headers,
        gstin,
        _csv("x,2026-09-08,,,1.00,18,1"),
        "x.csv",
        confirm=True,
        mapping={"not_a_field": "Invoice No"},
    )
    assert resp.status_code == 422
    assert "unknown mapping target" in resp.json()["error"]["message"]


async def test_bulk_import_second_import_flags_duplicate_invoice(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    body = _csv(f"INV-5001,2026-09-09,{supplier},{gstin},1000.00,18,1234")

    first = await _post(client, headers, gstin, body, "a.csv", confirm=True)
    assert first.json()["data"]["imported"] == 1

    second = await _post(client, headers, gstin, body, "a.csv", confirm=True)
    assert second.status_code == 200, second.text
    row = second.json()["data"]["rows"][0]
    assert row["status"] == "IMPORTED"
    assert any(flag["rule"] == "DUPLICATE_INVOICE" for flag in row["flags"])


async def test_bulk_import_writes_an_append_only_audit_row(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    body = _csv(f"INV-6001,2026-09-09,{supplier},{gstin},1000.00,18,1234")

    resp = await _post(client, headers, gstin, body, "audit.csv", confirm=True)
    assert resp.status_code == 200, resp.text

    async with api_sessionmaker() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.gstin == gstin, AuditLog.action == "BULK_IMPORT"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_user_id == uuid.UUID(user_id)
    assert rows[0].payload_diff["imported"] == 1
    assert rows[0].payload_diff["filename"] == "audit.csv"


# ---------------------------------------------------------------------------
# API: guards
# ---------------------------------------------------------------------------


async def test_bulk_import_requires_write_role(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    from app.core.auth.tokens import verify_access_token
    from app.core.gst_accounts import service as gstin_service

    owner_tokens = await register_and_login(client, make_mobile())
    owner_id = verify_access_token(owner_tokens["access_token"])
    account = await seed_gstin_for_user(api_sessionmaker, owner_id, state_code="27")
    await seed_open_period(api_sessionmaker, account.gstin, FP)

    viewer_tokens = await register_and_login(client, make_mobile())
    viewer_id = verify_access_token(viewer_tokens["access_token"])
    async with api_sessionmaker() as session:
        viewer = await session.get(User, viewer_id)
        assert viewer is not None
        await gstin_service.add_collaborator(
            session,
            account.gstin,
            email=viewer.email or "",
            role=AccessRole.VIEWER,
            actor_user_id=owner_id,
        )
    viewer_headers = {"Authorization": f"Bearer {viewer_tokens['access_token']}"}

    resp = await _post(
        client, viewer_headers, account.gstin, _csv("x,2026-09-01,,,1.00,18,1"), "v.csv"
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    assert await _drafts(api_sessionmaker, account.gstin) == []


async def test_bulk_import_unknown_gstin_is_404(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, _gstin, _user_id = await _tenant(client, api_sessionmaker)
    resp = await _post(
        client, headers, make_gstin(state_code="29"), _csv("x,2026-09-01,,,1,18,1"), "x.csv"
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "GSTIN_NOT_FOUND"


async def test_bulk_import_locked_period_returns_423(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")
    async with api_sessionmaker() as session:
        period = await session.get(FilingPeriod, (gstin, FP))
        assert period is not None
        period.status = FilingStatus.FILED
        await session.commit()

    resp = await _post(
        client,
        headers,
        gstin,
        _csv(f"INV-7001,2026-09-09,{supplier},{gstin},1000.00,18,1234"),
        "locked.csv",
        confirm=True,
    )

    assert resp.status_code == 423, resp.text
    assert resp.json()["error"]["code"] == "PERIOD_LOCKED"
    assert await _drafts(api_sessionmaker, gstin) == []


async def test_bulk_import_period_must_exist(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """A row for a period that was never created is a 404, not a silent write."""
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    supplier = make_gstin(state_code="27")

    resp = await _post(
        client,
        headers,
        gstin,
        _csv(f"INV-8001,2026-10-09,{supplier},{gstin},1000.00,18,1234"),
        "oct.csv",
        confirm=True,
        fp="102026",
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PERIOD_NOT_FOUND"


async def test_bulk_import_preview_still_works_with_no_write(
    client: httpx.AsyncClient,
    api_sessionmaker: async_sessionmaker[Any],
) -> None:
    """Preview is a read of the uploaded file only — no period, no writes."""
    headers, gstin, _user_id = await _tenant(client, api_sessionmaker)
    resp = await _post(
        client, headers, gstin, _csv("INV-9001,2026-10-09,,,1.00,18,1"), "oct.csv", fp="102026"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["confirmed"] is False
    assert await _drafts(api_sessionmaker, gstin) == []
