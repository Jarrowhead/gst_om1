#!/usr/bin/env python3
"""
GST Filing App — 3-Agent Autonomous Build Loop.

Roles (all via `hermes chat --oneshot` subprocesses):
  BUILDER  = glm-5.3-flash      (writes code, follows docs)
  TESTER   = deepseek-v4-flash  (runs tests, PASS/FAIL, re-verifies)
  MONITOR  = glm-5.3-flash      (decides next task / escalates / advances phases)

The loop runs until all phases in build/plan.json are done, or the monitor
escalates to the human, or MAX_ITERATIONS is hit. Every decision is logged
to build/state.json and build/run.log.

Usage:
    python scripts/build_loop.py [--once] [--max-iters N]

--once: run a single cycle (pick next task, build, test, monitor) then exit.
        Without it, loops until finished.
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

# ── Role model assignments ────────────────────────────────────────────────
BUILDER_MODEL = "glm-5.3-flash"
BUILDER_PROVIDER = "ollama-cloud"
TESTER_MODEL = "deepseek-v4-flash"
TESTER_PROVIDER = "ollama-cloud"
MONITOR_MODEL = "glm-5.3-flash"
MONITOR_PROVIDER = "ollama-cloud"

MAX_RETRIES = 2          # builder re-attempts after a tester FAIL
MAX_ITERATIONS = 200     # safety cap on total cycles


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def run_agent(model, provider, prompt, max_turns=250, timeout=1800):
    """Run one hermes chat --oneshot subprocess and return its stdout."""
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
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        log(f"  ✗ {model} TIMEOUT after {timeout}s")
        return f"TIMEOUT after {timeout}s"
    dt = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    log(f"  ✓ {model} finished in {dt:.0f}s (exit {proc.returncode})")
    return out


# ── Prompt builders ────────────────────────────────────────────────────────

def builder_prompt(task, phase, tester_feedback=None):
    fb = ""
    if tester_feedback:
        fb = (
            "\n\n=== PREVIOUS TESTER FEEDBACK (FIX THESE FIRST) ===\n"
            f"{tester_feedback}\n"
        )
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
8. If a doc is ambiguous, STOP and say so in your final summary — do not guess in the GST domain.
{fb}
FINISH by running the task's verification yourself and reporting exact test names + counts in your final summary. Your final summary is what the Tester will read — be precise.
"""


def tester_prompt(task, phase):
    return f"""You are the TESTER agent for the GST Filing Platform. You do NOT write feature code. You verify the Builder's work and report PASS or FAIL.

PROJECT ROOT: {ROOT}
Docs in {ROOT}/docs/ are the contract.

CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}
TASK DONE WHEN: {task.get('done_when', 'task complete')}
RELEVANT DOCS: {', '.join(task.get('docs', []))}

YOUR JOB:
1. Inspect the current state of the repo (git status, files, tests).
2. Run the task's verification commands yourself — NEVER trust a prior summary.
3. Check the docs-contract: API shapes, paise-not-float, guard dependency, lock rules.
4. Check for foreign edits: git diff --stat should show ONLY files in this task's scope.
5. Verify with REAL command output (test names + pass/fail counts).

OUTPUT FORMAT (exactly this, so the loop can parse it):
VERDICT: PASS  or  VERDICT: FAIL
FINDINGS:
- <concrete finding, with file:line or command output>
If FAIL, list exactly what the Builder must fix, ranked by severity.
"""


def monitor_prompt(task, phase, tester_result, state):
    next_phase = get_next_phase(phase)
    return f"""You are the MONITOR agent for the GST Filing Platform build loop. You keep the loop moving WITHOUT human input.

PROJECT ROOT: {ROOT}

CURRENT PHASE: {phase['id']} — {phase['name']}
CURRENT TASK: {task['id']} — {task['title']}
TASK DONE WHEN: {task.get('done_when', 'task complete')}

BUILD STATE (state.json): {json.dumps(state, indent=2)}

TESTER RESULT FOR THIS TASK:
{tester_result}

ALL PHASES (from build/plan.json) — {len(load_json(PLAN_PATH, {})['phases'])} phases. You are on phase {phase['id']}.

DECIDE and output EXACTLY this format:

ACTION: ADVANCE   |   ACTION: RETRY   |   ACTION: ESCALATE
NEXT: <task id to do next, or "none">
REASON: <one line why>

RULES:
- ADVANCE if the tester said PASS and there is a next pending task.
- RETRY if the tester said FAIL but a fix is clearly actionable (max {MAX_RETRIES} retries already tracked).
- ESCALATE if: a task failed after retries, docs are ambiguous and you cannot resolve, or all phases are complete.
- When a phase's tasks are all done, set NEXT to the first task of the next phase. If no next phase, ACTION: ESCALATE with NEXT:none and REASON "all phases complete".
- If you ADVANCE, also decompose any stub task whose title contains "expand at runtime" into concrete tasks and write them into build/plan.json BEFORE proceeding (real tasks with id/title/docs/done_when).
"""


def get_phases():
    return load_json(PLAN_PATH, {"phases": []})["phases"]


def get_next_phase(phase):
    phases = get_phases()
    for i, p in enumerate(phases):
        if p["id"] == phase["id"] and i + 1 < len(phases):
            return phases[i + 1]
    return None


def first_pending_task():
    phases = get_phases()
    state = load_json(STATE_PATH, {})
    for phase in phases:
        for task in phase["tasks"]:
            sid = task["id"]
            status = state.get("tasks", {}).get(sid, {}).get("status", "pending")
            if status == "pending":
                return task, phase
    return None, None


def main():
    once = "--once" in sys.argv
    max_iters = MAX_ITERATIONS
    if "--max-iters" in sys.argv:
        max_iters = int(sys.argv[sys.argv.index("--max-iters") + 1])

    os.makedirs(BUILD_DIR, exist_ok=True)
    state = load_json(STATE_PATH, {
        "iteration": 0, "status": "ready", "current_task": None,
        "tasks": {}, "last_monitor_decision": None, "escalations": [],
    })

    log("═══ 3-agent build loop started ═══")
    log(f"Builder={BUILDER_MODEL} · Tester={TESTER_MODEL} · Monitor={MONITOR_MODEL}")

    for it in range(1, max_iters + 1):
        state["iteration"] = it
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
        state["tasks"].setdefault(sid, {"status": "pending", "retries": 0, "history": []})
        save_json(STATE_PATH, state)

        # ── BUILD ──
        feedback = state["tasks"][sid].get("tester_feedback")
        b_out = run_agent(BUILDER_MODEL, BUILDER_PROVIDER, builder_prompt(task, phase, feedback))
        state["tasks"][sid]["history"].append({"role": "builder", "out": b_out[-4000:], "ts": datetime.now().isoformat()})
        save_json(STATE_PATH, state)

        # ── TEST ──
        t_out = run_agent(TESTER_MODEL, TESTER_PROVIDER, tester_prompt(task, phase))
        state["tasks"][sid]["history"].append({"role": "tester", "out": t_out[-4000:], "ts": datetime.now().isoformat()})
        passed = "VERDICT: PASS" in t_out
        state["tasks"][sid]["last_verdict"] = "PASS" if passed else "FAIL"
        save_json(STATE_PATH, state)

        # ── RETRY LOGIC ──
        if not passed:
            retries = state["tasks"][sid].get("retries", 0)
            if retries < MAX_RETRIES:
                state["tasks"][sid]["retries"] = retries + 1
                state["tasks"][sid]["tester_feedback"] = t_out
                log(f"  ↻ task {sid} FAIL — retry {retries + 1}/{MAX_RETRIES}")
                save_json(STATE_PATH, state)
                continue
            else:
                log(f"  ⚠ task {sid} FAIL after {MAX_RETRIES} retries — escalating to monitor")
                state["tasks"][sid]["status"] = "blocked"
                save_json(STATE_PATH, state)

        # ── MONITOR DECISION ──
        m_out = run_agent(MONITOR_MODEL, MONITOR_PROVIDER, monitor_prompt(task, phase, t_out, state))
        state["last_monitor_decision"] = m_out
        state["tasks"][sid]["history"].append({"role": "monitor", "out": m_out[-4000:], "ts": datetime.now().isoformat()})

        action = "ESCALATE"
        for line in m_out.splitlines():
            if line.strip().startswith("ACTION:"):
                action = line.split(":", 1)[1].strip()

        if action == "ADVANCE":
            state["tasks"][sid]["status"] = "done"
            log(f"  ✅ task {sid} DONE")
        elif action == "RETRY":
            state["tasks"][sid]["retries"] += 1
            state["tasks"][sid]["tester_feedback"] = t_out
            log(f"  ↻ monitor: retry task {sid}")
        elif action == "ESCALATE":
            reason = next((l for l in m_out.splitlines() if l.strip().startswith("REASON:")), "")
            state["escalations"].append({"task": sid, "reason": reason, "ts": datetime.now().isoformat()})
            state["status"] = "escalated"
            log(f"  🛑 ESCALATED: {reason}")
            save_json(STATE_PATH, state)
            break
        save_json(STATE_PATH, state)

        if once:
            log("--once: single cycle complete.")
            break

    log("═══ loop ended ═══")
    return 0


if __name__ == "__main__":
    sys.exit(main())
