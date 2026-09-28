#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the safe read-only assortment refresh for one registered BI client."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def registered_client(client_key: str) -> dict:
    app.hydrate_registered_clients()
    from client_registry import get_client

    with app.client_registry_connection() as conn:
        client = get_client(conn, app.normalize_client_key(client_key))
    if not client:
        raise ValueError("Клиент не найден в реестре")
    return client


def validate_credentials(client: dict, marketplaces: list[str]) -> None:
    missing: list[str] = []
    if "wb" in marketplaces and not app.registered_client_credential(client["key"], "wb_api_token"):
        missing.append("WB API token")
    if "ozon" in marketplaces:
        if not app.registered_client_credential(client["key"], "ozon_client_id"):
            missing.append("Ozon Client-Id")
        if not app.registered_client_credential(client["key"], "ozon_api_key"):
            missing.append("Ozon Api-Key")
    if missing:
        raise ValueError("Не сохранены ключи: " + ", ".join(missing))


def run_step(label: str, command: list[str], env: dict[str, str]) -> None:
    script_name = next((Path(value).name for value in command if str(value).lower().endswith(".py")), "—")
    print(f"ПРОГРЕСС: запуск | {label} | script={script_name}", flush=True)
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), env=env, check=False)
    if result.returncode:
        raise RuntimeError(f"{label}: процесс завершился с кодом {result.returncode}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--marketplaces", required=True, help="wb,ozon")
    parser.add_argument("--wb-seed-cache-dir", type=Path)
    parser.add_argument("--skip-views", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = parse_args(argv)
    started = time.monotonic()
    client = registered_client(args.client_key)
    if client["db_name"] != args.database_name:
        raise ValueError(
            f"Защита клиентской БД: в реестре {client['db_name']}, в команде {args.database_name}"
        )
    requested = [value.strip().lower() for value in args.marketplaces.split(",") if value.strip()]
    marketplaces = [value for value in ("ozon", "wb") if value in requested and value in set(client.get("marketplaces") or [])]
    if not marketplaces:
        raise ValueError("Не выбран ни один подключённый маркетплейс")
    validate_credentials(client, marketplaces)
    env = {
        **os.environ,
        "DASHBOARD_CLIENT": client["key"],
        "DASHBOARD_CLIENT_LABEL": client["label"],
        "DASHBOARD_DB_NAME": client["db_name"],
        "WB_API_TOKEN_ENV": f"WB_API_TOKEN_{client['key'].upper()}",
    }
    steps: list[tuple[str, list[str]]] = []
    if "ozon" in marketplaces:
        command = [
            sys.executable, "-X", "utf8", "-u",
            str(PROJECT_ROOT / "scripts" / "sync_ozon_assortment_soft.py"),
            "--resume-latest-incomplete",
        ]
        if args.skip_views:
            command.append("--skip-views")
        steps.append(("Ozon · ассортимент и характеристики", command))
    if "wb" in marketplaces:
        command = [
            sys.executable, "-X", "utf8", "-u",
            str(PROJECT_ROOT / "scripts" / "sync_wb_assortment.py"),
        ]
        if args.wb_seed_cache_dir:
            command.extend(("--seed-cache-dir", str(args.wb_seed_cache_dir)))
        if args.skip_views:
            command.append("--skip-views")
        steps.append(("WB · ассортимент и характеристики", command))
    print(
        f"ПЛАН: обновление ассортимента | клиент={client['key']} | БД={client['db_name']} | "
        f"маркетплейсы={','.join(marketplaces)} | шагов={len(steps)} | API только чтение | "
        "БД: мягкий upsert | delete/truncate=запрещены | пустые ответы не затирают данные | "
        "все запросы последовательные, прогресс и лимитные паузы выводятся ниже",
        flush=True,
    )
    completed = 0
    try:
        for index, (label, command) in enumerate(steps, start=1):
            elapsed = time.monotonic() - started
            eta = elapsed / completed * (len(steps) - completed) if completed else 0
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({(index - 1) / len(steps) * 100:.1f}%) | "
                f"{label} | completed={completed} | errors=0 | elapsed={duration(elapsed)} | ETA={duration(eta)}",
                flush=True,
            )
            step_started = time.monotonic()
            run_step(label, command, env)
            completed += 1
            print(
                f"[{index}/{len(steps)}] {label}: completed | errors=0 | elapsed={duration(time.monotonic() - step_started)}",
                flush=True,
            )
    except Exception:
        print(
            f"ИТОГ: ассортимент клиента | client={client['key']} | completed={completed}/{len(steps)} | "
            f"errors=1 | stopped=no | partial=yes | seller_api_writes=0 | "
            f"elapsed={duration(time.monotonic() - started)}",
            flush=True,
        )
        raise
    print(
        f"ИТОГ: ассортимент клиента | client={client['key']} | completed={completed}/{len(steps)} | "
        f"errors=0 | stopped=no | partial=no | seller_api_writes=0 | "
        f"elapsed={duration(time.monotonic() - started)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
