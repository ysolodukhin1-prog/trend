"""Daily Yandex task catalog and bounded execution for the scoped VPS admin."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg2

from api_completeness import marketplace_today
from psycopg2.extras import execute_values
from yandex_analytics_ingest import Collector, plan, check_scope_overlap, can_progress

SOURCES = {
    'sales_funnel': 'Воронка Яндекс Маркета',
    'boost_sales': 'Буст продаж Яндекс Маркета',
    'boost_shows': 'Буст показов Яндекс Маркета',
    'banners': 'Баннеры Яндекс Маркета',
    'shelves': 'Полки Яндекс Маркета',
    'payments': 'Выплаты и взаиморасчёты Яндекс Маркета',
    'services': 'Услуги и комиссии Яндекс Маркета',
    'realization': 'Реализация Яндекс Маркета',
    'stocks': 'Остатки Яндекс Маркета',
    'movement': 'Движение товаров Яндекс Маркета',
    'turnover': 'Оборачиваемость Яндекс Маркета',
    'prices': 'Цены Яндекс Маркета',
    'prices_report': 'Отчёт о ценах Яндекс Маркета',
    'catalog': 'Каталог Яндекс Маркета',
}
BASE_SOURCES = ('yandex_orders', 'yandex_order_stats', 'yandex_returns')
PENDING = {'queued', 'retry', 'polling'}
LIMITED = {'unavailable', 'limited'}


def expected_jobs(client, source, day):
    # Financial documents use calendar months. Open-month realization is not final.
    if source == 'realization':
        end = day.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    else:
        end = day
        start = day.replace(day=1) if source in {'payments', 'services'} else day
    return [job for job in plan(client, start, end) if job[4] == source]


def read_jobs(conn, keys):
    with conn.cursor() as cur:
        cur.execute('SELECT job_key,state,rows_count FROM yandex_analytics_jobs WHERE job_key=ANY(%s)', (list(keys),))
        return {key: (state, int(rows or 0)) for key, state, rows in cur.fetchall()}


def task_status(jobs, states, day):
    if not jobs:
        return 'limited', 'Не применимо', 'Нет доступных магазинов для этого источника'
    values = [states.get(job[0], ('missing', 0))[0] for job in jobs]
    period = f'{min(job[5] for job in jobs)}…{max(job[6] for job in jobs)}'
    if all(value == 'completed' for value in values):
        return 'ok', f'По {max(job[6] for job in jobs)}', f'Все {len(jobs)} заданий загружены; период {period}'
    if all(value == 'completed' or value in LIMITED for value in values):
        return 'limited', 'Ограничено', f'Часть источника недоступна; период {period}'
    return 'queued', f'Обновить {day}', f'Период {period}; готово {values.count("completed")}/{len(jobs)}'


def extend_plan(app, daily_plan, allowed_clients):
    """The base planner owns the three order sources; never append them again."""
    import client_registry
    day = marketplace_today() - timedelta(days=1)
    tasks = list(daily_plan['tasks'])
    if {task['client'] for task in tasks} - set(allowed_clients):
        raise ValueError('daily plan escaped VPS scope')
    # Execute original order tasks inside the explicit scoped writer bootstrap too.
    for task in tasks:
        source = str(task.get('key', '')).removeprefix('api_')
        if source in BASE_SOURCES:
            old = task['command']
            task['command'] = command(task['client'], source, day) + [
                '--date-from', old[old.index('--date-from') + 1],
                '--date-to', old[old.index('--date-to') + 1],
            ]
            task.update(nonfatal_error_patterns=['YANDEX_SOURCE_LIMITED'],
                        nonfatal_status_label='Ограничено', nonfatal_on_success=True)
            task.pop('cell_label', None)
    with app.client_registry_connection() as registry:
        clients = {client['key']: client for client in client_registry.list_clients(registry)
                   if client['key'] in allowed_clients}
    for client_key in sorted(allowed_clients):
        client = clients.get(client_key)
        if not client or client.get('status') != 'active' or 'yandex_market' not in client.get('marketplaces', []):
            continue
        jobs_by_source = {source: expected_jobs(client, source, day) for source in SOURCES}
        error = ''
        try:
            with psycopg2.connect(**app.read_db_config(client_key)) as conn:
                states = read_jobs(conn, [job[0] for jobs in jobs_by_source.values() for job in jobs])
        except Exception as exc:
            states = {}
            error = type(exc).__name__
        for index, (source, label) in enumerate(SOURCES.items()):
            status, text, detail = task_status(jobs_by_source[source], states, day)
            if error:
                status, text, detail = 'queued', 'Статус неизвестен', 'Проверка загрузки недоступна: ' + error
            task = make_task(client_key, source, label, day, 700 + index)
            task.update(initial_status=status, initial_progress_text=text, initial_detail=detail)
            tasks.append(task)
        tasks.append(make_task(client_key, 'views', 'Витрины BI Яндекс Маркета', day, 740))
    ids = [task['id'] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate daily task IDs')
    return app.admin_all_clients_row_major_plan(daily_plan['clients'], tasks)


def command(client, source, day):
    return [sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve()),
            '--client', client, '--source', source, '--day', str(day)]


def make_task(client, source, label, day, order):
    stage = 'views' if source == 'views' else ('assortment' if source == 'catalog' else 'imports')
    return dict(id=f'{client}:api:yandex_{source}:{day}', row_id=f'{stage}:yandex_{source}',
                row_label=label, row_order=order, stage=stage, source_kind='api',
                source_label='API · все кабинеты', client=client,
                client_label='TOPTOP' if client == 'toptop' else 'LERA NENA',
                key=f'api_yandex_{source}', report=label, command=command(client, source, day),
                cwd=str(Path(__file__).resolve().parent), env=dict(os.environ),
                initial_status='queued', initial_detail='Обновить аналитические витрины',
                initial_progress_text=f'Обновить {day}', failure_policy='continue', supports_resume=True,
                nonfatal_error_patterns=['YANDEX_SOURCE_LIMITED'],
                nonfatal_status_label='Ограничено', nonfatal_on_success=True)


def collect(client, conn, registry, key, source, day, log=print):
    jobs = expected_jobs(client, source, day)
    started = time.monotonic()
    log(f'ПЛАН: {client["key"]} | {source} | заданий={len(jobs)} | HTTP последовательно; паузы API 125/605с; resume=yes')
    if not jobs:
        log('YANDEX_SOURCE_LIMITED: источник не применим к подключённым магазинам')
        return
    # Shared lock also respects the existing full analytical collector.
    while True:
        with registry.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock_shared(hashtext('yandex_analytics_collector'))")
            acquired = cur.fetchone()
            acquired = next(iter(acquired.values())) if isinstance(acquired, dict) else acquired[0]
        registry.commit()
        if acquired:
            break
        log(f'ПРОГРЕСС: 0/{len(jobs)} (0%) | {source} | ожидание другой загрузки; повтор через 5с | ETA=ожидание')
        time.sleep(5)
    for business in sorted({str(job[2]) for job in jobs}):
        with registry.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s))", ('yandex_business:' + business,))
        registry.commit()
    keys = [job[0] for job in jobs]
    collector = Collector(client, conn, key, log, job_keys=keys)
    check_scope_overlap(conn, jobs)
    with conn.cursor() as cur:
        execute_values(cur, 'INSERT INTO yandex_analytics_jobs(job_key,client_key,business_id,campaign_id,source_key,date_from,date_to,request_body) VALUES %s ON CONFLICT(job_key) DO NOTHING', jobs)
    conn.commit()
    last = 0
    while True:
        states = read_jobs(conn, keys)
        pending = sum(value[0] in PENDING for value in states.values())
        done = sum(value[0] == 'completed' or value[0] in LIMITED for value in states.values())
        if time.monotonic() - last >= 15 or not pending:
            elapsed = time.monotonic() - started
            eta = f'{elapsed*(len(jobs)-done)/done:.0f}с' if done else 'ожидание API'
            log(f'ПРОГРЕСС: {done}/{len(jobs)} ({done/len(jobs)*100:.1f}%) | {source} {day} | rows={sum(v[1] for v in states.values())} | elapsed={elapsed:.0f}с | ETA={eta}')
            last = time.monotonic()
        if not pending:
            break
        progressed = collector.tick()
        if not progressed:
            with conn.cursor() as cur:
                cur.execute("SELECT business_id,source_key,state FROM yandex_analytics_jobs WHERE state IN ('polling','queued','retry','submitting','uncertain')")
                all_active = cur.fetchall()
            # Include other persisted lane reservations, but not unrelated queued jobs.
            own = {(str(job[2]), job[4]) for job in jobs}
            relevant = [(b,s,state) for b,s,state in all_active if state in {'submitting','uncertain'} or (b,s) in own]
            if not can_progress(relevant):
                raise RuntimeError('Незавершённая генерация отчёта блокирует очередь; checkpoint сохранён')
            time.sleep(5)
    states = read_jobs(conn, keys)
    errors = sum(value[0] not in {'completed'} | LIMITED for value in states.values())
    limited = sum(value[0] in LIMITED for value in states.values())
    log(f'ИТОГ: {source} | заданий={len(jobs)} errors={errors} limitations={limited} | rows={sum(v[1] for v in states.values())} | elapsed={time.monotonic()-started:.0f}с | output=client DB | partial={"yes" if errors or limited else "no"}')
    if errors:
        raise RuntimeError('Есть незавершённые задания; checkpoint сохранён')
    if limited:
        log('YANDEX_SOURCE_LIMITED: часть магазинов или отчётов недоступна')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=['toptop', 'lera_nena'], required=True)
    parser.add_argument('--source', choices=[*BASE_SOURCES, *SOURCES, 'views'], required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--date-from', type=date.fromisoformat)
    parser.add_argument('--date-to', type=date.fromisoformat)
    parser.add_argument('--resume', action='store_true', help='Продолжить с сохранённого checkpoint (режим по умолчанию)')
    args = parser.parse_args()
    if args.day >= marketplace_today():
        raise ValueError('Дневной период должен завершаться не позднее вчера')
    import pulse_vps_admin as v
    import client_registry
    v.configure_scope()
    token = v._USE_WRITER_CONFIG.set(True)
    try:
        with v.app.client_registry_connection() as registry:
            client = client_registry.get_client(registry, args.client)
            # Credential reads initialize the registry on another connection.
            # Release this read transaction before they acquire schema locks.
            registry.commit()
            if not client or client['db_name'] != args.client or client.get('status') != 'active':
                raise ValueError('Клиент или база вне разрешённого контура')
            with psycopg2.connect(**v.app.read_db_config(args.client)) as conn:
                conn.autocommit = False
                if args.source in BASE_SOURCES:
                    from yandex_market_history import Api, Storage, enabled_stores, run
                    key = v.app.registered_client_credential(args.client, 'yandex_market_api_key')
                    code = run(Api(key, log=print), Storage(conn, args.client), enabled_stores(client),
                               [args.source], args.date_from or args.day, args.date_to or args.day,
                               resume=True, overwrite=False, log=print)
                    if code == 3:
                        print('YANDEX_SOURCE_LIMITED: часть магазинов недоступна', flush=True)
                    elif code:
                        return code
                elif args.source == 'views':
                    from yandex_analytics import refresh_marts
                    print('ПЛАН: обновить 2 витрины Яндекс Маркета; API requests=0', flush=True)
                    refresh_marts(conn)
                    print('ПРОГРЕСС: 2/2 (100%) | Витрины Яндекс Маркета | errors=0 | ETA=0', flush=True)
                else:
                    key = v.app.registered_client_credential(args.client, 'yandex_market_api_key')
                    if not key:
                        raise ValueError('Нет API-ключа Яндекс Маркета')
                    collect(client, conn, registry, key, args.source, args.day,
                            log=lambda line: print(line, flush=True))
    finally:
        v._USE_WRITER_CONFIG.reset(token)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('ИТОГ: stopped=yes partial=yes; checkpoint сохранён', flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f'ИТОГ: errors=1 partial=yes | {type(exc).__name__}', flush=True)
        raise SystemExit(1)
