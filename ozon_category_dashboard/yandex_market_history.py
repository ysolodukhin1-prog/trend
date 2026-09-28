"""Read-only historical Yandex sources, scoped to client/business/store.

Contracts: official reference/orders/getBusinessOrders,
reference/orders-stats/getOrdersStats and reference/returns/getReturns.
Checkpoints describe fully fetched windows, including source-confirmed empties.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from psycopg2.extras import Json, execute_values

SOURCES = {
    "yandex_orders": ("Яндекс · Заказы", "orders", "Заказы по дате создания: статусы, товары, количество, цены и скидки."),
    "yandex_order_stats": ("Яндекс · Детализация заказов", "orders", "Заказы по дате создания: товарные позиции, платежи, комиссии и субсидии; не полный финансовый отчёт."),
    "yandex_returns": ("Яндекс · Невыкупы и возвраты", "returns", "Возвраты и невыкупы по дате обновления: связь с заказом, товары, статусы возврата денег и логистики."),
}
READ_OPERATIONS = (
    ("POST", r"/v1/businesses/[1-9]\d*/orders"),
    ("POST", r"/v2/campaigns/[1-9]\d*/stats/orders"),
    ("GET", r"/v2/campaigns/[1-9]\d*/returns"),
)


class SourceUnavailable(RuntimeError):
    pass


def enabled_stores(client):
    stores = {}
    for account in client.get("marketplace_accounts", {}).get("yandex_market", []):
        if account.get("is_accessible") is False:
            continue
        for store in account.get("stores") or []:
            if store.get("is_accessible") is False or store.get("import_enabled") is False:
                continue
            business, campaign = str(account.get("business_id") or ""), str(store.get("campaign_id") or "")
            if not re.fullmatch(r"[1-9]\d*", business) or not re.fullmatch(r"[1-9]\d*", campaign):
                raise ValueError("Перепроверьте API-ключ Яндекса: некорректный ID магазина")
            stores[business, campaign] = {"business_id": business, "campaign_id": campaign,
                                        "name": str(store.get("name") or campaign)}
    return list(stores.values())


def windows(start, end):
    if start > end:
        raise ValueError("Дата начала позже даты окончания")
    while start <= end:
        stop = min(start + timedelta(days=29), end)
        yield start, stop
        start = stop + timedelta(days=1)


def request_spec(source, store, start, end, token=""):
    query = {"limit": 50 if source == "yandex_orders" else 100}
    if token:
        query["pageToken"] = token
    if source == "yandex_orders":
        return "POST", f"/v1/businesses/{store['business_id']}/orders", query, {
            "campaignIds": [int(store["campaign_id"])], "fake": False,
            "dates": {"creationDateFrom": start.isoformat(), "creationDateTo": (end + timedelta(days=1)).isoformat()}}
    if source == "yandex_order_stats":
        return "POST", f"/v2/campaigns/{store['campaign_id']}/stats/orders", query, {
            "dateFrom": start.isoformat(), "dateTo": end.isoformat()}
    if source == "yandex_returns":
        query.update(fromDate=start.isoformat(), toDate=end.isoformat())
        return "GET", f"/v2/campaigns/{store['campaign_id']}/returns", query, None
    raise ValueError("Неизвестный раздел Яндекс Маркета")


class Api:
    def __init__(self, key, log=print, sleep=time.sleep, clock=time.monotonic):
        if not key or not key.isascii() or any(c.isspace() for c in key):
            raise ValueError("Сохраните корректный API-ключ Яндекс Маркета")
        self.key, self.log, self.sleep, self.clock = key, log, sleep, clock
        self.requests, self.last_request = 0, None

    def request(self, method, path, query, body):
        if not any(method == m and re.fullmatch(p, path) for m, p in READ_OPERATIONS):
            raise ValueError("Метод не разрешён для исторического чтения Яндекса")
        for attempt in range(5):
            if self.last_request is not None:
                self.sleep(max(0, 0.8 - (self.clock() - self.last_request)))
            request = Request("https://api.partner.market.yandex.ru" + path + "?" + urlencode(query),
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={"Api-Key": self.key, "Content-Type": "application/json", "Accept": "application/json"}, method=method)
            self.requests += 1
            self.last_request = self.clock()
            self.log(f"ЗАПРОС: {self.requests}/неизвестно | {method} {path} | попытка={attempt + 1}/5")
            try:
                with urlopen(request, timeout=45) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or payload.get("status") == "ERROR":
                    raise RuntimeError("Яндекс вернул ошибку или некорректный JSON")
                return payload
            except HTTPError as exc:
                # Never forward response bodies, headers or signed URLs into logs.
                if exc.code in (401, 403):
                    raise SourceUnavailable(f"Яндекс HTTP {exc.code}: нет доступа к разделу магазина") from None
                if exc.code not in (420, 429, 500, 502, 503, 504) or attempt == 4:
                    raise RuntimeError(f"Яндекс HTTP {exc.code}: окно не завершено") from None
                retry = exc.headers.get("Retry-After", "")
                pause = int(retry) if retry.isdigit() else 5 * 2 ** attempt
            except (URLError, TimeoutError):
                if attempt == 4:
                    raise RuntimeError("Яндекс: сеть недоступна, окно не завершено") from None
                pause = 5 * 2 ** attempt
            except (ValueError, UnicodeError):
                raise RuntimeError("Яндекс вернул некорректный JSON") from None
            while pause > 0:
                self.log(f"ОЖИДАНИЕ API: осталось {pause} с | запросов {self.requests} | повтор={attempt + 2}/5")
                wait = min(pause, 5)
                self.sleep(wait)
                pause -= wait


def pages(api, source, store, start, end):
    token, seen = "", set()
    for _ in range(100000):
        payload = api.request(*request_spec(source, store, start, end, token))
        result = payload if source == "yandex_orders" else payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get(SOURCES[source][1]), list):
            raise RuntimeError("Яндекс не вернул ожидаемый список; пустое покрытие не записано")
        yield result[SOURCES[source][1]]
        paging = result.get("paging") or {}
        if not isinstance(paging, dict):
            raise RuntimeError("Некорректная пагинация Яндекса")
        token = paging.get("nextPageToken") or ""
        if not token:
            return
        if not isinstance(token, str) or token in seen:
            raise RuntimeError("Яндекс повторил страницу; окно не завершено")
        seen.add(token)
    raise RuntimeError("Превышен предел страниц; окно не завершено")


def sanitize(value):
    excluded = {"buyer", "recipient", "address", "phone", "email", "firstname", "lastname", "middlename", "courier", "customer", "credentials", "token", "apikey"}
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower().replace("_", "").replace("-", "") not in excluded}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def entity(source, store, row):
    if not isinstance(row, dict):
        raise RuntimeError("Некорректная запись Яндекса")
    identity = row.get("orderId") if source == "yandex_orders" else row.get("id")
    if identity is None or not str(identity).isdigit():
        raise RuntimeError("Яндекс вернул запись без ID")
    if row.get("campaignId") is not None and str(row["campaignId"]) != store["campaign_id"]:
        raise RuntimeError("Ответ Яндекса относится к другому магазину")
    if source == "yandex_returns":
        if row.get("orderId") is None:
            raise RuntimeError("Возврат без ID заказа")
        identity = f"{row['orderId']}:{identity}"
    raw_date = row.get("updateDate" if source == "yandex_returns" else "creationDate")
    try:
        source_date = date.fromisoformat(str(raw_date)[:10])
    except ValueError:
        raise RuntimeError("Запись Яндекса без корректной даты; окно не завершено") from None
    return str(identity), source_date, sanitize(row)


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.yandex_market_entities (
            client_key text NOT NULL, business_id text NOT NULL, campaign_id text NOT NULL,
            source_key text NOT NULL, entity_id text NOT NULL, source_date date NOT NULL,
            payload jsonb NOT NULL, synced_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(client_key,business_id,campaign_id,source_key,entity_id));
            CREATE INDEX IF NOT EXISTS yandex_market_entities_dates
            ON public.yandex_market_entities(client_key,source_key,campaign_id,source_date);
            CREATE TABLE IF NOT EXISTS public.yandex_market_history_windows (
            client_key text NOT NULL, business_id text NOT NULL, campaign_id text NOT NULL,
            source_key text NOT NULL, date_from date NOT NULL, date_to date NOT NULL,
            status text NOT NULL, rows_count bigint NOT NULL DEFAULT 0, requests integer NOT NULL DEFAULT 0,
            started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, error text,
            PRIMARY KEY(client_key,business_id,campaign_id,source_key,date_from,date_to));""")
    conn.commit()


class Storage:
    def __init__(self, conn, client_key):
        self.conn, self.client_key = conn, client_key
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s),hashtext(%s))", ("yandex_history", client_key))
            if not cur.fetchone()[0]:
                raise RuntimeError("Историческая загрузка Яндекса уже выполняется для клиента")
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE yandex_history_stage(entity_id text PRIMARY KEY, source_date date, payload jsonb) ON COMMIT PRESERVE ROWS")
        conn.commit()

    def key(self, source, store, start, end):
        return self.client_key, store["business_id"], store["campaign_id"], source, start, end

    def completed(self, key):
        with self.conn.cursor() as cur:
            cur.execute("""SELECT status FROM public.yandex_market_history_windows WHERE
                (client_key,business_id,campaign_id,source_key,date_from,date_to)=(%s,%s,%s,%s,%s,%s)""", key)
            row = cur.fetchone()
        return bool(row and row[0] == "completed")

    def begin(self, key):
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE pg_temp.yandex_history_stage")
            cur.execute("""INSERT INTO public.yandex_market_history_windows
                (client_key,business_id,campaign_id,source_key,date_from,date_to,status)
                VALUES (%s,%s,%s,%s,%s,%s,'running') ON CONFLICT
                (client_key,business_id,campaign_id,source_key,date_from,date_to) DO UPDATE SET
                status='running',rows_count=0,requests=0,started_at=now(),finished_at=NULL,error=NULL""", key)
        self.conn.commit()

    def stage(self, source, store, rows, start, end):
        unique = {}
        for row in rows:
            identity, source_date, payload = entity(source, store, row)
            if source != 'yandex_returns' and not start <= source_date <= end:
                raise RuntimeError("Яндекс вернул запись вне выбранного окна; перезапись отменена")
            unique[identity] = (identity, source_date, Json(payload))
        if unique:
            with self.conn.cursor() as cur:
                execute_values(cur, """INSERT INTO pg_temp.yandex_history_stage VALUES %s
                    ON CONFLICT(entity_id) DO UPDATE SET source_date=EXCLUDED.source_date,payload=EXCLUDED.payload""", list(unique.values()))
        self.conn.commit()

    def finish(self, key, requests, overwrite=False):
        # Deletion, publication and completion are one transaction after ALL pages.
        with self.conn.cursor() as cur:
            if overwrite:
                cur.execute("SELECT EXISTS(SELECT 1 FROM pg_temp.yandex_history_stage WHERE source_date NOT BETWEEN %s AND %s)", key[4:6])
                if cur.fetchone()[0]:
                    raise RuntimeError("Ответ содержит обновления вне окна; используйте обычную загрузку без удаления")
                cur.execute("""DELETE FROM public.yandex_market_entities WHERE
                    (client_key,business_id,campaign_id,source_key)=(%s,%s,%s,%s)
                    AND source_date BETWEEN %s AND %s""", key)
            cur.execute("""INSERT INTO public.yandex_market_entities
                (client_key,business_id,campaign_id,source_key,entity_id,source_date,payload)
                SELECT %s,%s,%s,%s,entity_id,source_date,payload FROM pg_temp.yandex_history_stage
                ON CONFLICT(client_key,business_id,campaign_id,source_key,entity_id) DO UPDATE SET
                source_date=EXCLUDED.source_date,payload=EXCLUDED.payload,synced_at=now()""", key[:4])
            cur.execute("SELECT count(*) FROM pg_temp.yandex_history_stage")
            count = cur.fetchone()[0]
            cur.execute("""UPDATE public.yandex_market_history_windows SET status='completed',rows_count=%s,
                requests=%s,finished_at=now(),error=NULL WHERE
                (client_key,business_id,campaign_id,source_key,date_from,date_to)=(%s,%s,%s,%s,%s,%s)""", (count, requests, *key))
        self.conn.commit()
        return count

    def fail(self, key, status, message, requests):
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE public.yandex_market_history_windows SET status=%s,error=%s,
                requests=%s,finished_at=now() WHERE
                (client_key,business_id,campaign_id,source_key,date_from,date_to)=(%s,%s,%s,%s,%s,%s)""", (status, message, requests, *key))
        self.conn.commit()


def run(api, storage, stores, sources, start, end, *, resume=False, overwrite=False, log=print):
    if resume and overwrite:
        raise ValueError("Перезапись и продолжение несовместимы")
    if not stores or not sources or set(sources) - SOURCES.keys():
        raise ValueError("Выберите разделы и включите доступные магазины Яндекса в настройках клиента")
    plan = [(source, store, a, b) for source in sources for store in stores for a, b in windows(start, end)]
    started, total_rows, skipped, limited, errors = time.monotonic(), 0, 0, 0, 0
    completed, stopped = 0, False
    log(f"ПЛАН ЯНДЕКС: магазинов={len(stores)} | разделов={len(sources)} | окон={len(plan)} по <=30 дней | запросов минимум={len(plan)}, итог зависит от страниц | интервал=0.8с | до 5 попыток | resume={resume} overwrite={overwrite}")
    try:
        for index, (source, store, a, b) in enumerate(plan, 1):
            key = storage.key(source, store, a, b)
            context = f"{source} | business={store['business_id']} store={store['campaign_id']} | {a}..{b}"
            if resume and storage.completed(key):
                skipped += 1
                log(f"[{index}/{len(plan)}] {context}: checkpoint завершён, пропуск")
                continue
            storage.begin(key)
            before, received = api.requests, 0
            try:
                for page_index, rows in enumerate(pages(api, source, store, a, b), 1):
                    if source == 'yandex_returns':
                        outside = sum(not a <= entity(source, store, row)[1] <= b for row in rows)
                        if outside:
                            log(f"ЯНДЕКС: возвратов с текущей датой обновления вне окна={outside}; сохраняются по ID без удаления, дата источника сохранена")
                    storage.stage(source, store, rows, a, b)
                    received += len(rows)
                    elapsed = time.monotonic() - started
                    eta = f"{elapsed / (index - 1) * (len(plan) - index + 1):.0f}с" if index > 1 else "расчёт"
                    log(f"ЯНДЕКС ПРОГРЕСС: {index}/{len(plan)} ({(index-1)/len(plan):.1%}) | {context} | страница {page_index} batch={len(rows)} rows={received} saved={total_rows} | запросов {api.requests} | elapsed={elapsed:.0f}с ETA={eta}")
                count = storage.finish(key, api.requests - before, overwrite)
                total_rows += count
                completed += 1
                log(f"[{index}/{len(plan)}] {context}: imported={count} errors=0 | step_pct={index/len(plan)*100:.1f}")
            except SourceUnavailable as exc:
                limited += 1
                storage.fail(key, "limited", str(exc), api.requests - before)
                log(f"SOURCE_LIMITED: {context} | {exc}")
            except BaseException as exc:
                stopped = isinstance(exc, (KeyboardInterrupt, SystemExit))
                errors += 0 if stopped else 1
                storage.fail(key, "stopped" if stopped else "failed", "Остановлено" if stopped else type(exc).__name__, api.requests - before)
                raise
    finally:
        log(f"ИТОГ ЯНДЕКС: completed={completed}/{len(plan)} rows={total_rows} skipped={skipped} limitations={limited} errors={errors} | stopped={stopped} partial={completed+skipped < len(plan)} | запросов {api.requests} | elapsed={time.monotonic()-started:.0f}с | output_db=yandex_market_entities,yandex_market_history_windows")
    return 3 if limited else 0


def inspect_history(app, client, start, end):
    """Read-only coverage over each enabled store, never infer coverage from row dates."""
    import psycopg2
    stores = enabled_stores(client)
    config = dict(app.read_db_config())
    config["database"] = client["db_name"]
    rows = []
    conn = psycopg2.connect(**config)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '15000ms'")
            cur.execute("SELECT to_regclass('public.yandex_market_history_windows'),to_regclass('public.yandex_market_entities')")
            checkpoint_exists, data_exists = cur.fetchone()
            for source, (label, _, description) in SOURCES.items():
                for store in stores:
                    key = (client["key"], store["business_id"], store["campaign_id"], source)
                    checkpoints, count, first, last = [], None, None, None
                    if checkpoint_exists:
                        cur.execute("""SELECT date_from,date_to,status,rows_count,requests,started_at,finished_at
                            FROM public.yandex_market_history_windows WHERE
                            (client_key,business_id,campaign_id,source_key)=(%s,%s,%s,%s)
                            ORDER BY started_at DESC""", key)
                        checkpoints = cur.fetchall()
                    if data_exists:
                        cur.execute("""SELECT count(*),min(source_date),max(source_date) FROM public.yandex_market_entities
                            WHERE (client_key,business_id,campaign_id,source_key)=(%s,%s,%s,%s)
                            AND (%s::date IS NULL OR source_date >= %s) AND (%s::date IS NULL OR source_date <= %s)""",
                                    (*key, start, start, end, end))
                        count, first, last = cur.fetchone()
                    required = set()
                    if start and end:
                        required = {start + timedelta(days=n) for n in range((end-start).days+1)}
                    covered = set()
                    relevant = []
                    for a, b, status, n, requests, started, finished in checkpoints:
                        if (start and b < start) or (end and a > end):
                            continue
                        relevant.append((a,b,status,n,requests,started,finished))
                        if status == 'completed':
                            left, right = max(a, start or a), min(b, end or b)
                            covered.update(left + timedelta(days=n) for n in range((right-left).days+1))
                    missing = sorted(required - covered)
                    ranges = []
                    for day in missing:
                        if ranges and day == date.fromisoformat(ranges[-1]['date_to']) + timedelta(days=1):
                            ranges[-1]['date_to'] = day.isoformat()
                        else:
                            ranges.append({'date_from':day.isoformat(),'date_to':day.isoformat()})
                    latest = relevant[0] if relevant else None
                    latest_run = dict(zip(('date_from','date_to','status','rows','requests','started_at','finished_at'), latest)) if latest else None
                    if latest_run:
                        latest_run = {k:v.isoformat() if isinstance(v,(date,datetime)) else v for k,v in latest_run.items()}
                    status = 'loaded' if required and not missing else 'limited' if covered else 'missing'
                    if not covered and latest and latest[2] in ('failed','stopped','running'):
                        status = 'error'
                    if not covered and latest and latest[2] == 'limited':
                        status = 'limited'
                    rows.append({'key': f"{source}:{store['business_id']}:{store['campaign_id']}",
                        'label': f"{label} · {store['name']} ({store['campaign_id']})", 'description': description,
                        'object': 'yandex_market_entities', 'status': status,
                        'status_label': {'loaded':'Период проверен','limited':'Частично / нет доступа','missing':'Не проверено','error':'Окно не завершено'}[status],
                        'rows':count,'date_from':first.isoformat() if first else None,'date_to':last.isoformat() if last else None,
                        'latest_run':latest_run,'covered_days':len(covered),'requested_days':len(required),
                        'missing_days':len(missing),'missing_ranges':ranges,
                        'limit_note':'Окна до 30 дней. Пустой успешный ответ считается проверенным; ошибка не считается покрытием.'})
    finally:
        conn.close()
    return {'ok':True,'client':{k:client[k] for k in ('key','label','db_name')},'marketplace':'yandex_market',
            'requested_period':{'date_from':start.isoformat() if start else None,'date_to':end.isoformat() if end else None},
            'checked_at':datetime.now().isoformat(timespec='seconds'),'rows':rows,
            'summary':{'total':len(rows),**{s:sum(r['status']==s for r in rows) for s in ('loaded','limited','missing','error','empty')}}}
