#!/usr/bin/env python3
"""Phase-11 end-to-end verification (3-lens) — VERIFICATION_SWEEP.md applied
to PHASE11_13_RECOMMENDATION_BUILD.md (tasks 11.1–11.4).

Three lenses, all with real command evidence (no summary-only PASS):

  Lens 1 — Code (backend correctness)
      Full pytest regression (--no-cov, the behaviour suite) AND the default
      addopts run WITH coverage — the standing 80% floor itself:
      `--cov=app --cov-fail-under=80` (no --no-cov escape). Then ruff, mypy.

  Lens 2 — Frontend / user flow
      tsc --noEmit, eslint, then the FULL Playwright suite — now 35 tests:
      task7-2 upload-to-output, auth-onboarding, phase4-filing, all seven
      phase8-* specs, the six phase9-* specs, the two phase10 specs, and the
      NEW phase11-workflow-accelerators spec (nav reachability of the bulk
      upload tab / batch-confirm button / set-off matrix, nav-click only).
      Playwright boots the dev server itself (playwright.config.ts webServer).

  Lens 3 — GST domain correctness
      Re-runs the phase-relevant pytest modules for the hard rules:
        - test_bulk_import.py       (11.1 auto-mapper, paise, per-row report)
        - test_batch_confirm.py     (11.2 0.95 Decimal gate, audit, guards)
        - test_setoff_rule88a.py    (11.3 statutory order, no-fabricated-CPIN)
        - test_review_flow.py       (confirm path 11.2 shares, 423/409 codes)
        - test_returns_generate.py  (single-source 3B payload + paise math)
        - test_critical_gstin.py    (mod-36 GSTIN checksum)
        - test_gstr1a.py            (423 locked-period contract)
      Plus static audits, grep-not-eyeball, verifying the three phase-11 fixes:
        (a) 11.1 — the bulk-import route exists, is an additive decorator on
            the shipped guard, and the mapper never guesses (the bare-"GSTIN"
            header is NOT in COLUMN_ALIASES);
        (b) 11.2 — the batch-confirm route exists + BATCH_CONFIRM threshold
            is the exact Decimal("0.95") (never a float literal);
        (c) 11.3 — the setoff route exists + challan can never fabricate a
            CPIN (the service hardwires "cpin": None);
        (d) route reachability — every workspace route prefix is linked from
            ShellNav.tsx (incl. upload/returns/file/itc);
        (e) spec coverage — the phase11 spec exists and self-labels, and the
            phase8/9/10 specs survive untouched.

Usage:
    backend/.venv/Scripts/python.exe scripts/e2e_phase11.py
  (or, from the repo root, with no global python3 on this Windows host:)
    uv run --project backend python scripts/e2e_phase11.py

Exits 0 with a summary table if all gates pass, 1 on the first failure.
The Playwright lens requires PG:5436 + Redis:6380 + backend :8084
(scripts/bootstrap_stack.py; the frontend dev server is auto-booted).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
CLIENT_APP = FRONTEND / "app" / "(client)" / "app"
SHELL_NAV = FRONTEND / "components" / "shared" / "ShellNav.tsx"

PY = str(BACKEND / ".venv" / "Scripts" / "python.exe")

# Phase-11 backend modules — Lens 3 re-runs these explicitly; Lens 1 runs the
# whole tests/ tree including them.
PHASE11_MODULES = [
    "tests/test_bulk_import.py",     # 11.1 bulk Excel/CSV import + auto-mapper
    "tests/test_batch_confirm.py",   # 11.2 batch confirm (0.95 Decimal gate)
    "tests/test_setoff_rule88a.py",  # 11.3 Rule 88A set-off + challan
]

# Lens 3 — GST hard-rule modules (checksum, locked periods, paise math) plus
# the phase-11 domain surfaces.
DOMAIN_MODULES = PHASE11_MODULES + [
    "tests/test_review_flow.py",
    "tests/test_returns_generate.py",
    "tests/test_critical_gstin.py",
    "tests/test_gstr1a.py",
]

# Every screen route reachable by clicking the shared shell nav. Each must
# appear in ShellNav.tsx (directly or via the computed workspace hrefs).
PHASE11_ROUTES = [
    "/app",
    "/app/businesses",
    "/app/businesses/",   # detail: /app/businesses/<gstin>
    "/app/profile",
    "/app/settings",
    "/app/upload/",       # workspace: /app/upload/<gstin>/<fp> (+ 11.1 tab)
    "/app/returns/",      # workspace: /app/returns/<gstin>/<fp> (+ 11.3 cards)
    "/app/file/",         # workspace: /app/file/<gstin>/<fp> (+ 11.2 panel)
    "/app/itc/",          # workspace: /app/itc/<gstin>/<fp>
]

# Phase-11 Playwright spec (+ the self-label the spec header carries, so
# "covers 11.x" is a checked fact, not an assumption).
PHASE11_SPECS = {
    "11.1/11.2/11.3": (
        "phase11-workflow-accelerators.spec.ts",
        "Task 11.4 gate",
    ),
}

# Phase-8/9/10 specs that must survive the sweep untouched.
LEGACY_SPECS = [
    "phase8-product-shell.spec.ts",
    "phase9-review-editor.spec.ts",
    "phase9-extraction-polling.spec.ts",
    "phase9-batch-upload.spec.ts",
    "phase9-dashboard-empty-cta.spec.ts",
    "phase9-itc-dashboard.spec.ts",
    "phase9-review-advisory-warn.spec.ts",
    "phase10-shell-nav.spec.ts",
    "phase10-live-checksum.spec.ts",
]


def run(cmd, cwd, label, grep=None):
    """Run a command; return True on exit 0. `grep` = list of regexes whose
    matching stdout lines are echoed (evidence for coverage/summary lines)."""
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    # Windows: npx/npm are .cmd shims that CreateProcess cannot launch
    # directly from Python without a shell.
    r = subprocess.run(  # noqa: S603 — fixed argv, no user-supplied command
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        shell=os.name == "nt",
        check=False,
    )
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    if grep:
        for pat in grep:
            for line in out.splitlines():
                if re.search(pat, line):
                    print(line.strip())
    tail = out[-1200:]
    if tail.strip():
        print(tail)
    print(f"exit {r.returncode} [{label}]")
    return r.returncode == 0


def lens1_code() -> bool:
    print("\n=== Lens 1: Code (backend correctness) ===")
    ok = True
    # Behaviour suite (fast, no coverage).
    if not run(
        [PY, "-m", "pytest", "tests/", "-q", "--no-cov", "-p", "no:cacheprovider"],
        BACKEND,
        "Lens 1 full pytest (no-cov)",
        grep=[r"\d+ passed", r"\d+ failed"],
    ):
        ok = False
        print("FAIL: full backend behaviour regression is not green")
    # Standing gate: the DEFAULT addopts run, enforcing --cov-fail-under=80.
    if not run(
        [PY, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
        BACKEND,
        "Lens 1 coverage gate (--cov-fail-under=80)",
        grep=[r"Required test coverage", r"^TOTAL", r"\d+ passed", r"\d+ failed"],
    ):
        ok = False
        print("FAIL: pytest with the default addopts did not reach the 80% gate")
    if not run([PY, "-m", "ruff", "check", "."], BACKEND, "Lens 1 ruff"):
        ok = False
        print("FAIL: ruff check failed (lint failures block)")
    if not run([PY, "-m", "mypy", "app/"], BACKEND, "Lens 1 mypy"):
        ok = False
        print("FAIL: mypy app/ failed")
    return ok


def lens2_frontend() -> bool:
    print("\n=== Lens 2: Frontend / user flow ===")
    ok = True
    if not run(["npx", "tsc", "--noEmit"], FRONTEND, "Lens 2 tsc"):
        ok = False
        print("FAIL: tsc --noEmit failed")
    if not run(["npm", "run", "lint"], FRONTEND, "Lens 2 eslint"):
        ok = False
        print("FAIL: npm run lint failed")
    # Full Playwright suite: task7-2, auth-onboarding, phase4-filing, all
    # phase8-*/phase9-*/phase10-* specs, and the new phase11-*
    # workflow-accelerators spec. playwright.config.ts boots the dev server
    # on 9094 itself; the backend (8084) + PG/Redis must already be up
    # (bootstrap_stack.py).
    if not run(
        ["npx", "playwright", "test"],
        FRONTEND,
        "Lens 2 playwright",
        grep=[r"\d+ passed", r"\d+ failed", r"\d+ flaky"],
    ):
        ok = False
        print("FAIL: Playwright suite is not green")
    return ok


def strip_ts_comments(src: str) -> str:
    """Drop /* ... */ block comments and // line comments from TS/TSX source.

    Audit regexes run over comment-stripped source so a docblock mentioning a
    string can never be miscounted as a render/wire.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def lens3_domain() -> bool:
    print("\n=== Lens 3: GST domain correctness + phase-11 fix audits ===")
    ok = True
    if not run(
        [PY, "-m", "pytest", *DOMAIN_MODULES, "-q", "--no-cov", "-p", "no:cacheprovider"],
        BACKEND,
        "Lens 3 domain modules",
        grep=[r"\d+ passed", r"\d+ failed"],
    ):
        ok = False
        print("FAIL: domain module run failed")

    # (a) 11.1 — the auto-mapper never guesses: the ambiguous bare headers are
    # not aliases, and the route exists with the shipped guard (additive).
    bulk_service = (BACKEND / "app" / "services" / "bulk_import.py").read_text(
        encoding="utf-8"
    )
    bare_headers = re.findall(r'"(gstin|rate|value)":\s*\(', bulk_service)
    if bare_headers:
        ok = False
        print(f"FAIL [11.1]: bare ambiguous header(s) became alias(es): {bare_headers}")
    else:
        print("11.1 audit: COLUMN_ALIASES maps no bare 'gstin'/'rate'/'value' "
              "header — the mapper never guesses")
    bulk_router = (BACKEND / "app" / "api" / "routers" / "bulk_import.py").read_text(
        encoding="utf-8"
    )
    if "require_gstin_write(\"gstin\")" not in bulk_router:
        ok = False
        print("FAIL [11.1]: bulk-import route does not sit on require_gstin_write")
    elif '@router.post("/gst-accounts/{gstin}/months/{fp}/bulk-import")' not in bulk_router:
        ok = False
        print("FAIL [11.1]: bulk-import route decorator missing")
    else:
        print("11.1 audit: POST .../bulk-import is guarded by require_gstin_write "
              "(FILER/ADMIN), additively")
    main_src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    if "bulk_import_router" not in main_src:
        ok = False
        print("FAIL [11.1]: bulk_import_router is not wired into create_app()")
    # Additivity: `git show` for each phase-11 commit over the routers must
    # only ADD @router.* decorators (GST-domain lens, PHASE11_13 §4). The
    # commits are in-repo history; check the router diffs contain no '-@'.
    for commit, router_file in (
        ("e52f43a", "bulk_import.py"),
        ("b294cf0", "documents.py"),
        ("ddaaec3", "returns.py"),
    ):
        r = subprocess.run(  # noqa: S603
            ["git", "show", commit, "--", f"backend/app/api/routers/{router_file}"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
        )
        removed = [l for l in r.stdout.splitlines() if l.startswith("-@")]
        if removed:
            ok = False
            print(f"FAIL [additivity]: {commit} removed router "
                  f"decorator(s) in {router_file}: {removed[:2]}")
    if ok:
        print("additivity audit: phase-11 commits only ADDED @router.* "
              "decorators (bulk_import.py / documents.py / returns.py)")

    # (b) 11.2 — the batch-confirm threshold is the exact Decimal 0.95.
    bc_src = (BACKEND / "app" / "services" / "batch_confirm.py").read_text(
        encoding="utf-8"
    )
    if 'BATCH_CONFIRM_THRESHOLD = Decimal("0.95")' not in bc_src:
        ok = False
        print("FAIL [11.2]: BATCH_CONFIRM_THRESHOLD is not the exact "
              "Decimal(\"0.95\") (a float gate is a defect)")
    else:
        print("11.2 audit: BATCH_CONFIRM_THRESHOLD = Decimal(\"0.95\") — exact "
              "boundary, no float")
    bc_route = (BACKEND / "app" / "api" / "routers" / "documents.py").read_text(
        encoding="utf-8"
    )
    if "/review-queue/batch-confirm" not in bc_route or "require_gstin_write" not in bc_route:
        ok = False
        print("FAIL [11.2]: batch-confirm route missing or unguarded")

    # (c) 11.3 — the challan can NEVER fabricate a CPIN.
    setoff_src = (BACKEND / "app" / "services" / "returns" / "setoff.py").read_text(
        encoding="utf-8"
    )
    if '"cpin": None' not in setoff_src:
        ok = False
        print('FAIL [11.3]: setoff.challan does not hardwire "cpin": None')
    else:
        print('11.3 audit: challan hardwires "cpin": None — no fabricated CPIN')
    returns_router = (BACKEND / "app" / "api" / "routers" / "returns.py").read_text(
        encoding="utf-8"
    )
    for route in ("/gstr3b/setoff", "/gstr3b/challan"):
        if route not in returns_router:
            ok = False
            print(f"FAIL [11.3]: missing GET route {route}")
    # The returns page renders the matrix from the API (testids exist).
    returns_page = (CLIENT_APP / "returns" / "[gstin]" / "[fp]" / "page.tsx").read_text(
        encoding="utf-8"
    )
    for tid in ("setoff-section", "setoff-matrix", "challan-section",
                "challan-portal-link", "challan-cpin-note"):
        if tid not in returns_page:
            ok = False
            print(f"FAIL [11.3]: returns page missing testid {tid}")

    # (d) Reachability audit: every shell nav prefix is linked from ShellNav.tsx.
    shell = SHELL_NAV.read_text(encoding="utf-8")
    missing = [r for r in PHASE11_ROUTES if r not in shell]
    if missing:
        ok = False
        print(f"FAIL: routes missing from ShellNav.tsx: {missing}")
    else:
        print(f"reachability audit: all {len(PHASE11_ROUTES)} route prefixes "
              "linked from ShellNav.tsx (incl. upload/returns/file/itc)")

    # (e) Spec coverage: the phase-11 spec exists and self-labels; the
    # phase-8/9/10 specs survive.
    tests_dir = FRONTEND / "tests"
    for task, (fname, label) in PHASE11_SPECS.items():
        path = tests_dir / fname
        if not path.exists():
            ok = False
            print(f"FAIL: missing phase-11 spec for {task}: tests/{fname}")
            continue
        if label not in path.read_text(encoding="utf-8"):
            ok = False
            print(f"FAIL: tests/{fname} does not self-label as {label!r}")
        else:
            print(f"spec coverage: {task} -> tests/{fname} (self-labelled {label!r})")
    for fname in LEGACY_SPECS:
        if not (tests_dir / fname).exists():
            ok = False
            print(f"FAIL: {fname} (phase-8/9/10 regression spec) was removed")
    return ok


def main() -> int:
    print("=== Phase-11 E2E: 3-lens verification ===")
    gates = [
        ("Lens 1 — Code (pytest/coverage-gate/ruff/mypy)", lens1_code),
        ("Lens 2 — Frontend (tsc/lint/playwright)", lens2_frontend),
        ("Lens 3 — GST domain + reachability + fix audits", lens3_domain),
    ]
    results = {}
    for name, fn in gates:
        try:
            results[name] = fn()
        except Exception as e:  # noqa: BLE001 — harness safety net
            print(f"EXCEPTION in {name}: {e}")
            results[name] = False

    print("\n" + "=" * 60)
    print("PHASE-11 E2E RESULTS (3-lens)")
    print("=" * 60)
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("=" * 60)
    if all(results.values()):
        print("ALL LENSES PASSED — Phase 11 E2E green")
        return 0
    print("SOME LENSES FAILED — see above")
    return 1


if __name__ == "__main__":
    sys.exit(main())