# GST Filing App — Build Tracker

**Repo:** `github.com/Sudarshan077/gst1` · **Branch:** `main`  
**Stack:** Python 3.11 (FastAPI · SQLAlchemy · Alembic · PostgreSQL) backend · Next.js 16 + TypeScript (App Router · Tailwind · shadcn/ui) frontend  
**Build mode:** 3-agent loop — Builder / Tester / Monitor. Every task is independently verified by the Tester (real command output: pytest, ruff, mypy, tsc, Playwright) before the Monitor may ADVANCE it. Backend and frontend are built together in the same loop.  
**Last updated:** 2026-10-11 12:16 IST  
**Progress:** **74 / 80 tasks done (92%)**

---

## Phase summary

| Phase | Name | Tasks | Done | Remaining | Status |
|---|---|---|---|---|---|
| 0 | Skeleton | 9 | 9 | 0 | ✅ Complete |
| 1 | Capture & correlation | 8 | 8 | 0 | ✅ Complete |
| 2 | Returns engine | 8 | 8 | 0 | ✅ Complete |
| 3 | Firm scale & compliance | 6 | 6 | 0 | ✅ Complete |
| 4 | Direct filing (GSP) | 3 | 3 | 0 | ✅ Complete |
| 5 | Deployment, Compliance & Go-Live (Free/OSS-only) | 5 | 5 | 0 | ✅ Complete |
| 6 | Verification Sweep (re-test every prior phase with elite models; Tony, 6 Oct) | 6 | 6 | 0 | ✅ Complete |
| 7 | UX/Core Flow Redesign (email-only auth, single user, upload-to-output) — Tony, 6 Oct | 3 | 3 | 0 | ✅ Complete |
| 8 | Product Completeness: Profile, Settings, Business Management & Filing-Ready Returns — Tony, 8 Oct | 11 | 11 | 0 | ✅ Complete |
| 9 | QA Sweep Fixes: Review UX, Extraction Feedback, Empty States, Batch Upload & ITC Visibility — Tony, 9 Oct | 7 | 7 | 0 | ✅ Complete |
| 10 | Bug Sweep: FY rollover, shell nav, coverage gate, checksum hint — Tony, 9 Oct | 5 | 5 | 0 | ✅ Complete |
| 11 | Tier 1: Core Workflow Accelerators (bulk import, batch confirm, Rule 88A set-off) — recs 1/2/3, Tony 10 Oct | 4 | 3 | 1 | 🔄 In progress |
| 12 | Tier 2: CA Practice & Enterprise Controls (maker-checker sign-off + vendor 2B notice) — recs 4/5, Tony 10 Oct | 3 | 0 | 3 | ⬜ Not started |
| 13 | Tier 3: Logistics Moat (E-Way Bill sandbox) — rec 6, Tony 10 Oct | 2 | 0 | 2 | ⬜ Not started |
| **Total** | | **80** | **74** | **6** | |

---

## Full task ledger

### Phase 0 — Skeleton

| ID | Task | Status | Source docs |
|---|---|---|---|
| 0.1 | Repo scaffold + tooling | ✅ Done | `TECHNICAL_ARCHITECTURE.md`, `AI_BUILD_PLAYBOOK.md` |
| 0.2 | bootstrap_stack.py + compose | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 0.3 | DB models (GSTIN as primary key) + Alembic migrations | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 0.4 | Auth: OTP, JWT, refresh rotation, TOTP | ✅ Done | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 0.5 | Guard dependency (GSTIN access check) + audit logging | ✅ Done | `SECURITY_AND_ACCESS.md` |
| 0.6 | GST Accounts CRUD + GSTIN validation (Mod-36) | ✅ Done | `API_SPECIFICATION.md`, `TECHNICAL_ARCHITECTURE.md` |
| 0.7A | Auth API surface: Email OTP/Password + User GSTIN associations | ✅ Done | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 0.7B | Unified Web Shell (Next.js): Email Login + GSTIN Dashboard | ✅ Done | `FRONTEND_SPECIFICATION.md` |
| 0.8 | Phase-0 E2E + docs verification | ✅ Done | `TESTING_STRATEGY.md` |

### Phase 1 — Capture & correlation

| ID | Task | Status | Source docs |
|---|---|---|---|
| 1.1 | Document upload API + MinIO storage (scoped by GSTIN) | ✅ Done | `API_SPECIFICATION.md`, `SECURITY_AND_ACCESS.md` |
| 1.2 | Extraction worker: preprocess + OCR + LLM + validator | ✅ Done | `EXTRACTION_SPEC.md`, `TECHNICAL_ARCHITECTURE.md` |
| 1.3 | Review + confirm flow | ✅ Done | `EXTRACTION_SPEC.md`, `API_SPECIFICATION.md` |
| 1.4 | GSTIN User Access Management + Audit | ✅ Done | `PRD.md`, `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 1.5 | GSTIN search + bulk CSV onboarding | ✅ Done | `API_SPECIFICATION.md`, `PRD.md` |
| 1.6 | Dashboards: month card + registration periods | ✅ Done | `API_SPECIFICATION.md`, `PRD.md` |
| 1.7 | Sandbox IRP adapter | ✅ Done | `PRD.md`, `TECHNICAL_ARCHITECTURE.md` |
| 1.8 | Phase-1 E2E + composition scheme flag | ✅ Done | `TESTING_STRATEGY.md`, `PRD.md` |

### Phase 2 — Returns engine

| ID | Task | Status | Source docs |
|---|---|---|---|
| 2.1 | GSTR-1 JSON generator + pinned schema + self-validator + nil | ✅ Done | `PRD.md`, `EXTRACTION_SPEC.md` |
| 2.2 | CDNR/CDNUR in returns | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 2.3 | GSTR-3B build (outward auto-populated + locked) | ✅ Done | `TECHNICAL_ARCHITECTURE.md`, `API_SPECIFICATION.md` |
| 2.4 | GSTR-2B import + ITC reconciliation engine | ✅ Done | `API_SPECIFICATION.md`, `TESTING_STRATEGY.md` |
| 2.5 | GSTR-1A amendments | ✅ Done | `API_SPECIFICATION.md` |
| 2.6 | Deadline engine + reminders | ✅ Done | `TECHNICAL_ARCHITECTURE.md`, `PRD.md` |
| 2.7 | Golden set + extraction gates in CI | ✅ Done | `EXTRACTION_SPEC.md`, `TESTING_STRATEGY.md` |
| 2.8 | Phase-2 E2E | ✅ Done | `TESTING_STRATEGY.md`, `TESTING_STRATEGY.md` |

### Phase 3 — Firm scale & compliance

| ID | Task | Status | Source docs |
|---|---|---|---|
| 3.1 | Multi-client dashboard (CA roster) | ✅ Done | `PRD.md`, `API_SPECIFICATION.md` |
| 3.2 | Member permissions + audit UI | ✅ Done | `SECURITY_AND_ACCESS.md` |
| 3.3 | Notifications: email + WhatsApp + in-app | ✅ Done | `PRD.md`, `API_SPECIFICATION.md` |
| 3.4 | DPDP export/erasure + retention job | ✅ Done | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 3.5 | CMP-08 / GSTR-4 composition path | ✅ Done | `PRD.md` |
| 3.6 | Phase-3 E2E (timed month-end) | ✅ Done | `TESTING_STRATEGY.md` |

### Phase 4 — Direct filing (GSP)

| ID | Task | Status | Source docs |
|---|---|---|---|
| 4.1 | GSP adapter: live filing + 2B auto-fetch | ✅ Done | `TECHNICAL_ARCHITECTURE.md`, `API_SPECIFICATION.md` |
| 4.2 | Live IRP adapter (gated) | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 4.3 | Phase-4 E2E (sandbox GSP filing) | ✅ Done | `TESTING_STRATEGY.md` |

### Phase 5 — Deployment, Compliance & Go-Live (Free/OSS-only)

| ID | Task | Status | Source docs |
|---|---|---|---|
| 5.1 | Production Infrastructure Setup (Self-Hosted) | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 5.2 | GSP & IRP Free-Tier Developer Onboarding | ✅ Done | `PRD.md` |
| 5.3 | Security Hardening & Vulnerability Scan | ✅ Done | `SECURITY_AND_ACCESS.md` |
| 5.4 | DPDP Compliance & Statutory Data Retention Verification | ✅ Done | `SECURITY_AND_ACCESS.md` |
| 5.5 | Pilot / UAT & Full Regression Suite | ✅ Done | `TESTING_STRATEGY.md` |

### Phase 6 — Verification Sweep (re-test every prior phase with elite models; Tony, 6 Oct)

| ID | Task | Status | Source docs |
|---|---|---|---|
| 6.1 | VERIFY Phase 0 (Skeleton): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `TECHNICAL_ARCHITECTURE.md`, `AI_BUILD_PLAYBOOK.md` |
| 6.2 | VERIFY Phase 1 (Capture & correlation): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `API_SPECIFICATION.md`, `SECURITY_AND_ACCESS.md` |
| 6.3 | VERIFY Phase 2 (Returns engine): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `PRD.md`, `EXTRACTION_SPEC.md` |
| 6.4 | VERIFY Phase 3 (Firm scale & compliance): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `PRD.md`, `API_SPECIFICATION.md` |
| 6.5 | VERIFY Phase 4 (Direct filing (GSP)): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `TECHNICAL_ARCHITECTURE.md`, `API_SPECIFICATION.md` |
| 6.6 | VERIFY Phase 5 (Deployment, Compliance & Go-Live (Free/OSS-only)): 3-lens code/arch/user test + landing gate | ✅ Done | `VERIFICATION_SWEEP.md`, `AI_BUILD_PLAYBOOK.md`, `TECHNICAL_ARCHITECTURE.md` |

### Phase 7 — UX/Core Flow Redesign (email-only auth, single user, upload-to-output) — Tony, 6 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 7.1A | USER MODEL REDESIGN (backend): single email-only user, no CA/owner split | ✅ Done | `AUTH_SPEC.md`, `DB_SCHEMA.md` |
| 7.1B | AUTH FLOW REDESIGN: email-only login (no mobile field) end-to-end | ✅ Done | `AUTH_SPEC.md` |
| 7.2 | UX: 'upload file -> get output' happy path | ✅ Done | `EXTRACTION_PIPELINE.md`, `UI_SPEC.md` |

### Phase 8 — Product Completeness: Profile, Settings, Business Management & Filing-Ready Returns — Tony, 8 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 8.1 | Backend: User Profile API (GET/PATCH /me/profile, email read-only, audit) | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `API_SPECIFICATION.md`, `SECURITY_AND_ACCESS.md` |
| 8.2 | Backend: Settings API (GET /me/settings aggregate, PATCH delegates to prefs) | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `API_SPECIFICATION.md` |
| 8.3 | Backend: Business (GSTIN) management — editable fields + GET /gst-accounts/{gstin}/overview | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `TECHNICAL_ARCHITECTURE.md` |
| 8.4 | Backend: Returns generate pipeline + real .xlsx export + HSN summary + pre-file validation | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `EXTRACTION_SPEC.md` |
| 8.5 | Frontend: persistent app shell nav (Dashboard/Businesses/Upload/Returns/Profile/Settings + GSTIN switcher) | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.6 | Frontend: Profile screen (/app/profile) — view + inline edit full_name, email read-only | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.7 | Frontend: Settings screen (/app/settings) — notification prefs, security/TOTP, session | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.8 | Frontend: Manage businesses (/app/businesses list+add, /app/businesses/[gstin] detail+edit) | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.9 | Frontend: Returns workspace — generate step, JSON/Excel format picker, HSN table, pre-file validation checklist | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.10 | Frontend: Dashboard filing-status tracker (per GSTIN x period roll-up) on /app | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `FRONTEND_SPECIFICATION.md` |
| 8.11 | Phase-8 E2E + full regression (3-lens) + reachability audit | ✅ Done | `PHASE8_PRODUCT_COMPLETENESS.md`, `VERIFICATION_SWEEP.md` |

### Phase 9 — QA Sweep Fixes: Review UX, Extraction Feedback, Empty States, Batch Upload & ITC Visibility — Tony, 9 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 9.1 | Frontend: full-field review editor (all draft fields editable, checksum-validated GSTINs) | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md`, `EXTRACTION_SPEC.md` |
| 9.2 | Frontend: extraction progress polling on upload + queue auto-refresh | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md`, `EXTRACTION_SPEC.md` |
| 9.2B | Frontend: multi-file batch upload queue (<=10 files, per-file status rows) | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md`, `API_SPECIFICATION.md` |
| 9.3 | Frontend: dashboard empty-state CTA (+ Link or Register GSTIN -> /app/businesses) | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md` |
| 9.4 | Frontend: ITC reconciliation dashboard (/app/itc/[gstin]/[fp], books vs 2B, badges, 3B prefill) | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md`, `API_SPECIFICATION.md` |
| 9.5 | Backend: WARN-tier advisory validation rules (stale invoice date, short HSN) | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `EXTRACTION_SPEC.md`, `TESTING_GUIDE.md` |
| 9.6 | Phase-9 E2E + full regression (3-lens) + reachability audit update | ✅ Done | `PHASE9_QA_SWEEP_FIXES.md`, `VERIFICATION_SWEEP.md` |

### Phase 10 — Bug Sweep: FY rollover, shell nav, coverage gate, checksum hint — Tony, 9 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 10.1 | Backend: fix FY month rollover in get_this_fy_period_statuses (13/14/15 -> Jan-Mar of next calendar year) | ✅ Done | `PHASE10_BUG_SWEEP_FIXES.md`, `API_SPECIFICATION.md` |
| 10.2 | Frontend: shared client layout rendering ShellNav on every (client)/app route (upload/returns/review/einvoice/file), no double header | ✅ Done | `PHASE10_BUG_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md` |
| 10.3 | Backend: cross the 80% coverage gate (79.95% -> >=80.00%) with targeted tests | ✅ Done | `PHASE10_BUG_SWEEP_FIXES.md`, `TESTING_GUIDE.md` |
| 10.4 | Frontend: live mod-36 checksum feedback on the Add Business form | ✅ Done | `PHASE10_BUG_SWEEP_FIXES.md`, `FRONTEND_SPECIFICATION.md` |
| 10.5 | Phase-10 E2E + full regression (3-lens) + tracker regeneration | ✅ Done | `PHASE10_BUG_SWEEP_FIXES.md`, `VERIFICATION_SWEEP.md` |

### Phase 11 — Tier 1: Core Workflow Accelerators (bulk import, batch confirm, Rule 88A set-off) — recs 1/2/3, Tony 10 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 11.1 | Bulk Excel/CSV import + column auto-mapper (upload tab) -> InvoiceDraft bulk ingestion (rec 1) | ✅ Done | `PHASE11_13_RECOMMENDATION_BUILD.md`, `EXTRACTION_SPEC.md`, `API_SPECIFICATION.md` |
| 11.2 | 1-click Batch Confirm for clean high-confidence invoices (BATCH_CONFIRM audit) (rec 2) | ✅ Done | `PHASE11_13_RECOMMENDATION_BUILD.md`, `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 11.3 | Rule 88A interactive set-off matrix + PMT-06 challan preview (GSTR-3B) (rec 3) | ✅ Done | `PHASE11_13_RECOMMENDATION_BUILD.md`, `API_SPECIFICATION.md`, `TECHNICAL_ARCHITECTURE.md` |
| 11.4 | Phase-11 E2E + full regression (3-lens) + tracker regeneration | 🔄 In progress | `PHASE11_13_RECOMMENDATION_BUILD.md`, `VERIFICATION_SWEEP.md`, `TESTING_GUIDE.md` |

### Phase 12 — Tier 2: CA Practice & Enterprise Controls (maker-checker sign-off + vendor 2B notice) — recs 4/5, Tony 10 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 12.1 | Maker-checker sign-off + soft period freeze/unlock (ADMIN approves; FILER prepares) (rec 4) | ⬜ Not started | `PHASE11_13_RECOMMENDATION_BUILD.md`, `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 12.2 | 1-click GSTR-2B vendor discrepancy notice generator (formal + WhatsApp draft) (rec 5) | ⬜ Not started | `PHASE11_13_RECOMMENDATION_BUILD.md`, `API_SPECIFICATION.md` |
| 12.3 | Phase-12 E2E + full regression (3-lens) + tracker regeneration | ⬜ Not started | `PHASE11_13_RECOMMENDATION_BUILD.md`, `VERIFICATION_SWEEP.md`, `TESTING_GUIDE.md` |

### Phase 13 — Tier 3: Logistics Moat (E-Way Bill sandbox) — rec 6, Tony 10 Oct

| ID | Task | Status | Source docs |
|---|---|---|---|
| 13.1 | E-Way Bill (EWB) sandbox generation from confirmed outward invoices (value > Rs 50,000) (rec 6) | ⬜ Not started | `PHASE11_13_RECOMMENDATION_BUILD.md`, `API_SPECIFICATION.md`, `EXTRACTION_SPEC.md` |
| 13.2 | Phase-13 E2E + full regression (3-lens) + tracker regeneration | ⬜ Not started | `PHASE11_13_RECOMMENDATION_BUILD.md`, `VERIFICATION_SWEEP.md`, `TESTING_GUIDE.md` |

---

## Definition of done (per task)

A task counts as Done only when all of these hold:

| Gate | Command | Must be |
|---|---|---|
| Backend tests | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/` | all pass |
| Lint | `ruff check .` | clean |
| Types | `mypy .` | clean |
| Frontend types | `cd frontend && npx tsc --noEmit` | clean |
| Frontend lint | `npm run lint` | clean |
| E2E (where specified) | `npx playwright test` | pass |

Independent Tester re-runs these against the live tree; Monitor ADVANCEs only on `evidence=REAL`.

---

## How to read this file

This tracker is the single source of truth, generated from the build loop's `plan.json` + `state.json` by `scripts/generate_tracker.py`. It is regenerated and re-committed at every phase boundary, so the GitHub page never drifts from real progress. Task-level evidence (tester verdicts, defect reports, commits) lives in the git history and in `docs/GST_BUILD_STATUS.md`.
