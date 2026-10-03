"""TREND-only additions and writer bootstrap for existing marketplace importers."""
import argparse
from datetime import date, timedelta
from api_completeness import marketplace_today
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
EXTRA = {
    'ozon_assortment': ('Каталог и характеристики Ozon', 90),
    'ozon_prices': ('Цены и тарифы Ozon', 180),
    'ozon_media': ('Медийная реклама Ozon', 190),
    'wb_catalog': ('Каталог и характеристики WB', 290),
    'wb_marketplace': ('Новые FBS-заказы и склады WB', 390),
    'wb_finance_weekly': ('Недельная сверка финансов WB', 590),
    'wb_media': ('Медийная реклама WB', 595),
    'lamoda_orders': ('Заказы Lamoda', 690),
    'lamoda_stock': ('Остатки Lamoda', 700),
    'lamoda_prices': ('Цены и скидки Lamoda', 710),
    'lamoda_promotions': ('Продвижение и акции Lamoda', 720),
    'lamoda_fbo_shipments': ('Поставки и приёмка FBO Lamoda', 730),
    'lamoda_fbs_returns': ('Возвраты FBS Lamoda', 740),
    'lamoda_catalog': ('Ассортимент Lamoda', 750),
    'lamoda_views': ('Витрины и отчёты Lamoda', 790),
}


def extend_plan(app, plan):
    from yandex_daily import make_task
    from trend_views import tasks as view_tasks
    day = marketplace_today() - timedelta(days=1)
    # Replace the three opaque bundle rows with explicit, independently
    # runnable view groups.
    tasks = [task for task in plan['tasks'] if task.get('stage') != 'views']
    for client in plan['clients']:
        for key, (label, order) in EXTRA.items():
            task = make_task(client['key'], key, label, day, order)
            task.update(id=f'{client["key"]}:api:{key}:{day}', row_id='imports:' + key,
                        key='api_' + key, supports_resume=False,
                        initial_detail='Покрытие ещё не проверено',
                        command=[sys.executable, '-u', str(ROOT / 'trend_daily.py'),
                                 '--client', client['key'], '--source', key, '--day', str(day)])
            tasks.append(task)
        tasks.extend(view_tasks(client['key'], client['label'], day))
    for task in tasks:
        source = str(task.get('key', '')).removeprefix('api_')
        if source in {'ozon_assortment', 'wb_catalog', 'yandex_catalog', 'lamoda_catalog'}:
            task.update(stage='assortment', row_id='assortment:' + source,
                        row_order=100 + int(task.get('row_order') or 0))
        elif source == 'lamoda_views':
            task.update(stage='views', row_id='views:lamoda', row_order=790)
    for task in tasks:
        # All descendants inherit the scoped writer configuration via sitecustomize.
        task['env'] = {**task.get('env', os.environ), 'TREND_IMPORT_WORKER': '1',
                       'DASHBOARD_CLIENT': task['client'], 'KM_DB_NAME': task['client'],
                       'WB_API_OUTPUT_ROOT': '/var/lib/pulse/imports',
                       'PYTHONPATH': os.pathsep.join([str(ROOT / 'trend_runtime'), str(ROOT), str(ROOT.parent)])}
        task['nonfatal_error_patterns'] = list(task.get('nonfatal_error_patterns', [])) + ['TREND_SOURCE_LIMITED']
        task['nonfatal_on_success'] = True
    result = app.admin_all_clients_row_major_plan(plan['clients'], tasks)
    stage_order = {'imports': 0, 'assortment': 1, 'views': 2}
    client_order = {client['key']: index for index, client in enumerate(plan['clients'])}
    result['tasks'].sort(key=lambda task: (
        stage_order.get(task.get('stage'), 9), int(task.get('row_order') or 900),
        client_order.get(task.get('client'), len(client_order)), str(task.get('id') or ''),
    ))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=['toptop', 'lera_nena'], required=True)
    parser.add_argument('--source', choices=[*EXTRA, 'wb_views', 'ozon_views', 'lamoda_views'], required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--date-from', type=date.fromisoformat)
    parser.add_argument('--date-to', type=date.fromisoformat)
    parser.add_argument('--monthly-snapshot', action='store_true')
    args = parser.parse_args()
    import pulse_vps_admin as v
    v.configure_scope()
    v._USE_WRITER_CONFIG.set(True)
    v.app.CURRENT_CLIENT.set(args.client)
    os.environ.update(DASHBOARD_CLIENT=args.client, KM_DB_NAME=args.client)
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_client_pipeline import credentials_to_env
    os.environ.update(credentials_to_env(args.client))
    sys.path.insert(0, str(ROOT.parent / 'scripts'))
    print(f'ПЛАН: {args.client} | {args.source} | период {args.day}; API последовательно, штатные лимиты и повторы', flush=True)
    if args.source == 'lamoda_views':
        sys.argv = [str(ROOT / 'scripts' / 'rebuild_lamoda_views.py'), '--client', args.client]
        runpy.run_path(sys.argv[0], run_name='__main__')
    elif args.source.startswith('lamoda_'):
        lamoda_step = args.source.removeprefix('lamoda_')
        sys.argv = [str(ROOT / 'scripts' / 'sync_lamoda.py'), '--client-key', args.client,
                    '--database-name', args.client, '--date-from', str(args.date_from or args.day),
                    '--date-to', str(args.date_to or args.day), '--step', lamoda_step]
        if args.resume:
            sys.argv.append('--resume')
        runpy.run_path(sys.argv[0], run_name='__main__')
    elif args.source.endswith('_media'):
        from trend_media import ingest
        ingest(args.source.split('_')[0], args.day, v.app)
    else:
        params = ['--date-from', str(args.date_from or args.day), '--date-to', str(args.date_to or args.day)]
        script = 'sync_km_wb_extended_api.py'
        if args.source == 'ozon_assortment':
            script, params = 'sync_ozon_assortment.py', []
        elif args.source == 'ozon_prices':
            if not args.monthly_snapshot:
                print('ИТОГ: ozon_prices | пропуск: снимок выполняется раз в месяц | errors=0', flush=True)
                return
            script = 'sync_km_ozon_finance.py'
            snapshot_day = marketplace_today() - timedelta(days=1)
            params = ['--source', 'api', '--date-from', str(snapshot_day), '--date-to', str(snapshot_day),
                      '--with-product-snapshot']
        elif args.source == 'wb_catalog':
            params += ['--step', 'content']
        elif args.source == 'wb_marketplace':
            params += ['--step', 'marketplace']
        elif args.source == 'wb_finance_weekly':
            today = marketplace_today()
            end = args.date_to or (today - timedelta(days=today.weekday() + 1))
            params = ['--step', 'finance', '--finance-period', 'weekly',
                      '--date-from', str(args.date_from or (end - timedelta(days=6))), '--date-to', str(end)]
        else:
            script = 'sync_km_' + args.source.split('_')[0] + '_api.py'
            params += ['--step', 'views']
        sys.argv = [str(ROOT.parent / 'scripts' / script), *params]
        try:
            runpy.run_path(sys.argv[0], run_name='__main__')
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
        if args.source.endswith('_views'):
            from trend_media import refresh
            refresh(args.source.split('_')[0], v.app)
    print(f'ИТОГ: {args.source} | 1/1 (100%) | errors=0 | output=client DB', flush=True)


if __name__ == '__main__':
    main()
