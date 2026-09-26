#!/usr/bin/env python
"""GST Filing Platform — stack bootstrap (Phase 0, task 0.2).

Starts the data plane exactly as AI_BUILD_PLAYBOOK.md §3 + TECHNICAL_ARCHITECTURE.md
§2/§4 prescribe, the Farmer-App way:

    PostgreSQL :5436  — native pg_ctl from a local PG 16 install (Farmer App's tools/pg)
    Redis      :6380  — native redis-server (Farmer App's tools/redis)
    MinIO      :9001  — S3 API via tools/docker-compose.yml (Docker; native minio.exe is
                        no longer distributed by MinIO — dl.min.io returns 410 Gone, and
                        minio/minio + minio/mc were removed from Docker Hub 2026-09;
                        this repo pins pgsty/silo, the drop-in MinIO-compatible fork
                        whose image bundles the `mc` client — bucket ops run via
                        `docker exec` into the server container, no host networking)

Also: creates gst_filing_db, seeds schemas core/gst/extraction (TECH doc §3), creates the
versioned `gst-docs` bucket, then health-checks every port. Idempotent: re-running on a
running stack is a no-op that just re-verifies health.

Usage:
    python scripts/bootstrap_stack.py            # start + seed + verify
    python scripts/bootstrap_stack.py --check    # verify only (no side effects)
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Native service binaries — reused from the Farmer App install (same machine, ports differ).
FARMER_TOOLS = Path("D:/farmer_app/tools")
PG_BIN = FARMER_TOOLS / "pg/pgsql/bin"
PG_DATA = REPO_ROOT / "tools/pg/data"
PG_PORT = 5436
PG_DB = "gst_filing_db"
PG_USER = "gst"
PG_PASSWORD = "gst_dev_pass"  # local dev only; .env overrides in later phases

REDIS_BIN = FARMER_TOOLS / "redis/redis-server.exe"
REDIS_DIR = REPO_ROOT / "tools/redis"
REDIS_PORT = 6380

MINIO_CONTAINER = "gst_minio"
MINIO_PORT = 9001
MINIO_BUCKET = "gst-docs"
MINIO_ROOT_USER = os.environ.get("GST_MINIO_ROOT_USER", "gst_admin")
MINIO_ROOT_PASSWORD = os.environ.get("GST_MINIO_ROOT_PASSWORD", "gst_minio_dev_pass")
SCHEMAS = ("core", "gst", "extraction")

SERVICES: dict[str, int] = {
    "postgres": PG_PORT,
    "redis": REDIS_PORT,
    "minio": MINIO_PORT,
}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if port_open(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"{what} never opened port {port} within {timeout_s:.0f}s")


# ---------------------------------------------------------------- postgres


def start_postgres() -> None:
    pg_ctl = PG_BIN / "pg_ctl.exe"
    if not pg_ctl.exists():
        raise RuntimeError(f"pg_ctl not found at {pg_ctl}")
    if port_open(PG_PORT):
        print(f"[pg] port {PG_PORT} already open — assuming postgres is up")
        return
    PG_DATA.mkdir(parents=True, exist_ok=True)
    if not (PG_DATA / "PG_VERSION").exists():
        print(f"[pg] initdb -> {PG_DATA}")
        # pwfile must live OUTSIDE the target dir: initdb refuses a non-empty datadir.
        pwfile = PG_DATA.parent / ".pgpwfile"
        pwfile.write_text(PG_PASSWORD + "\n", encoding="ascii")
        try:
            subprocess.run(
                [
                    str(PG_BIN / "initdb.exe"),
                    "-D",
                    str(PG_DATA),
                    "-U",
                    PG_USER,
                    "-A",
                    "password",
                    f"--pwfile={pwfile}",
                    "-E",
                    "UTF8",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            pwfile.unlink(missing_ok=True)
    print(f"[pg] starting on :{PG_PORT}")
    subprocess.run(
        [
            str(pg_ctl),
            "-D",
            str(PG_DATA),
            "-o",
            f"-p {PG_PORT}",
            "-l",
            str(REPO_ROOT / "tools/pg/postgres.log"),
            "start",
        ],
        check=True,
    )
    wait_for_port(PG_PORT, 120, "postgres")


def _pg_env() -> dict[str, str]:
    return {**os.environ, "PGPASSWORD": PG_PASSWORD}


def psql(sql: str, dbname: str = "postgres") -> str:
    proc = subprocess.run(
        [
            str(PG_BIN / "psql.exe"),
            "-h",
            "127.0.0.1",
            "-p",
            str(PG_PORT),
            "-U",
            PG_USER,
            "-d",
            dbname,
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        env=_pg_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def seed_database() -> None:
    exists = psql("SELECT 1 FROM pg_database WHERE datname = 'gst_filing_db'") == "1"
    if not exists:
        subprocess.run(
            [
                str(PG_BIN / "createdb.exe"),
                "-h",
                "127.0.0.1",
                "-p",
                str(PG_PORT),
                "-U",
                PG_USER,
                PG_DB,
            ],
            check=True,
            env=_pg_env(),
            capture_output=True,
            text=True,
        )
        print(f"[pg] created database {PG_DB}")
    have = {
        s
        for s in psql(
            "SELECT nspname FROM pg_namespace WHERE nspname IN ('core','gst','extraction')",
            dbname=PG_DB,
        ).splitlines()
        if s
    }
    missing = [s for s in SCHEMAS if s not in have]
    for schema in missing:
        psql(f"CREATE SCHEMA {schema}", dbname=PG_DB)
        print(f"[pg] created schema {schema}")
    if not missing:
        print("[pg] schemas core/gst/extraction already present")


# ---------------------------------------------------------------- redis


def start_redis() -> None:
    if not REDIS_BIN.exists():
        raise RuntimeError(f"redis-server not found at {REDIS_BIN}")
    if port_open(REDIS_PORT):
        print(f"[redis] port {REDIS_PORT} already open — assuming redis is up")
        return
    REDIS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[redis] starting on :{REDIS_PORT}")
    # Windows redis ignores daemonize; spawn detached and rely on the health check.
    subprocess.Popen(
        [
            str(REDIS_BIN),
            "--port",
            str(REDIS_PORT),
            "--dir",
            str(REDIS_DIR),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )
    wait_for_port(REDIS_PORT, 30, "redis")


# ---------------------------------------------------------------- minio


def start_minio() -> None:
    if port_open(MINIO_PORT):
        print(f"[minio] port {MINIO_PORT} already open — assuming minio is up")
        return
    print("[minio] docker compose up -d (tools/docker-compose.yml)")
    subprocess.run(
        ["docker", "compose", "-f", str(REPO_ROOT / "tools/docker-compose.yml"), "up", "-d"],
        check=True,
        cwd=REPO_ROOT,
    )
    wait_for_port(MINIO_PORT, 60, "minio")


def minio_alive() -> bool:
    """MinIO liveness endpoint answers 200 once ready (GET / returns 403 for anon)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{MINIO_PORT}/minio/health/live", timeout=3) as r:
            return r.status == 200
    except (OSError, urllib.error.HTTPError):
        return False


def seed_bucket() -> None:
    """Create + version the gst-docs bucket via `mc` inside the server container.

    The pgsty/silo image bundles `mc`; running it in-container means it talks to the
    server over the container's own loopback — no host networking (broken on Docker
    Desktop) and no throwaway sidecar container needed.
    """
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not minio_alive():
        time.sleep(1)
    if not minio_alive():
        raise RuntimeError("MinIO is listening but never became ready")
    # mc writes config to ~/.mc — point HOME at a writable dir inside the container.
    script = (
        "export HOME=/tmp && "
        f"mc alias set local http://127.0.0.1:9000 {MINIO_ROOT_USER} {MINIO_ROOT_PASSWORD} && "
        f"mc mb --ignore-existing local/{MINIO_BUCKET} && "
        f"mc version enable local/{MINIO_BUCKET} && "
        "mc ls local/"
    )
    proc = subprocess.run(
        ["docker", "exec", MINIO_CONTAINER, "sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mc bucket seeding failed: {proc.stderr.strip() or proc.stdout.strip()}")
    print(f"[minio] bucket {MINIO_BUCKET} ready (versioned)")


# ---------------------------------------------------------------- health


def health_check() -> dict[str, bool]:
    results = {name: port_open(port) for name, port in SERVICES.items()}
    for name, ok in results.items():
        print(f"[health] {name}:{SERVICES[name]} -> {'OK' if ok else 'FAIL'}")
    if not all(results.values()):
        raise RuntimeError(f"health check failed: {results}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify health only")
    args = parser.parse_args()

    if args.check:
        health_check()
        return 0

    start_postgres()
    seed_database()
    start_redis()
    start_minio()  # docker; last so a cold Docker Desktop start doesn't stall pg/redis
    seed_bucket()
    health_check()
    print(
        "OK: PG:5436 Redis:6380 MinIO:9001 all healthy; "
        f"{PG_DB} + core/gst/extraction + {MINIO_BUCKET} seeded"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())