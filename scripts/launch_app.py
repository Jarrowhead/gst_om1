"""Unified launcher and supervisor for the GST Filing App stack.

Runs:
1. PostgreSQL on port 5436
2. Redis on port 6380
3. MinIO on port 9001 (console on 9002)
4. Alembic migrations
5. Backend FastAPI on port 8084
6. Demo seed (user + GSTINs)
7. Frontend Next.js on port 9094
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
PYTHON_EXE = str(BACKEND_DIR / ".venv/Scripts/python.exe")

FARMER_TOOLS = Path("D:/farmer_app/tools")
PG_BIN = FARMER_TOOLS / "pg/pgsql/bin"
PG_DATA = REPO_ROOT / "tools/pg/data"
PG_PORT = 5436
PG_DB = "gst_filing_db"
PG_USER = "gst"
PG_PASSWORD = "gst_dev_pass"

REDIS_BIN = FARMER_TOOLS / "redis/redis-server.exe"
REDIS_DIR = REPO_ROOT / "tools/redis"
REDIS_PORT = 6380

MINIO_BIN = REPO_ROOT / "tools/minio.exe"
MINIO_DATA = REPO_ROOT / "tools/minio/data"
MINIO_PORT = 9001
MINIO_CONSOLE_PORT = 9002
MINIO_BUCKET = "gst-docs"
MINIO_ROOT_USER = os.environ.get("GST_MINIO_ROOT_USER", "gst_admin")
MINIO_ROOT_PASSWORD = os.environ.get("GST_MINIO_ROOT_PASSWORD", "gst_minio_dev_pass")

BACKEND_PORT = 8084
FRONTEND_PORT = 9094

procs: list[subprocess.Popen] = []


def cleanup():
    print("[supervisor] Shutting down managed processes...")
    for p in reversed(procs):
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout_s: float = 60, name: str = "service") -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_port_open(port):
            print(f"[{name}] port {port} is open")
            return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {name} on port {port}")


def start_postgres():
    if is_port_open(PG_PORT):
        print(f"[pg] port {PG_PORT} already open")
        return

    pid_file = PG_DATA / "postmaster.pid"
    if pid_file.exists():
        try:
            pid_file.unlink()
            print("[pg] cleaned up stale postmaster.pid")
        except Exception as e:
            print(f"[pg] warning removing postmaster.pid: {e}")

    log_path = REPO_ROOT / "tools/pg/postgres.log"
    log_file = open(log_path, "a", encoding="utf-8")
    print(f"[pg] starting postgres on port {PG_PORT}...")
    p = subprocess.Popen(
        [
            str(PG_BIN / "postgres.exe"),
            "-D",
            str(PG_DATA),
            "-p",
            str(PG_PORT),
        ],
        stdout=log_file,
        stderr=log_file,
    )
    procs.append(p)
    wait_for_port(PG_PORT, 60, "postgres")


def start_redis():
    if is_port_open(REDIS_PORT):
        print(f"[redis] port {REDIS_PORT} already open")
        return

    REDIS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[redis] starting redis on port {REDIS_PORT}...")
    p = subprocess.Popen(
        [
            str(REDIS_BIN),
            "--port",
            str(REDIS_PORT),
            "--dir",
            str(REDIS_DIR),
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(p)
    wait_for_port(REDIS_PORT, 30, "redis")


def start_minio():
    if is_port_open(MINIO_PORT):
        print(f"[minio] port {MINIO_PORT} already open")
        return

    MINIO_DATA.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "MINIO_ROOT_USER": MINIO_ROOT_USER,
        "MINIO_ROOT_PASSWORD": MINIO_ROOT_PASSWORD,
    }
    log_path = REPO_ROOT / "tools/minio/minio.log"
    log_file = open(log_path, "a", encoding="utf-8")
    print(f"[minio] starting minio on port {MINIO_PORT}...")
    p = subprocess.Popen(
        [
            str(MINIO_BIN),
            "server",
            str(MINIO_DATA),
            "--address",
            f"127.0.0.1:{MINIO_PORT}",
            "--console-address",
            f"127.0.0.1:{MINIO_CONSOLE_PORT}",
        ],
        env=env,
        stdout=log_file,
        stderr=log_file,
    )
    procs.append(p)
    wait_for_port(MINIO_PORT, 30, "minio")


def init_db_and_minio():
    # Run bootstrap logic for schemas and bucket
    print("[setup] running schema and bucket initialization...")
    subprocess.run(
        [PYTHON_EXE, "scripts/bootstrap_stack.py"],
        cwd=str(REPO_ROOT),
        check=True,
    )

    print("[setup] running alembic migrations...")
    subprocess.run(
        [PYTHON_EXE, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        check=True,
    )


def start_backend():
    if is_port_open(BACKEND_PORT):
        print(f"[backend] port {BACKEND_PORT} already open")
        return

    log_path = REPO_ROOT / "tools/uvicorn.log"
    log_file = open(log_path, "a", encoding="utf-8")
    print(f"[backend] starting uvicorn on port {BACKEND_PORT}...")
    p = subprocess.Popen(
        [
            PYTHON_EXE,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--port",
            str(BACKEND_PORT),
            "--host",
            "127.0.0.1",
        ],
        cwd=str(BACKEND_DIR),
        stdout=log_file,
        stderr=log_file,
    )
    procs.append(p)
    wait_for_port(BACKEND_PORT, 30, "backend")

    # verify health
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/health") as resp:
                if resp.status == 200:
                    print("[backend] /api/v1/health is responding 200 OK")
                    break
        except Exception:
            time.sleep(0.5)


def seed_demo():
    print("[seed] seeding demo user and GSTINs...")
    try:
        res = subprocess.run(
            [PYTHON_EXE, "scripts/seed_demo.py"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        print(f"[seed] output: {res.stdout.strip()}")
        if res.stderr:
            print(f"[seed] stderr: {res.stderr.strip()}")
    except Exception as e:
        print(f"[seed] error: {e}")



def start_worker():
    log_path = REPO_ROOT / "tools/worker.log"
    log_file = open(log_path, "a", encoding="utf-8")
    print("[worker] starting extraction worker in poll mode...")
    p = subprocess.Popen(
        [PYTHON_EXE, "-m", "app.extraction.worker", "--poll"],
        cwd=str(BACKEND_DIR),
        stdout=log_file,
        stderr=log_file,
    )
    procs.append(p)

def start_frontend():
    if is_port_open(FRONTEND_PORT):
        print(f"[frontend] port {FRONTEND_PORT} already open")
        return

    next_bin = FRONTEND_DIR / "node_modules/.bin/next.cmd"
    cmd = [str(next_bin), "dev", "--port", str(FRONTEND_PORT)]
    print(f"[frontend] starting next dev on port {FRONTEND_PORT}...")
    p = subprocess.Popen(
        cmd,
        cwd=str(FRONTEND_DIR),
        shell=True,
    )
    procs.append(p)
    wait_for_port(FRONTEND_PORT, 60, "frontend")


def main():
    try:
        start_postgres()
        start_redis()
        start_minio()
        init_db_and_minio()
        start_backend()
        start_worker()
        seed_demo()
        start_frontend()

        print("\n=======================================================")
        print(" GST APP IS FULLY UP AND RUNNING!")
        print(f" Frontend: http://127.0.0.1:{FRONTEND_PORT}")
        print(f" Backend:  http://127.0.0.1:{BACKEND_PORT}")
        print(f" Demo user: demo.tony@example.com")
        print("=======================================================\n")

        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"[supervisor] warning: process {p.args} exited with code {p.returncode}")
            time.sleep(2)
    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        print(f"[supervisor] fatal error: {e}")
        cleanup()
        raise


if __name__ == "__main__":
    main()
