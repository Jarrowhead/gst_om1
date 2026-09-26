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

Roles: **Client** (business owner/clerk, may hold multiple GSTINs under one PAN) and **CA firm** (partners/CAs/clerks with granular permissions). GSTIN is the CA's search key per-registration; PAN is the entity key; mobile OTP identifies persons; a CA is never one person — it's a firm.

---

## 2. Architecture

```
                ┌─────────────────────────────────┐
                │  Next.js Web App :9094           │
                │  Client shell │ CA-firm shell    │
                │  (one codebase, role-based)      │
                └───────────────┬─────────────────┘
                                │ HTTPS + JWT (+TOTP for CA)
                ┌───────────────▼─────────────────────────────┐
                │  FastAPI Backend :8084                        │
                │  ┌──────────┐ ┌───────────┐ ┌────────────┐ │
                │  │ core      │ │ documents │ │ extraction │ │
                │  │ auth/OTP  │ │ upload/   │ │ worker:    │ │
                │  │ firms/    │ │ MinIO/    │ │ preprocess │ │
                │  │ linking/  │ │ jobs      │ │ OCR→LLM→   │ │
                │  │ consent/  │ │           │ │ validate   │ │
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

### `core` schema

| Table | Key columns | Notes |
|---|---|---|
| `users` | id (UUID PK), mobile (unique), email (unique, nullable), password_hash (nullable), full_name, mobile_verified_at, totp_secret, totp_enabled_at | Person ≠ business; TOTP mandatory before joining a firm |
| `ca_firms` | id, firm_name, ca_code (unique), pan, gstin (nullable) | The client-facing + liability unit |
| `ca_firm_members` | firm_id, user_id, role (PARTNER/CA/CLERK), can_export, can_revoke, can_invite_members, joined_at | Granular permissions; audit logs the *member* |
| `businesses` | id, pan (unique, validated), legal_name, trade_name, created_by | PAN = legal entity key; NEVER invent PANs/GSTINs |
| `gst_registrations` | id, business_id FK, gstin (unique, regex+mod-36), state_code, filing_scheme (REGULAR_MONTHLY/QRMP/COMPOSITION), irn_applicable, aato_latest_minor, registered_address | Every filing object hangs off a registration |
| `business_users` | business_id, user_id, role (OWNER/CLERK) | Client-side staff only |
| `ca_client_links` | id, ca_firm_id, business_id, status (PENDING/ACTIVE/REJECTED/REVOKED), initiated_by (FIRM_REQUEST/CLIENT_INVITE), invite_code, consent_record_id, timestamps | One active row per (firm, business); covers ALL registrations |
| `consent_records` | id, principal_user_id, fiduciary_type, purpose, consent_text_version, granted_at, withdrawn_at | DPDP: versioned, withdrawable |
| `audit_logs` | id, actor_user_id, ca_firm_id, business_id, registration_id, action, entity, entity_id, payload_diff (JSONB), at | Every CA view/export logged |

### `extraction` schema

| Table | Key columns | Notes |
|---|---|---|
| `documents` | id, registration_id, fp, capture_source (PDF_SCAN/DIGITAL/PHOTO/WHATSAPP), doc_type, minio_key, sha256, bytes, page_count, uploaded_by, uploaded_at | Immutable; sha256 dedupe; `capture_source` routes preprocessing |
| `extraction_jobs` | id, document_id, status (QUEUED/PREPROCESS/OCR_RUNNING/LLM_RUNNING/EXTRACTED/FAILED/NEEDS_REVIEW/CONFIRMED), preproc_report (JSONB), ocr_text_ref, raw_llm_output (JSONB), llm_model, llm_tokens_in/out, confidence_avg, error, timestamps | Durable truth; Redis holds job IDs only |
| `invoice_drafts` | id, extraction_job_id, registration_id, fp, payload (JSONB), field_confidence (JSONB) | LLM output pre-confirmation; never mixes with ledger |

### `gst` schema

| Table | Key columns | Notes |
|---|---|---|
| `document_series` | id, registration_id, doc_type (INV/CDN/DBN), series_code, fy, current_number | Feeds doc_issue ranges |
| `invoices` | id, registration_id, fp, direction (SALES/PURCHASE), supplier_gstin, buyer_gstin, invoice_no, series, invoice_date, place_of_supply, supply_type (INTRA/INTER), rchrg, inv_typ (R/SEWP/SEWOP/DE), export_type (WPAY/WOPAY), shipping_bill_no, port_code, total_value_minor (paise), source_doc_id, status (DRAFT/CONFIRMED/LOCKED), confirmed_by, confirmed_at | LOCKED once period filed; corrections via GSTR-1A |
| `invoice_lines` | id, invoice_id, line_no, description, hsn_sac, uqc, qty, unit_price_minor, gst_rate, taxable_value_minor, cgst/sgst/igst/cess_minor | Tax always recomputed server-side |
| `credit_debit_notes` | id, registration_id, fp, note_type (CDN/DBN), reason_code, source_invoice_id, buyer_gstin, party_name, taxable_value_minor, tax fields, series, note_no, note_date, status, irn | First-class entity → CDNR/CDNUR |
| `filing_periods` | registration_id, fp, scheme_snapshot, status (OPEN/READY_FOR_FILING/FILED), gstr1_due_date, gstr3b_due_date, iff_eligible, nil_return, locked_at, filed_at, filed_by | Deadline engine drives reminders |
| `gstr1_exports` | id, registration_id, fp, generated_by, json_minio_key, invoice_count, totals (JSONB), schema_version, export_type (ORIGINAL/AMENDMENT), generated_at | Versioned + immutable |
| `gstr1a_amendments` | id, target_export_id, invoice_id/cdn_id, field_deltas (JSONB), reason, status (DRAFT/CONFIRMED/EXPORTED), timestamps | Delta records; originals never edited |
| `e_invoices` | id, invoice_id, irn, ack_no, ack_date, signed_qr_base64, cancelled_at, cancel_window_until | IRN path storage |
| `gstr3b_exports` | id, registration_id, fp, auto_payload (JSONB), manual_overrides (JSONB, restricted), generated_by, generated_at | Outward locked per Jul-2025; overrides only ITC tables |
| `gstr2b_statements` | id, registration_id, fp, source (PORTAL_UPLOAD/GSP_API), raw_minio_key, downloaded_at, imported_by | Portal 2B JSON until GSP fetch |
| `gstr2b_entries` | id, statement_id, supplier_gstin, invoice_no, invoice_date, taxable_value_minor, tax fields, itc_eligible, doc_type | Parsed 2B rows |
| `itc_reconciliation` | id, registration_id, fp, purchase_invoice_id, gstr2b_entry_id, match_status (MATCHED/PROBABLE/UNMATCHED/MISSING_IN_2B/MISSING_IN_BOOKS), confidence, remarks | The report CAs pay for |
| `notifications` | id, user_id, business_id, type, payload (JSONB), read_at, created_at | In-app store |

**Legal engine (drives deadline + lock logic):** GSTR-1 due 11th monthly / 13th QRMP; IFF 13th months 1–2; 3B 20th monthly / 22nd-24th QRMP by state category; 2B on 14th; CMP-08 18th quarterly; GSTR-4 30 Apr; late fee ₹50/day (₹20 nil) cap ₹10k; GSTR-1A before that period's 3B; 3B outward auto-populated + LOCKED (Jul-2025).

---

## 4. Module layout (one repo, one language)

```
D:/gst_filing_app/
├── docs/                          # 8 canonical docs (see README.md)
├── backend/
│   ├── app/
│   │   ├── core/                  # auth (OTP/JWT/TOTP), users, firms, businesses,
│   │   │                          # registrations, linking, consent, audit, notify
│   │   ├── documents/            # upload API, MinIO client, job orchestration
│   │   ├── extraction/           # worker: preprocess (scan/photo branches), OCR,
│   │   │                          # LLM (pinned model), validator
│   │   ├── gst/                   # calculator (ported), invoices, CDNs, series,
│   │   │                          # periods, gstin/checksum
│   │   ├── returns/               # gstr1 (schema+generator+validator), gstr1a,
│   │   │                          # gstr3b, irp adapter (sandbox/live)
│   │   ├── itc/                   # 2B parser, reconciliation engine
│   │   ├── api/                   # FastAPI routers wiring the modules
│   │   ├── db/                    # SQLAlchemy models, Alembic migrations
│   │   └── config.py
│   ├── tests/                     # pytest; golden vectors for calculator
│   ├── pyproject.toml
│   └── alembic/
├── frontend/                      # Next.js App Router
│   ├── app/
│   │   ├── (auth)/                # login, register, totp setup
│   │   ├── client/…               # client shell routes
│   │   └── ca/…                    # CA-firm shell routes
│   ├── components/                # shadcn/ui + domain components
│   └── lib/                       # api client, types (generated from Pydantic)
├── extraction/golden_set/         # ground truth + harness fixtures (existing)
├── scripts/                      # measure_extraction.py (existing), bootstrap, e2e
└── tools/                         # PG/Redis/MinIO configs, mc mirror, backup
```

**Ports:** API 8084 · Web 9094 · PG 5436 · Redis 6380 · MinIO 9001/9002 · FreeLLMAPI 3001 (external, already running). Zero clash with Farmer App (8080–8083, 9091–9093, 5432–35, 9000).

---

## 5. Core flows

| Flow | Summary | Full detail |
|---|---|---|
| CA↔client linking (both flows, consent) | Firm requests by GSTIN / client invites by code; accept activates firm-wide access to all registrations; revocable; DPDP consent recorded | PRD §4.2 |
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