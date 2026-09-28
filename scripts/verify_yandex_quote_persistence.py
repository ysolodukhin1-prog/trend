"""Exercise new scenario fields in a rolled-back local version transaction."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ozon_category_dashboard'))
import app
import unit_economics_workspace as workspace
from km_trade_finance import connect_km


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    print('ПЛАН:2аккаунта; сохранение параметров тарифа внутри транзакции с обязательным rollback; проверка0оставшихся версий.', flush=True)
    receipt = []
    original = workspace.connect_km
    for index, client in enumerate(('toptop', 'lera_nena'), 1):
        config = app.read_db_config(); config['database'] = client
        checks = {'yandexQuoteMode': 1, 'yandexFeeCount': 1, 'yandexAcceptCount': 0,
                  'yandexTransferCount': None, 'yandexDeliveryCount': 1.25, 'yandexMiddleCount': 2, 'yandexPaymentFrequency': 2, 'yandexPaymentDelay': 0}
        settings = {'global': {}, 'markets': {}, 'products': {'yandex|1|sku:test': checks}}
        payload = dict(name='QA tariff fields rollback', settings=settings,
                       source_period={'from': '2026-08-01', 'to': '2026-08-31'}, model_version='portfolio-2026-09-12.4')
        @contextmanager
        def rollback_connection(cfg):
            conn = original(cfg)
            try:
                yield conn
            finally:
                conn.rollback(); conn.close()
        workspace.connect_km = rollback_connection
        try:
            result = workspace.save_portfolio(config, client, payload)
            assert result['settings']['products']['yandex|1|sku:test'] == checks
            for invalid in (-1, 1001):
                invalid_payload = {**payload, 'settings': {**settings, 'global': {'yandexFeeCount': invalid}}}
                try:
                    workspace.save_portfolio(config, client, invalid_payload)
                    raise AssertionError('Invalid exposure saved')
                except ValueError:
                    pass
        finally:
            workspace.connect_km = original
        with connect_km(config) as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) n FROM unit_portfolio_versions WHERE id=%s', (result['id'],))
            assert cur.fetchone()['n'] == 0
        receipt.append({'client': client, 'new_fields_roundtrip': True, 'rollback_verified': True,
                        'remaining_test_versions': 0, 'invalid_exposures_rejected': 2})
        print(f'ПРОГРЕСС:{index}/2 | {client}: параметры сохранены и прочитаны; rollback подтверждён', flush=True)
    (ROOT / 'reports/unit_economics_20260912/yandex_quote46_persistence.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print('ИТОГ:2/2; постоянных QA-версий0; yandex_quote46_persistence.json', flush=True)


if __name__ == '__main__':
    main()
