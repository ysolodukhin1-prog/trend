"""Read-only source reconciliation for the Yandex service-direction correction."""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ozon_category_dashboard'))
import app
from km_trade_finance import connect_km
from unit_economics_workspace import workspace


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    folder = ROOT / 'reports/unit_economics_20260912'
    result = {}; began = time.monotonic()
    print('ПЛАН: 2 аккаунта, август2026; сверка каждой строки и независимых сумм услуг; только SELECT, без запросов к маркетплейсам.', flush=True)
    for index, client in enumerate(['toptop', 'lera_nena'], 1):
        config = app.read_db_config(); config['database'] = client
        old = json.loads((folder / f'{client}_portfolio_source.json').read_text(encoding='utf-8-sig'))
        fresh = workspace(config, client, '2026-08-01', '2026-08-31')
        (folder / f'{client}_services45_source.json').write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding='utf-8')
        prior = {r['key']: r for r in old['rows']}; current = {r['key']: r for r in fresh['rows']}
        changes = []
        for key in prior.keys() | current.keys():
            if key not in prior or key not in current:
                changes.append({'key': key, 'error': 'row identity changed'}); continue
            for field in ('revenue', 'units', 'net'):
                a, b = prior[key].get(field), current[key].get(field)
                if (a is None) != (b is None) or (a is not None and abs(Decimal(str(a)) - Decimal(str(b))) > Decimal('.000001')):
                    changes.append({'key': key, 'field': field, 'old': a, 'new': b})
        rows = [r for r in fresh['rows'] if r['marketplace'] == 'yandex']
        with connect_km(config) as conn, conn.cursor() as cur:
            # Independent source predicates, not calls to the implementation classifier.
            cur.execute('''SELECT
              sum(service_amount) FILTER(WHERE service_type='delivery' OR
                (service_type='crossregional_delivery' AND service_name='Доставка покупателю (средняя миля)')) expected_forward,
              sum(service_amount) FILTER(WHERE
                (service_type='crossregional_delivery' AND service_name='Доставка невыкупов и возвратов') OR
                (service_type IN ('order_processing','order_processing_on_warehouse') AND service_name='Обработка невыкупа или возврата')) expected_reverse,
              sum(service_amount) FILTER(WHERE service_type='export_from_warehouse') warehouse_withdrawal,
              sum(service_amount) FILTER(WHERE service_type='delivery_via_transit_warehouse') transit,
              sum(service_amount) source_services
              FROM yandex_fact_services WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN '2026-08-01' AND '2026-08-31' ''', (client,))
            expected = cur.fetchone()
        total = lambda key: sum(Decimal(str(r.get(key) or 0)) for r in rows)
        ledger_total = sum(Decimal(str(e['amount'])) for r in rows for e in r.get('service_components', []) if e['amount'] is not None)
        entry = {'cashflow_differences': changes, 'rows': len(fresh['rows']), 'yandex_rows': len(rows),
                 'forward': total('logistics_cost'), 'reverse': total('reverse_cost'), 'fulfillment': total('fulfillment_cost'),
                 'unknown_direction_rows': sum(r.get('unclassified_service_rows', 0) for r in rows),
                 'ledger_total': ledger_total, **expected}
        assert not changes, f'{client}: source cashflows changed'
        assert entry['forward'] == expected['expected_forward'] and entry['reverse'] == expected['expected_reverse']
        assert ledger_total == expected['source_services']
        entry['samples'] = [r['key'] for r in rows if r.get('units', 0) and r['units'] > 0 and r.get('cogs') and
                            r.get('logistics_cost', 0) > 0 and r.get('reverse_cost', 0) > 0][:4]
        result[client] = entry
        elapsed = time.monotonic() - began
        print(f'ПРОГРЕСС: {index}/2 ({index/2:.0%}) | {client}: {len(fresh["rows"])} строк, расхождений0; прямая {entry["forward"]}, обратная {entry["reverse"]}; {elapsed:.1f}с; ETA {elapsed/index*(2-index):.1f}с', flush=True)
    target = folder / 'yandex_services45_reconciliation.json'
    target.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    print(f'ИТОГ: 2/2 аккаунта, денежные потоки сохранены, суммы классификации сверены; {target}', flush=True)


if __name__ == '__main__':
    main()
