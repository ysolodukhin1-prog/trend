"""Current-month gap audit and resumable execution for the scoped admin."""
from copy import deepcopy
from datetime import date, timedelta
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psycopg2
from api_completeness import API_COMPLETENESS_SPECS, _stored_dates, _relation_exists, marketplace_today

SNAPSHOTS = {'ozon_prices','ozon_assortment','wb_catalog','ozon_stock','wb_stock_current',
             'ozon_feedbacks','wb_marketplace',
             'yandex_prices','yandex_prices_report','yandex_catalog',
             'lamoda_stock','lamoda_prices','lamoda_promotions','lamoda_catalog'}
MONTHLY = {'yandex_payments','yandex_services'}
LEDGER = 'trend_import_coverage'


def days(start, end):
    return [start + timedelta(days=i) for i in range(max(0, (end-start).days+1))]


def windows(values):
    result = []
    for day in sorted(set(values)):
        if result and day == result[-1][1] + timedelta(days=1):
            result[-1] = (result[-1][0], day)
        else:
            result.append((day, day))
    return result


def _run_ranges(cur, table, step):
    if not _relation_exists(cur, table):
        return []
    cur.execute(f"SELECT date_from,date_to FROM {table} WHERE step=%s AND status='ok' AND date_from IS NOT NULL AND date_to IS NOT NULL", (step,))
    return cur.fetchall()


def ledger_ranges(cur, key):
    if not _relation_exists(cur, LEDGER):
        return []
    cur.execute(f'SELECT date_from,date_to FROM {LEDGER} WHERE source_key=%s AND status=%s', (key,'ok'))
    return cur.fetchall()


def limited_ranges(cur, key):
    if not _relation_exists(cur, LEDGER):
        return []
    cur.execute(f'SELECT date_from,date_to FROM {LEDGER} WHERE source_key=%s AND status=%s', (key,'limited'))
    return cur.fetchall()


def yandex_days(cur, client, key, expected):
    from yandex_daily import expected_jobs, BASE_SOURCES
    source = key.removeprefix('yandex_')
    base = key in BASE_SOURCES
    table = 'yandex_market_history_windows' if base else 'yandex_analytics_jobs'
    if not _relation_exists(cur, table):
        return set()
    if base:
        from yandex_market_history import enabled_stores
        stores = enabled_stores(client)
        lanes = {(str(s['business_id']),str(s['campaign_id'])) for s in stores}
        cur.execute(f'SELECT business_id,campaign_id,date_from,date_to FROM {table} WHERE client_key=%s AND source_key=%s AND status=%s', (client['key'],key,'completed'))
        rows=cur.fetchall()
        return {day for day in expected if lanes and all(any((str(b),str(c))==lane and a<=day<=z for b,c,a,z in rows) for lane in lanes)}
    cur.execute(f'SELECT business_id,campaign_id,date_from,date_to,request_body FROM {table} WHERE client_key=%s AND source_key=%s AND state=%s', (client['key'],source,'completed'))
    rows=cur.fetchall()
    covered=set()
    for day in expected:
        jobs=expected_jobs(client,source,marketplace_today()-timedelta(days=1) if source=='realization' else day)
        if source=='stocks' and day != max(expected):
            jobs=[job for job in jobs if job[5] != marketplace_today()]
        if jobs and all(any(str(b)==str(job[2]) and str(c)==str(job[3]) and (a<=day<=z if key in MONTHLY else a<=job[5] and z>=job[6])
                           and body.get('archived') == job[7].adapted.get('archived')
                           for b,c,a,z,body in rows) for job in jobs):
            covered.add(day)
    return covered


def audit(conn, client, key, today=None):
    today=today or marketplace_today()
    month_start, end = today.replace(day=1), today-timedelta(days=1)
    # Keep the previous month in the audit for the first week. Marketplace APIs
    # may publish the final day late; without this overlap it is never retried.
    previous_month_start = (month_start-timedelta(days=1)).replace(day=1)
    start = previous_month_start if today.day <= 7 else month_start
    snapshot=key in SNAPSHOTS
    if key=='yandex_stocks':
        stores=[s for acc in client.get('marketplace_accounts',{}).get('yandex_market',[]) for s in acc.get('stores',[]) if s.get('is_accessible') and s.get('import_enabled')]
        snapshot=bool(stores) and not any(s.get('placement_type')=='FBY' for s in stores)
    expected=days(start,end)
    note=''
    if key in {'lamoda_orders', 'lamoda_fbo_shipments', 'lamoda_fbs_returns'}:
        # Keep a short overlap across month boundaries so the first nightly
        # run of a new month still loads yesterday and retries recent gaps.
        start=min(start,today-timedelta(days=7))
        expected=days(start,end)
    if snapshot:
        expected=[today]
        note='Текущий снимок; прошлые даты этим API не восстанавливаются.'
    elif key=='yandex_realization':
        expected=[month_start-timedelta(days=1)]
        note='Реализация: последний закрытый месяц; текущий ещё не закрыт.'
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout='15000ms'")
        ranges=ledger_ranges(cur,key)
        limitations=limited_ranges(cur,key)
        covered={d for d in expected if any(a<=d<=b for a,b in ranges)}
        if key.startswith('yandex_'):
            covered |= yandex_days(cur,client,key,expected)
        elif key in API_COMPLETENESS_SPECS or key=='ozon_prices':
            spec=dict(API_COMPLETENESS_SPECS['ozon_finance' if key=='ozon_prices' else key])
            if key=='ozon_finance':
                spec={'relation':'ozon_finance_events','date_column':'operation_date','where':"source_kind = 'ozon_api'"}
            if spec.get('run_table') and not snapshot:
                ranges += _run_ranges(cur,spec['run_table'],spec['run_step'])
            if expected:
                covered.update(_stored_dates(cur,spec,min(expected),max(expected)))
            covered.update(d for d in expected if any(a<=d<=b for a,b in ranges))
    missing=sorted(set(expected)-covered)
    unavailable=[]
    retention=int(API_COMPLETENESS_SPECS.get(key,{}).get('retention_days',0))
    if retention:
        unavailable=[d for d in missing if d < end-timedelta(days=retention-1)]
        note=f'API загрузчика доступен за последние {retention} дней; более ранние пропуски требуют исторического отчёта.'
    runnable=[d for d in missing if d not in unavailable]
    if key=='wb_finance_weekly':
        # Reconcile complete Monday-Sunday documents intersecting this month.
        periods=[]
        sunday=start+timedelta(days=(6-start.weekday())%7)
        while sunday<=end:
            a=sunday-timedelta(days=6)
            if not any(x<=a and y>=sunday for x,y in ranges):
                periods.append((a,sunday))
            sunday+=timedelta(days=7)
        missing=sorted({d for a,b in periods for d in days(max(a,start),b)})
        execution=periods
        note='Закрытые недели, пересекающие текущий месяц; операция обновляется по rrd_id.'
    elif key in MONTHLY and runnable:
        by_month={}
        for day in runnable:
            by_month.setdefault((day.year,day.month),[]).append(day)
        execution=[(date(year,month,1),max(values)) for (year,month),values in sorted(by_month.items())]
        note='Месячный документ обновляется целиком до вчерашнего дня.'
    elif key=='yandex_realization' and runnable:
        execution=[(runnable[0].replace(day=1),runnable[0])]
    else:
        # Separate days avoid reloading already covered dates between gaps.
        execution=[(d,d) for d in runnable]
    return dict(expected_from=str(min(expected)) if expected else str(start),
                expected_to=str(max(expected)) if expected else str(end), snapshot=snapshot,
                missing_days=len(missing), missing_windows=[{'date_from':str(a),'date_to':str(b)} for a,b in windows(missing)],
                execution=execution, unavailable=unavailable, note=note,
                limited_days=sum(any(a<=d<=b for a,b in limitations) for d in missing))


def command_for(template, start, end):
    command=list(template)
    for flag,value in [('--date-from',start),('--date-to',end),('--day',end)]:
        if flag in command:
            command[command.index(flag)+1]=str(value)
    if any('yandex_daily.py' in value for value in command) and '--day' in command:
        if end >= marketplace_today() or 'realization' in command:
            command[command.index('--day')+1]=str(marketplace_today()-timedelta(days=1))
    if any('trend_daily.py' in value for value in command):
        command += ['--date-from',str(start),'--date-to',str(end)]
        if 'ozon_prices' in command and '--monthly-snapshot' not in command:
            command += ['--monthly-snapshot']
    return command


def extend_plan(app, plan):
    import client_registry
    with app.client_registry_connection() as registry:
        clients={c['key']:c for c in client_registry.list_clients(registry)}
    unique={}
    for task in plan['tasks']:
        unique.setdefault((task['client'],task['key']),deepcopy(task))
    for client in plan['clients']:
        own=[t for (c,_),t in unique.items() if c==client['key']]
        with psycopg2.connect(**app.read_db_config(client['key'])) as conn:
            for task in own:
                if task.get('stage')=='views':
                    continue
                key=task['key'].removeprefix('api_')
                try:
                    state=audit(conn,clients[client['key']],key)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    # Never silently interpret failed inspection as complete coverage.
                    raise RuntimeError(f'Проверка дат {client["key"]}/{key}: {type(exc).__name__}') from exc
                queue=[{'date_from':str(a),'date_to':str(b),'command':command_for(task['command'],a,b)} for a,b in state.pop('execution')]
                blocked=state.pop('unavailable')
                task.update(state)
                task['id']=f'{client["key"]}:monthly:{key}:{marketplace_today():%Y-%m}'
                known_limited=(
                    bool(state['missing_days'])
                    and state['limited_days'] + len(blocked) >= state['missing_days']
                )
                task['initial_status']='limited' if known_limited else ('queued' if queue else ('limited' if blocked else 'ok'))
                task['initial_progress_text']=f'Нет {state["missing_days"]} дн.' if state['missing_days'] else 'Актуально'
                if known_limited:
                    task['initial_progress_text'] += ' · ограничено'
                task['cell_label']=task['initial_progress_text']
                task['initial_detail']=state['note']+' Пропуски: '+(', '.join(str(d['date_from']) if d['date_from']==d['date_to'] else d['date_from']+'—'+d['date_to'] for d in state['missing_windows']) or 'нет')
                task['command']=[sys.executable,'-u',str(Path(__file__).resolve()),'--client',client['key'],
                                 '--source',key,'--queue',json.dumps(queue,ensure_ascii=False),
                                 '--patterns',json.dumps(task.get('nonfatal_error_patterns',[])),
                                 '--blocked',str(len(blocked))]
                task['supports_resume']=True
                task['nonfatal_error_patterns']=['TREND_SOURCE_LIMITED']
    return app.admin_all_clients_row_major_plan(plan['clients'],list(unique.values()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',choices=['toptop','lera_nena'],required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--queue',required=True)
    parser.add_argument('--patterns',default='[]')
    parser.add_argument('--blocked',type=int,default=0)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    import pulse_vps_admin as v
    v.configure_scope();v._USE_WRITER_CONFIG.set(True);v.app.CURRENT_CLIENT.set(args.client)
    queue=json.loads(args.queue); patterns=[p.casefold() for p in json.loads(args.patterns)] + ['source_limited','analytics | нет доступа','отзывы и вопросы wb недоступны','wb_finance_source_not_ready','scope is not allowed for this resource','wb http 403','ozon api http 403']
    started=time.monotonic();limited=args.blocked
    print(f'ПЛАН: текущий месяц | {args.client}/{args.source} | недостающих окон={len(queue)} | недоступных дат={args.blocked} | окна последовательно; штатные паузы API',flush=True)
    with psycopg2.connect(**v.app.read_db_config(args.client)) as conn:
        with conn.cursor() as cur:
            # PostgreSQL's CREATE TABLE IF NOT EXISTS can still race while two
            # workers create the relation and its composite type concurrently.
            # Serialize this one-time bootstrap per database; the transaction
            # lock is released immediately after the commit below.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(%s))", (f'public.{LEDGER}',))
            cur.execute(f'CREATE TABLE IF NOT EXISTS {LEDGER} (source_key text,date_from date,date_to date,status text NOT NULL,finished_at timestamptz DEFAULT now(),PRIMARY KEY(source_key,date_from,date_to))')
        conn.commit()
        for index,window in enumerate(queue,1):
            a,b=window['date_from'],window['date_to']
            with conn.cursor() as cur:
                cur.execute(f'SELECT 1 FROM {LEDGER} WHERE source_key=%s AND date_from<=%s AND date_to>=%s AND status=%s',(args.source,a,b,'ok'))
                done=bool(cur.fetchone())
            conn.commit()
            if not done:
                matched=False
                with subprocess.Popen(window['command'],env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace') as process:
                    for line in process.stdout:
                        matched |= any(p in line.casefold() for p in patterns)
                        print(line.rstrip(),flush=True)
                    code=process.wait()
                if code and not matched:
                    raise RuntimeError(f'Импорт {a}—{b} завершился с кодом {code}; следующие даты и витрины остановлены')
                status='limited' if matched else 'ok'
                limited+=int(matched)
                with conn.cursor() as cur:
                    cur.execute(f'INSERT INTO {LEDGER}(source_key,date_from,date_to,status) VALUES (%s,%s,%s,%s) ON CONFLICT(source_key,date_from,date_to) DO UPDATE SET status=EXCLUDED.status,finished_at=now()', (args.source,a,b,status))
                conn.commit()
            elapsed=time.monotonic()-started
            print(f'ПРОГРЕСС: {index}/{len(queue)} ({index/len(queue)*100:.1f}%) | {a}—{b} | checkpoint={done} | elapsed={elapsed:.0f}с | ETA={elapsed*(len(queue)-index)/index:.0f}с',flush=True)
    print(f'ИТОГ: окон={len(queue)} errors=0 limitations={limited} elapsed={time.monotonic()-started:.0f}с; output=client DB',flush=True)
    if limited:
        print('TREND_SOURCE_LIMITED: часть дат или метрик недоступна; пропуски сохранены',flush=True)


if __name__=='__main__':
    main()
