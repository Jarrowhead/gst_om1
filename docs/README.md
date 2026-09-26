# GST Filing Platform — Documentation Index

> **Project:** GST Returns Automation Platform (`gst_filing_app`)
> **Folder:** `D:/gst_filing_app/` — everything related to this project lives here.
> **Convention:** Same structure discipline as the Farmer App's 5 canonical docs. Each doc is the single source of truth for its domain. Numbers in docs are verified against live code at every phase exit — test counts drift, so docs get updated, not trusted blindly.

## Canonical documents

| # | Document | Owns | Status |
|---|---|---|---|
| 1 | [PRD.md](PRD.md) | Product: personas, journeys, features, acceptance criteria | v1.0 |
| 2 | [TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md) | System: stack, model, flows, ports, repo layout, phases | v3.0 (AI-first stack) |
| 3 | [SECURITY_AND_ACCESS.md](SECURITY_AND_ACCESS.md) | AuthN/AuthZ, consent, DPDP, audit, secrets | v1.0 |
| 4 | [FRONTEND_SPECIFICATION.md](FRONTEND_SPECIFICATION.md) | Next.js app structure, screens, states, design tokens | v1.0 |
| 5 | [EXTRACTION_SPEC.md](EXTRACTION_SPEC.md) | Extraction pipeline, schema, gates G1–G3, golden set | v1.0 |
| 6 | [API_SPECIFICATION.md](API_SPECIFICATION.md) | Endpoint contract per module (AI agents code against this) | v1.0 |
| 7 | [TESTING_STRATEGY.md](TESTING_STRATEGY.md) | Test pyramid, CI gates, per-phase DoD, QA checklist | v1.0 |
| 8 | [AI_BUILD_PLAYBOOK.md](AI_BUILD_PLAYBOOK.md) | How the AI build runs: agent roles, rules, review gates | v1.0 |

## Supporting artifacts (live code, not docs)

| Artifact | Path | Purpose |
|---|---|---|
| Extraction harness | `scripts/measure_extraction.py` | CI gate G1/G2/G3 — smoke-tested pass + fail paths |
| Golden set | `extraction/golden_set/` | Ground-truth layout + schema; needs ~20 real anonymized bills before Phase 2 exit |
| Golden-set rules | `extraction/golden_set/README.md` | Real-invoices-only, anonymization rules |

## Change control

| Rule | Detail |
|---|---|
| Versioning | Every doc carries `vX.Y` + date in its header; changes that alter the build bump the minor version |
| Decision record | All decisions live as tables inside the canonical docs; superseded decisions get a "Changelog" section, not deletion |
| Verify-against-code | At each phase exit, every count/number in these docs is re-verified against the live repo; drift is fixed in the doc, never left |
| Single source | No duplicate architecture content across docs — each domain has exactly one owner doc; others link to it |

## Changelog

| Date | Doc | Change |
|---|---|---|
| 2026-09-26 | All | Initial documentation set created; stack decision finalized as AI-first Python + TypeScript (supersedes v2 Java/Flutter architecture) |