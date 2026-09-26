# Product Requirements Document (PRD)

> **Status:** v1.0 (2026-09-26) · Owner doc for product scope, journeys, and acceptance criteria
> **System design:** see TECHNICAL_ARCHITECTURE.md · **Extraction quality:** see EXTRACTION_SPEC.md

---

## 1. Problem

| Today (WhatsApp + Excel) | Cost |
|---|---|
| Business owners share GST bills with CAs as WhatsApp photos/scans | Lost files, no month-wise organization, no history |
| CA's clerk manually retypes every invoice into the GST portal | Hours per client per month; typing errors become filed-return errors |
| Client cannot see status; CA chases via calls | Late GSTR-1/3B → ₹50/day late fee, 18% interest, ITC loss for buyers |
| Corrections after filing are ad-hoc | Legal exposure since 3B outward figures locked (Jul-2025 rule) |

**One line:** businesses upload bills once; the platform extracts, organizes month-wise, and produces portal-ready GST returns; the CA reviews and files — replacing the WhatsApp+retyping loop.

---

## 2. Personas

| Persona | Who | Needs | Success looks like |
|---|---|---|---|
| **Business owner** ("Client") | Runs a business with 1+ GSTINs; not a tax expert | Dump photos of bills monthly; know it's handled | Upload in minutes, see month status + deadline countdown, zero retyping |
| **Accounts clerk** (client side) | Does the bill collection | Fast multi-upload; clear review queue | All bills for the month confirmed before deadline |
| **CA partner** | Owns a CA firm; accountable for filings | Roster of all clients with per-GSTIN status; final review; export | Month-end for 30 clients in hours, not weeks |
| **CA / articled clerk** (firm side) | Does the grind work | Review queue, reconciliation report, bulk onboarding | No retyping; unmatched ITC list is actionable |

---

## 3. Goals & non-goals

| Goals (this product) | Non-goals (explicitly out) |
|---|---|
| Month-wise document organization per GSTIN | Being an accounting/bookkeeping system (no ledger, no Tally replacement) |
| Automated extraction → review → confirmed invoice ledger | Automated *filing* on gst.gov.in before Phase 4 (GSP) — Phase 1 delivers portal-ready JSON |
| GSTR-1 offline-utility JSON (≤ ₹5 Cr clients) and IRP e-invoice flow (> ₹5 Cr) | Payroll, income tax, TDS |
| GSTR-3B prep + GSTR-2B ITC reconciliation | Fabricating demo GSTINs/returns (illegal) |
| CA-firm ↔ client consent-based linking, both directions | gst.gov.in scraping (CAPTCHA + legal + IP blocks) |
| Deadline engine + reminders | Composition-scheme *CMP-08 payment computation* in MVP (tracked as reminder only until Phase 3) |

---

## 4. Core user journeys

### 4.1 Client onboarding

| Step | Actor | Action | Acceptance |
|---|---|---|---|
| 1 | Client | Sign up with mobile (OTP) | Account verified; JWT issued |
| 2 | Client | Create business: legal name, PAN | PAN validated (regex + structure) |
| 3 | Client | Add GST registration(s): GSTIN(s) | 15-char regex + mod-36 checksum; PAN==GSTIN[2..12]; duplicates rejected |
| 4 | System | Classify scheme + `irn_applicable` from AATO | Monthly/QRMP/Composition tagged; > ₹5 Cr flagged IRN |

### 4.2 CA-firm ↔ client linking (both flows, consent-based)

| Step | Flow A — firm requests by GSTIN | Flow B — client invites via code |
|---|---|---|
| 1 | Firm member enters client GSTIN | Client opens "My CA" → generate invite code |
| 2 | GSTIN validated; NO business data revealed for unlinked GSTINs | Code valid 7 days, single-use, shown once |
| 3 | Client notified → Accept (consent recorded) / Reject | Firm member redeems → link ACTIVE |
| 4 | Firm sees ALL registrations of that business | Same |
| 5 | Client may revoke anytime; access dies instantly; audit logged | Same |

### 4.3 Monthly cycle (the heart of the product)

| Step | Actor | Action | Acceptance |
|---|---|---|---|
| 1 | Client/clerk | Select GSTIN + month → multi-upload (photos/scans, multi-burst→one doc) | Files stored (versioned, sha256); jobs queued |
| 2 | System | Preprocess (photo branch: rotate/upscale/blur-reject) → OCR → LLM extract → validate | Per-doc status visible; blurry photos rejected with re-upload prompt |
| 3 | System | Auto-confirm all-clear docs; queue the rest for review | G3 gate ≥0.99 on auto-confirm subset (EXTRACTION_SPEC §4) |
| 4 | Client/CA | Review: original image beside extracted fields; amber low-confidence | Confirm → invoice ledger |
| 5 | System | Month dashboard: taxable, CGST/SGST/IGST, RCM, ITC-books, days-to-deadline | Totals live-update per confirmation |

### 4.4 Return preparation

| Path | Who | Flow |
|---|---|---|
| JSON path (AATO ≤ ₹5 Cr) | CA | Prepare → guard (0 pending reviews) → GSTR-1 JSON generated + self-validated → download → upload at gst.gov.in → mark FILED (locks period) |
| IRN path (AATO > ₹5 Cr) | CA | Confirmed B2B invoices + CDNs → IRP (sandbox in dev) → IRN + signed QR stored; GSTR-1 auto-populates |
| 3B (all regular) | CA | Outward figures auto-built (locked rule respected); 2B imported → ITC reconciliation → 3B export |
| Amendment | CA | Post-filing correction → GSTR-1A delta record; original never edited |

---

## 5. Feature list by phase (with acceptance criteria)

| Phase | Features | Acceptance criteria (phase exit) |
|---|---|---|
| **0 — Skeleton** | Repo + CI; PG/Redis/MinIO up; auth (OTP dev=email, JWT, TOTP for CA); full v2 data model migrations; both login shells | Client + CA-firm member can register/login; TOTP enforced for firm members; migrations run clean both ways; CI green |
| **1 — Capture & correlation** | Upload (scan+photo branches) → extraction pipeline → review → confirm; linking flows A+B; GSTIN search; bulk CSV onboarding; dashboards; sandbox IRP adapter; composition flag | E2E: photo-burst upload → auto/confirmed ledger; both linking flows work; 5-client CSV import with error report; sandbox IRN generated |
| **2 — Returns engine** | GSTR-1 JSON (pinned schema + self-validator + doc_issue + nil returns); CDNR/CDNUR; GSTR-3B build; GSTR-2B import + ITC reconciliation; GSTR-1A; deadline engine | JSON passes pinned-schema contract test; ITC report matches a manual CA reconciliation on a test client; golden set G1 ≥0.90 / G2 ≥0.95 / G3 ≥0.99 |
| **3 — Firm scale & compliance** | Multi-client dashboard; member permissions; audit UI; DPDP export/erasure; retention job; notifications (email + WhatsApp); CMP-08/GSTR-4 path | CA firm with 5 test businesses completes month-end < 30 min; DPDP export/erasure demo passes |
| **4 — Direct filing** | GSP adapter (live filing + 2B auto-fetch); Live IRP (real GSTIN + credentials) | Sandbox GSP filing succeeds end-to-end |
| **5 — Advanced** | Analytics; accounting-system import (Tally/Zoho books); buyer-side reconciliation | — |

---

## 6. Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Extraction precision (mandatory fields) | G1 ≥ 0.90 · G2 ≥ 0.95 · **G3 ≥ 0.99** | `scripts/measure_extraction.py` in CI (golden set) |
| Post-review ledger accuracy | ≥ 0.995 | E2E golden-set test |
| GSTR-1 JSON portal-acceptance | Zero schema rejections | Pinned-schema contract test + real upload when test GSTIN exists |
| CA month-end time per client | < 30 min at 5 clients (Phase 3 exit) | Timed E2E scenario |
| Client upload-to-confirmed | < 24h with < 20% needing review (post-tuning) | Pipeline telemetry |

---

## 7. Constraints & standing rules

| Rule | Detail |
|---|---|
| No fabricated GSTINs/PANs | Ever. Demo data = "registration pending" placeholders (offense under GST Act) |
| No fake data in demos | Fintech-grade honesty: placeholder markers, not invented tax numbers |
| Money | Integer paise everywhere; rupees only at display/JSON boundary |
| Period | `fp = MMYYYY` from invoice date; Apr–Mar fiscal year |
| Build method | AI-first — see AI_BUILD_PLAYBOOK.md; team reviews, AI writes all code |