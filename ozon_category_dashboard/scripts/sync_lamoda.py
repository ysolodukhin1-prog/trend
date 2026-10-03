#!/usr/bin/env python3
"""Read-only Lamoda Seller API v2 analytics importer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path[:0] = [str(DASHBOARD_ROOT), str(PROJECT_ROOT)]
AUTH_URL = "https://public-api-seller.lamoda.ru/api/v2/auth-token"
API_BASE = "https://public-api-seller.lamoda.ru/api/v2"
PAGE_LIMIT = 100

# Documented GET-only sources. Promotion writes and every other mutating method
# are deliberately absent from this allow-list.
SOURCES = {
    "orders": ("/orders", ("orders", "items")),
    "stock": ("/stocks-summary", ("stocks", "items")),
    "catalog": ("/nomenclatures", ("nomenclatures", "items")),
    # v2 nomenclatures carry prices/discounts; a separate dataset keeps TREND's
    # analytical grain independent without making a write request.
    "prices": ("/nomenclatures", ("nomenclatures", "items")),
    "promotions": ("/promotions", ("promotions", "items")),
    "fbo_shipments": ("/fbo/shipments", ("shipments", "items")),
    "fbs_returns": ("/fbs/return-items", ("returnItems", "items")),
}
READ_ONLY_PATHS = {source[0] for source in SOURCES.values()}


def stable_id(prefix, payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_dt(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def number(value):
    if isinstance(value, dict):
        value = value.get("amount") if value.get("amount") is not None else value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def records(payload, names):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for name in (*names, "data", "result", "response", "content"):
        value = payload.get(name)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = records(value, names)
            if nested:
                return nested
    return []


def stock_rows(payload):
    """Represent the non-paginated /stocks-summary response as one daily row."""
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        raise RuntimeError("Lamoda stock summary is missing")
    return [{"id": "summary", "summary": summary}]


def pick(row, *names):
    for name in names:
        if row.get(name) not in (None, ""):
            return row.get(name)
    return None


class LamodaClient:
    def __init__(self, client_id, client_secret, seller_id):
        self.client_id = client_id
        self.client_secret = client_secret
        self.seller_id = str(seller_id)
        self.token = ""
        self.requests_made = 0

    def _open(self, request):
        for attempt in range(1, 6):
            try:
                with urlopen(request, timeout=60) as response:
                    self.requests_made += 1
                    body = response.read()
                    return json.loads(body.decode()) if body else {}
            except HTTPError as exc:
                self.requests_made += 1
                detail = exc.read().decode("utf-8", "replace")[:500]
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 5:
                    raise RuntimeError(f"Lamoda API HTTP {exc.code}: {detail}") from exc
                time.sleep(min(2 ** (attempt - 1), 30))
            except (URLError, TimeoutError) as exc:
                if attempt == 5:
                    raise RuntimeError(f"Lamoda API unavailable: {exc}") from exc
                time.sleep(min(2 ** (attempt - 1), 30))
        raise RuntimeError("Lamoda API retries exhausted")

    def authenticate(self):
        body = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        payload = self._open(Request(AUTH_URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
        }))
        self.token = str(payload.get("access_token") or "").strip()
        if not self.token:
            raise RuntimeError("Lamoda v2 did not return access_token")

    def get(self, path, params=None):
        if path not in READ_ONLY_PATHS:
            raise RuntimeError(f"Lamoda read-only policy blocked GET {path}")
        query = urlencode(params or {}, doseq=True)
        url = API_BASE + path + (("?" + query) if query else "")
        for auth_attempt in range(2):
            request = Request(url, method="GET", headers={
                "Authorization": f"Bearer {self.token}", "Accept": "application/json",
            })
            try:
                return self._open(request)
            except RuntimeError as exc:
                if auth_attempt or "Lamoda API HTTP 401" not in str(exc):
                    raise
                print("WAIT: Lamoda access token expired; refreshing token and retrying GET", flush=True)
                self.authenticate()
        raise RuntimeError("Lamoda authentication retry exhausted")

    def paginated(self, path, names, params=None):
        result = []
        # Lamoda validates limits per operation. Nomenclatures accepts at most
        # 25, while the other currently used read-only operations accept 100.
        page_limit = 25 if path == "/nomenclatures" else PAGE_LIMIT
        for page in range(1, 10001):
            query = {"sellerId": self.seller_id, "page": page, "limit": page_limit, **(params or {})}
            batch = records(self.get(path, query), names)
            result.extend(batch)
            print(
                f"PROGRESS: LAMODA v2 {path} page={page} batch={len(batch)} "
                f"rows={len(result)} requests={self.requests_made}", flush=True,
            )
            if len(batch) < page_limit:
                return result
        raise RuntimeError(f"Lamoda pagination limit exceeded for {path}")


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.lamoda_api_runs (
            id bigserial PRIMARY KEY, step text NOT NULL, date_from date, date_to date,
            status text NOT NULL DEFAULT 'running', requests_made integer NOT NULL DEFAULT 0,
            rows_loaded integer NOT NULL DEFAULT 0, error text,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
        );
        CREATE TABLE IF NOT EXISTS public.lamoda_v2_entities (
            dataset text NOT NULL, record_key text NOT NULL, snapshot_date date NOT NULL,
            seller_sku text, lamoda_sku text, status text, event_at timestamptz,
            amount numeric, currency text, payload jsonb NOT NULL,
            synced_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (dataset, record_key, snapshot_date)
        );
        ALTER TABLE public.lamoda_v2_entities ADD COLUMN IF NOT EXISTS account_id uuid;
        ALTER TABLE public.lamoda_v2_entities ADD COLUMN IF NOT EXISTS fulfillment text;
        CREATE INDEX IF NOT EXISTS idx_lamoda_v2_entities_account ON public.lamoda_v2_entities(account_id);
        CREATE INDEX IF NOT EXISTS idx_lamoda_v2_entities_dataset_event
            ON public.lamoda_v2_entities(dataset, event_at);
        CREATE OR REPLACE VIEW public.v_lamoda_prices AS
            SELECT * FROM public.lamoda_v2_entities WHERE dataset = 'prices';
        CREATE OR REPLACE VIEW public.v_lamoda_promotions AS
            SELECT * FROM public.lamoda_v2_entities WHERE dataset = 'promotions';
        CREATE OR REPLACE VIEW public.v_lamoda_fbo_shipments AS
            SELECT * FROM public.lamoda_v2_entities WHERE dataset = 'fbo_shipments';
        CREATE OR REPLACE VIEW public.v_lamoda_fbs_returns AS
            SELECT * FROM public.lamoda_v2_entities WHERE dataset = 'fbs_returns';
        """)
    conn.commit()


def store_entities(conn, dataset, rows, account_id, fulfillment):
    from uuid import UUID
    account_id = str(UUID(str(account_id)))
    if fulfillment not in {"FBO", "FBS"}:
        raise ValueError("Explicit FBO/FBS account required")
    stamp = date.today()
    values = []
    for row in rows:
        identity = pick(
            row, "id", "orderId", "shipmentId", "returnItemId", "promotionId",
            "sku", "lamodaSku", "externalSku", "supplierSku",
        )
        key = account_id + ":" + str(identity or stable_id(dataset + ":", row))
        price = pick(row, "salePrice", "price", "amount")
        currency = price.get("currency") if isinstance(price, dict) else pick(row, "currency")
        values.append((
            dataset, key, stamp,
            str(pick(row, "externalSku", "sellerSku", "supplierSku") or ""),
            str(pick(row, "sku", "lamodaSku") or ""),
            str(pick(row, "status", "state") or ""),
            parse_dt(pick(row, "updatedAt", "createdAt", "date", "plannedDate")),
            number(price), str(currency or ""), Json(row), account_id, fulfillment,
        ))
    # Lamoda can return duplicate entity identifiers across pages. PostgreSQL
    # cannot update the same conflict target twice in one INSERT, so retain the
    # last observed payload for each exact primary key.
    values = list({(value[0], value[1], value[2]): value for value in values}.values())
    with conn.cursor() as cur:
        if values:
            execute_values(cur, """INSERT INTO public.lamoda_v2_entities
                (dataset,record_key,snapshot_date,seller_sku,lamoda_sku,status,event_at,amount,currency,payload,account_id,fulfillment)
                VALUES %s ON CONFLICT(dataset,record_key,snapshot_date) DO UPDATE SET
                seller_sku=excluded.seller_sku,lamoda_sku=excluded.lamoda_sku,
                status=excluded.status,event_at=excluded.event_at,amount=excluded.amount,
                currency=excluded.currency,payload=excluded.payload,account_id=excluded.account_id,fulfillment=excluded.fulfillment,synced_at=now()""", values)
    conn.commit()
    return len(values)


def verify_credentials(client_id, client_secret, seller_id):
    seller_id = str(seller_id or "").strip()
    if not seller_id:
        raise RuntimeError("Lamoda Seller ID is required")
    client = LamodaClient(client_id, client_secret, seller_id)
    client.authenticate()
    client.get("/orders", {"sellerId": seller_id, "page": 1, "limit": 1})
    return True


def registry_runtime():
    import app
    if os.environ.get("PULSE_DB_PASSWORD_FILE") and not os.environ.get("DASHBOARD_DB_PASSWORD"):
        import threading
        import pulse_vps_admin
        threading.current_thread().name = "history-lamoda"
        pulse_vps_admin.configure_scope()
    return app


def eligible_account(account, step):
    return not ((step == 'fbo_shipments' and account['fulfillment'] != 'FBO') or
                (step == 'fbs_returns' and account['fulfillment'] != 'FBS'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument("--step", choices=(*SOURCES, "finance"), required=True)
    parser.add_argument("--account-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.date_from > args.date_to:
        parser.error("--date-from must not exceed --date-to")
    if args.step == "finance":
        print(
            "SOURCE_UNAVAILABLE: Lamoda · Расходы и финансовые документы | "
            "в Seller API v2 нет подтверждённого read-only финансового метода; "
            "нужен отдельный экспорт Lamoda Seller/LAB", flush=True,
        )
        return 3

    app = registry_runtime()
    from lamoda_accounts import list_accounts, read_credentials, KEYS
    from psycopg2.extras import RealDictCursor
    # The scoped runtime always uses the central registry, including LERA NENA.
    registry = dict(app.read_db_config(args.client_key))
    registry['database'] = os.environ.get('DASHBOARD_REGISTRY_DB_NAME') or 'toptop'
    failures = 0
    completed = 0
    with psycopg2.connect(**registry) as rc:
        accounts = list_accounts(rc, args.client_key)
        if args.account_id:
            accounts = [a for a in accounts if a['account_id'] == args.account_id]
        accounts = [a for a in accounts if eligible_account(a, args.step)]
        if not accounts:
            print('SOURCE_UNAVAILABLE: Lamoda | Нет подключённого аккаунта для этого метода доставки', flush=True)
            return 3
        for account in accounts:
            # Keep a shared row lock throughout the import. Detach/rotation waits;
            # a concurrently detached account cannot start an import.
            with rc.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT account_id FROM public.bi_lamoda_accounts WHERE account_id=%s AND client_key=%s AND revision=%s FOR SHARE',
                            (account['account_id'], args.client_key, account['revision']))
                if not cur.fetchone():
                    continue
            credentials = read_credentials(rc, account['account_id'], app.client_credentials_master_key())
            try:
                import_account(app, args, account, *(credentials.get(k, '') for k in KEYS))
                completed += 1
            except Exception:
                failures += 1
                print(f"ACCOUNT_ERROR: Lamoda {account['account_id']} {account['fulfillment']}", flush=True)
            finally:
                rc.commit()
    return 1 if failures else (0 if completed else 3)


def import_account(app, args, account, client_id, secret, seller_id):
    db = dict(app.read_db_config(args.client_key))
    db["database"] = args.database_name
    api = LamodaClient(client_id, secret, seller_id)
    path, names = SOURCES[args.step]
    params = {"country": "RU"} if args.step in {"catalog", "prices"} else {}
    period_start = datetime.combine(args.date_from, dt_time.min, tzinfo=timezone.utc)
    period_end = datetime.combine(args.date_to + timedelta(days=1), dt_time.min, tzinfo=timezone.utc)
    if args.step == "orders":
        params.update({
            "updatedAtFrom": period_start.isoformat().replace("+00:00", "Z"),
            "updatedAtTo": period_end.isoformat().replace("+00:00", "Z"),
        })
    elif args.step == "fbs_returns":
        params.update({"returnDateFrom": args.date_from.isoformat(), "returnDateTo": args.date_to.isoformat()})
    elif args.step == "fbo_shipments":
        params.update({"from": args.date_from.isoformat(), "to": args.date_to.isoformat()})

    with psycopg2.connect(**db) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO public.lamoda_api_runs(step,date_from,date_to,details)
                VALUES(%s,%s,%s,%s) RETURNING id""", (
                args.step, args.date_from, args.date_to,
                Json({"api": "seller-v2", "read_only": True, "path": path, "account_id": account["account_id"], "fulfillment": account["fulfillment"]}),
            ))
            run_id = cur.fetchone()[0]
        conn.commit()
        total = 0
        try:
            api.authenticate()
            if args.step == "stock":
                # /stocks-summary is one aggregate object, not a paginated list.
                rows = stock_rows(api.get(path, {"sellerId": seller_id}))
            else:
                rows = api.paginated(path, names, params)
            def in_period(value):
                parsed = parse_dt(value)
                if not parsed:
                    return True
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return period_start <= parsed < period_end
            if args.step == "orders":
                rows = [row for row in rows if in_period(row.get("updatedAt"))]
            elif args.step == "fbs_returns":
                rows = [row for row in rows if in_period(row.get("returnDate"))]
            elif args.step == "fbo_shipments":
                rows = [row for row in rows if in_period(row.get("plannedDate"))]
            total = store_entities(conn, args.step, rows, account["account_id"], account["fulfillment"])
            with conn.cursor() as cur:
                cur.execute("""UPDATE public.lamoda_api_runs SET status='ok',requests_made=%s,
                    rows_loaded=%s,finished_at=now() WHERE id=%s""", (api.requests_made, total, run_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("""UPDATE public.lamoda_api_runs SET status='error',requests_made=%s,
                    rows_loaded=%s,error=%s,finished_at=now() WHERE id=%s""",
                    (api.requests_made, total, type(exc).__name__, run_id))
            conn.commit()
            raise
    print(f"RESULT: LAMODA v2 step={args.step} requests={api.requests_made} rows={total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
