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
| Access guard | every GSTIN-scoped route passes `require_gstin_access` (SECURITY §2) |

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
| GET | `/auth/me` | (JWT) | `{user, gst_accounts: [{gstin, role, legal_name}]}` | returns user and all accessible GSTINs with roles |

## 2. Users & GST Accounts — `/me/profile`, `/gst-accounts`

| Method | Path | Notes |
|---|---|---|
| GET | `/me/profile` | (JWT) own profile; same user shape as `/auth/me`'s `data.user`; email read-only (PHASE8 8.1) |
| PATCH | `/me/profile` | (JWT) `{full_name}` only — any other field (incl. `email`) → 422, never silent; audit `PROFILE_UPDATED` |
| GET | `/me/settings` | (JWT) consolidated settings aggregate: `notification_preferences`, `totp_enabled` (bool), `session.active_sessions` (live Redis refresh-family count) + `session.refresh_ttl_days` (PHASE8 8.2) |
| PATCH | `/me/settings` | (JWT) **delegates to the `/me/notification-prefs` handler** — body IS the notification-prefs payload; no duplicated write logic; response = what GET returns |
| POST | `/gst-accounts` | `{gstin, legal_name, trade_name?, registered_address?, aato_minor?}` — full GSTIN validation (mod-36 + PAN extraction from chars 3-12); sets `filing_scheme`, `irn_applicable`; creator becomes ADMIN |
| GET | `/gst-accounts` | my GSTINs (via user_gst_access) with role |
| GET | `/gst-accounts/{gstin}` | detail incl. scheme/irn flags |
| PATCH | `/gst-accounts/{gstin}` | (FILER/ADMIN) edit `legal_name`, `trade_name`, `registered_address`, `aato_minor`, `filing_scheme`; audit on every write (PHASE8 8.3) |
| GET | `/gst-accounts/{gstin}/overview` | account detail + this-FY filing status per period (`gstin`, `fy`, `detail`, `periods[]`) (PHASE8 8.3) |
| GET | `/gst-accounts/{gstin}/periods?fy=` | filing_periods with due dates + status |
| GET | `/gst-accounts/{gstin}/months/{fp}/summary` | month card data: doc counts, ledger totals, deadline, nil flag |
| POST | `/gst-accounts/{gstin}/collaborators` | `{email, role: ADMIN/FILER/VIEWER}` — invite another user to this GSTIN |
| GET | `/gst-accounts/{gstin}/collaborators` | list users with access |
| DELETE | `/gst-accounts/{gstin}/collaborators/{userId}` | revoke access (ADMIN only); audit logged |

## 3. (Removed) CA firms & Client-side linking

> **v4.0:** Sections 3 and 4 (CA firms, client-side linking) have been removed. GSTIN access management is now handled via `/gst-accounts/{gstin}/collaborators` endpoints in Section 2.

## 5. Documents & extraction — `/documents`

| Method | Path | Notes |
|---|---|---|
| POST | `/gst-accounts/{gstin}/months/{fp}/documents` | multipart; ≤25 MB/file; multi-file photo burst → one document; magic-byte check; sha256; capture_source param |
| POST | `/gst-accounts/{gstin}/months/{fp}/bulk-import` | (PHASE11 11.1) multipart `.xlsx`/`.csv` → draft queue. `confirm=false` (default) = column auto-map preview (`mapping`, `unmapped_headers`, `required_missing`, `sample`, `data_rows`), writes nothing. `confirm=true` = bulk-ingest one `extraction.invoice_drafts` row per valid line (`source=BULK_IMPORT`, no extraction job) + per-row report (`rows[]` `IMPORTED|INVALID` with `errors`/`flags`, `imported`, `invalid`, `error_report[]`); a bad row never aborts the file. 422 while a required column is unmapped or `mapping` is not a JSON object of `{canonical_field: header}`; 423 on a FILED period; 404 unknown period; 403 for VIEWER |
| GET | `/gst-accounts/{gstin}/months/{fp}/documents` | list + job status per doc |
| GET | `/documents/{docId}` | detail: statuses, preproc_report, confidence summary |
| GET | `/documents/{docId}/image?page=` | presigned URL (30 min) |
| GET | `/gst-accounts/{gstin}/months/{fp}/review-queue` | NEEDS_REVIEW docs, confidence-ascending |
| GET | `/documents/{docId}/draft` | draft fields + per-field confidence |
| PUT | `/documents/{docId}/draft` | edit fields → server validator re-runs |
| POST | `/documents/{docId}/confirm` | draft → invoices/invoice_lines; rejects if validator dirty |
| POST | `/documents/{docId}/reject` | failed-extraction path (e.g., blurry photo) |

## 6. Invoice ledger — `/gst-accounts/{gstin}/…`

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
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr1/prepare` | guard: 0 pending reviews; dual-path by irn_applicable; returns section summary + validation errors |
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr1/generate` | step-up; writes export record + MinIO JSON; 422 if validator errors |
| POST | `/gst-accounts/{gstin}/months/{fp}/returns/generate` | (PHASE8 8.4) idempotent orchestration — prepares + generates GSTR-1 **and** GSTR-3B in one call; returns per-return status envelope |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr1/export.json` | GSTR-1 JSON payload |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr1/export.xlsx` | (PHASE8 8.4) real `.xlsx` (openpyxl, OSS) — same numbers as the JSON: one payload, rendered twice; paise→₹ only at render |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr3b/export.json` | GSTR-3B JSON payload |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr3b/export.xlsx` | (PHASE8 8.4) real `.xlsx` — same contract as gstr1/export.xlsx |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr1/hsn-summary` | (PHASE8 8.4) HSN summary table; totals reconcile to the GSTR-1 payload |
| GET | `/gst-accounts/{gstin}/months/{fp}/returns/validation` | (PHASE8 8.4) pre-filing error-catcher: `{blocking: [], warnings: []}` — blocking entries must be resolved before file |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr1/exports` | history (immutable) |
| GET | `/exports/{exportId}/download` | step-up; presigned JSON URL |
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr1/nil` | nil-return JSON |
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr1a` | amendment delta (post-FILED only) |
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr3b/prepare` | outward auto-build + ITC prefill |
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr3b/generate` | step-up |
| POST | `/gst-accounts/{gstin}/months/{fp}/filed` | marks FILED → locks period (423 on any later mutation) |

## 8. e-Invoicing (IRN path) — `/einvoice`

| Method | Path | Notes |
|---|---|---|
| POST | `/invoices/{invId}/irn` | sandbox adapter (dev) / live (gated); idempotent; stores e_invoices row |
| POST | `/einvoices/{irnId}/cancel` | within 24h window |
| GET | `/gst-accounts/{gstin}/months/{fp}/einvoices` | IRN status board |

## 9. ITC — `/itc`

| Method | Path | Notes |
|---|---|---|
| POST | `/gst-accounts/{gstin}/months/{fp}/gstr2b/import` | portal 2B JSON upload → statement + entries |
| GET | `/gst-accounts/{gstin}/months/{fp}/gstr2b` | imported statements |
| POST | `/gst-accounts/{gstin}/months/{fp}/itc/reconcile` | runs engine → 5-status rows |
| GET | `/gst-accounts/{gstin}/months/{fp}/itc/report?status=` | reconciliation report |

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
|| GET | `/me/notification-prefs` | get channels per event type |
|| PATCH | `/me/notification-prefs` | update channels per event type |

---

## Non-negotiables for AI builders

| # | Rule |
|---|---|
| 1 | Every GSTIN-scoped route resolves access through `require_gstin_access` — no exceptions, no inline checks |
| 2 | 404 over 403 for other-tenant resources (existence is data) |
| 3 | Paise ints in, paise ints out — no float ever crosses the boundary |
| 4 | Responses match this doc's shapes; Pydantic models in `app/api/schemas.py` are the compiled contract, and the frontend types are generated from them |
| 5 | Locked periods return 423 with the lock reason — silently editing is a build-breaking bug |
| 6 | Step-up endpoints verify `X-OTP`/stepup_token — listed above — before doing the sensitive thing |