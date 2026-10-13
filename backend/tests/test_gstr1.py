"""Tests for GSTR-1 JSON generator, pinned schema, self-validator, and nil-return.

Task 2.1 verification.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.returns.gstr1 import (
    build_gstr1_from_invoices,
    generate_nil_gstr1,
    validate_gstr1_payload,
)

from tests.gstin_fixtures import make_gstin


class MockInvoiceLine:
    def __init__(
        self,
        hsn_sac,
        gst_rate,
        taxable_value_minor,
        cgst_minor,
        sgst_minor,
        igst_minor,
        cess_minor=0,
    ):
        self.hsn_sac = hsn_sac
        self.gst_rate = Decimal(str(gst_rate))
        self.taxable_value_minor = taxable_value_minor
        self.cgst_minor = cgst_minor
        self.sgst_minor = sgst_minor
        self.igst_minor = igst_minor
        self.cess_minor = cess_minor


class MockInvoice:
    def __init__(
        self,
        invoice_no,
        invoice_date,
        total_value_minor,
        place_of_supply,
        buyer_gstin,
        lines,
        inv_typ="R",
        rchrg=False,
    ):
        self.invoice_no = invoice_no
        self.invoice_date = invoice_date
        self.total_value_minor = total_value_minor
        self.place_of_supply = place_of_supply
        self.buyer_gstin = buyer_gstin
        self.lines = lines
        self.inv_typ = inv_typ
        self.rchrg = rchrg


class MockNote:
    def __init__(
        self,
        note_no,
        note_date,
        taxable_value_minor,
        cgst_minor,
        sgst_minor,
        igst_minor,
        note_type,
        reason_code,
        buyer_gstin=None,
    ):
        self.note_no = note_no
        self.note_date = note_date
        self.taxable_value_minor = taxable_value_minor
        self.cgst_minor = cgst_minor
        self.sgst_minor = sgst_minor
        self.igst_minor = igst_minor
        self.cess_minor = 0
        self.note_type = note_type  # CDN/DBN
        self.reason_code = reason_code
        self.buyer_gstin = buyer_gstin


def test_nil_gstr1_generation_and_validation():
    gstin = make_gstin(state_code="29")
    fp = "092026"

    nil_json = generate_nil_gstr1(gstin, fp)
    assert nil_json["gstin"] == gstin
    assert nil_json["fp"] == fp
    assert "nil" in nil_json
    assert len(nil_json["nil"]["inv"]) == 2

    valid, err = validate_gstr1_payload(nil_json)
    assert valid is True
    assert err is None


def test_gstr1_generator_with_invoices():
    supplier_gstin = make_gstin(state_code="29")
    buyer_gstin = make_gstin(state_code="29")
    fp = "092026"

    line = MockInvoiceLine(
        hsn_sac="998314",
        gst_rate=18.0,
        taxable_value_minor=1000000,  # ₹10,000.00
        cgst_minor=90000,
        sgst_minor=90000,
        igst_minor=0,
    )
    inv = MockInvoice(
        invoice_no="INV-2026-001",
        invoice_date=date(2026, 9, 15),
        total_value_minor=1180000,  # ₹11,800.00
        place_of_supply="29",
        buyer_gstin=buyer_gstin,
        lines=[line],
    )

    payload = build_gstr1_from_invoices(supplier_gstin, fp, [inv])

    valid, err = validate_gstr1_payload(payload)
    assert valid is True, f"Validation failed: {err}"
    assert payload["gstin"] == supplier_gstin
    assert payload["fp"] == fp
    assert len(payload["b2b"]) == 1
    assert payload["b2b"][0]["ctin"] == buyer_gstin
    assert len(payload["b2b"][0]["inv"]) == 1
    assert payload["b2b"][0]["inv"][0]["inum"] == "INV-2026-001"
    assert payload["b2b"][0]["inv"][0]["val_paise"] == 1180000


def test_gstr1_generator_with_notes():
    supplier_gstin = make_gstin(state_code="29")
    buyer_gstin = make_gstin(state_code="29")
    fp = "092026"

    note = MockNote(
        note_no="CDN-001",
        note_date=date(2026, 9, 20),
        taxable_value_minor=100000,
        cgst_minor=9000,
        sgst_minor=9000,
        igst_minor=0,
        note_type="CDN",
        reason_code="Sales Return",
        buyer_gstin=buyer_gstin,
    )

    payload = build_gstr1_from_invoices(supplier_gstin, fp, [], notes=[note])

    valid, err = validate_gstr1_payload(payload)
    assert valid is True, f"Validation failed: {err}"
    assert len(payload["cdnr"]) == 1
    assert payload["cdnr"][0]["ctin"] == buyer_gstin


def test_gstr1_unregistered_buyer_never_enters_b2b():
    """B2C (unregistered buyer) must NOT land in `b2b` with ctin='URP'.

    GSTR-1 Table 4 (b2b) requires a real 15-char counterparty GSTIN. An
    unregistered sale belongs in b2cs (Table 7) / b2cl (Table 5), never in
    b2b — a 'URP' ctin would be rejected by the GST offline utility.
    """
    supplier_gstin = make_gstin(state_code="29")
    fp = "092026"

    line = MockInvoiceLine(
        hsn_sac="998314",
        gst_rate=18.0,
        taxable_value_minor=100000,  # ₹1,000.00
        cgst_minor=9000,
        sgst_minor=9000,
        igst_minor=0,
    )
    inv = MockInvoice(
        invoice_no="INV-2026-100",
        invoice_date=date(2026, 9, 15),
        total_value_minor=118000,
        place_of_supply="29",
        buyer_gstin=None,  # unregistered retail sale
        lines=[line],
    )

    payload = build_gstr1_from_invoices(supplier_gstin, fp, [inv])

    valid, err = validate_gstr1_payload(payload)
    assert valid is True, f"Validation failed: {err}"
    # The invoice must not be smuggled into b2b under a fake 'URP' ctin.
    assert payload["b2b"] == [], f"URP leaked into b2b: {payload['b2b']}"
    # TODO(follow-up): unregistered sales should route into b2cs (Table 7) /
    # b2cl (Table 5, > ₹2.5L inter-state) — currently they are dropped, not
    # corrupted. That routing is a separate task and is NOT asserted here.


def test_self_validator_rejects_invalid_payload():
    # Invalid GSTIN length
    bad_data = {
        "gstin": "INVALID",
        "fp": "092026",
        "b2b": [],
        "nil": {}
    }
    valid, err = validate_gstr1_payload(bad_data)
    assert valid is False
    assert err is not None
