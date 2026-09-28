#!/usr/bin/env python3
"""Generated KOKOC BI views runner for LERA NENA."""
from __future__ import annotations
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def main() -> int:
    yesterday = date.today() - timedelta(days=1)
    command = [
        sys.executable, "-X", "utf8", "-u",
        str(PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "run_client_pipeline.py"),
        "--client-key", "lera_nena",
        "--database-name", "lera_nena",
        "--marketplaces", "ozon,wb",
        "--mode", "views",
        "--date-from", yesterday.isoformat(),
        "--date-to", yesterday.isoformat(),
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
