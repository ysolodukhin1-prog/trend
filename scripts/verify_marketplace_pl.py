"""Read-only real-client reconciliation; no finance/source writes."""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ozon_category_dashboard'))
import app
from marketplace_pl import selected_payload, total

out = []
clients = ('toptop', 'lera_nena')
print('ПЛАН: 2 аккаунта, 3 площадки и сводная; только чтение БД, без API импортов', flush=True)
for index, client in enumerate(clients, 1):
    cfg = app.read_db_config(); cfg['database'] = client
    data = selected_payload(cfg, '2026-08-12', '2026-09-10', client, 'all', ['ozon', 'wb', 'yandex'])
    json.dumps(data, allow_nan=False)
    expected = total(r.get('revenue') for r in data['marketplace_totals'])
    assert data['totals']['revenue'] == expected
    result = dict(client=client, totals=data['totals'], marketplaces=data['marketplace_totals'],
                  monthly_revenue=total(r.get('revenue') for r in data['monthly']), products=len(data['products']))
    if expected is not None and result['monthly_revenue'] is not None:
        assert abs(expected-result['monthly_revenue']) < .02
    out.append(result)
    print(f'ПРОГРЕСС: {index}/2 ({index*50}%) | {client} | сверка суммы и месяцев пройдена', flush=True)
dest = ROOT / 'reports/marketplace_pl_20260913.json'
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(f'ИТОГ: 2 аккаунта проверены; {dest}', flush=True)
