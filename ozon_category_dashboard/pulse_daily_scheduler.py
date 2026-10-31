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

EXPECTED_CLIENTS = {"toptop", "lera_nena"}

DAILY_HOUR = 6

DAILY_MINUTE = 0





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





def selected_tasks(plan):

    clients={c['key'] for c in plan.get('clients',[])}

    tasks=[t for t in plan.get('tasks',[]) if t.get('stage') in {'imports','views'}]

    ids=[t['id'] for t in tasks]

    if clients!=EXPECTED_CLIENTS or len(ids)!=len(set(ids)) or {t['client'] for t in tasks}!=clients:

        raise RuntimeError('Daily plan has foreign/missing clients or duplicate task IDs')

    per={c:{t['key'] for t in tasks if t['client']==c} for c in clients}

    if not all(per.values()) or per['toptop']!=per['lera_nena']:

        raise RuntimeError('Daily source catalogs differ: '+str(sorted(per['toptop']^per['lera_nena'])))

    return tasks



def tick(app, ledger_path: Path, now: datetime | None = None) -> dict:

    now=(now or datetime.now(timezone.utc)).astimezone(MOSCOW)

    if (now.hour,now.minute)<(DAILY_HOUR,DAILY_MINUTE):return {'status':'before_due'}

    business_date=(now.date()-timedelta(days=1)).isoformat()

    runner=app.admin_all_clients_daily_runner()

    with runner.lock:state=dict(runner.state)

    # Never adopt a partial manual refresh as proof of the full daily schedule.

    if state.get('status') in {'running','stopping'}:

        return {'status':'waiting_for_active_run','run_id':state.get('run_id')}

    tasks=selected_tasks(app.build_admin_all_clients_daily_plan())

    ids=[t['id'] for t in tasks];required=set(ids)

    ledger=_read_ledger(ledger_path)

    repair_same = False
    if ledger.get('origin') == 'owner_targeted_wb_history_repair' and ledger.get('business_date') == business_date:
        name = str(ledger.get('repair_ledger') or '')
        if name and Path(name).name == name:
            receipt = _read_ledger(ledger_path.parent / name)
            repair_same = (receipt.get('schedule_reconciled') is True
                and receipt.get('parent_execution_receipts_verified') is True
                and receipt.get('status') == 'completed'
                and receipt.get('run_id') == ledger.get('run_id') == state.get('run_id')
                and set(receipt.get('parent_scope_task_ids', [])) == required
                and set(receipt.get('scope_task_ids', [])) == set(ledger.get('task_ids', [])) == set(state.get('scope_task_ids', []))
                and all(t.get('status') in {'ok', 'limited', 'skipped'} for t in receipt.get('scoped_tasks', []))
                and bool(receipt.get('scoped_tasks')))
    same=ledger.get('business_date')==business_date and (set(ledger.get('task_ids',[]))==required or repair_same)

    current_same=same and ledger.get('run_id')==state.get('run_id')

    if current_same:

        ledger.update(run_status_at_check=state.get('status'),finished_at=state.get('finished_at'))

        _write_ledger(ledger_path,ledger)

        if state.get('status') in {'completed','stopped'}:return {'status':'already_recorded','run_id':state.get('run_id')}

        attempts=int(ledger.get('attempts',1))

        if attempts>=3:return {'status':'retry_limit','run_id':state.get('run_id')}

        if now.timestamp()-float(ledger.get('attempt_at',0))<900:return {'status':'retry_cooldown'}

    else:attempts=0

    if same and not current_same and ledger.get('run_status_at_check')=='completed':

        return {'status':'already_recorded','run_id':ledger.get('run_id')}

    # Resume completed source steps when a partial run already happened today.

    action='resume' if _run_moscow_date(str(state.get('started_at') or ''))==now.date() else 'start'

    result=app.handle_admin_all_clients_daily(action,ids)

    run_id=str(result.get('run_id') or '')

    if not run_id:raise RuntimeError('Daily runner did not return run_id')

    _write_ledger(ledger_path,{'business_date':business_date,'run_id':run_id,'started_at':result.get('started_at'),

        'origin':'vps_schedule','selected_tasks':len(ids),'task_ids':ids,'attempts':attempts+1,'attempt_at':now.timestamp(),

        'run_status_at_check':result.get('status')})

    return {'status':'started','run_id':run_id,'selected_tasks':len(ids)}





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

