"""Server-owned daily PULSE trigger. The reader process owns the runner."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")
EXPECTED_DAILY_TASKS = 120
DAILY_HOUR = 0
DAILY_MINUTE = 30


def _read_ledger(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    day = date.fromisoformat(record["business_date"]).isoformat()
    for target in (path.with_name(f"daily_schedule_{day}.json"), path):
        temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


def _run_moscow_date(started_at: str) -> date | None:
    if not started_at:
        return None
    try:
        value = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    # Runner timestamps are naive server-local UTC inside the reader container.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW).date()


def tick(app, ledger_path: Path, now: datetime | None = None) -> dict:
    """Start at most one full selected run per Moscow date; safe on restarts."""
    now = (now or datetime.now(timezone.utc)).astimezone(MOSCOW)
    if (now.hour, now.minute) < (DAILY_HOUR, DAILY_MINUTE):
        return {"status": "before_due"}
    business_date = (now.date() - timedelta(days=1)).isoformat()
    ledger = _read_ledger(ledger_path)
    if ledger.get("business_date") == business_date and ledger.get("run_id"):
        runner = app.admin_all_clients_daily_runner()
        with runner.lock:
            state = dict(runner.state)
        if state.get("run_id") == ledger["run_id"] and state.get("status") != ledger.get("run_status_at_check"):
            ledger["run_status_at_check"] = state.get("status")
            ledger["finished_at"] = state.get("finished_at")
            _write_ledger(ledger_path, ledger)
        return {"status": "already_recorded", "run_id": ledger["run_id"]}

    runner = app.admin_all_clients_daily_runner()
    with runner.lock:
        state = dict(runner.state)
    started_date = _run_moscow_date(str(state.get("started_at") or ""))
    if started_date == now.date() and state.get("run_id"):
        record = {
            "business_date": business_date,
            "run_id": state["run_id"],
            "started_at": state.get("started_at"),
            "origin": "existing_run",
            "run_status_at_check": state.get("status"),
        }
        _write_ledger(ledger_path, record)
        return {"status": "adopted_existing", "run_id": state["run_id"]}
    if state.get("status") in {"running", "stopping"}:
        return {"status": "waiting_for_active_run", "run_id": state.get("run_id")}

    plan = app.build_admin_all_clients_daily_plan()
    clients = {client.get("key") for client in plan.get("clients", [])}
    selected = [
        task for task in plan.get("tasks", [])
        if task.get("stage") != "assortment"
        or task.get("key") == "api_lamoda_catalog"
    ]
    task_ids = [str(task["id"]) for task in selected]
    if clients != {"toptop", "lera_nena"} or len(task_ids) != EXPECTED_DAILY_TASKS or len(set(task_ids)) != len(task_ids):
        raise RuntimeError(
            f"Daily scope changed: clients={sorted(clients)}, selected={len(task_ids)}; review required"
        )
    result = app.handle_admin_all_clients_daily("start", task_ids)
    run_id = str(result.get("run_id") or "")
    if not run_id:
        raise RuntimeError("Daily runner did not return run_id")
    record = {
        "business_date": business_date,
        "run_id": run_id,
        "started_at": result.get("started_at"),
        "origin": "vps_schedule",
        "selected_tasks": len(task_ids),
        "run_status_at_check": result.get("status"),
    }
    _write_ledger(ledger_path, record)
    return {"status": "started", "run_id": run_id, "selected_tasks": len(task_ids)}


def run_loop(app, ledger_path: Path, interval_seconds: int = 30) -> None:
    last_notice = None
    while True:
        try:
            result = tick(app, ledger_path)
            notice = result.get("status")
            if notice in {"started", "adopted_existing"} or (
                notice == "waiting_for_active_run" and notice != last_notice
            ):
                print(f"PULSE daily schedule: {result}", flush=True)
            last_notice = notice
        except Exception as exc:
            notice = f"error:{type(exc).__name__}:{exc}"
            if notice != last_notice:
                print(f"PULSE daily schedule: {notice}", flush=True)
            last_notice = notice
        threading.Event().wait(interval_seconds)


def start_in_reader(app, ledger_path: Path) -> threading.Thread:
    thread = threading.Thread(
        target=run_loop, args=(app, ledger_path), name="pulse-daily-scheduler", daemon=True
    )
    thread.start()
    return thread
