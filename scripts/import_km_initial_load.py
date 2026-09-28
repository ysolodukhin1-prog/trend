#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Back up and fully load the supported KM Trade dashboard sources."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


TARGET_DB = os.environ.get("KM_DB_NAME", "km_trade_products")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initial/full load for KM Trade dashboard data")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Do not truncate funnel/advertising history; import only unseen files.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Emergency-only: skip pg_dump before changing an existing database.",
    )
    return parser.parse_args()


def find_pg_dump() -> str | None:
    configured = os.environ.get("PG_DUMP")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("pg_dump")
    if found:
        return found
    root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
    candidates = sorted(root.glob(r"*\bin\pg_dump.exe"), reverse=True)
    return str(candidates[0]) if candidates else None


def database_exists(config: dict[str, object]) -> bool:
    with psycopg2.connect(**{**config, "database": "postgres"}) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET_DB,))
            return cur.fetchone() is not None


def backup_database(config: dict[str, object]) -> Path:
    pg_dump = find_pg_dump()
    if not pg_dump:
        raise RuntimeError("pg_dump не найден; задайте PG_DUMP или установите PostgreSQL client tools")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "backups" / f"km_trade_preimport_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    output_file = output_dir / f"{TARGET_DB}.dump"
    command = [
        pg_dump,
        "--format=custom",
        "--file",
        str(output_file),
        "--host",
        str(config["host"]),
        "--port",
        str(config["port"]),
        "--username",
        str(config["user"]),
        TARGET_DB,
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = str(config.get("password", ""))
    print(f"BACKUP: database={TARGET_DB}, output={output_file}", flush=True)
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"pg_dump failed with exit code {completed.returncode}: {completed.stderr.strip()}")
    print(f"BACKUP_DONE: bytes={output_file.stat().st_size}, file={output_file}", flush=True)
    return output_file


def run_step(index: int, total: int, label: str, command: list[str], started: float) -> None:
    print(f"[{index}/{total}] START: {label}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env={**os.environ, "DASHBOARD_DB_NAME": TARGET_DB, "KM_DB_NAME": TARGET_DB},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip(), flush=True)
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"Step failed: {label}; exit_code={return_code}")
    elapsed = time.monotonic() - started
    pct = index * 100 / total
    eta = elapsed / index * (total - index)
    print(f"[{index}/{total}] DONE: {label}; errors=0", flush=True)
    print(
        f"ПРОГРЕСС: {index}/{total} ({pct:.1f}%) | {label} | "
        f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
        flush=True,
    )


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    python = sys.executable
    force_funnel = [] if args.incremental else ["--force-reimport"]
    force_adv = [] if args.incremental else ["--force-reimport"]
    steps = [
        ("Справочник Ozon", [python, "-X", "utf8", "scripts/import_km_ozon_product_categories.py"]),
        ("Финансовые продажи Ozon", [python, "-X", "utf8", "scripts/import_km_ozon_fin_sales.py"]),
        ("Остатки Ozon", [python, "-X", "utf8", "scripts/import_km_ozon_stock_reports.py", "--no-refresh"]),
        (
            "Воронка Ozon",
            [python, "-X", "utf8", "scripts/import_km_ozon_funnel_reports.py", *force_funnel, "--skip-views"],
        ),
        (
            "Товарная реклама Ozon",
            [python, "-X", "utf8", "scripts/import_km_ozon_adv_daily_reports.py", *force_adv, "--skip-views"],
        ),
        ("Витрины KM Trade", [python, "-X", "utf8", "scripts/rebuild_km_dashboard_views.py"]),
        ("Контроль готовности", [python, "-X", "utf8", "scripts/audit_km_trade_readiness.py"]),
    ]
    mode = "incremental" if args.incremental else "full_replace"
    print(
        f"ПЛАН: database={TARGET_DB}, mode={mode}, steps={len(steps)}, "
        "backup=before_existing_database, pauses=none, stop_on_error=true",
        flush=True,
    )
    config = app.read_db_config()
    backup_file: Path | None = None
    if database_exists(config):
        if args.skip_backup:
            print("BACKUP_SKIPPED: explicitly requested by --skip-backup", flush=True)
        else:
            backup_file = backup_database(config)
    for index, (label, command) in enumerate(steps, start=1):
        run_step(index, len(steps), label, command, started)
    elapsed = time.monotonic() - started
    print(
        "ИТОГ: "
        f"database={TARGET_DB}, mode={mode}, steps={len(steps)}, errors=0, "
        f"backup={backup_file or 'not_required'}, elapsed={elapsed:.1f}s, "
        "stopped=false, partial=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
