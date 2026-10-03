"""Historical Yandex import. Credentials are read only from encrypted registry."""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import psycopg2
from client_registry import get_client
from yandex_market_history import Api, SOURCES, Storage, enabled_stores, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-key', required=True)
    parser.add_argument('--database-name', required=True)
    parser.add_argument('--date-from', type=date.fromisoformat, required=True)
    parser.add_argument('--date-to', type=date.fromisoformat, required=True)
    parser.add_argument('--step', choices=list(SOURCES), required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--resume', action='store_true')
    mode.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.date_from > args.date_to or args.date_to >= app.marketplace_today():
        raise ValueError('Исторический период должен заканчиваться не позже вчерашнего дня')
    with app.client_registry_connection() as conn:
        client = get_client(conn, args.client_key)
    if not client or client.get('status') != 'active' or 'yandex_market' not in client.get('marketplaces', []):
        raise ValueError('Яндекс Маркет не подключён клиенту')
    if client['db_name'] != args.database_name:
        raise ValueError('База не соответствует клиенту')
    stores = enabled_stores(client)
    if not stores:
        raise ValueError('Проверьте API-ключ и включите магазины для импорта в настройках клиента')
    key = app.registered_client_credential(args.client_key, 'yandex_market_api_key')
    log = lambda text: print(text, flush=True)
    api = Api(key, log=log)
    config = dict(app.read_db_config())
    config['database'] = client['db_name']
    conn = psycopg2.connect(**config)
    try:
        return run(api, Storage(conn, client['key']), stores, [args.step], args.date_from, args.date_to,
                   resume=args.resume, overwrite=args.overwrite, log=log)
    finally:
        conn.close()  # releases the per-client advisory lock and temporary staging


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('ИТОГ ЯНДЕКС: stopped=yes partial=yes', flush=True)
        sys.exit(130)
    except Exception as exc:
        # Known application messages are safe; DB/driver exceptions can contain payloads.
        message = str(exc) if type(exc) in (ValueError, RuntimeError) else type(exc).__name__
        print(f'ИТОГ ЯНДЕКС: errors=1 partial=yes | {message}', flush=True)
        sys.exit(1)
