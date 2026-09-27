# GST Filing App — Build Tracker

**Repo:** `github.com/Sudarshan077/gst1` · **Branch:** `main`  
**Stack:** Python 3.11 (FastAPI · SQLAlchemy · Alembic · PostgreSQL) backend · Next.js 16 + TypeScript (App Router · Tailwind · shadcn/ui) frontend  
**Build mode:** 3-agent loop — Builder / Tester / Monitor. Every task is independently verified by the Tester (real command output: pytest, ruff, mypy, tsc, Playwright) before the Monitor may ADVANCE it. Backend and frontend are built together in the same loop.  
**Last updated:** 2026-09-27 20:00 IST  
**Progress:** **8 / 34 tasks done (23%)**

---

## Phase summary

| Phase | Name | Tasks | Done | Remaining | Status |
|---|---|---|---|---|---|
| 0 | Skeleton | 9 | 8 | 1 | 🔄 In progress |
| 1 | Capture & correlation | 8 | 0 | 8 | ⬜ Not started |
| 2 | Returns engine | 8 | 0 | 8 | ⬜ Not started |
| 3 | Firm scale & compliance | 6 | 0 | 6 | ⬜ Not started |
| 4 | Direct filing (GSP) | 3 | 0 | 3 | ⬜ Not started |
| **Total** | | **34** | **8** | **26** | |

---

## Full task ledger

### Phase 0 — Skeleton

| ID | Task | Status | Source docs |
|---|---|---|---|
| 0.1 | Repo scaffold + tooling | ✅ Done | `TECHNICAL_ARCHITECTURE.md`, `AI_BUILD_PLAYBOOK.md` |
| 0.2 | bootstrap_stack.py + compose | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 0.3 | DB models + Alembic migrations | ✅ Done | `TECHNICAL_ARCHITECTURE.md` |
| 0.4 | Auth: OTP, JWT, refresh rotation, TOTP | ✅ Done | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 0.5 | Guard dependency + audit logging | ✅ Done | `SECURITY_AND_ACCESS.md` |
| 0.6 | Businesses/registrations CRUD + GSTIN validation | ✅ Done | `API_SPECIFICATION.md`, `TECHNICAL_ARCHITECTURE.md` |
| 0.7A | Auth API surface: session bootstrap + step-up wiring for web | ✅ Done | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 0.7B | Auth screens + shells (Next.js) | ✅ Done | `FRONTEND_SPECIFICATION.md` |
| 0.8 | Phase-0 E2E + docs verification | 🔄 In progress | `TESTING_STRATEGY.md` |

### Phase 1 — Capture & correlation

| ID | Task | Status | Source docs |
|---|---|---|---|
| 1.1 | Document upload API + MinIO storage | ⬜ Not started | `API_SPECIFICATION.md`, `SECURITY_AND_ACCESS.md` |
| 1.2 | Extraction worker: preprocess + OCR + LLM + validator | ⬜ Not started | `EXTRACTION_SPEC.md`, `TECHNICAL_ARCHITECTURE.md` |
| 1.3 | Review + confirm flow | ⬜ Not started | `EXTRACTION_SPEC.md`, `API_SPECIFICATION.md` |
| 1.4 | CA-firm <-> client linking (both flows) + consent + audit | ⬜ Not started | `PRD.md`, `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 1.5 | GSTIN search + bulk CSV onboarding | ⬜ Not started | `API_SPECIFICATION.md`, `PRD.md` |
| 1.6 | Dashboards: month card + registration periods | ⬜ Not started | `API_SPECIFICATION.md`, `PRD.md` |
| 1.7 | Sandbox IRP adapter | ⬜ Not started | `PRD.md`, `TECHNICAL_ARCHITECTURE.md` |
| 1.8 | Phase-1 E2E + composition scheme flag | ⬜ Not started | `TESTING_STRATEGY.md`, `PRD.md` |

### Phase 2 — Returns engine

| ID | Task | Status | Source docs |
|---|---|---|---|
| 2.1 | GSTR-1 JSON generator + pinned schema + self-validator + nil | ⬜ Not started | `PRD.md`, `EXTRACTION_SPEC.md` |
| 2.2 | CDNR/CDNUR in returns | ⬜ Not started | `TECHNICAL_ARCHITECTURE.md` |
| 2.3 | GSTR-3B build (outward auto-populated + locked) | ⬜ Not started | `TECHNICAL_ARCHITECTURE.md`, `API_SPECIFICATION.md` |
| 2.4 | GSTR-2B import + ITC reconciliation engine | ⬜ Not started | `API_SPECIFICATION.md`, `TESTING_STRATEGY.md` |
| 2.5 | GSTR-1A amendments | ⬜ Not started | `API_SPECIFICATION.md` |
| 2.6 | Deadline engine + reminders | ⬜ Not started | `TECHNICAL_ARCHITECTURE.md`, `PRD.md` |
| 2.7 | Golden set + extraction gates in CI | ⬜ Not started | `EXTRACTION_SPEC.md`, `TESTING_STRATEGY.md` |
| 2.8 | Phase-2 E2E | ⬜ Not started | `TESTING_STRATEGY.md`, `TESTING_STRATEGY.md` |

### Phase 3 — Firm scale & compliance

| ID | Task | Status | Source docs |
|---|---|---|---|
| 3.1 | Multi-client dashboard (CA roster) | ⬜ Not started | `PRD.md`, `API_SPECIFICATION.md` |
| 3.2 | Member permissions + audit UI | ⬜ Not started | `SECURITY_AND_ACCESS.md` |
| 3.3 | Notifications: email + WhatsApp + in-app | ⬜ Not started | `PRD.md`, `API_SPECIFICATION.md` |
| 3.4 | DPDP export/erasure + retention job | ⬜ Not started | `SECURITY_AND_ACCESS.md`, `API_SPECIFICATION.md` |
| 3.5 | CMP-08 / GSTR-4 composition path | ⬜ Not started | `PRD.md` |
| 3.6 | Phase-3 E2E (timed month-end) | ⬜ Not started | `TESTING_STRATEGY.md` |

### Phase 4 — Direct filing (GSP)

| ID | Task | Status | Source docs |
|---|---|---|---|
| 4.1 | GSP adapter: live filing + 2B auto-fetch | ⬜ Not started | `TECHNICAL_ARCHITECTURE.md`, `API_SPECIFICATION.md` |
| 4.2 | Live IRP adapter (gated) | ⬜ Not started | `TECHNICAL_ARCHITECTURE.md` |
| 4.3 | Phase-4 E2E (sandbox GSP filing) | ⬜ Not started | `TESTING_STRATEGY.md` |

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

This tracker is the single source of truth, generated from the build loop's `plan.json` + `state.json`. It is regenerated and re-committed at every phase boundary. Task-level evidence (tester verdicts, defect reports, commits) lives in the git history and in `GST_BUILD_STATUS.md`.
