"""Pure-function edge cases for the bulk Excel/CSV mapper (bulk_import.py)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from app.services.bulk_import import (
    _to_bool,
    _to_paise,
    _to_qty,
    _to_rate,
    apply_mapping_override,
    derive_fp,
    missing_required,
    parse_date,
    unmapped_headers,
)

# --------------------------------------------------------------------------- #
# Money / rate / qty conversions
# --------------------------------------------------------------------------- #


def test_to_paise_accounting_negative_parentheses() -> None:
    assert _to_paise("(1234.56)") == -123456


def test_to_paise_int_and_float_and_currency() -> None:
    assert _to_paise(5) == 500
    assert _to_paise(1.5) == 150
    assert _to_paise("\u20b91,234.56") == 123456
    assert _to_paise("$9.99") == 999


def test_to_paise_bool_and_blank_raise() -> None:
    with pytest.raises(ValueError):
        _to_paise(True)
    with pytest.raises(ValueError):
        _to_paise("")
    with pytest.raises(ValueError):
        _to_paise("abc")


def test_to_rate_valid_and_out_of_range() -> None:
    assert _to_rate("18") == 18.0
    assert _to_rate("18%") == 18.0
    with pytest.raises(ValueError):
        _to_rate("150")
    with pytest.raises(ValueError):
        _to_rate("-5")
    with pytest.raises(ValueError):
        _to_rate(True)


def test_to_qty_variants() -> None:
    assert _to_qty(3) == 3
    assert _to_qty(2.5) == 3  # ROUND_HALF_UP
    assert _to_qty("") is None
    assert _to_qty(None) is None
    with pytest.raises(ValueError):
        _to_qty("-1")
    with pytest.raises(ValueError):
        _to_qty(True)


def test_to_bool_variants() -> None:
    assert _to_bool("Y") is True
    assert _to_bool("yes") is True
    assert _to_bool("Applicable") is True
    assert _to_bool("no") is False
    assert _to_bool("") is False
    assert _to_bool(None) is False


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #


def test_parse_date_variants() -> None:
    assert parse_date("15-08-2026") == date(2026, 8, 15)
    assert parse_date("15/08/2026") == date(2026, 8, 15)
    assert parse_date("15.08.2026") == date(2026, 8, 15)
    assert parse_date("2026-08-15") == date(2026, 8, 15)
    assert parse_date("15-Aug-2026") == date(2026, 8, 15)
    assert parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert parse_date(datetime(2026, 1, 2, 10, 30)) == date(2026, 1, 2)
    assert parse_date("not a date") is None
    assert parse_date("") is None


def test_parse_date_excel_serial() -> None:
    from datetime import timedelta

    from app.services.bulk_import import EXCEL_EPOCH

    serial = 46253
    assert parse_date(serial) == EXCEL_EPOCH + timedelta(days=serial)


def test_parse_date_excel_serial_out_of_range() -> None:
    assert parse_date(1) is None  # below _EXCEL_SERIAL_MIN
    assert parse_date(99999) is None  # above _EXCEL_SERIAL_MAX


def test_derive_fp() -> None:
    assert derive_fp(date(2026, 8, 15)) == "082026"
    assert derive_fp(date(2025, 1, 3)) == "012025"


# --------------------------------------------------------------------------- #
# Column mapping helpers
# --------------------------------------------------------------------------- #


def test_apply_mapping_override_and_missing_required() -> None:
    base = {
        "invoice_no": None,
        "invoice_date": None,
        "taxable_value": None,
        "supplier_gstin": None,
        "buyer_gstin": None,
        "gst_rate": None,
    }
    merged = apply_mapping_override(base, {"invoice_no": "Bill No"})
    assert merged["invoice_no"] == "Bill No"
    assert missing_required(merged) == [
        "invoice_date",
        "taxable_value",
        "supplier_gstin or buyer_gstin",
        "gst_rate or cgst or sgst or igst",
    ]


def test_apply_mapping_override_unknown_target() -> None:
    from app.api.errors import ServiceError

    with pytest.raises(ServiceError):
        apply_mapping_override({}, {"not_a_field": "X"})


def test_unmapped_headers() -> None:
    headers = ["Invoice No", "Extra Col", "Date"]
    mapping = {"invoice_no": "Invoice No", "invoice_date": None}
    assert unmapped_headers(headers, mapping) == ["Extra Col", "Date"]
