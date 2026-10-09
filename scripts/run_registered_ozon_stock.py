from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


WORKSPACE = Path("/workspace")
DASHBOARD_ROOT = WORKSPACE / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(DASHBOARD_ROOT / "scripts"))

from run_client_pipeline import credentials_to_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one registered client's current Ozon stock snapshot.")
    parser.add_argument("--client", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()

    started = time.monotonic()
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "DASHBOARD_CLIENT": args.client,
        "DASHBOARD_DB_NAME": args.database,
        "KM_DB_NAME": args.database,
    }
    env.update(credentials_to_env(args.client))
    print(
        f"ПЛАН: зарегистрированный Ozon stock | client={args.client} | "
        f"database={args.database} | шагов=1 | исторический backfill недоступен",
        flush=True,
    )
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-u",
        str(WORKSPACE / "scripts" / "sync_km_ozon_api.py"),
        "--step",
        "stock",
    ]
    completed = subprocess.run(command, cwd=WORKSPACE, env=env, check=False)
    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: status={'ok' if completed.returncode == 0 else 'error'} | "
        f"client={args.client} | code={completed.returncode} | elapsed={elapsed:.1f}s | "
        f"stopped=no | partial={'no' if completed.returncode == 0 else 'yes'}",
        flush=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
