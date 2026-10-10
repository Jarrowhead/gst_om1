# Phase 11–13 — Investor-Recommendation Build (Tiers 1–3)

> Source: investor/product recommendation doc (pasted by Tony, 10 Oct 2026).
> This is a **new feature-set request** appended as three additive phases to the
> existing 71-task ledger. Nothing existing is removed or retired. Plan/state
> surgery + relaunch follow this doc.

## 1. Gap analysis — what already exists vs. what the recommendation asks for

Verified against the live tree (routers, services, models, frontend pages), not
the recommendation's assumptions.

| # | Recommendation (tier) | Current state in the app | Verdict | Rationale |
|---|---|---|---|---|
| 1 | Bulk Excel / Tally-Busy CSV importer + column auto-mapper (T1) | **Missing.** `documents` router has single/multi-file PDF/OCR upload only; no spreadsheet ingestion. `openpyxl` already in `backend/pyproject.toml`. | ✅ BUILD | Free/OSS (openpyxl + stdlib `csv`). Ingests into existing `InvoiceDraft` bulk flow. Money = integer paise. |
| 2 | 1-click Batch Confirm for high-confidence invoices (T1) | **Missing.** `review-queue`, per-draft `confirm` exist; `confidence_avg` (`Numeric(4,3)`) and validation flags already computed. No bulk action. | ✅ BUILD | Data foundation present; only a bulk endpoint + `BATCH_CONFIRM` audit row + button. |
| 3 | Interactive Rule 88A set-off matrix + PMT-06 challan preview (T1) | **Missing.** `returns` router computes GSTR-3B liability + ITC but has no set-off-order matrix and no challan summary. | ✅ BUILD | Pure computation, no paid dependency. **Caveat:** a real CPIN is generated on the GST portal — we render the cash-deposit summary + a portal link, not a fabricated CPIN. |
| 4 | Maker-Checker sign-off + period lock/unlock (T2) | **Partial.** `AccessRole` ADMIN/FILER/VIEWER + collaborators exist (`gst_accounts` router); `filed` already yields 423 `PERIOD_LOCKED`. No approval state machine, no soft freeze/unlock. | ✅ BUILD (additive) | Built **on top of** the existing single-user-type + collaborator-role model — no CA/owner split introduced. Freeze is a NEW soft-lock state distinct from FILED. |
| 5 | Vendor GSTR-2B discrepancy + 1-click follow-up notice (T2) | **Partial.** ITC reconciliation (`itc` router) already flags `MISSING_IN_2B` / `UNMATCHED` (`MatchStatus`). No notice generator. | ✅ BUILD | Notice/WhatsApp-draft generation is free text templating + a `wa.me` link. |
| 6 | E-Way Bill 1-click generation (T3) | **Missing.** E-Invoice/IRN sandbox exists (`generate_sandbox_irn`); no EWB. | ✅ BUILD (sandbox) | Same sandbox pattern as IRN/GSP. Distance via OSRM (free) with a deterministic fallback. |
| 7 | Inward invoicing via email + WhatsApp forwarding (T3) | **Missing.** | ⛔ DEFER | Not in the rec's own sprint matrix. Email half needs self-hosted mail infra (Postfix/Dovecot/IMAP) + per-GSTIN aliases; WhatsApp Business API is a **paid** Meta product → violates the free/OSS-only standing constraint. Highest infra lift, lowest ROI. |

## 2. Scope decision

- **Build now (6):** recs 1, 2, 3, 4, 5, 6 → Phases 11, 12, 13.
- **Defer (1):** rec 7 (email/WhatsApp ingestion) — revisit after Tier 1/2 ship; email half is feasible via self-hosted mail only, WhatsApp needs a paid gateway.

All work is **additive**: no route, screen, testid, or model field is removed. Prior E2E
specs (`download-gstr1`, `download-gstr3b`, `gst-account-card`, `upload-link`, review/confirm
testids) must keep passing.

## 3. Per-task plan (3 phases, 9 tasks)

### Phase 11 — Tier 1: Core Workflow Accelerators
- `11.1` Bulk Excel/CSV import + column auto-mapper (rec 1)
- `11.2` Batch confirm for high-confidence invoices (rec 2)
- `11.3` Rule 88A set-off matrix + PMT-06 challan preview (rec 3)
- `11.4` Phase-11 E2E + full regression (3-lens) + tracker regen

### Phase 12 — Tier 2: CA Practice & Enterprise Controls
- `12.1` Maker-checker sign-off + period lock/unlock (rec 4)
- `12.2` Vendor GSTR-2B discrepancy notice generator (rec 5)
- `12.3` Phase-12 E2E + full regression (3-lens) + tracker regen

### Phase 13 — Tier 3: Logistics Moat
- `13.1` E-Way Bill sandbox generation from outward invoices (rec 6)
- `13.2` Phase-13 E2E + full regression (3-lens) + tracker regen

## 4. Verification gates (per phase, same 3-lens standard as phases 6/8/9/10)

1. **Code lens:** `pytest tests/ -q` (full suite, default `--cov=app --cov-fail-under=80`
   floor) + `ruff check .` + `mypy .` — all clean, no `--no-cov` escape.
2. **Frontend/user lens:** `npx tsc --noEmit` + `npm run lint` + Playwright user-journey
   for any new screen; every new route must be **reachable** (nav/Link), not orphaned.
3. **GST-domain lens:** integer-paise everywhere, mod-36 GSTIN checksums (never hand-typed
   literals), audit rows append-only keyed by `gstin`, additive route decorators only
   (`git show <commit> -- <router>.py` must only ADD `@router.*`).

Each phase's E2E task ships `scripts/e2e_phase<N>.py` (run via
`uv run --project backend python scripts/e2e_phase<N>.py`, `--no-cov` on targeted pytest)
and regenerates `docs/BUILD_TRACKER.md` via `scripts/generate_tracker.py`.

## 5. Agent assignment (unchanged — Tony's standing set, do NOT edit)

Verified from `scripts/build_loop.py` + `build/role_models.json` (swapped 9 Oct, commit `2ebf8ea`):
- Builder = `deepseek-v4.1-flash` (ollama-cloud) → `kimi-k3` → `kimi-k2.7-code`
- Tester = `minimax-m2.7` (ollama-cloud) → `glm-5.3-flash` → `kimi-k2.6`
- Monitor = `glm-5.3` (ollama-cloud) — independent of tester's primary
- Feedback = `glm-5.3` (ollama-cloud) → `kimi-k2.7-code`

## 6. Deferred item (rec 7) — honest caveats

Email ingestion is **feasible free/OSS** via a self-hosted mail stack (Postfix/Dovecot or a
polling IMAP client against any mailbox) + a per-GSTIN alias rewrite into MinIO and the
extraction queue. It is **deferred**, not cancelled, because: (a) it is absent from the
recommendation's own Sprint 1/2/3 matrix, (b) the WhatsApp leg requires a paid gateway
(WhatsApp Business API), and (c) it is the highest infra lift of the seven. Reopen on
explicit instruction.
