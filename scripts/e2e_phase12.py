#!/usr/bin/env python3
"""Phase-12 end-to-end verification (3-lens) — CA Practice & Enterprise Controls.

Covers tasks 12.1, 12.2, and 12.3 (Recommendations 4 & 5):
  Lens 1 — Code correctness (pytest backend, ruff, mypy)
  Lens 2 — Frontend / user flow (tsc, eslint, phase12 spec)
  Lens 3 — GST domain invariants:
    - Maker-checker sign-off + period soft freeze/unlock (ADMIN approves, FILER prepares).
    - When FROZEN or FILED, writing/confirming raises 423 PERIOD_LOCKED.
    - Vendor discrepancy notice generation citing Section 16(2)(aa) of CGST Act.
    - Money = integer paise everywhere; zero floats across boundaries.
    - Append-only audit logs (FREEZE_PERIOD, UNLOCK_PERIOD).
    - Additive routes on returns & itc routers.

Usage:
    backend/.venv/Scripts/python.exe scripts/e2e_phase12.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

PY = sys.executable


def run_cmd(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Execute a command and return (code, output)."""
    p = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=(os.name == "nt"),
    )
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def main() -> int:
    print("================================================================")
    print(" GST FILING PLATFORM -- PHASE 12 (ENTERPRISE CONTROLS) 3-LENS E2E")
    print("================================================================\n")

    failures: list[str] = []

    # -------------------------------------------------------------
    # LENS 1: Code correctness
    # -------------------------------------------------------------
    print("[Lens 1: Backend Code]")

    code, out = run_cmd(
        [PY, "-m", "pytest", "tests/test_freeze.py", "tests/test_discrepancy_notice.py", "--no-cov", "-q"],
        cwd=BACKEND,
    )
    if code == 0:
        print("  OK: Pytest Phase 12 tests passed: " + out.splitlines()[-1])
    else:
        print("  FAIL: Pytest failed:\n" + out)
        failures.append("Lens 1: Pytest tests")

    code, out = run_cmd(
        [
            PY,
            "-m",
            "ruff",
            "check",
            "app/services/filing_freeze.py",
            "app/services/discrepancy_notice.py",
            "app/api/routers/returns.py",
            "app/api/routers/itc.py",
            "tests/test_freeze.py",
            "tests/test_discrepancy_notice.py",
        ],
        cwd=BACKEND,
    )
    if code == 0:
        print("  OK: Ruff check clean")
    else:
        print("  FAIL: Ruff check failed:\n" + out)
        failures.append("Lens 1: Ruff check")

    code, out = run_cmd(
        [
            PY,
            "-m",
            "mypy",
            "app/services/filing_freeze.py",
            "app/services/discrepancy_notice.py",
            "app/api/routers/returns.py",
            "app/api/routers/itc.py",
            "tests/test_freeze.py",
            "tests/test_discrepancy_notice.py",
        ],
        cwd=BACKEND,
    )
    if code == 0:
        print("  OK: Mypy clean")
    else:
        print("  FAIL: Mypy failed:\n" + out)
        failures.append("Lens 1: Mypy")

    # -------------------------------------------------------------
    # LENS 2: Frontend & User Journey
    # -------------------------------------------------------------
    print("\n[Lens 2: Frontend & User Journey]")

    code, out = run_cmd(["npx", "--no-install", "tsc", "--noEmit"], cwd=FRONTEND)
    if code == 0:
        print("  OK: TypeScript compilation clean")
    else:
        print("  FAIL: tsc failed:\n" + out)
        failures.append("Lens 2: TypeScript")

    code, out = run_cmd(["npm", "run", "lint"], cwd=FRONTEND)
    if code == 0:
        print("  OK: ESLint clean")
    else:
        print("  FAIL: ESLint failed:\n" + out)
        failures.append("Lens 2: ESLint")

    code, out = run_cmd(
        ["npx", "playwright", "test", "tests/phase12-controls.spec.ts"],
        cwd=FRONTEND,
    )
    if code == 0:
        print("  OK: Playwright phase12 spec passed")
    else:
        print("  FAIL: Playwright failed:\n" + out)
        failures.append("Lens 2: Playwright spec")

    # -------------------------------------------------------------
    # LENS 3: GST Domain Invariants
    # -------------------------------------------------------------
    print("\n[Lens 3: GST Domain Invariants]")

    # Domain check 1: Money is integer paise
    freeze_svc = (BACKEND / "app/services/filing_freeze.py").read_text(encoding="utf-8")
    notice_svc = (BACKEND / "app/services/discrepancy_notice.py").read_text(encoding="utf-8")
    if "float(" in freeze_svc or "float(" in notice_svc:
        print("  FAIL: Found float() in Phase 12 services (money must strictly be integer paise)")
        failures.append("Lens 3: Integer paise invariant")
    else:
        print("  OK: No float conversion across service boundary (integer paise maintained)")

    # Domain check 2: Statutory citation present
    if "Section 16(2)(aa)" in notice_svc:
        print("  OK: Statutory citation Section 16(2)(aa) properly cited in discrepancy notice")
    else:
        print("  FAIL: Statutory citation Section 16(2)(aa) missing in notice service")
        failures.append("Lens 3: Section 16(2)(aa) citation")

    # Domain check 3: Append-only audit actions
    if "FREEZE_PERIOD" in freeze_svc and "UNLOCK_PERIOD" in freeze_svc:
        print("  OK: Period freeze/unlock logged with append-only audit entries")
    else:
        print("  FAIL: Missing FREEZE_PERIOD or UNLOCK_PERIOD audit logging")
        failures.append("Lens 3: Audit logging")

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print("\n================================================================")
    if not failures:
        print(" ALL 3 LENSES PASSED CLEANLY FOR PHASE 12!")
        print("================================================================")
        return 0
    else:
        print(f" {len(failures)} VERIFICATION FAILURES:")
        for f in failures:
            print(f"   - {f}")
        print("================================================================")
        return 1


if __name__ == "__main__":
    sys.exit(main())
