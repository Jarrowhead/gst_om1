#!/usr/bin/env python3
"""
GST Filing App — 3-Agent Autonomous Build Loop.

Roles (all via `hermes chat --oneshot` subprocesses):
  BUILDER  — writes code, follows docs
  TESTER   — runs the verification itself, emits PASS/FAIL with real evidence
  MONITOR  — SUPERVISES: inspects the Tester's evidence, inspects the Builder's
             output, then either ADVANCE / RETRY / ESCALATE, and issues a
             concrete BUILDER INSTRUCTION that is fed into the next build
             attempt. The Monitor is not a rubber stamp: it must state whether
             the Tester produced REAL command evidence or only a summary, and
             it must name the exact defect the Builder has to fix.

MODEL ROUTING — every role is a FALLBACK CHAIN, not a single hard-coded id.
Each candidate is liveness-probed once per run (build/role_models.json) and the
first live one wins, so a rate-limited or retired route degrades gracefully
instead of stalling the loop.

A singleton lock (build/loop.lock) prevents duplicate instances racing on
state.json — a second launch exits immediately while a live loop holds it.

The loop runs until all phases in build/plan.json are done, or the monitor
escalates to the human, or MAX_ITERATIONS is hit. Every decision is logged
to build/state.json and build/run.log.

Usage:
    python scripts/build_loop.py [--once] [--max-iters N] [--no-probe]

--once:     run a single cycle (pick next task, build, test, monitor) then exit.
--no-probe: skip liveness probing and use the first candidate of each chain.

DEBUGGING QUICK MAP
-------------------
  build/run.log          — timestamped trace of every cycle (spawn, verdict, monitor ACTION)
  build/state.json       — tasks[].status, retries, tester_feedback, monitor_instruction
  build/prompt.txt       — last agent prompt written (what hermes received)
  build/loop.lock        — PID of live loop; stale PID → auto take-over on next start
  build/role_models.json — probed model per role for this run

  Exit 3                 — duplicate loop (another pid_alive holds lock)
  ESCALATED in state     — retries exhausted OR monitor ESCALATE OR bad ADVANCE on weak evidence

  Cycle order: first_pending_task → BUILD → TEST → MONITOR → (ADVANCE|RETRY|ESCALATE)
  Weak evidence          — has_real_evidence() False → monitor_blocks even if VERDICT: PASS
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD_DIR = os.path.join(ROOT, "build")
PLAN_PATH = os.path.join(BUILD_DIR, "plan.json")
STATE_PATH = os.path.join(BUILD_DIR, "state.json")
LOG_PATH = os.path.join(BUILD_DIR, "run.log")
PROMPT_FILE = os.path.join(BUILD_DIR, "prompt.txt")
LOCK_PATH = os.path.join(BUILD_DIR, "loop.lock")
ROLES_PATH = os.path.join(BUILD_DIR, "role_models.json")

# ── Role model chains (order = preference; first LIVE candidate wins) ───────
# Each chain is walked TWICE: once at probe time (one-line ping) and again at
# runtime (first real tool-using turn). Free `:free` tiers pass the ping and then
# die mid-build with a 401/402 from their upstream, so the head of each chain is
# the route that has actually completed a full build/test turn in this repo.
#
# The user's omniroute "best coding"/"best reasoning" routes are kept in the
# chains — they are currently BROKEN STUBS (they answer with a local qwen2.5
# fallback and fail the ping), so they are listed after the working routes and
# will be picked up automatically once the omniroute side is repaired:
#   auto/best-coding    → currently serves qwen2.5:7b-ctx64k (wrong arithmetic)
#   auto/best-reasoning → currently serves qwen2.5:7b-ctx64k (wrong arithmetic)
ROLE_CHAINS = {
    "builder": [
        ("openrouter/cohere/north-mini-code:free", "omniroute"),  # $0 code-specialised; tools proven
        ("auto/best-coding", "omniroute"),                        # omniroute's own "best coding" route
        ("kimi-k2.7-code", "ollama-cloud"),
        ("glm-5.3-flash", "ollama-cloud"),
        ("gpt-oss:120b", "ollama-cloud"),
    ],
    "tester": [
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "omniroute"),
        ("auto/best-reasoning", "omniroute"),                     # omniroute's own "best reasoning" route
        ("deepseek-v4.1-flash", "ollama-cloud"),
        ("deepseek-v4-pro:0813", "ollama-cloud"),
        ("gpt-oss:120b", "ollama-cloud"),
    ],
    "monitor": [
        ("glm-5.3", "ollama-cloud"),
        ("kimi-k3", "ollama-cloud"),
        ("deepseek-v4.1-flash", "ollama-cloud"),
    ],
}

# Builder-feedback / rework model chain (used to turn tester findings into a fix).
FEEDBACK_CHAIN = [
    ("deepseek-v4.1-flash", "ollama-cloud"),
    ("glm-5.3-flash", "ollama-cloud"),
]

# Routes that currently answer with a LOCAL STUB rather than the real upstream:
# they return 200 and plausible-looking text from local qwen2.5:7b-ctx64k, but
# the arithmetic is wrong (GST 12345.67 @18% → 14567.9 instead of 14567.89), so
# their output must never be accepted as build/test work. They stay in the
# chains above (so the loop re-probes them every run and promotes them the moment
# omniroute repairs them) but runtime failover skips them. Delete a name here to
# re-enable it.
STUB_ROUTES = {
    "auto/best-coding",
    "auto/best-reasoning",
    "auto/coding",
    "auto/chat",
    "auto/reasoning",
    "auto/fast",
    "auto/cheap",
    "auto/combo",
}

MAX_RETRIES = 2          # builder re-attempts after a tester FAIL
MAX_ITERATIONS = 200     # safety cap on total cycles
PROBE_TIMEOUT = 180      # seconds allowed for one liveness probe


def log(msg):
    """Append one timestamped line to stdout and build/run.log.

    Flow:
        1. Format ISO timestamp + message.
        2. print(flush=True) for live tailing.
        3. Append same line to LOG_PATH (creates file on first write).

    Debug:
        Tail run.log during long hermes runs; search for ✗/⚠/🛑 markers.
    """
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    """Read JSON from path or return default if missing.

    Flow:
        1. If path exists → utf-8 load and return object.
        2. Else → return default (caller supplies shape, e.g. {} or {"phases": []}).

    Debug:
        Corrupt JSON raises json.JSONDecodeError — fix file or delete to re-seed default.
    """
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    """Write obj to path as pretty-printed UTF-8 JSON (indent=2).

    Flow:
        1. Truncate/overwrite path atomically at process level (not atomic rename).

    Debug:
        If state.json looks stale mid-cycle, another process may be writing — check loop.lock.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


# ── Singleton lock ─────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Best-effort cross-platform 'is this PID running?' check.

    Flow:
        1. Windows: tasklist /FI PID eq {pid}; look for quoted pid in CSV output.
        2. Unix: os.kill(pid, 0) — signal 0 tests existence without killing.
        3. Any exception → False (treat as not running → stale lock takeover).

    Debug:
        False positives rare; False negatives if PID reused quickly after crash.
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout
            return f'"{pid}"' in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock() -> None:
    """Ensure only one build_loop.py mutates build/state.json at a time.

    Flow:
        1. If loop.lock exists → read PID.
        2. If PID alive → print error and sys.exit(3).
        3. If stale → log NOTE and overwrite lock with current os.getpid().
        4. If no lock → write current PID.

    Debug:
        Zombie lock after kill -9: delete build/loop.lock manually if pid not running.
    """
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, encoding="utf-8") as f:
                other = int(f.read().strip())
        except (ValueError, OSError):
            other = None
        if other and pid_alive(other):
            print(f"ERROR: another build_loop.py is already running (pid {other}); "
                  "refusing to start a duplicate.", flush=True)
            sys.exit(3)
        print(f"NOTE: stale lock (pid {other} not running) — taking over.", flush=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def release_lock() -> None:
    """Remove loop.lock when this process is the recorded owner.

    Flow:
        1. If lock missing → no-op.
        2. Read owner PID string; if matches os.getpid() → os.remove(lock).
        3. Swallow OSError (best-effort cleanup on shutdown).

    Debug:
        Lock left after crash → next run takes over if pid_alive False.
    """
    try:
        if os.path.exists(LOCK_PATH):
            with open(LOCK_PATH, encoding="utf-8") as f:
                owner = f.read().strip()
            if owner == str(os.getpid()):
                os.remove(LOCK_PATH)
    except OSError:
        pass


def agent_env():
    """Build environment dict for child `hermes` subprocesses.

    Flow:
        1. Copy os.environ.
        2. Force TERM=dumb (avoids prompt_toolkit issues when parent is MSYS/bash on Windows).
        3. Set NO_COLOR=1 if unset (stable log parsing).

    Returns:
        dict suitable for subprocess.run(..., env=...).

    Debug:
        If hermes hangs interactively, verify TERM=dumb is present in child env.
    """
    env = dict(os.environ)
    env["TERM"] = "dumb"
    env.setdefault("NO_COLOR", "1")
    return env


def run_agent(model, provider, prompt, max_turns=250, timeout=1800):
    """Run one hermes chat --oneshot subprocess and return combined stdout+stderr.

    Flow:
        1. Write full prompt to build/prompt.txt (inspect for agent instructions).
        2. Spawn: hermes chat --oneshot --query-file prompt.txt -m model --provider ...
           with --in ROOT, --yolo --accept-hooks, -Q (quiet), max_turns cap.
        3. Wait up to timeout seconds; on TimeoutExpired return "TIMEOUT after Ns".
        4. Concatenate stdout+stderr; log duration and exit code.

    Returns:
        str agent transcript (or TIMEOUT prefix string).

    Debug:
        Empty output + short log → agent_failed() likely True → run_role tries next model.
        Re-run hermes manually with same PROMPT_FILE contents to reproduce.
    """
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(prompt)
    cmd = [
        "hermes", "chat", "--oneshot",
        "--query-file", PROMPT_FILE,
        "-m", model,
        "--provider", provider,
        "--yolo", "--accept-hooks",
        "--in", ROOT,
        "--max-turns", str(max_turns),
        "-Q",
    ]
    log(f"  ▸ spawning {model} ({provider})")
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", env=agent_env(),
        )
    except subprocess.TimeoutExpired:
        log(f"  ✗ {model} TIMEOUT after {timeout}s")
        return f"TIMEOUT after {timeout}s"
    dt = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    log(f"  ✓ {model} finished in {dt:.0f}s (exit {proc.returncode})")
    return out


# Patterns that mean "this route died at runtime", not "the model did bad work".
# A liveness ping passes for these routes, then the upstream provider 401s/403s
# or the route has no credentials at all once a real workload is sent.
RUNTIME_FAIL_MARKERS = (
    "authentication token is invalid",
    "invalid_api_key",
    "no active credentials",
    "401",
    "402",
    "403",
    "429",
    "insufficient",
    "credits_exhausted",
    "rate limit",
)


def agent_failed(model, provider, out: str) -> bool:
    """True when the child agent produced no usable work (dead route / auth wall).

    Flow:
        1. Strip output; empty → failed.
        2. Leading "TIMEOUT" → failed.
        3. Lowercase scan for RUNTIME_FAIL_MARKERS (401, rate limit, invalid_api_key, ...).
        4. Healthy runs include "session_id:" in output — marker without session → failed.
        5. Very short output (<400 chars) with marker → failed.

    Returns:
        True → run_role should try next model in chain.

    Debug:
        Log line "FAILED AT RUNTIME — next candidate" — compare out[:500] for marker text.
        False here but bad code → model ran; Tester/Monitor must catch quality issues.
    """
    text = (out or "").strip()
    if not text:
        return True
    if "TIMEOUT" in text[:40]:
        return True
    low = text.lower()
    has_marker = any(m in low for m in RUNTIME_FAIL_MARKERS)
    # a healthy run always records a session id
    has_session = "session_id:" in low
    if has_marker and not has_session:
        return True
    # an almost-empty report is not a build attempt
    return len(text) < 400 and has_marker


def run_role(role: str, chain, prompt, roles_path_fallback=None, timeout=1800, start=None):
    """Run a role, failing over across its chain at RUNTIME (not just at probe).

    Flow:
        1. Normalize chain to list; if start model is in chain, rotate list to begin at start.
        2. Drop STUB_ROUTES from candidates (known local qwen stubs); fall back to full list if empty.
        3. For each (model, provider): run_agent → if not agent_failed, return (out, meta).
        4. Log failover; if all fail, return last_out anyway (Monitor may ESCALATE).

    Returns:
        tuple[str, dict] — (transcript, {"model": ..., "provider": ...}).

    Debug:
        roles_path_fallback is unused legacy param — ignore.
        All candidates fail → Tester gets empty/401 text → weak evidence → retry loop.
    """
    candidates = list(chain if isinstance(chain, list) else [chain])
    if start and any(m == start for m, _ in candidates):
        i = next(k for k, (m, _) in enumerate(candidates) if m == start)
        candidates = candidates[i:] + candidates[:i]
    # never let failover land on a known local-stub route
    live_candidates = [(m, p) for (m, p) in candidates if m not in STUB_ROUTES]
    if not live_candidates:
        live_candidates = candidates
    last_out = ""
    for idx, (model, provider) in enumerate(live_candidates):
        out = run_agent(model, provider, prompt, timeout=timeout)
        if not agent_failed(model, provider, out):
            if idx:
                log(f"  ⤶ role {role}: failed over to {model} ({provider})")
            return out, {"model": model, "provider": provider}
        log(f"  ✗ role {role}: {model} ({provider}) FAILED AT RUNTIME — next candidate")
        last_out = out
    log(f"  ⚠ role {role}: every candidate failed at runtime; returning last output")
    return last_out, {"model": live_candidates[-1][0], "provider": live_candidates[-1][1]}


# ── Role resolution (liveness-probed fallback chains) ──────────────────────

def probe_agent(model, provider):
    """Tiny liveness probe using the same hermes spawn path as run_agent.

    Flow:
        1. Write prompt "Reply with exactly: PING-OK\\n" to prompt.txt.
        2. hermes --max-turns 2, PROBE_TIMEOUT seconds.
        3. Return True iff "PING-OK" appears in stdout+stderr.

    Debug:
        False for all chain entries → resolve_roles uses head of chain anyway (logged).
        PING passes but run fails → auth wall on real workload; agent_failed handles failover.
    """
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write("Reply with exactly: PING-OK\n")
    cmd = [
        "hermes", "chat", "--oneshot",
        "--query-file", PROMPT_FILE,
        "-m", model,
        "--provider", provider,
        "--yolo", "--accept-hooks",
        "--in", ROOT,
        "--max-turns", "2",
        "-Q",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            encoding="utf-8", errors="replace", env=agent_env(),
        )
    except subprocess.TimeoutExpired:
        return False
    return "PING-OK" in ((proc.stdout or "") + (proc.stderr or ""))


def resolve_roles(probe: bool = True) -> dict:
    """Pick the first live model per role; optionally cache in build/role_models.json.

    Flow:
        1. If probe=False → assign first entry of each ROLE_CHAINS + FEEDBACK_CHAIN (fast/dev).
        2. Else for each role chain: probe_agent until one returns True → chosen.
        3. If none live → log warning and use chain[0] anyway.
        4. Same for feedback chain; save resolved dict to ROLES_PATH.

    Returns:
        dict with keys builder, tester, monitor, feedback → {model, provider}.

    Debug:
        Use --no-probe to skip 3×chain×PROBE_TIMEOUT wait on startup.
        Edit ROLE_CHAINS order to prefer routes that work on your machine.
    """
    resolved = {}
    if not probe:
        for role, chain in ROLE_CHAINS.items():
            resolved[role] = {"model": chain[0][0], "provider": chain[0][1]}
        resolved["feedback"] = {"model": FEEDBACK_CHAIN[0][0], "provider": FEEDBACK_CHAIN[0][1]}
        return resolved

    for role, chain in ROLE_CHAINS.items():
        chosen = None
        for model, provider in chain:
            if probe_agent(model, provider):
                chosen = {"model": model, "provider": provider}
                log(f"  role {role:8s} → {model} ({provider})  [live]")
                break
            log(f"  role {role:8s} → {model} ({provider})  [DEAD, next]")
        if chosen is None:
            chosen = {"model": chain[0][0], "provider": chain[0][1]}
            log(f"  role {role:8s} → {chosen['model']} (NOTHING LIVE — using head of chain)")
        resolved[role] = chosen

    fb = None
    for model, provider in FEEDBACK_CHAIN:
        if probe_agent(model, provider):
            fb = {"model": model, "provider": provider}
            break
    resolved["feedback"] = fb or {"model": FEEDBACK_CHAIN[0][0], "provider": FEEDBACK_CHAIN[0][1]}

    save_json(ROLES_PATH, resolved)
    return resolved


# ── Prompt builders ────────────────────────────────────────────────────────

def builder_prompt(task, phase, tester_feedback=None, monitor_instruction=None):
    """Assemble the BUILDER agent system prompt for the current task.

    Flow:
        1. Optionally prepend tester_feedback block (prior FAIL findings).
        2. Optionally prepend monitor_instruction block (binding fix directive).
        3. Inject ROOT, phase/task ids, done_when, doc list, AI_BUILD_PLAYBOOK hard rules.

    Returns:
        str written to build/prompt.txt before hermes spawn.

    Debug:
        If Builder ignores scope, compare RELEVANT DOCS in prompt vs git diff --stat.
    """
    parts = []
    if tester_feedback:
        parts.append(
            "\n=== PREVIOUS TESTER FEEDBACK (FIX THESE FIRST) ===\n"
            f"{tester_feedback}\n"
        )
    if monitor_instruction:
        parts.append(
            "\n=== MONITOR INSTRUCTION (BINDING — DO THIS EXACTLY) ===\n"
            f"{monitor_instruction}\n"
        )
    fb = "".join(parts)
    return f"""You are the BUILDER agent for the GST Filing Platform. You write code and only code.

PROJECT ROOT: {ROOT}
All 8 canonical docs are in {ROOT}/docs/. READ the docs listed below before writing anything — they are the contract. If code and doc disagree, fix whichever is wrong and say which.

CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}

TASK DONE WHEN: {task.get('done_when', 'task complete')}

RELEVANT DOCS: {', '.join(task.get('docs', []))}

HARD RULES (from docs/AI_BUILD_PLAYBOOK.md):
1. Never fabricate GSTINs/PANs/invoices. Synthetic fixture GSTINs must pass the mod-36 checksum (helper exists in scripts/ or tests/).
2. Money is integer paise everywhere. No float crosses any boundary.
3. Every registration-scoped route goes through the access guard dependency.
4. Locked periods return 423; originals never edited post-filing.
5. ruff + mypy clean (backend); eslint + tsc clean (frontend). Lint failures block.
6. Declare deps in pyproject.toml / package.json — never pip install at runtime.
7. Do NOT touch files outside this task's scope.
8. NEVER modify any file under build/ (plan.json, state.json, run.log, prompt.txt) or scripts/build_loop.py — those are the loop's own control files, not project code. Editing them corrupts the build loop.
9. If a doc is ambiguous, STOP and say so in your final summary — do not guess in the GST domain.
{fb}
FINISH by running the task's verification yourself and reporting exact test names + counts in your final summary. Your final summary is what the Tester will read — be precise.
"""


def tester_prompt(task, phase, builder_out):
    """Assemble the TESTER agent prompt (verify Builder claims with real commands).

    Flow:
        1. Embed task/phase metadata and done_when criteria.
        2. Append last 6000 chars of builder_out as untrusted claims.
        3. Require COMMANDS RUN / VERDICT / FINDINGS output format for parsing.

    Returns:
        str for hermes tester role.

    Debug:
        If has_real_evidence False, search tester history in state.json for missing
        "COMMANDS RUN:" or pytest/ruff markers in slot history tester out.
    """
    return f"""You are the TESTER agent for the GST Filing Platform. You do NOT write feature code. You verify the Builder's work and report PASS or FAIL.

PROJECT ROOT: {ROOT}
Docs in {ROOT}/docs/ are the contract.

CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}
TASK DONE WHEN: {task.get('done_when', 'task complete')}
RELEVANT DOCS: {', '.join(task.get('docs', []))}

=== WHAT THE BUILDER CLAIMS (DO NOT TRUST — VERIFY) ===
{builder_out[-6000:]}

YOUR JOB:
1. Inspect the current state of the repo (git status, files, tests).
2. Run the task's verification commands yourself — NEVER trust a prior summary.
3. Check the docs-contract: API shapes, paise-not-float, guard dependency, lock rules.
4. Check for foreign edits: git diff --stat should show ONLY files in this task's scope.
5. Verify with REAL command output (test names + pass/fail counts).

EVIDENCE REQUIREMENT (a verdict without this is rejected):
- Paste the ACTUAL command lines you ran and their ACTUAL output (test names + counts, or exact error text).
- Name the files you inspected with path:line where relevant.
- If you did not run anything, say so explicitly — never invent output.

OUTPUT FORMAT (exactly this, so the loop can parse it):
COMMANDS RUN:
- <exact command> → <observed result>
VERDICT: PASS  or  VERDICT: FAIL
FINDINGS:
- <concrete finding, with file:line or command output>
If FAIL, list exactly what the Builder must fix, ranked by severity.
"""


def monitor_prompt(task, phase, tester_result, builder_out, state, evidence_flag):
    """Assemble the MONITOR agent prompt (supervise Builder + Tester, decide ACTION).

    Flow:
        1. Set flag_note from evidence_flag ok|weak (matches has_real_evidence pre-check).
        2. Embed state.json snapshot, truncated builder/tester outputs.
        3. Require structured fields: TESTER_EVIDENCE, DEFECT, BUILDER_INSTRUCTION, ACTION, NEXT, REASON.

    Returns:
        str for hermes monitor role.

    Debug:
        parse_field(m_out, "ACTION") empty → defaults to ESCALATE in main().
        Monitor ADVANCE overridden when monitor_blocks True — see main() evidence gate.
    """
    next_phase = get_next_phase(phase)
    flag_note = {
        "ok": "The loop's independent evidence-gate found concrete command output in the Tester report.",
        "weak": ("WARNING: the loop's independent evidence-gate did NOT find concrete command "
                 "output (no test names/counts, no command lines) in the Tester report. Treat "
                 "the verdict as UNPROVEN and judge accordingly."),
    }[evidence_flag]
    return f"""You are the MONITOR agent for the GST Filing Platform build loop. You SUPERVISE the other two agents. You keep the loop moving WITHOUT human input.

PROJECT ROOT: {ROOT}

CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}
TASK DONE WHEN: {task.get('done_when', 'task complete')}

BUILD STATE (state.json): {json.dumps(state, indent=2)}

=== BUILDER OUTPUT (what was produced) ===
{builder_out[-4000:]}

=== TESTER REPORT (what was verified) ===
{tester_result[-6000:]}

=== INDEPENDENT EVIDENCE GATE ===
{flag_note}

ALL PHASES (from build/plan.json) — {len(load_json(PLAN_PATH, {})['phases'])} phases. You are on phase {phase['id']}.
There {'IS' if next_phase else 'is NO'} a next phase{' — ' + next_phase['id'] + ' ' + next_phase['name'] if next_phase else ''}.

YOUR SUPERVISION DUTIES — answer each explicitly:
A. EVIDENCE CHECK: did the Tester actually run commands and show real output, or did it just restate the Builder's claims? Quote the proof or say MISSING.
B. SCOPE CHECK: does the work match this task's scope and the docs contract? Name any drift.
C. CORRECTNESS GAP: name the single most important defect remaining, with file:line if possible. If truly none, say NONE.

Then DECIDE and output EXACTLY this format:

TESTER_EVIDENCE: REAL | WEAK
DEFECT: <one line — the most important remaining defect, or NONE>
BUILDER_INSTRUCTION: <one concrete, imperative instruction the Builder must follow next
                      attempt. Must name files/functions/commands. Write NONE only if you
                      are ADVANCEing with no further work.>
ACTION: ADVANCE   |   ACTION: RETRY   |   ACTION: ESCALATE
NEXT: <task id to do next, or "none">
REASON: <one line why>

RULES:
- ADVANCE only if the tester said PASS AND the evidence is REAL AND you found no blocking defect.
- RETRY if the tester said FAIL, OR the evidence is WEAK (force a real re-verification), OR a fix is clearly actionable. Always supply a BUILDER_INSTRUCTION on RETRY (max {MAX_RETRIES} retries already tracked).
- ESCALATE if: a task failed after retries, docs are ambiguous and you cannot resolve, or all phases are complete.
- When a phase's tasks are all done, set NEXT to the first task of the next phase. If no next phase, ACTION: ESCALATE with NEXT:none and REASON "all phases complete".
- If you ADVANCE, also decompose any stub task whose title contains "expand at runtime" into concrete tasks and write them into build/plan.json BEFORE proceeding (real tasks with id/title/docs/done_when).
"""


def feedback_prompt(task, phase, tester_result, monitor_instruction):
    """Assemble BUILDER-FEEDBACK prompt (condense tester + monitor into fix list).

    Flow:
        1. Embed tester findings and monitor BUILDER_INSTRUCTION.
        2. Ask for ≤30 line ordered fix list with proof commands.

    Returns:
        str for optional feedback model on RETRY path.

    Debug:
        Stored in slot tester_feedback when non-empty; else raw t_out is reused.
    """
    return f"""You are the BUILDER-FEEDBACK agent for the GST Filing Platform. You do NOT build the whole feature. You turn the Tester's findings and the Monitor's instruction into a precise fix plan for the Builder.

PROJECT ROOT: {ROOT}
CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}
TASK DONE WHEN: {task.get('done_when', 'task complete')}

=== TESTER FINDINGS ===
{tester_result[-6000:]}

=== MONITOR INSTRUCTION ===
{monitor_instruction}

Produce a short, ordered fix list. For each item: the file plus the exact change, and the
command that proves it is fixed. Be specific enough that no guessing is required.
Keep it under 30 lines. Do not add new scope beyond this task.
"""


def get_phases():
    """Load phase list from build/plan.json (key "phases").

    Flow:
        load_json(PLAN_PATH, {"phases": []})["phases"]

    Debug:
        Missing plan.json → empty phases → first_pending_task returns None immediately.
    """
    return load_json(PLAN_PATH, {"phases": []})["phases"]


def get_next_phase(phase):
    """Return the phase dict after `phase` in plan order, or None if last.

    Flow:
        1. Linear scan get_phases() for matching phase["id"].
        2. Return phases[i+1] if exists.

    Debug:
        Used only in monitor_prompt text (phase transition hints).
    """
    phases = get_phases()
    for i, p in enumerate(phases):
        if p["id"] == phase["id"] and i + 1 < len(phases):
            return phases[i + 1]
    return None


def first_pending_task():
    """Select the next task to run from plan + state.

    Flow:
        1. Walk phases in plan order, tasks in array order.
        2. Read state["tasks"][task_id].status default "pending".
        3. First task with status in ("pending", "blocked") wins.
        4. If none → (None, None) → main sets status complete.

    Returns:
        tuple[task dict, phase dict] or (None, None).

    Debug:
        Task stuck "blocked" after escalation — edit state.json status to pending to retry.
        "done" tasks are skipped; no backward revisit unless status reset manually.
    """
    phases = get_phases()
    state = load_json(STATE_PATH, {})
    for phase in phases:
        for task in phase["tasks"]:
            sid = task["id"]
            status = state.get("tasks", {}).get(sid, {}).get("status", "pending")
            if status in ("pending", "blocked"):
                return task, phase
    return None, None


def parse_field(text, field):
    """Pull `FIELD: value` out of an agent report (first line match, case-insensitive key).

    Flow:
        1. Split text into lines; strip each.
        2. First line starting with field: (case insensitive) → value after first colon.

    Returns:
        str value or "" if not found.

    Debug:
        Monitor must emit "ACTION: ADVANCE" exactly — extra words on same line OK after first colon.
    """
    prefix = field + ":"
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith(prefix.upper()):
            return s.split(":", 1)[1].strip()
    return ""


def has_real_evidence(tester_out: str) -> bool:
    """Independent gate: does the Tester report contain real command evidence?

    Flow:
        1. Lowercase tester output.
        2. signal +1 if "commands run:" present.
        3. signal +1 if any tool marker (pytest, ruff, npm, git, ...) found.
        4. signal +1 if any result marker (passed, failed, traceback, ...).
        5. Return signals >= 2 (needs both command-ish and outcome-ish text).

    Returns:
        bool — False triggers evidence_flag "weak" in monitor_prompt and monitor_blocks.

    Debug:
        Tester pasted commands but still weak → missing result keywords or COMMANDS RUN header.
        Deliberately mechanical; tune markers list if legitimate reports fail gate.
    """
    t = (tester_out or "").lower()
    lowered = t
    signals = 0
    if "COMMANDS RUN:".lower() in lowered:
        signals += 1
    for marker in ("pytest", "python -m", "npm ", "npx ", "ruff", "mypy", "git ",
                   "curl ", "alembic", "eslint", "tsc "):
        if marker in lowered:
            signals += 1
            break
    for marker in ("passed", "failed", "error", "traceback", "assert", "no tests ran"):
        if marker in lowered:
            signals += 1
            break
    # a bare restatement of the builder's claims has neither command lines nor results
    return signals >= 2


def main():
    """CLI entry: run the 3-agent build loop until done, escalated, or capped.

    Flow:
        1. Parse --once, --no-probe, --max-iters N.
        2. mkdir build/, acquire_lock(), load/seed state.json.
        3. resolve_roles(probe=not no_probe) → log model picks.
        4. FOR it in 1..max_iters:
           a. first_pending_task() — break complete if None.
           b. Reset slot status pending; save state.
           c. BUILD: run_role builder + builder_prompt(feedback, instruction from prior retry).
           d. TEST: run_role tester; set passed = "VERDICT: PASS" in t_out;
              real_ev = has_real_evidence(t_out).
           e. MONITOR: run_role monitor with evidence ok|weak; parse ACTION, BUILDER_INSTRUCTION, etc.
           f. monitor_blocks = not passed OR not real_ev OR m_evidence == "WEAK".
           g. If ACTION ADVANCE and not monitor_blocks → slot done.
           h. If ACTION ADVANCE and monitor_blocks → retry or escalate (evidence override).
           i. If ACTION RETRY → increment retries, optional feedback agent, continue loop.
           j. Else ESCALATE → break.
           k. if --once: break after one full cycle.
        5. release_lock(); return 0.

    Debug:
        Watch log lines verdict= / monitor: ACTION= / ↻ retry / 🛑 ESCALATED.
        state["tasks"][sid]["history"] holds truncated agent outputs per role.
    """
    once = "--once" in sys.argv
    no_probe = "--no-probe" in sys.argv
    max_iters = MAX_ITERATIONS
    if "--max-iters" in sys.argv:
        max_iters = int(sys.argv[sys.argv.index("--max-iters") + 1])

    os.makedirs(BUILD_DIR, exist_ok=True)
    acquire_lock()
    state = load_json(STATE_PATH, {
        "iteration": 0, "status": "ready", "current_task": None,
        "tasks": {}, "last_monitor_decision": None, "escalations": [],
    })

    log("═══ 3-agent build loop started ═══")
    roles = resolve_roles(probe=not no_probe)
    log(f"Builder={roles['builder']['model']} · "
        f"Tester={roles['tester']['model']} · "
        f"Monitor={roles['monitor']['model']} · "
        f"Feedback={roles['feedback']['model']}")

    for it in range(1, max_iters + 1):
        state["iteration"] = it
        state["roles"] = roles
        save_json(STATE_PATH, state)

        task, phase = first_pending_task()
        if task is None:
            log("No pending tasks — all phases complete or plan exhausted.")
            state["status"] = "complete"
            save_json(STATE_PATH, state)
            break

        sid = task["id"]
        log(f"\n━━ CYCLE {it} · phase {phase['id']} · task {sid} — {task['title']} ━━")
        state["current_task"] = sid
        slot = state["tasks"].setdefault(sid, {"status": "pending", "retries": 0, "history": []})
        slot["status"] = "pending"
        save_json(STATE_PATH, state)

        # ── BUILD (carrying the Monitor's standing instruction from prior RETRY) ──
        feedback = slot.get("tester_feedback")
        instruction = slot.get("monitor_instruction")
        b_out, b_used = run_role("builder", ROLE_CHAINS["builder"],
                                 builder_prompt(task, phase, feedback, instruction),
                                 start=roles["builder"]["model"])
        slot["builder_model_used"] = b_used["model"]
        slot["history"].append({"role": "builder", "out": b_out[-4000:],
                                "ts": datetime.now().isoformat()})
        save_json(STATE_PATH, state)

        # ── TEST (tester sees the builder's claims, must verify independently) ──
        t_out, t_used = run_role("tester", ROLE_CHAINS["tester"],
                                 tester_prompt(task, phase, b_out),
                                 start=roles["tester"]["model"])
        slot["tester_model_used"] = t_used["model"]
        slot["history"].append({"role": "tester", "out": t_out[-4000:],
                                "ts": datetime.now().isoformat()})
        passed = "VERDICT: PASS" in t_out
        real_ev = has_real_evidence(t_out)
        slot["last_verdict"] = "PASS" if passed else "FAIL"
        slot["tester_evidence"] = "real" if real_ev else "weak"
        save_json(STATE_PATH, state)
        log(f"  verdict={'PASS' if passed else 'FAIL'} evidence={'real' if real_ev else 'WEAK'}")

        # ── MONITOR SUPERVISION (inspects BOTH agents, issues ACTION + BUILDER_INSTRUCTION) ──
        role_chain = ROLE_CHAINS["monitor"]
        m_out, _ = run_role("monitor", role_chain,
                            monitor_prompt(task, phase, t_out, b_out, state,
                                             "ok" if real_ev else "weak"),
                            start=roles["monitor"]["model"], timeout=1200)
        state["last_monitor_decision"] = m_out
        slot["history"].append({"role": "monitor", "out": m_out[-4000:],
                                "ts": datetime.now().isoformat()})
        action = (parse_field(m_out, "ACTION") or "ESCALATE").upper()
        m_instruction = parse_field(m_out, "BUILDER_INSTRUCTION")
        m_evidence = (parse_field(m_out, "TESTER_EVIDENCE") or "").upper()
        slot["monitor_evidence_call"] = m_evidence
        slot["monitor_defect"] = parse_field(m_out, "DEFECT")
        log(f"  monitor: ACTION={action} evidence={m_evidence or '?'} "
            f"defect={slot['monitor_defect'][:80]!r}")

        # Stricter than Monitor alone: cannot ADVANCE on FAIL, weak evidence, or TESTER_EVIDENCE: WEAK
        monitor_blocks = (not passed) or (not real_ev) or (m_evidence == "WEAK")

        if action == "ADVANCE" and not monitor_blocks:
            slot["status"] = "done"
            log(f"  ✅ task {sid} DONE (evidence verified)")
        elif action == "ADVANCE" and monitor_blocks:
            # monitor tried to advance but the evidence does not support it
            if slot.get("retries", 0) < MAX_RETRIES:
                slot["retries"] = slot.get("retries", 0) + 1
                slot["tester_feedback"] = t_out
                slot["monitor_instruction"] = (
                    m_instruction if m_instruction.upper() != "NONE"
                    else "The previous verification was not backed by real command output. "
                         "Re-run the task's verification commands and paste the exact output."
                )
                log(f"  ↻ evidence gate overrode ADVANCE — retry "
                    f"{slot['retries']}/{MAX_RETRIES}")
            else:
                slot["status"] = "blocked"
                state["escalations"].append({
                    "task": sid, "reason": "Monitor advanced on unproven evidence",
                    "ts": datetime.now().isoformat()})
                state["status"] = "escalated"
                log("  🛑 ESCALATED: Monitor advanced on unproven evidence")
                save_json(STATE_PATH, state)
                break
            save_json(STATE_PATH, state)
            continue
        elif action == "RETRY":
            if slot.get("retries", 0) < MAX_RETRIES:
                slot["retries"] = slot.get("retries", 0) + 1
                # the feedback model turns findings + instruction into a fix list
                fix = ""
                if m_instruction and m_instruction.upper() != "NONE":
                    fix, _ = run_role("feedback", FEEDBACK_CHAIN,
                                      feedback_prompt(task, phase, t_out, m_instruction),
                                      start=roles["feedback"]["model"], timeout=900)
                    slot["history"].append({"role": "feedback", "out": fix[-3000:],
                                            "ts": datetime.now().isoformat()})
                slot["tester_feedback"] = (fix or t_out)
                slot["monitor_instruction"] = m_instruction
                log(f"  ↻ monitor: retry {slot['retries']}/{MAX_RETRIES} — "
                    f"instructed builder")
            else:
                slot["status"] = "blocked"
                state["escalations"].append({
                    "task": sid, "reason": "retries exhausted",
                    "ts": datetime.now().isoformat()})
                state["status"] = "escalated"
                log("  🛑 ESCALATED: retries exhausted")
                save_json(STATE_PATH, state)
                break
        else:  # ESCALATE
            reason = parse_field(m_out, "REASON")
            state["escalations"].append({"task": sid, "reason": reason,
                                         "ts": datetime.now().isoformat()})
            state["status"] = "escalated"
            log(f"  🛑 ESCALATED: {reason}")
            save_json(STATE_PATH, state)
            break

        save_json(STATE_PATH, state)

        if once:
            log("--once: single cycle complete.")
            break

    log("═══ loop ended ═══")
    release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
