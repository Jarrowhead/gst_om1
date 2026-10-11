# Scripts — GST Filing App

Run everything from the **repo root** with the backend venv unless noted.

| Script | What it does | Command |
|---|---|---|
| `bootstrap_stack.py` | Start + health-check PG:5436 / Redis:6380 / MinIO:9001 (idempotent) | `backend/.venv/Scripts/python.exe scripts/bootstrap_stack.py [--check]` |
| `seed_demo.py` | Create the demo user + 4 checksum-valid GSTINs on the running backend | `backend/.venv/Scripts/python.exe scripts/seed_demo.py` |
| `app_tour.cjs` | Drive the real UI (login→upload→review→confirm→returns), write screenshots + `tour-report.json` | `cd frontend && NODE_PATH="$PWD/node_modules" node ../scripts/app_tour.cjs` |
| `generate_tracker.py` | Regenerate `docs/BUILD_TRACKER.md` from `build/plan.json` + `build/state.json` | `python scripts/generate_tracker.py` |
| `build_loop.py` | The 3-agent build loop (Builder/Tester/Monitor). Build is **complete** — do not relaunch unless re-testing | `python scripts/build_loop.py` |
| `e2e_phase0..11.py` | Per-phase E2E harnesses (phases 0–11), each a 3-lens gate (Code / Frontend / GST-domain) — expects backend :8084 up. `e2e_phase11.py` is the current gate (covers 11.1 bulk import, 11.2 batch confirm, 11.3 Rule 88A set-off) | python scripts/e2e_phase11.py |
| `measure_extraction.py` | Extraction accuracy harness against the golden set | `backend/.venv/Scripts/python.exe scripts/measure_extraction.py` |
| `generate_golden_set.py` / `generate_predictions.py` | Golden-set tooling | see file docstrings |
| `backup_production.py` | Self-hosted backup routine | see file docstring |
| `auto_shutdown*.py` | Unattended-shutdown watcher variants (deploy as a Windows Scheduled Task, not a background terminal) | see file docstrings |

## Notes

- **`NODE_PATH` is mandatory** for `app_tour.cjs`: node resolves `require('playwright')` relative to the *script's* directory, not the cwd. Without it you get `Cannot find module 'playwright'`.
- **`generate_tracker.py` needs `build/plan.json` + `build/state.json`**, which are gitignored. On a fresh clone those files are absent — the committed `docs/BUILD_TRACKER.md` is then the record.
- **E2E harnesses carry a `#!/usr/bin/env python3` shebang, but `python3` does not exist on this Windows host.** Run them via `uv run --project backend python scripts/e2e_phaseN.py` from the repo root.
- `build/` and `tools/` are gitignored (loop state + native binaries).

- `scripts/e2e_phase13.py`: 3-lens E2E verification for Phase 13 (Logistics Moat / E-Way Bill sandbox, tasks 13.1 & 13.2).
