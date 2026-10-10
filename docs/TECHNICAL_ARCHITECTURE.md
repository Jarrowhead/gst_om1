# Technical Architecture

> **Status:** v3.0 (2026-09-26) — **AI-first stack** (Python + TypeScript), superseding the v2 Java/Flutter layout. Change driver: team builds entirely with AI agents; stack re-chosen for AI reliability, not human-language familiarity.
> **Owner doc for:** system design, data model, flows, ports, repo layout, roadmap. Product scope lives in PRD.md.

---

## 0. Stack decision record (v3.0)

| # | Decision | Rationale |
|---|---|---|
| 1 | **Backend: Python 3.11 + FastAPI + Pydantic + SQLAlchemy 2 + Alembic** | AI's strongest language; Pydantic = compile-time-like shape checking that catches AI mistakes; extraction (OCR/LLM) folds into the SAME codebase — zero cross-language contracts; fastest iterate-run-fix loop |
| 2 | **Frontend: TypeScript + Next.js (App Router) + Tailwind + shadcn/ui** | AI's strongest UI stack by training volume; normal DOM (debuggable by AI agents, unlike Flutter canvas) |
| 3 | Java/Spring **dropped** | Its only justification was porting proven Java GST code. Mitigation for the loss is #4 |
| 4 | **GstCalculator + GSTIN checksum + GSTR-1 JSON: ported to Python, gated by tests cross-checked against the Java reference's test vectors** | Port is small pure-logic; the golden tests (intra/inter, 0/5/12/18/28%, HALF_UP rounding edges) are the safety net, not the original language |
| 5 | IRP e-invoicing adapter: Python port, **sandbox-first**; escape hatch = one tiny Java service *only* for IRP if crypto/signing proves hairy | Only high-risk port; deferred to Phase 3 |
| 6 | Data plane unchanged: PostgreSQL :5436, Redis :6380, MinIO :9001 (versioned + `mc mirror`) | Not a language decision |

---

## 1. What we are building

A platform that replaces the WhatsApp+Excel GST compliance loop:

```
Client uploads bills (photo/scan) for GSTIN G, month M
        ↓
preprocess (photo branch) → OCR → LLM extract → rule-validate → review (human-in-the-loop)
        ↓
Confirmed invoice ledger per (registration, fp = MMYYYY)
        ↓
Return prep:  ├─ AATO ≤ ₹5 Cr → GSTR-1 offline-utility JSON (gst.gov.in upload)
              ├─ AATO > ₹5 Cr → IRP e-invoicing (IRN; GSTR-1 auto-populates)
              └─ both → GSTR-3B build + GSTR-2B ITC reconciliation
        ↓
CA uploads JSON on portal → file (DSC/EVC) → period locked
        ↓
Post-filing corrections → GSTR-1A delta records (locked data never edited)
```

Roles: Any **User** (business owner, CA, accountant, clerk — we don't distinguish) logs in with email, attaches one or more **GSTINs**, and files/uploads for those GSTINs. The GSTIN is the primary entity key. PAN is derived from GSTIN chars 3-12. Billing and access control is per-GSTIN.

---

## 2. Architecture

```
                ┌─────────────────────────────────┐
                │  Next.js Web App :9094           │
                │  Unified GSTIN Dashboard         │
                │  (single shell, GSTIN-centric)      │
                └───────────────┬─────────────────┘
                                │ HTTPS + JWT (+TOTP for CA)
                ┌───────────────▼─────────────────────────────┐
                │  FastAPI Backend :8084                        │
                │  ┌──────────┐ ┌───────────┐ ┌────────────┐ │
                │  │ core      │ │ documents │ │ extraction │ │
                │  │ auth/     │ │ upload/   │ │ worker:    │ │
                │  │ users/    │ │ MinIO/    │ │ preprocess │ │
                │  │ gst_accs/ │ │ jobs      │ │ OCR→LLM→   │ │
                │  │ access/   │ │           │ │ validate   │ │
                │  │ audit     │ └───────────┘ └────────────┘ │
                │  ┌──────────┐ ┌───────────┐ ┌────────────┐ │
                │  │ gst       │ │ returns   │ │ itc        │ │
                │  │ invoices/ │ │ GSTR-1/1A │ │ 2B import  │ │
                │  │ CDNs/     │ │ JSON gen+ │ │ reconcile  │ │
                │  │ series/   │ │ validator │ │            │ │
                │  │ periods  │ │ 3B build   │ │            │ │
                │  │ calculator│ │ IRP adapter│ │           │ │
                │  └──────────┘ └───────────┘ └────────────┘ │
                └──┬──────────┬──────────────┬───────────────┘
                   │          │              │
     ┌─────────────▼──┐ ┌────▼─────┐ ┌──────▼────────────────┐
     │ PostgreSQL     │ │ Redis    │ │ MinIO :9001           │
     │ :5436          │ │ :6380    │ │ gst-docs (versioned,  │
     │ gst_filing_db  │ │ queue/OTP│ │ mc-mirror replicated) │
     │ core/gst/      │ │ caps/rate│ │ {gstin}/{fp}/{docId}  │
     │ extraction     │ └──────────┘ └───────────────────────┘
     └────────────────┘        │
                    ┌──────────▼───────────┐
                    │ LLM: FreeLLMAPI :3001│
                    │ pinned free/local    │
                    │ model, NOT flm/auto  │
                    └──────────────────────┘

Phase 4: GSP adapter → GSTN · IRP adapter → NIC (sandbox in Phase 1)
```

**Why one backend, not API + extraction microservices:** AI maintains one codebase more reliably than contracts between two. The extraction worker is a *process* in the same repo (`python -m app.extraction.worker`), separated by module boundaries, scaled independently at ops level if ever needed.

---

## 3. Data model (v2 model carried forward, unchanged in shape)

### `core` schema (v4.0 — unified GSTIN-first model)

> **Architecture change (v4.0):** The dual-hierarchy model (CA firms vs. business owners with linking flows) has been replaced by a unified GSTIN-centric model. Users log in with email, attach one or more GSTINs, and all operations are keyed directly to the GSTIN. We don't care if the user is a business owner or a CA — the GSTIN is the billing and filing unit.

| Table | Key columns | Notes |
|---|---|---|
| `users` | id (UUID PK), email (unique, NOT NULL — primary login), mobile (unique, nullable), password_hash (nullable), full_name, created_at | Person identity; email is the login credential |
| `gst_accounts` | gstin (String(15) PK, mod-36 validated), pan (derived from gstin[2:12]), legal_name, trade_name (nullable), state_code (2 chars), filing_scheme (REGULAR_MONTHLY/QRMP/COMPOSITION), irn_applicable (bool), aato_latest_minor (bigint), registered_address (nullable), created_at | **Primary entity = GSTIN**. Every filing object, document, and invoice hangs off a GSTIN directly. PAN is extracted from GSTIN chars 3-12, not stored independently |
| `user_gst_access` | id (UUID PK), user_id FK→users, gstin FK→gst_accounts, role (ADMIN/FILER/VIEWER), granted_at | Links users to GSTINs they can operate on. ADMIN can invite others, FILER can upload/file, VIEWER is read-only |
| `audit_logs` | id, actor_user_id, gstin (nullable), action, entity, entity_id, payload_diff (JSONB), at | Every data access logged; gstin replaces business_id/ca_firm_id |

**Removed tables (v4.0):** `ca_firms`, `ca_firm_members`, `businesses`, `business_users`, `ca_client_links`, `consent_records` — all replaced by the simpler `user_gst_access` join table.

### `extraction` schema

| Table | Key columns | Notes |
|---|---|---|
| `documents` | id, gstin FK→gst_accounts, fp, capture_source (PDF_SCAN/DIGITAL/PHOTO/WHATSAPP), doc_type, minio_key, sha256, bytes, page_count, uploaded_by, uploaded_at | Immutable; sha256 dedupe; `capture_source` routes preprocessing; scoped directly by GSTIN |
| `extraction_jobs` | id, document_id, status (QUEUED/PREPROCESS/OCR_RUNNING/LLM_RUNNING/EXTRACTED/FAILED/NEEDS_REVIEW/CONFIRMED), preproc_report (JSONB), ocr_text_ref, raw_llm_output (JSONB), llm_model, llm_tokens_in/out, confidence_avg, error, timestamps | Durable truth; Redis holds job IDs only |
| `invoice_drafts` | id, extraction_job_id (nullable — NULL for a bulk-imported row), gstin FK→gst_accounts, fp, source (`EXTRACTION`\|`BULK_IMPORT`), payload (JSONB), field_confidence (JSONB) | Draft pre-confirmation; never mixes with ledger. `source` separates OCR-produced drafts from Excel/CSV-imported ones (task 11.1) |

### `gst` schema

| Table | Key columns | Notes |
|---|---|---|
| `document_series` | id, gstin FK→gst_accounts, doc_type (INV/CDN/DBN), series_code, fy, current_number | Feeds doc_issue ranges |
| `invoices` | id, gstin FK→gst_accounts, fp, direction (SALES/PURCHASE), supplier_gstin, buyer_gstin, invoice_no, series, invoice_date, place_of_supply, supply_type (INTRA/INTER), rchrg, inv_typ (R/SEWP/SEWOP/DE), export_pay_type (WPAY/WOPAY), shipping_bill_no, port_code, total_value_minor (paise), source_doc_id, status (DRAFT/CONFIRMED/LOCKED), confirmed_by, confirmed_at | LOCKED once period filed; corrections via GSTR-1A. Column named `export_pay_type` (WPAY/WOPAY = payment terms) — distinct from `gstr1_exports.export_type` (ORIGINAL/AMENDMENT) |
| `invoice_lines` | id, invoice_id, line_no, description, hsn_sac, uqc, qty, unit_price_minor, gst_rate, taxable_value_minor, cgst/sgst/igst/cess_minor | Tax always recomputed server-side |
| `credit_debit_notes` | id, gstin FK→gst_accounts, fp, note_type (CDN/DBN), reason_code, source_invoice_id, buyer_gstin, party_name, taxable_value_minor, tax fields, series, note_no, note_date, status, irn | First-class entity → CDNR/CDNUR |
| `filing_periods` | gstin FK→gst_accounts, fp, scheme_snapshot, status (OPEN/READY_FOR_FILING/FILED), gstr1_due_date, gstr3b_due_date, iff_eligible, nil_return, locked_at, filed_at, filed_by | Deadline engine drives reminders |
| `gstr1_exports` | id, gstin FK→gst_accounts, fp, generated_by, json_minio_key, invoice_count, totals (JSONB), schema_version, export_type (ORIGINAL/AMENDMENT), generated_at | Versioned + immutable |
| `gstr1a_amendments` | id, target_export_id, invoice_id/cdn_id, field_deltas (JSONB), reason, status (DRAFT/CONFIRMED/EXPORTED), timestamps | Delta records; originals never edited |
| `e_invoices` | id, invoice_id, irn, ack_no, ack_date, signed_qr_base64, cancelled_at, cancel_window_until | IRN path storage |
| `gstr3b_exports` | id, gstin FK→gst_accounts, fp, auto_payload (JSONB), manual_overrides (JSONB, restricted), generated_by, generated_at | Outward locked per Jul-2025; overrides only ITC tables |
| `gstr2b_statements` | id, gstin FK→gst_accounts, fp, source (PORTAL_UPLOAD/GSP_API), raw_minio_key, downloaded_at, imported_by | Portal 2B JSON until GSP fetch |
| `gstr2b_entries` | id, statement_id, supplier_gstin, invoice_no, invoice_date, taxable_value_minor, tax fields, itc_eligible, doc_type | Parsed 2B rows |
| `itc_reconciliation` | id, gstin FK→gst_accounts, fp, purchase_invoice_id, gstr2b_entry_id, match_status (MATCHED/PROBABLE/UNMATCHED/MISSING_IN_2B/MISSING_IN_BOOKS), confidence, remarks | The report CAs pay for |
| `notifications` | id, user_id, business_id, type, payload (JSONB), read_at, created_at | In-app store |

**Legal engine (drives deadline + lock logic):** GSTR-1 due 11th monthly / 13th QRMP; IFF 13th months 1–2; 3B 20th monthly / 22nd-24th QRMP by state category; 2B on 14th; CMP-08 18th quarterly; GSTR-4 30 Apr; late fee ₹50/day (₹20 nil) cap ₹10k; GSTR-1A before that period's 3B; 3B outward auto-populated + LOCKED (Jul-2025).

---

## 4. Module layout (one repo, one language)

```
D:/gst_filing_app/
├── docs/                          # canonical docs (see docs/README.md) + AI_HANDOFF (start here)
├── backend/
│   ├── app/
│   │   ├── api/                   # FastAPI routers (auth, documents, gst_accounts, returns,
│   │   │                          # profile, settings, notifications, dpdp, itc, einvoice)
│   │   │                          # + schemas.py — the compiled Pydantic contract
│   │   ├── core/                  # access guard (require_gstin_access), audit,
│   │   │                          # gstin validation, auth/ (OTP, JWT rotation, TOTP,
│   │   │                          # Redis sessions), gst_accounts/ (service layer)
│   │   ├── services/              # documents (MinIO), review, notifications, retention,
│   │   │                          # dpdp, irp_service, returns/ (gstr1, gstr1a, gstr3b,
│   │   │                          # gstr2b, pipeline, validation, xlsx_export, gsp adapters)
│   │   ├── extraction/            # worker: preprocess (scan/photo branches), OCR,
│   │   │                          # LLM (pinned model), validator, seed
│   │   ├── db/                    # SQLAlchemy models (core/gst/extraction schemas), session
│   │   └── config.py
│   ├── tests/                     # pytest — 41 modules, 243 tests; shared v4_helpers seed
│   ├── alembic/                   # migrations
│   └── pyproject.toml
├── frontend/                      # Next.js 16 App Router
│   ├── app/
│   │   ├── (auth)/                # login, register, totp setup
│   │   ├── (client)/app/          # unified shell: dashboard, businesses, upload, review,
│   │   │                          # returns workspace, profile, settings, file, einvoice
│   │   └── (ca)/ca/               # CA roster view (additive)
│   ├── components/shared/         # ShellNav (persistent nav + GSTIN switcher), ui/
│   ├── lib/                       # typed API client, session helpers
│   └── tests/                     # Playwright E2E — 10 spec files
├── extraction/golden_set/         # ground truth + harness fixtures
├── scripts/                       # bootstrap_stack, build_loop, seed_demo, app_tour.cjs,
│                                  # generate_tracker, e2e_phase0..8
├── test-samples/                  # ready-to-upload fixtures (valid + failure cases)
├── tools/                         # PG/Redis/MinIO configs, mc mirror, backup (gitignored)
└── build/                         # 3-agent loop state (gitignored)
```

**Ports:** API 8084 · Web 9094 · PG 5436 · Redis 6380 · MinIO 9001/9002 · FreeLLMAPI 3001 (external, already running). Zero clash with Farmer App (8080–8083, 9091–9093, 5432–35, 9000).

---

## 5. Core flows

| Flow | Summary | Full detail |
|---|---|---|
| GSTIN access management | Owner invites collaborator by email with role (ADMIN/FILER/VIEWER); instant revocation; audit logged | PRD §4.2 |
| Upload → extract → review → confirm | Photo branch preprocessing, OCR→LLM→validate, auto-confirm only when every mandatory field ≥ source threshold (scan 0.90 / photo-WhatsApp 0.97), else review | EXTRACTION_SPEC.md |
| Return prep dual-pipeline | JSON path vs IRN path by `irn_applicable`; guard: 0 pending reviews; self-validator before handover; nil-return auto-detect | PRD §4.4 |
| 3B + ITC | Outward auto-build (locked rule), 2B import, 5-status reconciliation, ITC prefill | PRD §4.4 |
| Amendments | GSTR-1A delta records against locked originals; chain preserved | PRD §4.4 |
| Bulk onboarding | CSV dry-run validate-all-first → error report → batch invite codes | PRD §4.2 |

---

## 6. Phased roadmap (unchanged scope, updated exits)

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Repo, CI, PG/Redis/MinIO, full v2 model migrations, auth (OTP+TOTP), both shells | Migrations clean; login both roles; TOTP enforced; CI green |
| 1 | Capture pipeline + linking + dashboards + bulk import + sandbox IRP + composition flag | E2E photo-burst→ledger; both linking flows; CSV import; sandbox IRN |
| 2 | Returns engine: GSTR-1 JSON, CDNR/CDNUR, 3B, 2B/ITC, 1A, deadline engine | Pinned-schema test passes; ITC report matches manual recon; golden gates G1/G2/G3 |
| 3 | Firm scale, DPDP rights, retention, notifications, CMP-08/GSTR-4 path | 5-client month-end <30 min; DPDP demo passes |
| 4 | GSP direct filing + Live IRP | Sandbox GSP filing E2E |
| 5 | Analytics, Tally/Zoho import, buyer-side recon | — |

---

## 7. Changelog

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-09-26 | Initial Java/Flutter baseline |
| v2.0 | 2026-09-26 | All 18 review findings folded in (multi-GSTIN businesses, CA firms, 3B/ITC first-class, DPDP, etc.) |
| v3.0 | 2026-09-26 | **AI-first stack**: Python/FastAPI backend (extraction folded in), Next.js/TS frontend; Java/Flutter retired; GstCalculator port to Python gated by Java-reference test vectors; IRP port deferred w/ escape hatch |
| v4.0 | 2026-10-02 | **Unified GSTIN-first model**: removed CA firms, businesses, business_users, ca_client_links, consent_records. GSTIN is the primary key. Users attach GSTINs via user_gst_access. Email is the login credential. Single unified web shell. |
| v5.0 | 2026-10-09 | **Product completeness (Phase 8)**: profile + settings + business-management APIs, persistent app-shell nav (Dashboard/Businesses/Upload/Returns/Profile/Settings + GSTIN switcher), returns generate orchestration with real `.xlsx` export (openpyxl, OSS) + HSN summary + pre-file validation, dashboard filing-status grid. Build complete: 59/59 tasks. |
