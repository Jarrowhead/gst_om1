"""GSTR-1 JSON generator, pinned Pydantic schema, self-validator, and nil-return.

Conforms to standard GSTR-1 offline utility format and PRD / Technical Architecture.
Money is integer paise.
"""

# CDNR/CDNUR support added.

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Gstr1LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num: int
    hsn_sac: str | None = None
    txval_paise: int = 0
    rt: float = 0.0
    iamt_paise: int = 0
    camt_paise: int = 0
    samt_paise: int = 0
    csamt_paise: int = 0


class Gstr1InvoiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inum: str
    idt: str  # DD-MM-YYYY or YYYY-MM-DD
    val_paise: int
    pos: str
    inv_typ: str = "R"
    rchrg: str = "N"  # Y/N
    itms: list[Gstr1LineItem]


class Gstr1B2BEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ctin: str
    inv: list[Gstr1InvoiceItem]


class Gstr1CdnrItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nt_num: str
    nt_dt: str  # DD-MM-YYYY
    nt_typ: str  # C/D
    p_gst: str  # Y/N
    rsn: str
    val: int
    itms: list[Gstr1LineItem]


class Gstr1CdnrEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ctin: str
    nt: list[Gstr1CdnrItem]


class Gstr1CdnurItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inum: str | None = None
    idt: str | None = None
    nt_num: str
    nt_dt: str
    nt_typ: str
    rsn: str
    val: int
    pos: str
    itms: list[Gstr1LineItem]


class Gstr1CdnurEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    typ: str  # B2CL/B2CS
    nt: list[Gstr1CdnurItem]


class Gstr1NilEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exem_amt_paise: int = 0
    nil_amt_paise: int = 0
    ngsup_amt_paise: int = 0
    sply_cd: str


class Gstr1DocIssueRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num: int
    from_num: str
    to_num: str
    tot_num: int
    cancel: int
    net_issue: int


class Gstr1DocIssueSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_det: list[Gstr1DocIssueRange]


class Gstr1Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gstin: str = Field(min_length=15, max_length=15)
    fp: str = Field(min_length=6, max_length=6)
    gt: int | None = 0
    cur_gt: int | None = 0
    b2b: list[Gstr1B2BEntry] = Field(default_factory=list)
    b2cs: list[Any] = Field(default_factory=list)
    cdnr: list[Gstr1CdnrEntry] = Field(default_factory=list)
    cdnur: list[Gstr1CdnurEntry] = Field(default_factory=list)
    nil: dict[str, Any] = Field(default_factory=dict)
    doc_issue: Gstr1DocIssueSection | None = None


def generate_nil_gstr1(gstin: str, fp: str) -> dict[str, Any]:
    """Generate a valid nil-return GSTR-1 payload."""
    payload = Gstr1Payload(
        gstin=gstin,
        fp=fp,
        gt=0,
        cur_gt=0,
        b2b=[],
        b2cs=[],
        cdnr=[],
        cdnur=[],
        nil={
            "inv": [
                {
                    "exem_amt_paise": 0,
                    "nil_amt_paise": 0,
                    "ngsup_amt_paise": 0,
                    "sply_cd": "INTRA_NIL",
                },
                {
                    "exem_amt_paise": 0,
                    "nil_amt_paise": 0,
                    "ngsup_amt_paise": 0,
                    "sply_cd": "INTER_NIL",
                },
            ]
        },
        doc_issue=Gstr1DocIssueSection(
            doc_det=[
                Gstr1DocIssueRange(
                    num=1,
                    from_num="1",
                    to_num="1",
                    tot_num=1,
                    cancel=0,
                    net_issue=1,
                )
            ]
        )
    )
    return payload.model_dump()


def validate_gstr1_payload(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Self-validator: validates dict against the pinned Gstr1Payload schema."""
    try:
        Gstr1Payload.model_validate(data)
        return True, None
    except ValidationError as e:
        return False, str(e)



def build_gstr1_from_invoices(
    gstin: str,
    fp: str,
    invoices: list[Any],
    notes: list[Any] | None = None,
) -> dict[str, Any]:
    """Convert confirmed DB invoices and notes into a GSTR-1 payload."""
    b2b_map: dict[str, list[Gstr1InvoiceItem]] = {}
    cdnr_map: dict[str, list[Gstr1CdnrItem]] = {}
    cdnur_list: list[Gstr1CdnurItem] = []
    notes = notes or []

    for inv in invoices:
        buyer = inv.buyer_gstin or "URP"
        if buyer not in b2b_map:
            b2b_map[buyer] = []

        line_items = []
        for idx, line in enumerate(inv.lines, start=1):
            line_items.append(
                Gstr1LineItem(
                    num=idx,
                    hsn_sac=line.hsn_sac,
                    txval_paise=line.taxable_value_minor,
                    rt=float(line.gst_rate),
                    iamt_paise=line.igst_minor,
                    camt_paise=line.cgst_minor,
                    samt_paise=line.sgst_minor,
                    csamt_paise=line.cess_minor,
                )
            )

        inv_item = Gstr1InvoiceItem(
            inum=inv.invoice_no,
            idt=(
                inv.invoice_date.strftime("%d-%m-%Y")
                if isinstance(inv.invoice_date, date)
                else str(inv.invoice_date)
            ),
            val_paise=inv.total_value_minor,
            pos=inv.place_of_supply,
            inv_typ=str(inv.inv_typ),
            rchrg="Y" if inv.rchrg else "N",
            itms=line_items,
        )
        b2b_map[buyer].append(inv_item)

    for note in notes:
        # Calculate rate
        total_tax = note.cgst_minor + note.sgst_minor + note.igst_minor
        rate = (
            float(
                (Decimal(total_tax) / Decimal(note.taxable_value_minor) * 100).quantize(
                    Decimal("0.01")
                )
            )
            if note.taxable_value_minor > 0
            else 0.0
        )

        note_line = Gstr1LineItem(
            num=1,
            txval_paise=note.taxable_value_minor,
            rt=rate,
            iamt_paise=note.igst_minor,
            camt_paise=note.cgst_minor,
            samt_paise=note.sgst_minor,
            csamt_paise=note.cess_minor,
        )

        note_item = Gstr1CdnrItem(
            nt_num=note.note_no,
            nt_dt=note.note_date.strftime("%d-%m-%Y"),
            nt_typ="C" if note.note_type == "CDN" else "D",
            p_gst="Y" if note.buyer_gstin else "N",
            rsn=note.reason_code,
            val=note.taxable_value_minor,
            itms=[note_line]
        )

        if note.buyer_gstin:  # CDNR
            if note.buyer_gstin not in cdnr_map:
                cdnr_map[note.buyer_gstin] = []
            cdnr_map[note.buyer_gstin].append(note_item)
        else:  # CDNUR
            cdnur_item = Gstr1CdnurItem(
                nt_num=note.note_no,
                nt_dt=note.note_date.strftime("%d-%m-%Y"),
                nt_typ="C" if note.note_type == "CDN" else "D",
                rsn=note.reason_code,
                val=note.taxable_value_minor,
                pos="07",
                itms=[note_line]
            )
            cdnur_list.append(cdnur_item)

    b2b_list = [
        Gstr1B2BEntry(ctin=ctin, inv=invs)
        for ctin, invs in b2b_map.items()
        if ctin != "URP"
    ]
    # NOTE: unregistered (B2C) sales are deliberately kept out of `b2b` — a
    # "URP" ctin is 3 chars and would be rejected by the GST offline utility.
    # Routing them into b2cs (Table 7) / b2cl (Table 5, > ₹2.5L inter-state)
    # is a separate, still-open task; for now they must NOT corrupt the B2B
    # table with an invalid counterparty GSTIN.
    cdnr_list = [
        Gstr1CdnrEntry(ctin=ctin, nt=nts) for ctin, nts in cdnr_map.items()
    ]
    cdnur_entry = Gstr1CdnurEntry(typ="B2CL", nt=cdnur_list) if cdnur_list else None

    payload = Gstr1Payload(
        gstin=gstin,
        fp=fp,
        b2b=b2b_list,
        cdnr=cdnr_list,
        cdnur=[cdnur_entry] if cdnur_entry else [],
        nil={},
        doc_issue=Gstr1DocIssueSection(
            doc_det=[
                Gstr1DocIssueRange(
                    num=1,
                    from_num="INV-1",
                    to_num=f"INV-{len(invoices)}",
                    tot_num=len(invoices),
                    cancel=0,
                    net_issue=len(invoices),
                )
            ]
        ) if invoices else None
    )

    return payload.model_dump()
