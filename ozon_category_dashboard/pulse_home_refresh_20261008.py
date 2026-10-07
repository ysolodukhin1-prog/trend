"""One guarded Home mart refresh after the 2026-10-07 scheduled run."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/var/lib/pulse")
ADMIN = ROOT / "admin_all_clients_daily_state.json"
LEDGER = ROOT / "daily_schedule_2026-10-07.json"
OUT = ROOT / "home_refresh_2026-10-07.json"
PARENT = "3e7f7781-f1aa-4dca-b145-c59cf01a121c"


def write(record):
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, OUT)


def main():
    if OUT.exists():
        raise RuntimeError("Home refresh ledger already exists")
    admin = json.loads(ADMIN.read_text(encoding="utf-8"))
    schedule = json.loads(LEDGER.read_text(encoding="utf-8"))
    if (admin.get("run_id") != PARENT or admin.get("status") != "completed"
            or admin.get("active") or schedule.get("business_date") != "2026-10-07"
            or schedule.get("run_id") != PARENT or schedule.get("selected_tasks") != 120):
        raise RuntimeError("Scheduled run or scope changed; refusing refresh")
    record = {"business_date": "2026-10-07", "parent_run_id": PARENT,
              "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "action": "refresh existing Home marts for both clients"}
    write(record)
    command = ["/usr/local/bin/python", "-u",
               "/workspace/ozon_category_dashboard/home_marts.py",
               "--clients", "toptop", "lera_nena", "--refresh"]
    tail = []
    with subprocess.Popen(command, cwd="/workspace/ozon_category_dashboard",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as process:
        for line in process.stdout:
            tail.append(line.rstrip())
            tail = tail[-20:]
        code = process.wait()
    record.update(returncode=code, log_tail=tail,
                  status="ok" if code == 0 else "error",
                  finished_at=datetime.now(timezone.utc).isoformat())
    write(record)
    if code:
        raise RuntimeError("Home refresh failed; inspect ledger")


if __name__ == "__main__":
    main()
