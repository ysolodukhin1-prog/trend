"""Read-only Ozon Performance phrase reports. Client/period isolated durable cache."""
from __future__ import annotations
import hashlib, json, threading, time, math, os
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import requests

BASE = "https://api-performance.ozon.ru"
CACHE = Path(__file__).parent / ".cache" / "ad_search_queries"
LOCK = threading.Lock()
RUNNING = set()
STATES = {"CAMPAIGN_STATE_RUNNING": "Активные", "CAMPAIGN_STATE_INACTIVE": "На паузе",
          "CAMPAIGN_STATE_FINISHED": "Завершённые", "CAMPAIGN_STATE_ARCHIVED": "В архиве",
          "CAMPAIGN_STATE_PLANNED": "Запланированные"}

def campaign_coverage(campaigns):
    eligible = [c for c in campaigns if "PLACEMENT_TOP_PROMOTION" in (c.get("placement") or [])]
    return {"total": len(campaigns), "eligible": len(eligible),
        "eligible_active": sum(c.get("state") == "CAMPAIGN_STATE_RUNNING" for c in eligible),
        "eligible_archived": sum(c.get("state") == "CAMPAIGN_STATE_ARCHIVED" for c in eligible),
        "other_placements": len(campaigns)-len(eligible)}
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def parse_rows(body, campaigns):
    """Ozon supplies traffic only; never allocate campaign spend/orders to phrases."""
    output = []
    for campaign_id, block in body.items():
        if not isinstance(block, dict): continue
        report = block.get("report") or {}
        for raw in report.get("rows", []):
            phrase = raw.get("phrase") or raw.get("searchQuery") or raw.get("query")
            if phrase is None:
                raise ValueError("Неизвестный формат фразы в отчёте Ozon")
            def number(*keys):
                for key in keys:
                    if raw.get(key) is not None:
                        return float(str(raw[key]).replace(" ", "").replace(",", "."))
                return None
            views, clicks = number("views", "shows", "impressions"), number("clicks")
            item = campaigns.get(str(campaign_id), {})
            output.append({"norm_query": str(phrase), "advert_id": str(campaign_id),
                "campaign_name": item.get("title") or str(campaign_id),
                "nm_id": str(raw.get("sku") or raw.get("id") or ""),
                "product_name": raw.get("title") or raw.get("name") or str(raw.get("sku") or "Товар не указан источником"),
                "seller_article": "", "views": views, "clicks": clicks,
                "ctr_pct": clicks * 100 / views if views and clicks is not None else None,
                "spend_rub": None, "orders": None, "cpc_rub": None, "cpo_rub": None,
                "cvr_pct": None, "bid_rub": None, "avg_position": None,
                "status": "traffic", "status_label": "Трафик", "recommendation": "Проверить релевантность запроса товару",
                "campaign_status": item.get("state") or "unknown",
                "campaign_status_label": STATES.get(item.get("state"), "Статус неизвестен"),
                "date": raw.get("date"), "payment_type": "cpc", "bid_type": "",
                "is_excluded": None, "is_active": item.get("state") == "CAMPAIGN_STATE_RUNNING"})
    return output

def worker(app, client, start, end, path, key):
    state = {"state": "loading", "progress": "Получение кампаний Ozon", "rows": [], "campaigns": [], "completed_batches": 0}
    save(path, state)
    def progress(message):
        state["progress"] = message
        save(path, state)
        print("ПРОГРЕСС: Ozon рекламные запросы | " + message, flush=True)
    try:
        cid, secret = app.ozon_performance_credentials_value(client)
        if not cid or not secret: raise ValueError("Не настроен доступ к Ozon Performance для этого клиента")
        session = requests.Session()
        def request(method, route, **kwargs):
            response = session.request(method, BASE + route, timeout=35, **kwargs)
            if not response.ok: raise ValueError(f"Ozon ответил HTTP {response.status_code}. Повторите загрузку позже.")
            return response.json()
        auth = request("POST", "/api/client/token", json={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
        session.headers["Authorization"] = "Bearer " + auth["access_token"]
        campaigns = request("GET", "/api/client/campaign").get("list", [])
        eligible = [c for c in campaigns if "PLACEMENT_TOP_PROMOTION" in (c.get("placement") or [])]
        captured = datetime.now(timezone.utc).isoformat()
        state["coverage"] = campaign_coverage(campaigns)
        state["campaigns"] = [{"id": str(c["id"]), "name": c.get("title") or str(c["id"]),
             "campaign_status": c.get("state") or "unknown", "campaign_status_label": STATES.get(c.get("state"), "Статус неизвестен"),
             "campaign_captured_at": captured} for c in eligible]
        index = {str(c["id"]): c for c in eligible}
        batches = [eligible[i:i+10] for i in range(0, len(eligible), 10)]
        progress(f"План: {len(eligible)} кампаний, {len(batches)} пакетов по 10; ожидание отчёта до 180 секунд на пакет")
        for i, batch in enumerate(batches, 1):
            progress(f"{i}/{len(batches)} | запрос отчёта | строк {len(state['rows'])}")
            job = request("POST", "/api/client/statistics/phrases/json", json={
                 "campaigns": [str(c["id"]) for c in batch], "dateFrom": start, "dateTo": end, "groupBy": "DATE"})
            uid = job["UUID"]
            deadline = time.monotonic() + 180
            while True:
                info = request("GET", "/api/client/statistics/" + uid)
                if info.get("state") == "OK": break
                if info.get("state") in ("ERROR", "FAILED"): raise ValueError("Ozon не сформировал отчёт. Повторите загрузку позже.")
                if time.monotonic() >= deadline: raise ValueError("Ozon не завершил отчёт за 180 секунд. Повторите загрузку позже.")
                progress(f"{i}/{len(batches)} | формирование отчёта | ожидание 5 секунд")
                time.sleep(5)
            body = request("GET", "/api/client/statistics/report", params={"UUID": uid})
            state["rows"].extend(parse_rows(body, index))
            state["completed_batches"] = i
            progress(f"{i}/{len(batches)} ({i*100/len(batches):.0f}%) | пакет завершён | строк {len(state['rows'])}")
        state.update(state="ok", captured_at=captured, progress=f"Готово: {len(eligible)} кампаний, {len(state['rows'])} строк", eligible_count=len(eligible))
        save(path, state)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Ошибка связи с Ozon. Повторите загрузку позже."
        state.update(state="partial" if state["completed_batches"] else "error", message=message)
        save(path, state)
    finally:
        with LOCK: RUNNING.discard(key)
        path.with_suffix(".lock").unlink(missing_ok=True)

def payload(app, query):
    from wb_ad_search_queries_dashboard import _first, _int, _unavailable
    client = app.current_client_key()
    end = _first(query, "date_to") or (date.today()-timedelta(days=1)).isoformat()
    start = _first(query, "date_from") or (date.fromisoformat(end)-timedelta(days=29)).isoformat()
    date.fromisoformat(start); date.fromisoformat(end)
    if start > end:
        return _unavailable("Начальная дата не может быть позже конечной.")
    key = hashlib.sha256(f"{client}|{start}|{end}".encode()).hexdigest()
    path = CACHE / (key + ".json")
    with LOCK:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        refresh = _first(query, "refresh") == "1"
        if key not in RUNNING and (state is None or refresh or state.get("state") == "loading"):
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_suffix(".lock")
            if lock_path.exists() and time.time() - lock_path.stat().st_mtime > 900:
                lock_path.unlink()
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
            except FileExistsError:
                if state is None: state = {"state": "loading", "progress": "Загрузка уже выполняется"}
            else:
                RUNNING.add(key)
                state = {"state": "loading", "progress": "Подключение к Ozon Performance"}
                save(path, state)
                threading.Thread(target=worker, args=(app, client, start, end, path, key), daemon=True).start()
    if state["state"] not in ("ok", "partial"):
        result = _unavailable(state.get("message") or state.get("progress") or "Загрузка")
        result.update(data_status=state["state"], progress=state.get("progress"), marketplace="ozon")
        result["period"].update(date_from=start, date_to=end)
        return result
    rows = state["rows"]
    campaigns = state["campaigns"]
    ids = set(_first(query, "campaign_ids").split(",")) - {""}
    statuses = set(_first(query, "campaign_status").split(",")) - {""}
    term = _first(query, "query").lower()
    product = _first(query, "nm_id")
    rows = [dict(r) for r in rows if (not ids or str(r["advert_id"]) in ids)
        and (not statuses or r["campaign_status"] in statuses)
        and (not product or str(r["nm_id"]) == product)
        and (not term or term in r["norm_query"].lower())]
    # Aggregate dates before filtering or paginating. Missing metrics stay missing.
    groups = {}
    cluster = _first(query, "group_by") == "cluster"
    for r in rows:
        k = r["norm_query"] if cluster else (r["norm_query"], r["advert_id"], r["nm_id"])
        if k not in groups:
            groups[k] = dict(r, views=None, clicks=None, _placements=set())
            if cluster: groups[k].update(advert_id=0, nm_id=0, campaign_name="Несколько связок", product_name="По кластеру", campaign_status_label="Несколько связок")
        groups[k]["_placements"].add((r["advert_id"], r["nm_id"]))
        for metric in ("views", "clicks"):
            if r[metric] is not None: groups[k][metric] = (groups[k][metric] or 0) + r[metric]
    aggregated = list(groups.values())
    for r in aggregated:
        r["ctr_pct"] = r["clicks"]*100/r["views"] if r["views"] and r["clicks"] is not None else None
        r["placement_count"] = len(r.pop("_placements"))
    from ad_search_filtering import filter_rows
    aggregated = filter_rows(aggregated, _first(query, "column_filters"))
    sort = _first(query, "sort", "clicks")
    if sort not in ("norm_query", "campaign_name", "product_name", "views", "clicks", "ctr_pct"): sort = "clicks"
    present = [r for r in aggregated if r.get(sort) is not None]
    absent = [r for r in aggregated if r.get(sort) is None]
    present.sort(key=lambda r:r[sort], reverse=_first(query,"sort_dir","desc")!="asc")
    aggregated = present + absent
    def total(metric):
        vals=[r[metric] for r in aggregated if r.get(metric) is not None]
        return sum(vals) if vals else None
    views, clicks = total("views"), total("clicks")
    size = min(300,max(10,_int(_first(query,"page_size"),50)))
    count=len(aggregated); pages=max(1,math.ceil(count/size)); page=min(pages,max(1,_int(_first(query,"page"),1)))
    return {"ok":True,"marketplace":"ozon","data_status":state["state"],"message":state.get("message"),"available":True,
      "period":{"date_from":start,"date_to":end,"available_from":start,"available_to":end},
      "summary":{"views":views,"clicks":clicks,"ctr_pct":clicks*100/views if views and clicks is not None else None,
          "query_count":len({r["norm_query"] for r in aggregated}),"placement_count":sum(r["placement_count"] for r in aggregated)},
      "rows":aggregated[(page-1)*size:page*size],"total":count,"page":page,"page_size":size,"total_pages":pages,
      "daily":[],"filters":{"campaigns":campaigns,"products":[],"statuses":[]},"captured_at":state.get("captured_at"),
      "coverage":state.get("coverage"),"source_row_count":len(state["rows"]),
      "notes":["Ozon: поисковые фразы для кампаний с размещением «Поиск» (PLACEMENT_TOP_PROMOTION).",
       "Источник передаёт трафик. Расход, заказы и CPO по фразе недоступны и не распределяются из кампаний.",
       f"Проверено подходящих кампаний: {state.get('eligible_count',0)}. Пустой отчёт не означает отсутствие рекламы на других размещениях."]}
