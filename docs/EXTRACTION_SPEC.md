# Extraction Specification & Quality Gates

> **Status:** v1.0 (2026-09-26). Defines the OCR→LLM→validate→review pipeline contract, the canonical extraction schema, the golden set, and the **CI-gated precision targets**. This is the measurement layer that decides whether the product works — not a design nicety.
> **Companion docs:** `TECHNICAL_ARCHITECTURE.md` (§2 system design, §6 phases), `PRD.md` (§4.3 monthly cycle).
> **Harness:** `scripts/measure_extraction.py`. **Golden set:** `extraction/golden_set/`.

---

## 1. Purpose

Every downstream feature — month dashboard, GSTR-1 JSON, GSTR-3B, ITC reconciliation — consumes numbers extracted from client bills. A wrong GSTIN, invoice number, date, or amount is a **filed-return error**, not a UI bug. This spec pins down, as measurable gates:

1. **What** we extract (the canonical schema).
2. **How well** we must extract it (per-field precision/recall, enforced in CI).
3. **When** we trust the machine vs. require a human (confidence thresholds).
4. **How** we measure it (golden set + harness).
5. **What** we spend (LLM cost controls).

---

## 2. Input Reality

The platform replaces WhatsApp document exchange, so the *default* input is a phone photo, not a clean scan. The pipeline must treat this as the baseline, not the edge case.

| Capture source | Typical properties | Preprocess branch |
|---|---|---|
| `PDF_SCAN` | Flatbed scan, 200–300 DPI, upright, single/multi-page | deskew, denoise, 300 DPI normalize |
| `DIGITAL` | Native PDF (Tally/Busy/Zoho export), text layer may exist | direct text extraction first; OCR fallback |
| `PHOTO` | Phone photo, 3–8 MP, slight angle, good light, single page | auto-rotate, upscale/sharpen, blur check |
| `WHATSAPP` | Compressed (~100–500 KB), heavy JPEG artifacts, rotated, multi-burst, mixed light | same as PHOTO + stronger sharpening + multi-image→PDF assembly; **highest confidence threshold** |

**Blur rejection rule:** Laplacian-variance blur score below threshold → job `FAILED` with "re-upload a clearer image", never extract garbage.

---

## 3. Canonical Extraction Schema

One JSON shape for both ground truth and predictions. Money is **integer paise** (mirrors the DB convention). `confidence` appears only on predictions, never on ground truth.

```jsonc
{
  "doc_id": "INV-0001",
  "capture_source": "PHOTO",            // PDF_SCAN | DIGITAL | PHOTO | WHATSAPP
  "fields": {
    "supplier_gstin": "29AABCU9603R1ZM",
    "buyer_gstin": null,                 // null for B2C
    "invoice_no": "INV-2025-0042",
    "invoice_date": "2025-05-04",
    "place_of_supply": "29",             // 2-digit state code
    "is_inter_state": false,
    "rchrg": false,
    "inv_typ": "R",                      // R | SEWP | SEWOP | DE
    "taxable_value_paise": 1500000,      // ₹15,000.00
    "total_value_paise": 1770000,        // ₹17,700.00
    "cgst_paise": 135000,
    "sgst_paise": 135000,
    "igst_paise": 0,
    "cess_paise": 0
  },
  "lines": [                             // per-line detail (secondary, v2 harness)
    {"hsn_sac": "73269099", "desc": "Steel bracket", "uqc": "NOS",
     "qty": 100, "unit_price_paise": 15000, "gst_rate": 18.0,
     "taxable_value_paise": 1500000, "cgst_paise": 135000, "sgst_paise": 135000}
  ],
  "confidence": {                        // predictions only, 0.0–1.0 per field
    "supplier_gstin": 0.98, "invoice_no": 0.95, "total_value_paise": 0.91
  }
}
```

### Field taxonomy

| Tier | Fields | Why the tier |
|---|---|---|
| **Mandatory** (go to government) | supplier_gstin, buyer_gstin, invoice_no, invoice_date, place_of_supply, is_inter_state, rchrg, inv_typ, taxable_value_paise, total_value_paise, cgst/sgst/igst/cess_paise | Feed GSTR-1/3B, ITC, doc_issue. Errors here are filing errors. |
| **Secondary** (display/context) | supplier_name, supplier_address, buyer_name, buyer_address, per-line {desc, uqc, qty, unit_price} | Shown on dashboards; a wrong value annoys but doesn't file wrongly. |
| **Derived** (never extracted, always computed) | supply_type, fp, tax split, GSTIN validity | Computed by the validator from extracted fields — never taken from the LLM. |

**Rule:** the LLM extracts *facts on the page*; the validator *computes* everything derivable (tax math, supply type, filing period, checksum validity). The model is never trusted to do arithmetic or classify supply type.

---

## 4. Precision Targets & CI Gates

Two distinct bars, both enforced by `scripts/measure_extraction.py` and wired into CI (fails the build below threshold):

| Gate | Metric | Threshold | Meaning |
|---|---|---|---|
| **G1 — surface** | mandatory-field **precision** | ≥ 0.90 | When the model emits a value, it's right ≥90% of the time. |
| **G2 — coverage** | mandatory-field **recall** | ≥ 0.95 | The model finds ≥95% of the mandatory fields present on the bill (nothing silently dropped). |
| **G3 — auto-confirm safety** | precision over the **auto-confirm-eligible subset** | ≥ 0.99 | When we *skip* human review (all fields above confidence threshold), we are essentially never wrong. This is the gate that protects filing correctness. |

Post-review ledger (human-in-the-loop completes) holds the fintech-grade bar: **mandatory-field accuracy ≥ 0.995** — enforced as an E2E golden-set test, not just extraction.

**Rationale:** G1/G2 are achievable by a good LLM extractor alone. G3 is the critical one — it decides whether a doc can be auto-confirmed. If G3 drops below 0.99 on the golden set, the auto-confirm threshold is raised (or auto-confirm disabled) — the fix is always "more human review", never "ship lower accuracy".

---

## 5. Auto-Confirm vs Human Review

| Capture source | Auto-confirm confidence threshold (all mandatory fields) | Effect |
|---|---|---|
| `PDF_SCAN` / `DIGITAL` | ≥ 0.90 | below → `NEEDS_REVIEW` |
| `PHOTO` | ≥ 0.97 | below → `NEEDS_REVIEW` |
| `WHATSAPP` | ≥ 0.97 | below → `NEEDS_REVIEW` |

A document is **auto-confirmed** only when *every* mandatory field is present and ≥ threshold. Any single low-confidence or missing mandatory field → the whole document goes to human review (with the low-confidence fields amber-highlighted). Partial auto-confirm is forbidden — a half-trusted invoice is worse than an untrusted one.

---

## 6. Validation Rules (the validator contract)

Runs after LLM extraction, before anything is confirmed. Violations → `NEEDS_REVIEW` (amber flag) or auto-correction where the rule is deterministic.

| Rule | Action on violation |
|---|---|
| GSTIN format `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{2}$` + mod-36 checksum (both parties) | FLAG — invalid GSTIN never proceeds; review forces human re-key |
| PAN consistency: registration PAN == GSTIN[2..12] | BLOCK (onboarding-level) |
| Tax recompute: per-line cgst+sgt/igst from `taxable × rate`, HALF_UP per line, sum to invoice totals | AUTO-CORRECT to recomputed; FLAG if printed ≠ recomputed by > ₹1 |
| Supply type: intra (POS == supplier state) vs inter | COMPUTE — never trust `is_inter_state` from LLM |
| `fp` derivation from invoice_date (MMYYYY) | COMPUTE — upload date never used |
| Place-of-supply code ∈ valid state-code set | FLAG |
| Duplicate invoice_no within (registration, fp) | FLAG — potential re-upload or genuine duplicate series |
| `rchrg`/`inv_typ` inference from invoice keywords (e.g. "SEZ", "deemed export", "reverse charge") | SUGGEST — confirm in review |
| Negative amounts, zero-value lines | FLAG |

---

## 7. Golden Set Plan

The measurement foundation. **No fabricated data** — every entry is a real, anonymized invoice.

| Attribute | Target |
|---|---|
| Size | ~50 documents, growing; start ≥ 20 to make G3 statistically meaningful |
| Stratification | ≥ 50% `WHATSAPP`/`PHOTO` (the real input); rest `PDF_SCAN` + `DIGITAL`; include sales invoice, purchase invoice, credit note, debit note, export, RCM, multi-page |
| Ground truth | Hand-annotated by a human (CA or the operator) into `ground_truth.schema.json`; double-checked for the mandatory fields |
| Anonymization | Real GSTIN/PAN/names/addresses replaced with **valid-format synthetic identifiers** (pass checksum) before committing — never real client PII in the repo; a mapping key kept out-of-band for re-annotation |
| Location | `extraction/golden_set/ground_truth/*.json` + source images in `extraction/golden_set/images/` (git-LFS or MinIO-backed, not raw git) |

**Sourcing:** ask one willing client for a month of their actual bills (with consent + DPDP retention note), or generate from the operator's own registered dummy business bills. A real-GSTIN round-trip to the portal remains out of scope for the golden set (see TESTING_STRATEGY.md §4) — the golden set proves *extraction*, the schema contract test proves *JSON shape*.

---

## 8. Measurement Harness

`scripts/measure_extraction.py` — runnable today, needs only real fixtures dropped into the golden set.

```bash
python scripts/measure_extraction.py \
  --ground-truth extraction/golden_set/ground_truth \
  --predictions extraction/golden_set/predictions \
  --thresholds extraction/golden_set/thresholds.json   # optional override
# exit 0 = all gates pass, exit 1 = a gate failed
```

| Detail | Behavior |
|---|---|
| Field matching | string/GSTIN normalized (strip/case/alnum), dates → `YYYY-MM-DD`, paise exact int compare, bool/enum case-insensitive |
| Missing docs | warns on ground-truth-without-prediction and prediction-without-ground-truth; never silently passes |
| Auto-confirm gate | selects docs where every mandatory field is present and ≥ the capture-source threshold, computes precision over that subset, enforces ≥ 0.99 |
| CI wiring | GitHub Actions job `extraction-gates` runs the harness against the committed golden set; PR fails on any gate breach |

---

## 9. Prompt Contract v1

- **Model:** pinned local/free model via FreeLLMAPI (NOT `flm/auto`). Model choice finalized by a Phase-1 bake-off on the golden set (highest G3 wins).
- **Temperature:** 0. **Output:** strict JSON per §3 schema. **Few-shot:** 3 annotated real Indian layouts (sales, purchase, credit note).
- **Never invent:** missing value → `null` + low confidence → human review. The prompt explicitly forbids filling gaps from context.
- **OCR text in, JSON out:** the LLM sees only the OCR text (not the image) for v1; image-native multimodal is a Phase-3 upgrade if G1/G2 stall below target.

### LLM cost controls (from ARCHITECTURE §7.3)

| Control | Mechanism |
|---|---|
| Pinned model | extraction service key routes to a named free/local model, never auto-routing to paid |
| Hard caps | Redis counters: max tokens/doc + max docs/day; over cap → queue, never silent spend |
| Visibility | `extraction_jobs.llm_model/tokens_in/out` → per-day usage report |
| Circuit breaker | 3 failures on pinned model → fallback → alert |

---

## 10. Failure Handling

| Stage | Failure | Handling |
|---|---|---|
| Preprocess | blur below threshold | job `FAILED` + "re-upload clearer image" |
| OCR | empty text | job `FAILED` |
| LLM | timeout / error | retry ×3 → poison queue; circuit breaker to fallback model |
| Validation | GSTIN checksum fail | `NEEDS_REVIEW`, GSTIN field locked to manual re-key |
| Validation | tax mismatch | auto-correct to recomputed, amber flag on source |
| Review | human edits fields | re-run validator on save; confirm only if clean |

---

## 11. Definition of Done (extraction)

- [ ] Golden set ≥ 20 real anonymized invoices committed (≥ 50% photo/WhatsApp)
- [ ] `scripts/measure_extraction.py` runs in CI and gates G1/G2/G3
- [ ] G1 ≥ 0.90, G2 ≥ 0.95, G3 ≥ 0.99 on the golden set
- [ ] Auto-confirm thresholds per §5 wired into the pipeline
- [ ] Validator rules §6 all implemented with unit tests
- [ ] Cost controls §9 active with per-day usage visible
