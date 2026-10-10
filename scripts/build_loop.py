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
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
# 10 Oct 2026: HYBRID SET (Tony's explicit decision). Ollama-cloud primaries;
# OmniRoute (localhost:20128) frontier routes as the coding/reasoning failovers.
#   builder  = glm-5.3-flash        → auto/best-coding     (OmniRoute)
#   tester   = deepseek-v4.1-flash  → auto/best-reasoning  (OmniRoute)
#   monitor  = glm-5.3 (no failover)
# OmniRoute auto/best-coding + auto/best-reasoning are the REAL frontier routes
# (verified live 10 Oct: 12345.67*0.18 → 2222, NOT the stub's 14567.9); neither
# is in STUB_ROUTES. Monitor glm-5.3 stays OFF the tester route (judge must not
# share the evidence producer's upstream).
ROLE_CHAINS = {
    "builder": [
        ("glm-5.3-flash", "ollama-cloud"),
        ("auto/best-coding", "omniroute"),
    ],
    "tester": [
        ("deepseek-v4.1-flash", "ollama-cloud"),
        ("auto/best-reasoning", "omniroute"),
    ],
    "monitor": [
        ("glm-5.3", "ollama-cloud"),
    ],
}

# Builder-feedback / rework model chain (used to turn tester findings into a fix).
FEEDBACK_CHAIN = [
    ("glm-5.3", "ollama-cloud"),
    ("auto/best-coding", "omniroute"),
]

# Routes that currently answer with a LOCAL STUB rather than the real upstream:
# they return 200 and plausible-looking text from local qwen2.5:7b-ctx64k, but
# the arithmetic is wrong (GST 12345.67 @18% → 14567.9 instead of 14567.89), so
# their output must never be accepted as build/test work. They stay in the
# chains above (so the loop re-probes them every run and promotes them the moment
# omniroute repairs them) but runtime failover skips them. Delete a name here to
# re-enable it.
STUB_ROUTES = {
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
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


# ── Singleton lock ─────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Best-effort cross-platform 'is this PID running?' check."""
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
    """Refuse to start when another live loop holds the lock."""
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
    """Remove the lock file if this process owns it."""
    try:
        if os.path.exists(LOCK_PATH):
            with open(LOCK_PATH, encoding="utf-8") as f:
                owner = f.read().strip()
            if owner == str(os.getpid()):
                os.remove(LOCK_PATH)
    except OSError:
        pass


def agent_env():
    """Env for child `hermes` processes.

    TERM=dumb matters on Windows: when the loop is launched from a bash/MSYS
    shell, TERM=xterm-256color leaks in and prompt_toolkit misbehaves in the
    child, so `hermes chat` behaves inconsistently. Forcing a dumb terminal
    keeps the child in plain line mode.
    """
    env = dict(os.environ)
    env["TERM"] = "dumb"
    env.setdefault("NO_COLOR", "1")
    return env


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
        # CREATE_NEW_PROCESS_GROUP lets us kill the whole tree on timeout. On
        # Windows, subprocess.run(timeout=...) kills only the top process (the
        # .cmd shim); the hermes oneshot child it spawned survives as an orphan
        # and keeps the stdout pipe open, so run()'s internal communicate() then
        # blocks FOREVER and the loop freezes with no TIMEOUT line in run.log
        # (observed 3 Oct 2026: two silent hangs, 62 min and 46+ min).
        #
        # Liveness policy (Tony, 3 Oct): do NOT kill a working long-runner.
        # 1800s is no longer a hard kill deadline except as the STUCK-DOWN
        # backstop below. kill_tree() is only called when the agent is judged
        # stuck: for every check window, if the whole agent tree accumulated
        # less than STUCK_MIN_CPU seconds of CPU time, it is idle-waiting
        # (dead upstream reply, hung tool subprocess) — kill it. A genuinely
        # thinking/streaming agent accumulates CPU every window and keeps its
        # run no matter how long it takes. MEASURED 3 Oct 16:05 on this host:
        # a live, actively tool-calling agent tree accumulates only ~0.45–1.05
        # CPU-seconds per 120 s (i.e. ~0.04s CPU per 10 min); a genuinely idle
        # tree measures 0.00s. The original 10.0s/120s threshold was ~10x above
        # the working rate and false-killed every builder rung for ~90 min
        # (15:04–16:05, 10 STUCK kills, zero verdicts). Threshold is now
        # 0.2s/600s: ~5x below the observed floor, still above measured idle 0.
        # Never raise it back to a seconds-per-2-min figure.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=agent_env(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        import psutil  # installed into this interpreter (tools py3.14) 3 Oct

        def tree_pids(root_pid: int) -> list[int]:
            """Agent + all descendants (hermes shim -> agent kernel -> tools)."""
            p_ids: list[int] = []
            try:
                parent = psutil.Process(root_pid)
                p_ids.append(root_pid)
                for ch in parent.children(recursive=True):
                    p_ids.append(ch.pid)
            except psutil.Error:
                pass
            return p_ids

        def tree_cpu(root_pid: int) -> float:
            total = 0.0
            for pid in tree_pids(root_pid):
                try:
                    total += psutil.Process(pid).cpu_times().user
                except (psutil.Error, OSError):
                    pass
            return total

        def kill_tree(root_pid: int) -> None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(root_pid)],
                           capture_output=True, check=False)
            try:
                proc.kill()
            except Exception:
                pass

        # ── Multi-signal liveness (Tony, 7 Oct) ──────────────────────────
        # CPU alone has a blind spot: an agent waiting on a network response
        # from ollama-cloud shows ~0 CPU but is still alive. We now check FOUR
        # signals: CPU, active TCP connections, disk I/O, and memory growth.
        # An agent is only STUCK if ALL FOUR are flat — any one being active
        # means it's working (streaming, reading, writing, or waiting on API).
        # This lets us safely check every 120s instead of 600s.

        def tree_net_connections(root_pid: int) -> list:
            """Active REMOTE TCP connections across the whole agent tree.

            Only connections to a non-loopback peer count as evidence the agent
            is 'waiting on the API'. The agent process keeps one or two
            ESTABLISHED sockets to the LOCAL Hermes gateway (127.0.0.1 / ::1);
            those stay up even after the upstream model socket has died, so
            counting them made a dead route read as alive forever (a monitor sat
            29 min on a ~2.5 min median with only localhost sockets open).
            Loopback peers are therefore excluded from the liveness signal.
            """
            conns = []
            for pid in all_agent_pids():
                try:
                    for c in psutil.Process(pid).net_connections(kind="tcp"):
                        if c.status != psutil.CONN_ESTABLISHED or not c.raddr:
                            continue
                        peer = str(c.raddr.ip) if hasattr(c.raddr, "ip") else str(c.raddr[0])
                        if peer.startswith("127.") or peer in ("::1", "0.0.0.0", "::"):
                            continue  # local gateway socket, not the model API
                        conns.append((pid, c.laddr, c.raddr, c.status))
                except (psutil.Error, OSError):
                    pass
            return conns

        def tree_io(root_pid: int) -> int:
            """Total disk I/O bytes (read + write) across the whole agent tree."""
            total = 0
            for pid in all_agent_pids():
                try:
                    io = psutil.Process(pid).io_counters()
                    total += io.read_bytes + io.write_bytes
                except (psutil.Error, OSError, AttributeError):
                    pass
            return total

        def tree_mem(root_pid: int) -> int:
            """Total RSS memory (bytes) across the whole agent tree."""
            total = 0
            for pid in all_agent_pids():
                try:
                    total += psutil.Process(pid).memory_info().rss
                except (psutil.Error, OSError):
                    pass
            return total

        STUCK_CHECK_INTERVAL = 120   # every 2 min (was 600 — safe with 4 signals)
        STUCK_MIN_CPU = 0.2          # < 0.2s CPU per window = idle
        # Hard cap = the per-role `timeout` arg (builder 7200, tester 1800,
        # monitor 1200/900, feedback 900). This is the busy-wait safety net:
        # a hung agent busy-polls at ~0.6s CPU/120s (above STUCK_MIN_CPU=0.2)
        # so the 4-signal detector misses it; the wall-clock cap catches it.
        # An agent with active TCP connections is waiting on the API — NOT stuck.
        # Require ALL signals flat to declare stuck (CPU + no TCP + no I/O delta + no mem delta).
        STUCK_MIN_IO_DELTA = 1024          # < 1KB I/O delta = no disk activity
        STUCK_MIN_MEM_DELTA = 256 * 1024   # < 256KB mem delta = no streaming/loading
        # Number of CONSECUTIVE all-flat windows required before a kill. Requiring
        # only one window let a transient lull (GC pause, a sample where a child
        # had not yet spawned) look dead, and — worse — the window reset on any
        # one noisy reading, so a genuinely dead socket could wait an hour before
        # a window happened to stay flat. Three consecutive 120s windows (6 min)
        # is long enough to be certain a socket is dead and short enough to stop
        # paying ~58 min per dead agent. Measured: 144 of 218 min in one stretch
        # (66% of wall-clock) went to two agents that had already died.
        STUCK_WINDOWS_TO_KILL = 3

        # On Windows, psutil.children(recursive=True) can miss grandchildren that
        # spawned between checks, so tree_pids() may return a subset. As a safety
        # net, also scan for ANY python.exe/hermes.exe process whose command line
        # matches this agent's --query-file — those are guaranteed to be our
        # children even if the parent chain is broken.
        AGENT_QUERY_FILE = PROMPT_FILE  # the --query-file arg passed to hermes

        def all_agent_pids() -> list[int]:
            """Find all processes that share this agent's --query-file arg."""
            pids = set(tree_pids(proc.pid))
            try:
                import psutil as _ps
                for p in _ps.process_iter(["pid", "cmdline"]):
                    cl = " ".join(p.info.get("cmdline") or [])
                    if PROMPT_FILE.replace("\\", "\\\\") in cl or PROMPT_FILE in cl:
                        pids.add(p.pid)
            except Exception:
                pass
            return list(pids)

        def tree_cpu_safe() -> float:
            total = 0.0
            for pid in all_agent_pids():
                try:
                    total += psutil.Process(pid).cpu_times().user
                except (psutil.Error, OSError):
                    pass
            return total

        deadline = time.time() + timeout
        window_start = time.time()
        cpu_start = tree_cpu_safe()
        io_start = tree_io(proc.pid)
        mem_start = tree_mem(proc.pid)
        flat_windows = 0
        while proc.poll() is None:
            now_t = time.time()
            if now_t >= deadline:
                log(f"  ✗ {model} HARD CAP {timeout}s reached — killing tree")
                kill_tree(proc.pid)
                try:
                    proc.communicate(timeout=60)
                except Exception:
                    pass
                return f"TIMEOUT after {timeout}s"
            if now_t - window_start >= STUCK_CHECK_INTERVAL:
                cpu_now = tree_cpu_safe()
                window_cpu = cpu_now - cpu_start
                net_conns = tree_net_connections(proc.pid)
                io_now = tree_io(proc.pid)
                io_delta = io_now - io_start
                mem_now = tree_mem(proc.pid)
                mem_delta = mem_now - mem_start
                # A live process can never DECREASE its cumulative CPU counter or
                # have its whole tree's disk I/O / RSS shrink. A negative delta
                # means the PID set changed between samples (a short-lived child
                # exited and its totals dropped out of the sum), NOT that the
                # agent is idle. Treating "< threshold" as flat then made
                # `all_flat` true for a perfectly healthy agent and killed it
                # (observed: STUCK after 240s and 720s with CPU=-17.3s/-23.1s,
                # IO_delta=-52MB/-76MB, MEM_delta=-27MB/-68MB, while the builder
                # was streaming a real reply). Only a genuinely flat-or-growing
                # signal counts as inactive.
                def flat(delta: float) -> bool:
                    return 0 <= delta < STUCK_MIN_CPU

                all_flat = (
                    flat(window_cpu)
                    and len(net_conns) == 0
                    and 0 <= io_delta < STUCK_MIN_IO_DELTA
                    and 0 <= mem_delta < STUCK_MIN_MEM_DELTA
                )
                if all_flat:
                    flat_windows += 1
                else:
                    flat_windows = 0
                if flat_windows >= STUCK_WINDOWS_TO_KILL:
                    log(f"  ✗ {model} STUCK {now_t - t0:.0f}s in — "
                        f"{flat_windows} consecutive flat windows, "
                        f"CPU={window_cpu:.1f}s, TCP={len(net_conns)}, "
                        f"IO_delta={io_delta}B, MEM_delta={mem_delta}B — "
                        f"killing tree")
                    kill_tree(proc.pid)
                    try:
                        proc.communicate(timeout=60)
                    except Exception:
                        pass
                    return f"TIMEOUT (stuck) after {now_t - t0:.0f}s"
                # At least one signal is active — agent is working or waiting on API
                cpu_start = cpu_now
                window_start = now_t
                io_start = io_now
                mem_start = mem_now
            time.sleep(5)
        out, _ = proc.communicate()
    except Exception as exc:  # spawn failure = route fault, let the chain fail over
        log(f"  ✗ {model} SPAWN FAILED: {exc}")
        return f"SPAWN FAILED: {exc}"
    dt = time.time() - t0
    return_code = proc.returncode
    out_text = out or ""
    log(f"  ✓ {model} finished in {dt:.0f}s (exit {return_code})")
    return out_text


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

    A route is dead only when it produced NO usable output and shows a real
    auth/quota marker, or it emitted essentially nothing at all. A long,
    coherent report is a successful run even if it happens to mention a number
    like '403' while describing the code — so the marker only counts against a
    SHORT/empty report.

    Historical bug this guards against: an earlier version also required the
    literal string "session_id:" as proof of a healthy run, but the child agents
    never emit it. That made every primary rung read as FAILED AT RUNTIME even
    though it exited 0 and committed its work (e.g. glm-5.3 committed task 8.1 at
    14:00:59 and was discarded), costing a full redundant walk of the chain
    (~4-10 min) on every task. Do not re-introduce a mandatory session marker.
    """
    text = (out or "").strip()
    if not text:
        return True
    if "TIMEOUT" in text[:40] or text.startswith("SPAWN FAILED"):
        return True
    low = text.lower()
    has_marker = any(m in low for m in RUNTIME_FAIL_MARKERS)
    # A substantial report is real work regardless of incidental numbers in it.
    if len(text) >= 400:
        return False
    # Short output: it is only a dead route if it actually carries a failure marker.
    return has_marker


def run_role(role: str, chain, prompt, roles_path_fallback=None, timeout=1800, start=None):
    """Run a role, failing over across its chain at RUNTIME (not just at probe).

    The probe only proves the route answers a one-liner; free tiers routinely die
    on the first real tool-using turn. So the chain is walked here too and the
    first route that returns usable work wins.

    `start` = the model the probe declared live for this role. The walk BEGINS
    there instead of at the head of the chain, so a run does not burn minutes
    re-discovering an already-known-dead route. Rotating keeps the rest of the
    chain as fallback in priority order.
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
    """Tiny liveness probe using the loop's real spawn path."""
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
    """Pick the first live model per role. Cached in build/role_models.json.

    With probe=False we reuse the cached live roles if they exist. Falling
    straight to each chain HEAD is wrong: the head is the *preferred* model, not
    a known-live one, so a --no-probe restart can hand the loop a model that was
    already probed DEAD (observed: omniroute free tiers, every role).
    """
    if not probe:
        cached = load_json(ROLES_PATH, {})
        if all(r in cached and cached[r].get("model") for r in ROLE_CHAINS) \
                and cached.get("feedback", {}).get("model"):
            log("  using cached live roles from build/role_models.json")
            return cached
        log("  ⚠ no usable role cache — falling back to chain heads")
        resolved = {role: {"model": chain[0][0], "provider": chain[0][1]}
                    for role, chain in ROLE_CHAINS.items()}
        resolved["feedback"] = {"model": FEEDBACK_CHAIN[0][0], "provider": FEEDBACK_CHAIN[0][1]}
        return resolved

    resolved: dict = {}
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
7. Do NOT touch files outside this task's scope. In your summary, declare the EXACT list of files you created/modified.
8. NEVER modify any file under build/ (plan.json, state.json, run.log, prompt.txt) or scripts/build_loop.py — those are the loop's own control files, not project code. Editing them corrupts the build loop.
9. If a doc is ambiguous, STOP and say so in your final summary — do not guess in the GST domain.
10. NEVER run git reset --hard, git checkout -- ., git clean, or any command that discards the working tree or other tasks' files. Your scope is this task's files only.
11. COMMIT YOUR WORK before you finish (this is how your work survives): git add ONLY the files you created/modified for THIS task (never git add -A, never git add .), then git commit -m "TASK {task['id']}: <short title>". If tests pass, the commit is mandatory — uncommitted work is treated as not delivered. If commit hooks fail, fix the committed issue itself; never amend or rewrite others' commits.
{fb}
FINISH by running the task's verification yourself and reporting exact test names + counts in your final summary, then the commit SHA (git log --oneline -1). Your final summary is what the Tester will read — be precise.
"""


def tester_prompt(task, phase, builder_out):
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
4. Check for foreign edits: git status --porcelain should show ONLY untracked test-results/playwright junk plus this task's files. If the Builder already COMMITTED its work (a TASK-n commit is the desired end state), git status is the check; otherwise the task's declared files sit modified/untracked and MUST be committed before PASS — a PASS with the deliverable uncommitted is invalid.
5. Verify with REAL command output (test names + pass/fail counts).
6. Check the Builder committed the work: `git log --oneline -1` should show a TASK-commit containing the deliverable. If not, verdict FAIL with the exact git add/commit commands listed.

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
There {'IS' if next_phase else 'is NO'} a next phase{' — ' + str(next_phase['id']) + ' ' + next_phase['name'] if next_phase else ''}.

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
- A BUILDER_INSTRUCTION must NEVER prescribe destructive or history-rewriting git commands: no `git reset --hard`, no `git checkout -- .`, no `git clean`, no revert of other tasks' commits, no force-push. Those destroyed verified work once already. Instruct fixes forward (edit/add/commit the named files), never backward.
- ESCALATE if: a task failed after retries, docs are ambiguous and you cannot resolve, or all phases are complete. Every ESCALATE MUST carry a REASON — escalation halts the build and waits for a human, so an unexplained one is treated as a formatting error and re-asked.
- When a phase's tasks are all done, set NEXT to the first task of the next phase. If no next phase, ACTION: ESCALATE with NEXT:none and REASON "all phases complete".
- If you ADVANCE, also decompose any stub task whose title contains "expand at runtime" into concrete tasks and write them into build/plan.json BEFORE proceeding (real tasks with id/title/docs/done_when).

OUTPUT LANGUAGE: English ONLY. The field labels TESTER_EVIDENCE / DEFECT /
BUILDER_INSTRUCTION / ACTION / NEXT / REASON must be written exactly as given, in
ASCII English, each on its own line. The loop parses these mechanically — a
translated label (e.g. 操作/原因/证据) is invisible to it and stops the build.
You may quote foreign-language text inside a field's VALUE, never as a label.
"""


def feedback_prompt(task, phase, tester_result, monitor_instruction):
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
            if status in ("pending", "blocked"):
                return task, phase
    return None, None


FIELD_ALIASES = {
    "ACTION": ("ACTION", "操作", "动作", "行动", "决定"),
    "NEXT": ("NEXT", "下一步", "下一任务", "下一个"),
    "REASON": ("REASON", "原因", "理由"),
    "DEFECT": ("DEFECT", "缺陷", "问题", "剩余缺陷"),
    "TESTER_EVIDENCE": ("TESTER_EVIDENCE", "测试人员证据", "证据", "测试证据"),
    "BUILDER_INSTRUCTION": ("BUILDER_INSTRUCTION", "BUILDER 指令", "构建器指令",
                            "构建指令", "BUIDER 指令"),
}

# Verdict keywords as they may appear inside ACTION values (any language).
ACTION_TOKENS = ("ADVANCE", "RETRY", "ESCALATE", "进行", "继续", "推进",
                 "重试", "升级", "上报", "停止")


def _clean_line(line: str) -> str:
    """Strip markdown decoration and normalise punctuation on one report line.

    Agents decorate their own output (`**ACTION:** ADVANCE`, `> ACTION: RETRY`,
    `- ACTION: ...`) and some reply with full-width CJK punctuation. None of that
    is a signal about the work — it must not break field extraction.
    """
    s = line.strip()
    s = s.lstrip("-*>#0123456789. \t")     # list bullets / headings / bold / quotes
    s = s.replace("*", "").replace("`", "").replace("_", " ")
    s = s.replace("：", ":").replace("　", " ").replace("\u200b", "")
    return s.strip()


def parse_field(text, field):
    """Pull `FIELD: value` out of an agent report (first match).

    Tolerant by design: agents answer in their own language and decorate lines
    with markdown. A strict English-only match here silently returns "" and the
    caller falls back to a default (ESCALATE), which halts the whole loop on a
    perfectly good cycle — so label aliases and decoration are both handled.
    """
    names = FIELD_ALIASES.get(field.upper(), (field,))
    for line in (text or "").splitlines():
        s = _clean_line(line)
        up = s.upper()
        for name in names:
            prefix = name.upper() + ":"
            if up.startswith(prefix):
                val = s.split(":", 1)[1].strip()
                return val.strip("*`_ ")
    return ""


def parse_action(text):
    """Resolve the Monitor's ACTION, tolerating language and formatting drift.

    Falls back to scanning the whole report for a verdict token, because a
    missed ACTION is not a neutral event: it defaults to ESCALATE and stops the
    build. An unknown action still returns "" so the caller can decide.
    """
    val = parse_field(text, "ACTION").upper()
    for token in ("ADVANCE", "RETRY", "ESCALATE"):
        if token in val:
            return token
    for zh, en in (("进行", "ADVANCE"), ("继续", "ADVANCE"), ("推进", "ADVANCE"),
                   ("重试", "RETRY"), ("升级", "ESCALATE"), ("上报", "ESCALATE"),
                   ("停止", "ESCALATE")):
        if zh in val:
            return en
    # No labelled ACTION — scan for a bare verdict token, but only when the
    # report names exactly ONE of them. Prose that mentions several ("I would
    # not ADVANCE, RETRY instead") is ambiguous and must not be guessed at.
    found = {t for t in ("ADVANCE", "RETRY", "ESCALATE") if t in (text or "").upper()}
    return found.pop() if len(found) == 1 else ""


def has_real_evidence(tester_out: str) -> bool:
    """Independent gate: does the Tester report contain real command evidence?

    Deliberately mechanical — the Monitor also judges this, but a cheap
    deterministic cross-check stops a chatty Tester from auto-passing a task.
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

        # ── BUILD (carrying the Monitor's standing instruction) ──
        feedback = slot.get("tester_feedback")
        instruction = slot.get("monitor_instruction")
        b_out, b_used = run_role("builder", ROLE_CHAINS["builder"],
                                 builder_prompt(task, phase, feedback, instruction),
                                 start=roles["builder"]["model"], timeout=7200)
        slot["builder_model_used"] = b_used["model"]
        slot["history"].append({"role": "builder", "out": b_out[-4000:],
                                "ts": datetime.now().isoformat()})
        save_json(STATE_PATH, state)

        # ── TEST (tester sees the builder's claims, must verify independently) ──
        t_out, t_used = run_role("tester", ROLE_CHAINS["tester"],
                                 tester_prompt(task, phase, b_out),
                                 start=roles["tester"]["model"], timeout=1800)
        slot["tester_model_used"] = t_used["model"]
        slot["history"].append({"role": "tester", "out": t_out[-4000:],
                                "ts": datetime.now().isoformat()})
        # Markdown-tolerant verdict parse. A strict `"VERDICT: PASS" in t_out`
        # missed a correct report whose author wrote `**VERDICT: PASS**`
        # (minimax-m2.7, task 8.5), which logged a FAIL for a green task and
        # burned a retry. Strip markdown emphasis and collapse whitespace before
        # matching so decoration cannot change the verdict.
        _flat = re.sub(r"[*_`]", "", t_out)
        _flat = re.sub(r"\s+", " ", _flat)
        passed = "VERDICT: PASS" in _flat
        real_ev = has_real_evidence(t_out)
        slot["last_verdict"] = "PASS" if passed else "FAIL"
        slot["tester_evidence"] = "real" if real_ev else "weak"
        save_json(STATE_PATH, state)
        log(f"  verdict={'PASS' if passed else 'FAIL'} evidence={'real' if real_ev else 'WEAK'}")

        # ── MONITOR SUPERVISION (inspects BOTH agents, issues instruction) ──
        role_chain = ROLE_CHAINS["monitor"]
        m_out, _ = run_role("monitor", role_chain,
                            monitor_prompt(task, phase, t_out, b_out, state,
                                             "ok" if real_ev else "weak"),
                            start=roles["monitor"]["model"], timeout=1200)
        state["last_monitor_decision"] = m_out
        slot["history"].append({"role": "monitor", "out": m_out[-4000:],
                                "ts": datetime.now().isoformat()})
        action = parse_action(m_out)
        # An unreadable ACTION *or* an unjustified ESCALATE both trigger the
        # re-ask: escalation halts the build and waits on a human, so it must
        # arrive with a reason. A missing reason means the report did not follow
        # the format (wrong language, decoration), not that the build is broken.
        if not action or (action == "ESCALATE" and not parse_field(m_out, "REASON")):
            # A parse miss must not halt the build: ESCALATE is the *default*
            # here, so an unreadable report would stop a good cycle (this is
            # exactly how a Chinese-language Monitor reply killed a cycle whose
            # Tester had PASSED with real evidence). Re-ask once, plainly.
            log("  ⚠ monitor ACTION unreadable — re-asking for a plain-English verdict")
            m_out2, _ = run_role(
                "monitor", role_chain,
                monitor_prompt(task, phase, t_out, b_out, state,
                               "ok" if real_ev else "weak")
                + "\n\nREPLY FORMAT OVERRIDE (overrides everything above): reply ONLY with the "
                  "six English field lines, one per line, no markdown, no other language:\n"
                  "TESTER_EVIDENCE: REAL | WEAK\nDEFECT: ...\nBUILDER_INSTRUCTION: ...\n"
                  "ACTION: ADVANCE | RETRY | ESCALATE\nNEXT: ...\nREASON: ...\n",
                start=roles["monitor"]["model"], timeout=900)
            slot["history"].append({"role": "monitor-reask", "out": m_out2[-4000:],
                                    "ts": datetime.now().isoformat()})
            action = parse_action(m_out2)
            m_instruction = parse_field(m_out2, "BUILDER_INSTRUCTION")
            m_evidence = (parse_field(m_out2, "TESTER_EVIDENCE") or "").upper()
            slot["monitor_defect"] = parse_field(m_out2, "DEFECT")
            if action:
                m_out = m_out2
                state["last_monitor_decision"] = m_out
            else:
                # Still unreadable — fall back to the deterministic gate rather
                # than halting: the loop's own tester-verdict + evidence checks
                # are mechanical and trustworthy.
                action = "RETRY" if ((not passed) or (not real_ev)) else "ADVANCE"
                log(f"  ⚠ monitor still unreadable — deterministic gate decides: {action}")
        else:
            m_instruction = parse_field(m_out, "BUILDER_INSTRUCTION")
            m_evidence = (parse_field(m_out, "TESTER_EVIDENCE") or "").upper()
            slot["monitor_defect"] = parse_field(m_out, "DEFECT")
        slot["monitor_evidence_call"] = m_evidence
        log(f"  monitor: ACTION={action} evidence={m_evidence or '?'} "
            f"defect={slot['monitor_defect'][:80]!r}")

        # The Monitor may not wave a task through on WEAK evidence — the loop
        # honours the stricter of (tester verdict, evidence gate, monitor call).
        monitor_blocks = (not passed) or (not real_ev) or (m_evidence == "WEAK")

        # ── Deterministic landing gate (belt over the prompt braces) ──
        # A verified deliverable that is not committed is not landed: today's
        # worktree wipe destroyed three tasks' worth of never-committed work.
        # If the monitor wants to ADVANCE but the tree still carries modified/
        # untracked task files (anything outside build/ and the known-ignorable
        # junk), run a mechanical, minimal commit of exactly those files first.
        # Nothing here touches build/, scripts/build_loop.py, or history.
        if action == "ADVANCE" and not monitor_blocks:
            try:
                st = subprocess.run(["git", "status", "--porcelain"],
                                    capture_output=True, text=True,
                                    cwd=ROOT, check=False)
                lines_in = [l for l in (st.stdout or "").splitlines() if l.strip()]
                ignorable = ("test-results/", "playwright-report/", "build/")
                landable = [
                    l for l in lines_in
                    if not any(ig in l for ig in ignorable)
                    and not l.strip().startswith("?? tools/")
                ]
                if landable:
                    log(f"  ⤓ landing gate: {len(landable)} uncommitted task file(s) — committing before done")
                    paths = [l[3:].strip().strip('"') for l in landable]
                    add = subprocess.run(["git", "add", "--", *paths],
                                         capture_output=True, text=True,
                                         cwd=ROOT, check=False)
                    if add.returncode == 0:
                        cm = subprocess.run(
                            ["git", "commit", "-m",
                             f"TASK {sid}: land verified work (loop landing gate)"],
                            capture_output=True, text=True, cwd=ROOT, check=False)
                        log("  ⤓ landing gate: " +
                            ((cm.stdout or cm.stderr or "").strip().splitlines() or ["done"])[-1][:100])
                else:
                    log("  ⤓ landing gate: tree clean — nothing to land")
            except Exception as exc:
                log(f"  ⤓ landing gate skipped ({exc}) — proceed on prompt-level rule 11")
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
                state.setdefault("escalations", []).append({
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
                state.setdefault("escalations", []).append({
                    "task": sid, "reason": "retries exhausted",
                    "ts": datetime.now().isoformat()})
                state["status"] = "escalated"
                log("  🛑 ESCALATED: retries exhausted")
                save_json(STATE_PATH, state)
                break
        else:  # ESCALATE
            reason = parse_field(m_out, "REASON")
            state.setdefault("escalations", []).append({"task": sid, "reason": reason,
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
