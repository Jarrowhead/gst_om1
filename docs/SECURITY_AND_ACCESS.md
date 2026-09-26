# Security & Access Specification

> **Status:** v1.0 (2026-09-26) · Owner doc for AuthN/AuthZ, consent, DPDP compliance, audit, secrets
> **Model detail:** TECHNICAL_ARCHITECTURE.md §3 · **Endpoints:** API_SPECIFICATION.md

---

## 1. Authentication

| Concern | Control |
|---|---|
| Identity | Mobile number (OTP-verified) primary; email+password optional secondary. One person = one `users` row. GSTIN is NEVER a login identifier (printed on every invoice — semi-public) |
| OTP | 6-digit, 5-minute expiry, Redis-stored with attempt counter; rate limit 5/hour/identifier; dev mode = email OTP (SMS provider Phase 1) |
| Session | JWT access 15 min + rotating refresh token 7 days; refresh rotation detected (reuse of a rotated token kills the family) |
| Passwords | argon2id; only for users who opt into password login; never required |
| 2FA (CA) | **TOTP mandatory for every ca_firm_member** — enforced at firm-join, not optional. Setup flow: QR + verify code before membership activates |
| Step-up auth | Fresh OTP required for: export generation, link revocation, member invites, DPDP erasure, TOTP reset |
| Brute force | Per-identifier + per-IP limits in Redis; exponential backoff; lockout notification via email |

## 2. Authorization

| Rule | Enforcement |
|---|---|
| Client users see only businesses where `business_users` grants them OWNER/CLERK | Single FastAPI dependency (`require_business_access`) — never per-route ad-hoc checks |
| CA firm members see only businesses with an ACTIVE `ca_client_links` row for their firm | Same dependency resolves member → firm → link → business → registration |
| Granular firm permissions: `can_export`, `can_revoke`, `can_invite_members` | Checked by the same dependency, declared per route |
| A CA cannot browse unlinked businesses: GSTIN search reveals only "send request" availability, zero business data | Enforced at service layer; covered by an explicit test |
| Every filing object resolves through a `registration_id` → registration → business → access chain | No endpoint accepts a raw registration/business id without the guard |
| Locked periods: FILED periods reject invoice/CDN mutations; amendments are delta records only | Service-layer rule + test |

## 3. Data protection

| Concern | Control |
|---|---|
| Transport | TLS everywhere (dev: localhost, self-signed permitted; prod: real certs, HSTS) |
| At rest | PG + MinIO on-host volumes encrypted at the disk level; LLM raw outputs (which contain bill content) stored in the `extraction` schema, access-guarded identically |
| Documents | MinIO presigned URLs, 30-min expiry; no public buckets; keys `{gstin}/{fp}/{docId}` |
| Integrity | sha256 on every document at upload; versioned bucket + `mc mirror` replication + integrity-verify job |
| Secrets | `.env` (never committed) + `.env.example` maintained; redacted tool output restored byte-exact from git/`.bak` per standing rule |
| PII minimization | OCR text retained (evidentiary), never rendered cross-business; supplier/buyer contact details stored only as needed for returns |

## 4. Consent & DPDP Act 2023

| Obligation | Implementation |
|---|---|
| Lawful basis | `consent_records` at link accept (both flows) — purpose-named, version-numbered consent text |
| Withdrawal | Link revoke = instant access death; consent `withdrawed_at` recorded; DPDP erasure request flows below |
| Data-principal rights | (a) Self-service export: all business data machine-readable (JSON+CSV+documents manifest); (b) erasure request → workflow with CA-firm notification, 30-day SLA, legal-retention carve-out documented (GST records must be retained — erasure applies to platform copies beyond statutory retention, default 8 FYs) |
| Breach | 72-hour notification runbook (detect → assess → notify principals + firm) in docs/COMPLIANCE runbook section of this doc's Phase-3 update |
| Roles | CA firm = data fiduciary; platform = data processor; contract template delivered Phase 3 |
| Retention | Default 8 FYs (GST law floor); retention job flags + purge workflow Phase 3 |

## 5. Audit

| Event | Logged fields |
|---|---|
| Link request / accept / reject / revoke | actor, firm, business, consent version |
| Document upload / view / download | actor, registration, doc |
| Invoice confirm / edit | actor, payload_diff (JSONB) |
| Export generation / download | actor, registration, fp, schema_version |
| Amendment create / export | actor, deltas |
| Member join / permission change | actor, firm, target member |
| Auth events: login, OTP fail-storm, TOTP enable | actor, ip, user-agent |

Audit rows are append-only; no update/delete paths exist in code; DB role has INSERT-only grant on `audit_logs` (migration-enforced).

## 6. Application hardening

| Layer | Control |
|---|---|
| API | Pydantic validation on every input (AI mistakes fail fast); request size caps (upload 25 MB/file); CORS locked to the frontend origin |
| Uploads | Magic-byte type check (not extension); page-count cap; sha256 dedupe |
| LLM calls | Pinned model; prompt-injection defense: OCR text is wrapped as untrusted data, model output validated against the Pydantic extraction schema, unknown fields dropped |
| Rate limits | Global + per-user (Redis); extraction queue caps |
| Dependencies | `pip-audit` in CI; lockfile committed |
| Frontend | No tokens in localStorage (httpOnly refresh cookie); CSP headers |

## 7. Testing the security layer

| Test | Type |
|---|---|
| OTP rate limit + expiry | Unit + integration |
| JWT refresh rotation + reuse kill | Integration |
| TOTP enforcement for firm members | Integration |
| Cross-tenant access attempts (CA→unlinked business, client→other business) return 403/404, never data | Integration, matrix-parameterized |
| Locked-period mutation rejection | Integration |
| GSTIN search on unlinked business leaks no data | Integration (response-shape assertion) |
| Audit append-only (UPDATE/DELETE fails) | Migration test |
| Step-up auth on export/revoke/invite/erasure | Integration |

See TESTING_STRATEGY.md §4 for the full gate wiring.