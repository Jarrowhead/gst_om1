# Testing Guide — GST Filing App

Everything a tester (human or AI) needs: the quality gates, the test inventory, the E2E recipes, and the API-only probes for screens that have no nav link.

**Backend venv:** `backend/.venv/Scripts/python.exe` (`mvnw` is Windows-only; unrelated — this is Python/Node).
**Prereqs:** infra up (`scripts/bootstrap_stack.py`), and for E2E a running backend on :8084.

---

## 1. Quality gates (the definition of done)

Run from the repo root unless noted.

```bash
# --- Backend ---
cd backend
./.venv/Scripts/python.exe -m pytest tests/ -q              # behaviour + coverage floor
./.venv/Scripts/python.exe -m pytest tests/ -q --no-cov     # behaviour only (273 tests)
./.venv/Scripts/python.exe -m ruff check .                  # lint
./.venv/Scripts/python.exe -m mypy .                        # types

# --- Frontend ---
cd frontend
npx tsc --noEmit                                            # types
npm run lint                                                # lint

# --- E2E ---
npx playwright test tests/task7-2-upload-to-output.spec.ts  # upload→output happy path
```

### Expected results (as of the last full run — Phase-10 exit, 9 Oct 2026)
| Gate | Result | Note |
|---|---|---|
| `pytest tests/ -q --no-cov` | **273 passed, 0 failed** | verified live |
| `pytest tests/ -q` | **273 passed, 0 failed, coverage 84.12 % ≥ 80 %** | floor met (10.3) |
| `ruff check .` (backend) | clean | |
| `mypy app/` | **clean — 0 issues in 66 source files** | gate is `app/`; pre-existing `tests/` errors out of scope |
| `npx tsc --noEmit` | clean (exit 0) | |
| `npm run lint` | clean (exit 0) | |
| `npx playwright test` | **33 passed** (1 worker) | needs backend :8084 up; `retries: 1` absorbs a contended first run |
| `python scripts/e2e_phase8.py` | **ALL LENSES PASSED** (Code / Frontend / GST-domain) | the Phase-8 gate |
| `python scripts/e2e_phase10.py` | **ALL LENSES PASSED** (Code / Frontend / GST-domain) | the Phase-10 gate |
| `python scripts/e2e_phase11.py` | **ALL LENSES PASSED** (Code / Frontend / GST-domain) | the Phase-11 gate |

> To get an unambiguous pass count when the pytest summary line vanishes, add `--junitxml=<absolute native path>` and parse the `testsuite` attributes. MSYS `$TMPDIR` is invisible to native python — use a `C:/...` path.

---

## 2. Test inventory

### Backend — `backend/tests/` (43 test modules, 273 tests)
| Area | Modules |
|---|---|
| Auth | `test_auth_otp`, `test_auth_password`, `test_auth_refresh`, `test_auth_totp`, `test_auth_me`, `test_auth_logout_all` |
| Access & tenancy | `test_access_guard`, `test_critical_gstin` |
| Account (PHASE8) | `test_profile_router`, `test_settings_router` |
| GST accounts & models | `test_gst_accounts`, `test_db_models`, `test_migrations`, `test_skeleton`, `test_business_mgmt_overview` |
| Documents & review | `test_documents`, `test_review_flow`, `test_review_service` |
| Extraction | `test_extraction_pipeline`, `test_extraction_preprocess`, `test_extraction_validator`, `test_extraction_worker` |
| Returns | `test_gstr1`, `test_gstr1a`, `test_gstr2b_itc`, `test_returns`, `test_returns_services`, `test_returns_generate` |
| Filing (GSP/IRP) | `test_gsp`, `test_gsp_irp_onboarding`, `test_irp_live_gated`, `test_einvoice` |
| Compliance | `test_dpdp`, `test_notification_prefs`, `test_notifications`, `test_notifications_router` |
| Deadlines | `test_deadline`, `test_critical_deadline` |
| Phase E2E | `test_e2e_phase2`, `test_e2e_phase3` |
| UAT | `test_uat_closed_beta` |

**Helpers (not tests):** `conftest.py`, `auth_helpers.py`, `v4_helpers.py`, `gstin_fixtures.py`. The **shared seed helper is `tests/v4_helpers.py`** — one stale generator there turns the whole tree red, so fix generators there, never per-module.

### Frontend — `frontend/tests/`
| Spec | Covers |
|---|---|
| `auth-onboarding.spec.ts` | email login → shell onboarding, route guards |
| `phase4-filing.spec.ts` | sandbox GSP filing flow, IRN board |
| `phase8-product-shell.spec.ts` | **nav reachability audit** — every Phase-8 screen reachable by clicking from `/app` |
| `phase8-profile.spec.ts` | profile: edit name → save → persists; email read-only |
| `phase8-settings.spec.ts` | settings: prefs toggle persists; TOTP enrollment link; sign-out-everywhere revokes the other session |
| `phase8-businesses.spec.ts` | businesses: add GSTIN → list → detail → edit legal_name; client-side mod-36 checksum rejection |
| `phase8-dashboard-status-grid.spec.ts` | dashboard filing-status grid (filed/draft/pending per period) |
| `phase8-returns-workspace.spec.ts` | generate → status → HSN table → validation checklist → Excel download |
| `task7-2-upload-to-output.spec.ts` | **upload → extract → review → confirm → GSTR-1/3B download** |
| `phase9-review-editor.spec.ts` | 9.1 full-field review editor (all draft fields editable) |
| `phase9-extraction-polling.spec.ts` | 9.2 extraction progress polling on upload |
| `phase9-batch-upload.spec.ts` | 9.2B multi-file batch upload queue (≤10 files) |
| `phase9-dashboard-empty-cta.spec.ts` | 9.3 dashboard empty-state CTA (Link/Register GSTIN) |
| `phase9-itc-dashboard.spec.ts` | 9.4 ITC reconciliation dashboard (/app/itc) |
| `phase9-review-advisory-warn.spec.ts` | 9.5 WARN-tier advisory validation rules |
| `phase10-shell-nav.spec.ts` | 10.2 shared client shell layout (header on workspace routes, no double header) |
| `phase10-live-checksum.spec.ts` | 10.4 live mod-36 checksum feedback on the Add Business form |
| `totp-utils.ts` | TOTP test helper (not a test) |

---

## 3. E2E: upload-to-output (the core product path)

```bash
# backend up first (background, persist_on_release=true)
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --port 8084 --host 127.0.0.1

cd ../frontend && npx playwright test tests/task7-2-upload-to-output.spec.ts
```
The spec registers a throwaway user, creates a GSTIN, drives the UI, triggers dev-mode extraction, confirms, and downloads both returns. It self-boots the dev server (`reuseExistingServer: true`).

### Guided tour (screenshots for a demo)
```bash
cd frontend
NODE_PATH="D:/gst_filing_app/frontend/node_modules" node ../scripts/app_tour.cjs
```
Output → `docs/demo-screenshots/` (PNGs + `tour-report.json`); one `[STEP]` line per verified screen.

### Diagnosing an E2E failure
Trace the network + console (a full-load `goto` drops the in-memory bearer token, so an early `401` then a retry `200` is normal):
```js
page.on("response", r => { if (r.url().includes("/api/v1")) console.log("RESP:", r.status(), r.url()); });
```
Fingerprints seen in this repo:
- `{doc_id}/draft` → **500 `MultipleResultsFound`**: duplicate `invoice_drafts` rows for one job (re-extraction inserted a 2nd row). Fixed by upserting one draft per job in `app/extraction/worker.py`.
- `{doc_id}/confirm` → **401** after a successful draft load: concurrent 401s each called `refreshTokens()` directly, replaying the rotated cookie → `REFRESH_REUSE_DETECTED`. Fixed by routing the retry through the deduplicated `silentRefresh()` in `frontend/lib/api/client.ts`.
- Confirm button `disabled`: only `severity == "BLOCK"` flags should lock it (non-blocking `FLAG`/`SUGGEST`/`AUTO_CORRECT` must not).

---

## 4. API-only probes (features with no screen)

Seeded token required (`scripts/seed_demo.py`). `$T` = access token, `$G` = a GSTIN, `FP=092026`.

```bash
# health
curl -s http://127.0.0.1:8084/api/v1/health

# month summary (invoices + totals)
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/months/$FP/summary"

# returns exports (JSON downloads)
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/months/$FP/gstr1/export.json"
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/months/$FP/gstr3b/export.json"

# review queue, audit, collaborators, notifications, DPDP
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/months/$FP/review-queue"
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/audit"
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/gst-accounts/$G/collaborators"
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/notifications"
curl -s -X POST -H "Authorization: Bearer $T" "http://127.0.0.1:8084/api/v1/me/data/export"   # DPDP; step-up may 403 STEP_UP_REQUIRED
```

**Expected error codes (these are correct behaviour, not bugs):**
| Probe | Expected |
|---|---|
| `POST /me/data/export` with no step-up | `403 STEP_UP_REQUIRED` |
| Upload into a FILED period | `423 PERIOD_LOCKED` |
| Duplicate invoice_no on confirm | `409 DUPLICATE_INVOICE` |
| Second confirm of same doc | `409 ALREADY_CONFIRMED` |
| Invalid GSTIN checksum | `422` with `invalid GSTIN checksum` |
| Bad `fp` (e.g. `132026`) | `422` (path pattern) |
| Phone-format identifier | `422 IDENTIFIER_INVALID` |

---

## 5. GST-domain checks (Lens 3)

For anything touching GSTINs or money:
1. **Checksum:** every GSTIN literal must pass mod-36. Cross-check with `python-stdnum`'s Luhn mod-36 (`luhn.calc_check_digit(first14, alphabet='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')`, weights 1-2-1-2…); a stricter `stdnum.in_.gstin.is_valid` also checks the PAN's 4th-char holder type, so a `False` there on a synthetic fixture does **not** mean the checksum failed. Prefer `app.core.gstin.make_gstin()` for every new fixture.
2. **Paise:** all money columns are integer paise; never compare a paise column to a rupee constant.
3. **Schema contract:** `docs/TECHNICAL_ARCHITECTURE.md` §3 is the governing table-shape contract (some task briefs name stale docs — verify the doc exists before asserting against it).
4. **Guard contract:** `AccessDenied → 404 GSTIN_NOT_FOUND` (existence never leaks); `PermissionDenied → 403`; audit is append-only keyed by gstin.
