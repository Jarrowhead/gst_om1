"""1-click GSTR-2B vendor discrepancy notice generator (Task 12.2, rec 5).

Generates:
1. Formal legal notice copy citing Section 16(2)(aa) of the Central Goods and
   Services Tax Act, 2017 (read with Rule 36(4)).
2. Friendly, actionable WhatsApp draft for immediate vendor follow-up.
3. Pre-encoded `wa.me` URL for one-click WhatsApp dispatch.

Amounts are strictly maintained as integer paise internally and formatted to
two-decimal rupees only in human-facing text templates.
"""

from __future__ import annotations

import urllib.parse
import uuid
from typing import Any

from app.api.errors import ServiceError
from app.core.gstin import gstin_checksum_valid
from app.db.models.gst import Gstr2bEntry, Invoice, ItcReconciliation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

STATUTORY_CITATION = "Section 16(2)(aa) of the Central Goods and Services Tax Act, 2017"


def _format_rupees(paise: int) -> str:
    """Paise to formatted INR string with two decimals."""
    rupees = paise / 100.0
    return f"{rupees:,.2f}"


def build_notice_texts(
    *,
    recipient_gstin: str,
    fp: str,
    supplier_gstin: str,
    supplier_name: str,
    invoice_no: str,
    invoice_date: str,
    taxable_paise: int,
    tax_paise: int,
) -> tuple[str, str, str]:
    """Build formal notice, WhatsApp draft, and wa.me URL."""
    taxable_str = _format_rupees(taxable_paise)
    tax_str = _format_rupees(tax_paise)
    total_str = _format_rupees(taxable_paise + tax_paise)

    formal_notice = (
        f"NOTICE REGARDING ITC DISCREPANCY UNDER SECTION 16(2)(aa) OF CGST ACT, 2017\n\n"
        f"To:\n{supplier_name}\nGSTIN: {supplier_gstin}\n\n"
        f"From:\nGSTIN: {recipient_gstin}\nTax Period: {fp}\n\n"
        f"Subject: Request for immediate rectification and upload of "
        f"Invoice No. {invoice_no} in GSTR-1\n\n"
        f"Dear Sir/Madam,\n\n"
        f"Upon reconciliation of our purchase register with the auto-drafted Form GSTR-2B "
        f"for tax period {fp}, the following invoice issued by your organization was found "
        f"missing / unreflected in our GSTR-2B:\n\n"
        f"  • Invoice Number: {invoice_no}\n"
        f"  • Invoice Date:   {invoice_date}\n"
        f"  • Taxable Value:  ₹{taxable_str}\n"
        f"  • GST Amount:     ₹{tax_str}\n"
        f"  • Total Value:    ₹{total_str}\n\n"
        f"STATUTORY MANDATE:\n"
        f"As per {STATUTORY_CITATION} read with Rule 36(4) of the CGST Rules, "
        f"input tax credit (ITC) cannot be availed by a recipient unless the details of such "
        f"invoice have been duly furnished by the supplier in Form GSTR-1 / IFF and communicated "
        f"in Form GSTR-2B.\n\n"
        f"The absence of this invoice in your GSTR-1 blocks our eligible credit of ₹{tax_str}, "
        f"imposing unwarranted financial strain. Kindly upload/amend this invoice in your "
        f"upcoming GSTR-1 on high priority and confirm.\n\n"
        f"Sincerely,\nAuthorized Signatory\nGSTIN: {recipient_gstin}"
    )

    whatsapp_draft = (
        f"*Urgent: GST ITC Discrepancy Notice (Sec 16(2)(aa))*\n\n"
        f"Dear {supplier_name} ({supplier_gstin}),\n"
        f"Invoice *{invoice_no}* dated *{invoice_date}* "
        f"(Taxable: ₹{taxable_str}, GST: ₹{tax_str}) "
        f"is not reflecting in our GSTR-2B for period *{fp}*.\n\n"
        f"As per *{STATUTORY_CITATION}*, "
        f"we cannot claim our ITC until you file this in your GSTR-1.\n\n"
        f"Please upload and file this invoice in your next GSTR-1 filing "
        f"immediately to avoid credit blockage. Thank you!"
    )

    encoded = urllib.parse.quote(whatsapp_draft)
    whatsapp_url = f"https://wa.me/?text={encoded}"

    return formal_notice, whatsapp_draft, whatsapp_url


async def generate_discrepancy_notice(
    session: AsyncSession,
    gstin: str,
    fp: str,
    *,
    recon_id: uuid.UUID | None = None,
    invoice_no: str | None = None,
    supplier_gstin: str | None = None,
    supplier_name: str | None = None,
    taxable_paise: int | None = None,
    tax_paise: int | None = None,
    invoice_date: str | None = None,
) -> dict[str, Any]:
    """Fetch reconciliation context or use supplied params to generate notice."""
    resolved_inv_no = invoice_no
    resolved_inv_date = invoice_date
    resolved_supplier_gstin = supplier_gstin
    resolved_taxable_paise = taxable_paise
    resolved_tax_paise = tax_paise

    if recon_id is not None:
        recon = await session.get(ItcReconciliation, recon_id)
        if recon is None or recon.gstin != gstin or recon.fp != fp:
            raise ServiceError("discrepancy record not found", 404, "DISCREPANCY_NOT_FOUND")

        if recon.purchase_invoice_id is not None:
            inv = (
                await session.execute(
                    select(Invoice)
                    .options(selectinload(Invoice.lines))
                    .where(Invoice.id == recon.purchase_invoice_id)
                )
            ).scalar_one_or_none()
            if inv is not None:
                resolved_inv_no = inv.invoice_no
                resolved_inv_date = inv.invoice_date.isoformat()
                resolved_supplier_gstin = inv.supplier_gstin or resolved_supplier_gstin
                resolved_taxable_paise = sum(line.taxable_value_minor for line in inv.lines)
                resolved_tax_paise = sum(
                    line.cgst_minor + line.sgst_minor + line.igst_minor + line.cess_minor
                    for line in inv.lines
                )
        elif recon.gstr2b_entry_id is not None:
            entry = await session.get(Gstr2bEntry, recon.gstr2b_entry_id)
            if entry is not None:
                resolved_inv_no = entry.invoice_no
                resolved_inv_date = entry.invoice_date.isoformat()
                resolved_supplier_gstin = entry.supplier_gstin
                resolved_taxable_paise = entry.taxable_value_minor
                resolved_tax_paise = (
                    entry.cgst_minor + entry.sgst_minor + entry.igst_minor + entry.cess_minor
                )

    inv_num = resolved_inv_no or "INV-UNKNOWN"
    inv_dt = resolved_inv_date or "—"
    supp_gstin = resolved_supplier_gstin or "—"
    if supp_gstin != "—" and not gstin_checksum_valid(supp_gstin):
        raise ServiceError(f"invalid supplier GSTIN checksum: {supp_gstin}", 422, "INVALID_GSTIN")

    supp_name = supplier_name or (f"M/s {supp_gstin}" if supp_gstin != "—" else "Valued Vendor")
    taxable = resolved_taxable_paise or 0
    tax = resolved_tax_paise or 0

    formal_notice, whatsapp_draft, whatsapp_url = build_notice_texts(
        recipient_gstin=gstin,
        fp=fp,
        supplier_gstin=supp_gstin,
        supplier_name=supp_name,
        invoice_no=inv_num,
        invoice_date=inv_dt,
        taxable_paise=taxable,
        tax_paise=tax,
    )

    return {
        "gstin": gstin,
        "fp": fp,
        "supplier_gstin": supp_gstin,
        "supplier_name": supp_name,
        "invoice_no": inv_num,
        "invoice_date": inv_dt,
        "taxable_paise": taxable,
        "tax_paise": tax,
        "total_paise": taxable + tax,
        "statutory_citation": STATUTORY_CITATION,
        "formal_notice": formal_notice,
        "whatsapp_draft": whatsapp_draft,
        "whatsapp_url": whatsapp_url,
    }
