"""Scoped TREND scheduler: three marketplaces, row barriers, inline retries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import threading

MARKETS = ('wb', 'ozon', 'yandex', 'lamoda')


def market(task):
    key = str(task.get('key', '')).removeprefix('api_')
    for name in MARKETS:
        if key.startswith(name + '_'):
            return name
    raise ValueError('Unclassified marketplace task: ' + str(task.get('id')))


def with_views(tasks, selected):
    pairs = {(t['client'], market(t)) for t in tasks
             if t['id'] in selected and t.get('stage') != 'views'}
    return selected | {t['id'] for t in tasks if t.get('stage') == 'views'
                       and (t['client'], market(t)) in pairs}


def run(runner, internal, only_clients=None):
    with runner.lock:
        ids = {tid for _, chain in runner._client_chains(only_clients) for tid in chain}
        runner._append_log_locked('ПЛАН: 4 площадки параллельно; 2 клиента в строке; витрины после импорта своей площадки', 'start')
        runner._save_locked(force=True)
    tasks = [t for tid, t in internal.items() if tid in ids]
    locks = {t['client']: threading.Lock() for t in tasks}
    view_slots = threading.Semaphore(runner.max_view_workers)

    def execute_client(group):
        for task in group:
            tid, client = task['id'], task['client']
            retries, backoff, _ = runner._failure_policy(task)
            for attempt in range(1, retries + 2):
                with runner.lock:
                    if runner.stop_requested or client in runner.paused_clients:
                        return False
                    if tid in runner.paused_task_ids:
                        runner._set_task_locked(
                            tid, status='stopped', detail='Остановлено по этапу',
                            progress_text='Остановлено',
                        )
                        runner._save_locked(force=True)
                        # Assortment is independent from report views. Pausing
                        # it must not block the later views in this market lane.
                        if task.get('stage') == 'assortment':
                            break
                        return False
                is_view = task.get('stage') == 'views'
                with locks[client] if is_view else nullcontext():
                    with view_slots if is_view else nullcontext():
                        outcome = runner._execute_task(tid, internal, attempt=attempt)
                if outcome in {'done', 'limited', 'skipped'}:
                    break
                if outcome == 'stopped' and task.get('stage') == 'assortment':
                    break
                if outcome == 'stopped' or attempt > retries:
                    return False
                if not runner._interruptible_sleep(max(1, backoff * attempt),
                    f'Повтор в текущей строке: {tid}, попытка {attempt + 1}/{retries + 1}'):
                    return False
        return True

    def lane(name):
        rows = {}
        stage_order = {'imports': 0, 'assortment': 1, 'views': 2}
        for task in tasks:
            if market(task) == name:
                rows.setdefault((stage_order.get(task.get('stage'), 9), task.get('row_order', 0),
                                 task['row_id']), []).append(task)
        for row_key, row in sorted(rows.items()):
            if runner.stop_requested:
                return
            clients = {}
            for task in row:
                clients.setdefault(task['client'], []).append(task)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(execute_client, clients.values()))
            except Exception as exc:
                with runner.lock:
                    runner.state['status'] = 'error'
                    runner._append_log_locked(f'{name}: сбой строки {row_key[2]}: {type(exc).__name__}: {exc}', 'error')
                return
            if not all(results):
                with runner.lock:
                    runner._append_log_locked(f'{name}: следующие строки и витрины ожидают успешного завершения {row_key[2]}', 'warning')
                    runner._save_locked(force=True)
                return
    # Validate every task before any process is launched.
    for task in tasks:
        market(task)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lane, MARKETS))
