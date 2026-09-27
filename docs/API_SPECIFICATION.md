# API Specification

> **Status:** v1.0 (2026-09-26) · Owner doc for the FastAPI backend endpoint contract. AI agents code against THIS doc; drift between code and this doc is a build defect.
> **Auth model:** SECURITY_AND_ACCESS.md §1–2 · **Screens consuming these:** FRONTEND_SPECIFICATION.md

---

## Conventions

| Rule | Detail |
|---|---|
| Base | `http://localhost:8084/api/v1` |
| Auth | `Authorization: Bearer <jwt>`; step-up endpoints additionally accept/require `X-OTP` |
| Envelope | `{"success": true, "data": …}` / `{"success": false, "error": {"code": "…", "message": "…"}}` |
| Money | integer paise, always |
| Periods | `fp = "MMYYYY"` strings |
| IDs | UUIDs (except `doc_id`-style human keys in extraction fixtures only) |
| Pagination | `?page=0&size=20` → `{"content": […], "page": 0, "size": 20, "totalElements": N, "last": bool}` |
| Errors | 401 unauthenticated · 403 unauthorized · 404 not-found-or-no-access (never leak existence) · 422 validation · 409 conflict · 423 locked period |
| Access guard | every business/registration-scoped route passes `require_business_access` (SECURITY §2) |

---

## 1. Auth — `/auth`

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| POST | `/auth/otp/request` | `{identifier: mobile-or-email, purpose: LOGIN\|REGISTER}` | `{otp_sent: true, dev_otp?: "…"}` | dev_otp present only in dev mode; rate limit 5/h |
| POST | `/auth/otp/verify` | `{identifier, otp}` | `{user?, access_token, refresh_token}` | New user auto-created on REGISTER purpose |
| POST | `/auth/refresh` | refresh cookie | `{access_token}` | rotation; reuse kills family |
| POST | `/auth/stepup` | (JWT) + `{otp}` | `{stepup_token}` | 15-min step-up proof for sensitive routes |
| POST | `/auth/totp/setup` | (JWT) | `{secret, qr_uri}` | client-side verify before enabling |
| POST | `/auth/totp/verify` | (JWT) + `{code}` | `{enabled: true}` | mandatory before firm join |
| GET | `/auth/me` | (JWT) | `{user, businesses?, firm?}` | role-resolved profile |

## 2. Users & businesses — `/users`, `/businesses`

| Method | Path | Notes |
|---|---|---|
| GET/PATCH | `/users/me` | profile update |
| POST | `/businesses` | `{legal_name, pan, trade_name?}` — PAN validated |
| GET | `/businesses` | my businesses (via business_users) |
| GET | `/businesses/{id}` | detail incl. registrations |
| POST | `/businesses/{id}/registrations` | `{gstin, registered_address?, aato_minor? (paise, int)}` — full GSTIN validation + PAN==GSTIN[2..12]; sets `filing_scheme`, `irn_applicable` |
| GET/PATCH | `/registrations/{regId}` | incl. scheme/irn flags |
| GET | `/registrations/{regId}/periods?fy=` | filing_periods with due dates + status |
| GET | `/registrations/{regId}/months/{fp}/summary` | month card data: doc counts, ledger totals, deadline, nil flag |

## 3. CA firms — `/firm`

| Method | Path | Notes |
|---|---|---|
| POST | `/firm` | `{firm_name, pan}` — creates firm + PARTNER membership; requires TOTP enabled |
| GET | `/firm` | my firm + members |
| POST | `/firm/members/invite` | step-up; invite email/mobile |
| POST | `/firm/members/{inviteId}/accept` | (JWT of invitee, TOTP verified) |
| PATCH | `/firm/members/{userId}` | step-up; roles/permissions |
| GET | `/firm/clients?query=&status=` | roster: businesses linked to firm; GSTIN prefix + name search |
| POST | `/firm/clients/request` | `{gstin}` → PENDING link (Flow A); reveals NO business data |
| POST | `/firm/clients/redeem` | `{invite_code}` → ACTIVE link (Flow B) |
| POST | `/firm/clients/{businessId}/revoke` | step-up; firm-side revoke |
| POST | `/firm/import/dry-run` | CSV upload → per-row validation report, nothing committed |
| POST | `/firm/import/commit` | step-up; batch create + invite codes |

## 4. Client-side linking — `/links`

| Method | Path | Notes |
|---|---|---|
| GET | `/links` | my incoming firm requests + active links (firm name, status, consent version) |
| POST | `/links/invite-code` | generate code for a business (7-day, single-use) |
| POST | `/links/{linkId}/accept` | consent record created with version |
| POST | `/links/{linkId}/reject` | |
| POST | `/links/{linkId}/revoke` | step-up; instant access death |

## 5. Documents & extraction — `/documents`

| Method | Path | Notes |
|---|---|---|
| POST | `/registrations/{regId}/months/{fp}/documents` | multipart; ≤25 MB/file; multi-file photo burst → one document; magic-byte check; sha256; capture_source param |
| GET | `/registrations/{regId}/months/{fp}/documents` | list + job status per doc |
| GET | `/documents/{docId}` | detail: statuses, preproc_report, confidence summary |
| GET | `/documents/{docId}/image?page=` | presigned URL (30 min) |
| GET | `/registrations/{regId}/months/{fp}/review-queue` | NEEDS_REVIEW docs, confidence-ascending |
| GET | `/documents/{docId}/draft` | draft fields + per-field confidence |
| PUT | `/documents/{docId}/draft` | edit fields → server validator re-runs |
| POST | `/documents/{docId}/confirm` | draft → invoices/invoice_lines; rejects if validator dirty |
| POST | `/documents/{docId}/reject` | failed-extraction path (e.g., blurry photo) |

## 6. Invoice ledger — `/registrations/{regId}/…`

| Method | Path | Notes |
|---|---|---|
| GET | `/months/{fp}/invoices?direction=&status=&page=` | ledger |
| GET | `/invoices/{invId}` | with lines |
| PATCH | `/invoices/{invId}` | only while DRAFT & period not FILED (else 423) |
| POST | `/invoices` | manual entry (no doc) — validator runs identically |
| GET/POST | `/months/{fp}/cdns` | credit/debit notes CRUD (same lock rule) |
| GET/POST | `/series` | document_series management |
| GET | `/gstin-lookup?gstin=` | validity check + state code ONLY (no business data) |

## 7. Returns — `/returns`

| Method | Path | Notes |
|---|---|---|
| POST | `/registrations/{regId}/months/{fp}/gstr1/prepare` | guard: 0 pending reviews; dual-path by irn_applicable; returns section summary + validation errors |
| POST | `/registrations/{regId}/months/{fp}/gstr1/generate` | step-up; writes export record + MinIO JSON; 422 if validator errors |
| GET | `/registrations/{regId}/months/{fp}/gstr1/exports` | history (immutable) |
| GET | `/exports/{exportId}/download` | step-up; presigned JSON URL |
| POST | `/registrations/{regId}/months/{fp}/gstr1/nil` | nil-return JSON |
| POST | `/registrations/{regId}/months/{fp}/gstr1a` | amendment delta (post-FILED only) |
| POST | `/registrations/{regId}/months/{fp}/gstr3b/prepare` | outward auto-build + ITC prefill |
| POST | `/registrations/{regId}/months/{fp}/gstr3b/generate` | step-up |
| POST | `/registrations/{regId}/months/{fp}/filed` | marks FILED → locks period (423 on any later mutation) |

## 8. e-Invoicing (IRN path) — `/einvoice`

| Method | Path | Notes |
|---|---|---|
| POST | `/invoices/{invId}/irn` | sandbox adapter (dev) / live (gated); idempotent; stores e_invoices row |
| POST | `/einvoices/{irnId}/cancel` | within 24h window |
| GET | `/registrations/{regId}/months/{fp}/einvoices` | IRN status board |

## 9. ITC — `/itc`

| Method | Path | Notes |
|---|---|---|
| POST | `/registrations/{regId}/months/{fp}/gstr2b/import` | portal 2B JSON upload → statement + entries |
| GET | `/registrations/{regId}/months/{fp}/gstr2b` | imported statements |
| POST | `/registrations/{regId}/months/{fp}/itc/reconcile` | runs engine → 5-status rows |
| GET | `/registrations/{regId}/months/{fp}/itc/report?status=` | reconciliation report |

## 10. DPDP data-principal rights — `/me/data`

| Method | Path | Notes |
|---|---|---|
| POST | `/me/data/export` | step-up; async job → downloadable archive manifest (JSON+CSV+doc URLs) |
| GET | `/me/data/export/{jobId}` | job status + download |
| POST | `/me/data/erasure-request` | step-up; workflow + firm notification; retention carve-outs applied |

## 11. Notifications — `/notifications`

| Method | Path | Notes |
|---|---|---|
| GET | `/notifications?unread=` | in-app list |
| POST | `/notifications/{id}/read` | |
| PATCH | `/me/notification-prefs` | channels per event type |

---

## Non-negotiables for AI builders

| # | Rule |
|---|---|
| 1 | Every registration-scoped route resolves access through the guard dependency — no exceptions, no inline checks |
| 2 | 404 over 403 for other-tenant resources (existence is data) |
| 3 | Paise ints in, paise ints out — no float ever crosses the boundary |
| 4 | Responses match this doc's shapes; Pydantic models in `app/api/schemas.py` are the compiled contract, and the frontend types are generated from them |
| 5 | Locked periods return 423 with the lock reason — silently editing is a build-breaking bug |
| 6 | Step-up endpoints verify `X-OTP`/stepup_token — listed above — before doing the sensitive thing |