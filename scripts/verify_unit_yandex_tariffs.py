"""Read-only six-store live tariff smoke check; saves no credentials or raw cards."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ozon_category_dashboard'))
import app
from km_trade_finance import connect_km
from unit_yandex_tariffs import quote


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out = []
    start = time.monotonic()
    print('ПЛАН: 6 магазинов, по 1 товару; только расчёт тарифов, последовательно, пауза от 0,65 с.', flush=True)
    for client in ['toptop', 'lera_nena']:
        cfg = app.read_db_config(); cfg['database'] = client
        with connect_km(cfg) as conn, conn.cursor() as cur:
            cur.execute('SELECT campaign_id,business_id FROM yandex_dim_store WHERE client_key=%s AND is_accessible AND import_enabled ORDER BY campaign_id', (client,))
            stores = cur.fetchall()
        raw = json.loads((ROOT / f'reports/unit_economics_20260912/{client}_portfolio_source.json').read_text(encoding='utf-8-sig'))
        for store in stores:
            campaign = str(store['campaign_id'])
            rows = [r for r in raw['rows'] if r['marketplace'] == 'yandex' and str(r['cabinet']) == campaign
                    and r.get('units') and r['units'] > 0 and r.get('revenue') and r['revenue'] > 0 and not r.get('unallocated')]
            if rows:
                row = max(rows, key=lambda r: r['revenue'])
                payload = dict(cabinet=campaign, sku=row['sku'], price=round(row['revenue'] / row['units'], 2))
                sample_basis = 'historical average seller price, test input only'
            else:
                with connect_km(cfg) as conn, conn.cursor() as cur:
                    cur.execute('''SELECT r.payload->'offer' offer FROM yandex_analytics_raw r JOIN yandex_analytics_jobs j USING(job_key)
                      WHERE j.client_key=%s AND j.business_id=%s AND j.source_key='catalog' AND j.state='completed'
                      AND NOT coalesce((r.payload->'offer'->>'archived')::boolean,false)
                      AND r.payload->'offer'->'basicPrice'->>'value' IS NOT NULL ORDER BY j.finished_at DESC LIMIT 1''', (client, store['business_id']))
                    row = cur.fetchone()['offer']
                payload = dict(cabinet=campaign, sku=row['offerId'], price=row['basicPrice']['value'])
                sample_basis = 'catalog seller price, test input only; no sales claimed'
            try:
                result = quote(cfg, client, payload, app.registered_client_credential)
                out.append(dict(result, sample_basis=sample_basis))
                detail = f"{result['status']}; услуг {len(result['services'])}"
            except ValueError as exc:
                out.append(dict(client=client, **payload, error=str(exc), sample_basis=sample_basis))
                detail = str(exc)
            elapsed = time.monotonic() - start
            print(f'ПРОГРЕСС: {len(out)}/6 ({len(out)/6:.0%}) | {client} {campaign} | {detail} | {elapsed:.1f} с | ETA {elapsed/len(out)*(6-len(out)):.1f} с', flush=True)
    target = ROOT / 'reports/unit_economics_20260912/yandex_tariff_quote_probe.json'
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    success = sum(bool(r.get('ok')) for r in out)
    print(f'ИТОГ: {success}/{len(out)} ответов; ошибок/пропусков {len(out)-success}; {time.monotonic()-start:.1f} с; {target}', flush=True)


if __name__ == '__main__':
    main()
