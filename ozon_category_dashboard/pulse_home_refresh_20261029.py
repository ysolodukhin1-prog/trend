"""One guarded Home mart refresh after the 2026-10-28 scheduled run."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/var/lib/pulse")
ADMIN = ROOT / "admin_all_clients_daily_state.json"
LEDGER = ROOT / "daily_schedule.json"
OUT = ROOT / "home_refresh_2026-10-28.json"
PARENT = "fdf87f51-0f3f-4467-8364-85c2b079cbb7"


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
    if (admin.get("run_id") != PARENT or admin.get("status") not in {"completed", "idle"}
            or admin.get("active") or schedule.get("business_date") != "2026-10-28"
            or schedule.get("run_id") != PARENT or schedule.get("selected_tasks") != 122):
        raise RuntimeError("Scheduled run or scope changed; refusing refresh")
    scoped = [t for t in admin.get("tasks", []) if t.get("id") in set(admin.get("scope_task_ids", []))]
    if set(schedule.get("task_ids", [])) != set(admin.get("scope_task_ids", [])):
        raise RuntimeError("Exact current schedule scope mismatch")
    end = datetime.fromisoformat(admin["finished_at"])
    if len(scoped) != 122 or any(t.get("status") in {"running", "queued", "stopping", "error", "failed"} for t in scoped):
        raise RuntimeError("Active or failed scoped task")
    if not any(x.get("type") == "done" and str(x.get("text", "")).startswith("Обновление завершено") and x.get("time") == end.strftime("%H:%M:%S") for x in admin.get("logs", [])):
        raise RuntimeError("No terminal scheduled-run log")
    record = {"business_date": "2026-10-28", "parent_run_id": PARENT,
              "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "covered_business_dates": ["2026-10-26", "2026-10-27", "2026-10-28"], "action": "refresh stale PlanFact then Home marts for both clients"}
    write(record)
    record["planfact_refresh"] = []
    for client in ("toptop", "lera_nena"):
        pf = subprocess.run(["/usr/local/bin/python", "-u", "/workspace/ozon_category_dashboard/trend_views.py", "--client", client, "--group", "ozon_view_planfact"], cwd="/workspace/ozon_category_dashboard", capture_output=True, text=True)
        record["planfact_refresh"].append({"client": client, "returncode": pf.returncode, "log_tail": (pf.stdout + pf.stderr).splitlines()[-12:]})
        write(record)
        if pf.returncode:
            record.update(status="error", finished_at=datetime.now(timezone.utc).isoformat())
            write(record)
            raise RuntimeError("Dependent PlanFact refresh failed; inspect ledger")
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


