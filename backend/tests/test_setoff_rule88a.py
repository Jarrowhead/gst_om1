"""Task 11.3 tests: Rule 88A set-off matrix + PMT-06 challan summary.

Asserts the SET-OFF ORDER itself (steps 1..6, from/to heads and amounts),
the zero-payable case, the no-fabricated-CPIN rule, and the guard. Route-
level suites run against the live PG:5436 dev DB via the shared conftest
(ASGI client + fakeredis), seeding GSTINs through tests.v4_helpers so every
fixture GSTIN is mod-36-valid.

Worked example used throughout (all paise):
  outward intra 18% of 100000 -> liability CGST 9000, SGST 9000
  ITC: IGST 10000, CGST 3000, SGST 1000, CESS 0
  Rule 88A order:
    1. IGST->IGST   0    (no IGST liability)
    2. IGST->CGST   9000 (remainder fills CGST fully; credit left 1000)
    3. IGST->SGST   1000 (credit exhausted; SGST liability left 8000)
    4. CGST->CGST   0    (CGST liability already fully paid)
    5. SGST->SGST   1000 (own SGST credit pays more SGST; left 7000)
    6. CESS->CESS   0
  net cash: IGST 0, CGST 0, SGST 7000, CESS 0 -> total 7000.
"""

from __future__ import annotations

from typing import Any

from app.services.returns.setoff import (
    CPIN_NOTE,
    GST_PORTAL_URL,
    TAX_HEADS,
    compute_rule88a_setoff,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.gstin_fixtures import make_gstin
from tests.v4_helpers import seed_account, seed_open_period

SessionMaker = async_sessionmaker[Any]

# ------------------------------------------------------------------ fixtures


def _payload(
    *,
    iamt: int = 0,
    camt: int = 0,
    samt: int = 0,
    csamt: int = 0,
    itc_iamt: int = 0,
    itc_camt: int = 0,
    itc_samt: int = 0,
    itc_csamt: int = 0,
) -> dict[str, Any]:
    """A minimal *valid* GSTR-3B-shaped payload with one osup_det row + ITC.

    Shapes mirror services/returns/gstr3b.build_gstr3b_from_invoices so the
    computation is exercised exactly as production feeds it.
    """
    zeros: dict[str, int] = {
        "txval_paise": 0,
        "iamt_paise": 0,
        "camt_paise": 0,
        "samt_paise": 0,
        "csamt_paise": 0,
    }
    return {
        "gstin": "27ABCDE1234F1Z5",
        "fp": "102026",
        "sup_details": {
            "osup_det": {
                "txval_paise": 100000,
                "iamt_paise": iamt,
                "camt_paise": camt,
                "samt_paise": samt,
                "csamt_paise": csamt,
            },
            "osup_zero": zeros,
            "osup_nil_exmp": zeros,
            "osup_rev": zeros.copy(),
            "isup_rev": zeros.copy(),
            "isup_nil_exmp": zeros.copy(),
            "nc_supp": zeros.copy(),
        },
        "inter_sup": {},
        "itc_elg": {
            "itc_avl": [
                {"ty": "IMPG", "iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
                {"ty": "IMPS", "iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
                {
                    "ty": "ISRC",
                    "iamt_paise": itc_iamt,
                    "camt_paise": itc_camt,
                    "samt_paise": itc_samt,
                    "csamt_paise": itc_csamt,
                },
                {"ty": "ISD", "iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
            ]
        },
        "inward_sup": {},
        "intr_latefee": {"iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
        "interest": {"iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
        "others": {"iamt_paise": 0, "camt_paise": 0, "samt_paise": 0, "csamt_paise": 0},
    }


MIXED = _payload(camt=9000, samt=9000, itc_iamt=10000, itc_camt=3000, itc_samt=1000)
ZERO_CASE = _payload(camt=9000, samt=9000, itc_iamt=0, itc_camt=10000, itc_samt=10000)
ALL_IGST = _payload(iamt=50000, itc_iamt=50000, itc_camt=2000, itc_samt=2000)

# --------------------------------------------------- pure service: the ORDER


def test_setoff_order_steps() -> None:
    result = compute_rule88a_setoff(MIXED, gstin="27ABCDE1234F1Z5", fp="102026")
    steps = result["matrix"]["steps"]
    assert [(s["order"], s["from_head"], s["to_head"]) for s in steps] == [
        (1, "IGST", "IGST"),
        (2, "IGST", "CGST"),
        (3, "IGST", "SGST"),
        (4, "CGST", "CGST"),
        (5, "SGST", "SGST"),
        (6, "CESS", "CESS"),
    ]
    assert [s["amount_paise"] for s in steps] == [0, 9000, 1000, 0, 1000, 0]
    # running credit balances after each step
    assert [s["credit_after_paise"] for s in steps] == [10000, 1000, 0, 3000, 0, 0]
    # running liability balances after each step
    assert [s["liability_after_paise"] for s in steps] == [0, 0, 8000, 0, 7000, 0]


def test_setoff_net_cash_and_challan_zero_cpin() -> None:
    result = compute_rule88a_setoff(MIXED, gstin="27ABCDE1234F1Z5", fp="102026")
    assert result["itc_credits"] == {
        "igst_paise": 10000,
        "cgst_paise": 3000,
        "sgst_paise": 1000,
        "cess_paise": 0,
    }
    assert result["liability"] == {
        "igst_paise": 0,
        "cgst_paise": 9000,
        "sgst_paise": 9000,
        "cess_paise": 0,
    }
    assert result["net_cash"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 7000,
        "cess_paise": 0,
    }
    assert result["credit_leftover"] == {
        "igst_paise": 0,
        "cgst_paise": 3000,
        "sgst_paise": 0,
        "cess_paise": 0,
    }
    assert result["challan"]["deposit"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 7000,
        "cess_paise": 0,
    }
    assert result["challan"]["total_net_paise"] == 7000
    assert result["challan"]["zero_payable"] is False
    assert result["challan"]["portal_url"] == GST_PORTAL_URL
    # NO fabricated CPIN — ever.
    assert result["challan"]["cpin"] is None
    assert result["challan"]["cpin_note"] == CPIN_NOTE


def test_setoff_zero_payable_case() -> None:
    """ITC fully covers liability: zero payable, explicit state, no portal link."""
    result = compute_rule88a_setoff(ZERO_CASE, gstin="27ABCDE1234F1Z5", fp="102026")
    assert result["net_cash"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 0,
        "cess_paise": 0,
    }
    assert result["challan"]["total_net_paise"] == 0
    assert result["challan"]["zero_payable"] is True
    assert result["challan"]["portal_url"] is None
    assert result["challan"]["cpin"] is None


def test_setoff_igst_credit_never_pays_cgst_before_igst_liability() -> None:
    """IGST liability must be paid FIRST (step 1) — the CGST/SGST remainder
    only starts after the IGST head is fully cleared."""
    result = compute_rule88a_setoff(ALL_IGST, gstin="27ABCDE1234F1Z5", fp="102026")
    steps = result["matrix"]["steps"]
    assert steps[0]["amount_paise"] == 50000  # IGST credit -> IGST liability first
    assert steps[1]["amount_paise"] == 0  # nothing left for CGST
    assert steps[2]["amount_paise"] == 0
    assert steps[4]["amount_paise"] == 0  # no SGST liability to absorb the credit
    assert result["net_cash"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 0,
        "cess_paise": 0,
    }
    assert result["challan"]["zero_payable"] is True
    # Unused CGST/SGST credit carries forward — never re-pointed at IGST.
    assert result["credit_leftover"] == {
        "igst_paise": 0,
        "cgst_paise": 2000,
        "sgst_paise": 2000,
        "cess_paise": 0,
    }


def test_setoff_cgst_credit_never_pays_sgst() -> None:
    """Own-head rule: CGST credit can never be used against SGST liability."""
    payload = _payload(camt=9000, samt=9000, itc_camt=12000, itc_samt=0)
    result = compute_rule88a_setoff(payload, gstin="27ABCDE1234F1Z5", fp="102026")
    steps = result["matrix"]["steps"]
    # step 4 CGST->CGST capped at the CGST liability (9000), NOT 12000;
    # the leftover CGST credit (3000) is carried forward, never split to SGST.
    assert steps[3]["amount_paise"] == 9000
    assert steps[3]["credit_after_paise"] == 3000
    assert steps[5]["amount_paise"] == 0  # SGST credit is 0
    assert result["net_cash"]["sgst_paise"] == 9000  # SGST still payable in cash
    assert result["credit_leftover"]["cgst_paise"] == 3000


def test_setoff_works_from_real_gstr3b_payload() -> None:
    """End-to-end purity: the builder's own payload feeds the same result."""
    from app.services.returns.gstr3b import build_gstr3b_from_invoices

    class _Line:  # mirrors db InvoiceLine fields the builder touches
        igst_minor = 0
        cgst_minor = 9000
        sgst_minor = 9000
        cess_minor = 0

    class _Inv:  # mirrors db Invoice fields the builder touches
        direction_value = "SALES"

        class direction:  # noqa: N801 - duck-typed .value
            value = "SALES"

        rchrg = False
        supply_type_value = "INTRA"

        class supply_type:  # noqa: N801
            value = "INTRA"

        buyer_gstin = "27AAAAA0000A1Z5"
        total_value_minor = 100000
        lines = [_Line()]

    payload = build_gstr3b_from_invoices("27ABCDE1234F1Z5", "102026", [_Inv])
    result = compute_rule88a_setoff(payload, gstin="27ABCDE1234F1Z5", fp="102026")
    assert result["liability"]["cgst_paise"] == 9000
    assert result["net_cash"]["cgst_paise"] == 9000
    assert result["challan"]["zero_payable"] is False
    # The no-credit case has an all-zero matrix.
    assert all(step["amount_paise"] == 0 for step in result["matrix"]["steps"])
    assert [step["order"] for step in result["matrix"]["steps"]] == [1, 2, 3, 4, 5, 6]


def test_tax_heads_constant() -> None:
    assert TAX_HEADS == ("IGST", "CGST", "SGST", "CESS")


# ------------------------------------------------------------- route level


def _auth(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_confirmed_sales_and_purchases(
    sessionmaker: SessionMaker,
    gstin: str,
    fp: str,
    user_id: Any,
) -> None:
    """Seed the worked-example ledger: 1 intra 18% sale, 1 purchase each head."""
    from datetime import date
    from decimal import Decimal

    from app.db.models.gst import (
        Invoice,
        InvoiceDirection,
        InvoiceLine,
        InvoiceStatus,
        InvType,
        SupplyType,
    )

    async with sessionmaker() as session:
        sale = Invoice(
            gstin=gstin,
            fp=fp,
            direction=InvoiceDirection.SALES,
            supplier_gstin=gstin,
            buyer_gstin=make_gstin(state_code="27"),
            invoice_no="SET-OFF-S1",
            invoice_date=date(2026, 10, 5),
            place_of_supply="27",
            supply_type=SupplyType.INTRA,
            rchrg=False,
            inv_typ=InvType.R,
            total_value_minor=118000,
            status=InvoiceStatus.CONFIRMED,
            confirmed_by=user_id,
        )
        session.add(sale)
        await session.flush()
        session.add(
            InvoiceLine(
                invoice_id=sale.id,
                line_no=1,
                description="Consulting",
                hsn_sac="998314",
                uqc="NOS",
                qty=Decimal("1"),
                unit_price_minor=100000,
                gst_rate=Decimal("18"),
                taxable_value_minor=100000,
                cgst_minor=9000,
                sgst_minor=9000,
                igst_minor=0,
                cess_minor=0,
            )
        )
        purchase = Invoice(
            gstin=gstin,
            fp=fp,
            direction=InvoiceDirection.PURCHASE,
            supplier_gstin=make_gstin(state_code="29"),
            buyer_gstin=gstin,
            invoice_no="SET-OFF-P1",
            invoice_date=date(2026, 10, 6),
            place_of_supply="27",
            supply_type=SupplyType.INTER,
            rchrg=False,
            inv_typ=InvType.R,
            total_value_minor=50000,
            status=InvoiceStatus.CONFIRMED,
            confirmed_by=user_id,
        )
        session.add(purchase)
        await session.flush()
        session.add(
            InvoiceLine(
                invoice_id=purchase.id,
                line_no=1,
                description="Imports (inter-state)",
                hsn_sac="73269099",
                uqc="NOS",
                qty=Decimal("1"),
                unit_price_minor=50000,
                gst_rate=Decimal("18"),
                taxable_value_minor=50000,
                cgst_minor=0,
                sgst_minor=0,
                igst_minor=9000,
                cess_minor=0,
            )
        )
        await session.commit()


async def test_setoff_endpoint_matrix_and_challan(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    tokens, account = await seed_account(client, api_sessionmaker, state_code="27")
    gstin = account.gstin
    fp = "102026"
    user_id = tokens["user_id"] if "user_id" in tokens else None
    await seed_open_period(api_sessionmaker, gstin, fp)
    await _seed_confirmed_sales_and_purchases(api_sessionmaker, gstin, fp, user_id)

    headers = _auth(tokens)
    resp = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/setoff",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["gstin"] == gstin
    assert data["fp"] == fp

    # The ORDER: [(1, IGST->IGST), (2, IGST->CGST), (3, IGST->SGST),
    #             (4, CGST->CGST), (5, SGST->SGST), (6, CESS->CESS)]
    steps = data["matrix"]["steps"]
    assert [(s["order"], s["from_head"], s["to_head"]) for s in steps] == [
        (1, "IGST", "IGST"),
        (2, "IGST", "CGST"),
        (3, "IGST", "SGST"),
        (4, "CGST", "CGST"),
        (5, "SGST", "SGST"),
        (6, "CESS", "CESS"),
    ]
    # All amounts are ints (never floats).
    for step in steps:
        assert isinstance(step["amount_paise"], int)
        assert isinstance(step["credit_after_paise"], int)
        assert isinstance(step["liability_after_paise"], int)
    for group in ("itc_credits", "liability", "net_cash", "credit_leftover"):
        for head, amount in data[group].items():
            assert isinstance(amount, int), (group, head, amount)

    # Liability: 9000 CGST + 9000 SGST (sale) ; ITC: 9000 IGST (purchase).
    assert data["liability"]["cgst_paise"] == 9000
    assert data["liability"]["sgst_paise"] == 9000
    assert data["itc_credits"]["igst_paise"] == 9000
    # IGST credit -> IGST first (0), then remainder: 9000 to CGST, 0 to SGST;
    # SGST still payable in cash.
    assert data["net_cash"]["cgst_paise"] == 0
    assert data["net_cash"]["sgst_paise"] == 9000
    assert data["challan"]["deposit"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 9000,
        "cess_paise": 0,
    }
    assert data["challan"]["total_net_paise"] == 9000
    assert data["challan"]["zero_payable"] is False
    assert data["challan"]["portal_url"] == GST_PORTAL_URL
    assert data["challan"]["cpin"] is None

    # challan endpoint returns the identical challan block only.
    cresp = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/challan",
        headers=headers,
    )
    assert cresp.status_code == 200, cresp.text
    cdata = cresp.json()["data"]
    assert cdata == data["challan"]
    assert "matrix" not in cdata


async def test_setoff_endpoint_zero_payable_case(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    """Purchases fully cover the liability -> zero-payable challan state."""
    from datetime import date
    from decimal import Decimal

    from app.db.models.gst import (
        Invoice,
        InvoiceDirection,
        InvoiceLine,
        InvoiceStatus,
        InvType,
        SupplyType,
    )

    tokens, account = await seed_account(client, api_sessionmaker, state_code="27")
    gstin = account.gstin
    fp = "102026"
    user_id = None
    await seed_open_period(api_sessionmaker, gstin, fp)

    async with api_sessionmaker() as session:
        sale = Invoice(
            gstin=gstin,
            fp=fp,
            direction=InvoiceDirection.SALES,
            supplier_gstin=gstin,
            buyer_gstin=make_gstin(state_code="27"),
            invoice_no="ZERO-S1",
            invoice_date=date(2026, 10, 5),
            place_of_supply="27",
            supply_type=SupplyType.INTRA,
            rchrg=False,
            inv_typ=InvType.R,
            total_value_minor=11800,
            status=InvoiceStatus.CONFIRMED,
            confirmed_by=user_id,
        )
        session.add(sale)
        await session.flush()
        session.add(
            InvoiceLine(
                invoice_id=sale.id,
                line_no=1,
                description="Consulting",
                hsn_sac="998314",
                uqc="NOS",
                qty=Decimal("1"),
                unit_price_minor=10000,
                gst_rate=Decimal("18"),
                taxable_value_minor=10000,
                cgst_minor=900,
                sgst_minor=900,
                igst_minor=0,
                cess_minor=0,
            )
        )
        purchase = Invoice(
            gstin=gstin,
            fp=fp,
            direction=InvoiceDirection.PURCHASE,
            supplier_gstin=make_gstin(state_code="29"),
            buyer_gstin=gstin,
            invoice_no="ZERO-P1",
            invoice_date=date(2026, 10, 6),
            place_of_supply="27",
            supply_type=SupplyType.INTRA,
            rchrg=False,
            inv_typ=InvType.R,
            total_value_minor=11800,
            status=InvoiceStatus.CONFIRMED,
            confirmed_by=user_id,
        )
        session.add(purchase)
        await session.flush()
        session.add(
            InvoiceLine(
                invoice_id=purchase.id,
                line_no=1,
                description="Local purchase",
                hsn_sac="73269099",
                uqc="NOS",
                qty=Decimal("1"),
                unit_price_minor=10000,
                gst_rate=Decimal("18"),
                taxable_value_minor=10000,
                cgst_minor=900,
                sgst_minor=900,
                igst_minor=0,
                cess_minor=0,
            )
        )
        await session.commit()

    headers = _auth(tokens)
    resp = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/setoff",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["net_cash"] == {
        "igst_paise": 0,
        "cgst_paise": 0,
        "sgst_paise": 0,
        "cess_paise": 0,
    }
    challan = data["challan"]
    assert challan["total_net_paise"] == 0
    assert challan["zero_payable"] is True
    assert challan["portal_url"] is None
    assert challan["cpin"] is None
    assert challan["cpin_note"] == CPIN_NOTE


async def test_setoff_guard_outsider_gets_404(
    client: AsyncClient, api_sessionmaker: SessionMaker
) -> None:
    tokens, account = await seed_account(client, api_sessionmaker, state_code="27")
    gstin = account.gstin
    fp = "102026"
    await seed_open_period(api_sessionmaker, gstin, fp)

    other_tokens, _ = await seed_account(client, api_sessionmaker, state_code="29")
    other_headers = {"Authorization": f"Bearer {other_tokens['access_token']}"}

    for path in ("/gstr3b/setoff", "/gstr3b/challan"):
        resp = await client.get(
            f"/api/v1/gst-accounts/{gstin}/months/{fp}{path}",
            headers=other_headers,
        )
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json()["error"]["code"] == "GSTIN_NOT_FOUND"
