"""Read-only live reconciliation. PASS here is structural, never financial acceptance."""
import json
import sys
import time
from pathlib import Path
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ozon_category_dashboard'))
import app
from km_trade_finance import connect_km
from unit_economics_workspace import workspace


def main():
    results={};started=time.monotonic()
    print('ПЛАН: 2 клиента | август 2026 | только чтение PostgreSQL | без запросов в маркетплейсы',flush=True)
    for i,client in enumerate(('toptop','lera_nena'),1):
        cfg=app.read_db_config();cfg['database']=client
        data=workspace(cfg,client,'2026-08-01','2026-08-31')
        with connect_km(cfg) as conn,conn.cursor() as q:
            q.execute("SELECT count(*) n,sum(amount) amount FROM ozon_finance_lines WHERE operation_date BETWEEN '2026-08-01' AND '2026-08-31'")
            ozon=q.fetchone()
            q.execute("SELECT count(*) n FROM wb_finance_lines WHERE operation_date BETWEEN '2026-08-01' AND '2026-08-31'")
            wb=q.fetchone()
            q.execute("SELECT count(*) n FROM yandex_dim_store WHERE client_key=%s",(client,))
            ya=q.fetchone()
        summary={r['marketplace']:r for r in data['summary']}
        checks={
            'unique_row_keys':len({r['key'] for r in data['rows']})==len(data['rows']),
            'ozon_source_rows':summary['ozon']['source_rows']==ozon['n'],
            'ozon_amount_reconciles':abs(Decimal(str(summary['ozon']['net']))-ozon['amount'])<Decimal('.005'),
            'wb_source_rows':summary['wb']['source_rows']==wb['n'],
            'all_yandex_stores':len(data['stores'])==ya['n'],
            'missing_cogs_not_profit':all('profit' not in r for r in data['rows'] if r['cogs_status']!='sourced'),
            'no_duplicate_scenario_keys':len({r['scenario_key'] for r in data['scenarios']})==len(data['scenarios']),
        }
        results[client]={'checks':checks,'passed':all(checks.values()),'rows':len(data['rows']),
                         'summary':data['summary'],'financial_acceptance':'NOT_ACCEPTED'}
        elapsed=time.monotonic()-started;eta=elapsed/i*(2-i)
        print(f'ПРОГРЕСС: {i}/2 ({i*50}%) | {client} | строк {len(data["rows"])} | ошибок {sum(not v for v in checks.values())} | elapsed={elapsed:.1f}s | ETA={eta:.1f}s',flush=True)
    out=ROOT/'reports/unit_economics_20260912/structural_verification.json'
    out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'ИТОГ: {len(results)} клиента | структурные проверки {all(r["passed"] for r in results.values())} | финансовая приёмка PARTIAL | {out} | {time.monotonic()-started:.1f}s',flush=True)
    return 0 if all(r['passed'] for r in results.values()) else 1


if __name__=='__main__':raise SystemExit(main())
