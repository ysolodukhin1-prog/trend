#!/usr/bin/env python3
"""Load read-only Avito Ads account metadata and daily advertising statistics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

API_BASE = "https://api.avito.ru"
AVITO_METHOD_STAGES = (
    ("avito_account", "Аккаунт"),
    ("avito_balance", "Баланс"),
    ("avito_campaigns", "Кампании"),
    ("avito_groups", "Группы"),
    ("avito_creatives", "Объявления"),
    ("avito_advertising", "Дневная статистика"),
)


def print_method_stage(key: str, status: str, *, rows: int | None = None, requests: int | None = None) -> None:
    index, label = next(
        ((position, title) for position, (method_key, title) in enumerate(AVITO_METHOD_STAGES, start=1) if method_key == key),
        (0, key),
    )
    parts = [
        f"ПРОГРЕСС: AVITO-МЕТОД {index}/{len(AVITO_METHOD_STAGES)}",
        f"key={key}",
        f"label={label}",
        f"status={status}",
    ]
    if rows is not None:
        parts.append(f"rows={rows}")
    if requests is not None:
        parts.append(f"requests={requests}")
    print(" | ".join(parts), flush=True)


MAX_STATS_DAYS = 100

READ_ONLY_ADS_OPERATIONS = (
    ("GET", re.compile(r"^/ads/v1/account/\d+$")),
    ("GET", re.compile(r"^/ads/v1/account/\d+/balance$")),
    ("POST", re.compile(r"^/ads/v1/account/\d+/(?:campaigns|groups|creatives)$")),
    ("POST", re.compile(r"^/ads/v1/account/\d+/campaigns/\d+/stats$")),
)


def assert_read_only_ads_operation(method: str, path: str) -> None:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    if any(
        normalized_method == allowed_method and pattern.fullmatch(normalized_path)
        for allowed_method, pattern in READ_ONLY_ADS_OPERATIONS
    ):
        return
    raise RuntimeError(
        f"Avito Ads read-only policy blocked {normalized_method} {normalized_path}"
    )
PAGE_LIMIT = 100
MAX_RETRIES = 5


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _kopeks(payload: dict, kopeks_key: str, rubles_key: str) -> int | None:
    value = payload.get(kopeks_key)
    if value is not None:
        return _int(value)
    rubles = payload.get(rubles_key)
    return _int(rubles) * 100 if rubles is not None else None


def _date_from_timestamp(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _datetime_from_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = re.sub(r"\s+UTC$", "", text, flags=re.IGNORECASE)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def iter_windows(date_from: date, date_to: date):
    cursor = date_from
    while cursor <= date_to:
        end = min(date_to, cursor + timedelta(days=MAX_STATS_DAYS - 1))
        yield cursor, end
        cursor = end + timedelta(days=1)


class AvitoAdsClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = ""
        self.requests_made = 0
        self.api_point_balance: int | None = None

    def _open(self, request: Request) -> tuple[dict, object]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with urlopen(request, timeout=60) as response:
                    self.requests_made += 1
                    point_balance = response.headers.get("Api-Point-Balance")
                    if point_balance is not None:
                        self.api_point_balance = _int(point_balance)
                    body = response.read()
                    return (json.loads(body.decode("utf-8")) if body else {}), response.headers
            except HTTPError as exc:
                self.requests_made += 1
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= MAX_RETRIES:
                    raise RuntimeError(f"Avito Ads API HTTP {exc.code}: {detail}") from exc
                retry_after = _int(exc.headers.get("Retry-After"), 0) if exc.headers else 0
                wait_seconds = max(retry_after, min(2 ** (attempt - 1), 30))
                print(
                    f"ОЖИДАНИЕ ЛИМИТА API: HTTP {exc.code} | попытка={attempt}/{MAX_RETRIES} | "
                    f"пауза={wait_seconds}с | токены не выводятся",
                    flush=True,
                )
                time.sleep(wait_seconds)
            except (URLError, TimeoutError) as exc:
                if attempt >= MAX_RETRIES:
                    raise RuntimeError(f"Avito Ads API недоступен: {exc}") from exc
                wait_seconds = min(2 ** (attempt - 1), 30)
                print(
                    f"ОЖИДАНИЕ API: сеть | попытка={attempt}/{MAX_RETRIES} | пауза={wait_seconds}с",
                    flush=True,
                )
                time.sleep(wait_seconds)
        raise RuntimeError("Avito Ads API: исчерпаны попытки")

    def authenticate(self) -> None:
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        payload, _headers = self._open(Request(
            f"{API_BASE}/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        ))
        self.access_token = str(payload.get("access_token") or "")
        if not self.access_token:
            raise RuntimeError("Avito Ads не вернул access token")

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        assert_read_only_ads_operation(method, path)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        result, _response_headers = self._open(Request(
            f"{API_BASE}{path}", data=data, headers=headers, method=method,
        ))
        return result

    def paginated(self, account_id: int, entity: str) -> list[dict]:
        page = 1
        rows: list[dict] = []
        while True:
            payload = self.request(
                "POST",
                f"/ads/v1/account/{account_id}/{entity}",
                {"filter": {}, "limit": PAGE_LIMIT, "page": page},
            )
            batch = payload.get(entity)
            if not isinstance(batch, list):
                batch = payload.get("items") if isinstance(payload.get("items"), list) else []
            rows.extend(item for item in batch if isinstance(item, dict))
            total = _int(payload.get("total"), len(rows))
            print(
                f"ПРОГРЕСС: справочник {entity} | страница={page} | batch={len(batch)} | "
                f"получено={len(rows)}/{total} | requests={self.requests_made}",
                flush=True,
            )
            if not batch or len(batch) < PAGE_LIMIT or len(rows) >= total:
                return rows
            page += 1


def ensure_schema(conn, client_key: str, db_name: str) -> None:
    from scripts.scaffold_client import BOOTSTRAP_SQL

    safe_label = str(client_key).replace("\r", " ").replace("\n", " ")
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP_SQL.format(key=client_key, label=safe_label, db_name=db_name))
    conn.commit()


def save_catalogs(conn, account_id: int, account: dict, balance: dict, campaigns: list[dict], groups: list[dict], creatives: list[dict]) -> None:
    snapshot_date = date.today()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.avito_ads_account_snapshots (account_id, snapshot_date, payload)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id, snapshot_date) DO UPDATE SET payload=EXCLUDED.payload, synced_at=now()
            """,
            (account_id, snapshot_date, Json(account)),
        )
        cur.execute(
            """
            INSERT INTO public.avito_ads_balances_daily
                (account_id, snapshot_date, balance_kopeks, bonus_balance_kopeks, payload)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (account_id, snapshot_date) DO UPDATE SET
                balance_kopeks=EXCLUDED.balance_kopeks,
                bonus_balance_kopeks=EXCLUDED.bonus_balance_kopeks,
                payload=EXCLUDED.payload,
                synced_at=now()
            """,
            (
                account_id,
                snapshot_date,
                _kopeks(balance, "balanceKopeks", "balance"),
                _kopeks(balance, "bonusBalanceKopeks", "bonusBalance"),
                Json(balance),
            ),
        )
        campaign_rows = [(
            account_id, _int(row.get("id") or row.get("campaignID")), row.get("name"), row.get("status"),
            _int(row.get("advertiserId") or row.get("advertiserID")) or None,
            _int(row.get("contractId") or row.get("contractID")) or None,
            row.get("campaignType"), row.get("paymentModel"),
            _kopeks(row, "budgetKopeks", "budget"), _datetime_from_timestamp(row.get("updatedAt")), Json(row),
        ) for row in campaigns if _int(row.get("id") or row.get("campaignID"))]
        if campaign_rows:
            execute_values(cur, """
                INSERT INTO public.avito_ads_campaigns
                    (account_id, campaign_id, name, status, advertiser_id, contract_id,
                     campaign_type, payment_model, budget_kopeks, source_updated_at, payload)
                VALUES %s
                ON CONFLICT (account_id, campaign_id) DO UPDATE SET
                    name=EXCLUDED.name, status=EXCLUDED.status, advertiser_id=EXCLUDED.advertiser_id,
                    contract_id=EXCLUDED.contract_id, campaign_type=EXCLUDED.campaign_type,
                    payment_model=EXCLUDED.payment_model, budget_kopeks=EXCLUDED.budget_kopeks,
                    source_updated_at=EXCLUDED.source_updated_at, payload=EXCLUDED.payload, synced_at=now()
            """, campaign_rows)
        group_rows = [(
            account_id, _int(row.get("id") or row.get("groupID")), _int(row.get("campaignID") or row.get("campaignId")),
            row.get("name"), row.get("status"), _kopeks(row, "budgetKopeks", "budget"),
            _kopeks(row, "priceKopeks", "price"), Json(row),
        ) for row in groups if _int(row.get("id") or row.get("groupID"))]
        if group_rows:
            execute_values(cur, """
                INSERT INTO public.avito_ads_groups
                    (account_id, group_id, campaign_id, name, status, budget_kopeks, price_kopeks, payload)
                VALUES %s
                ON CONFLICT (account_id, group_id) DO UPDATE SET
                    campaign_id=EXCLUDED.campaign_id, name=EXCLUDED.name, status=EXCLUDED.status,
                    budget_kopeks=EXCLUDED.budget_kopeks, price_kopeks=EXCLUDED.price_kopeks,
                    payload=EXCLUDED.payload, synced_at=now()
            """, group_rows)
        creative_rows = [(
            account_id, _int(row.get("id") or row.get("creativeID")),
            _int(row.get("campaignID") or row.get("campaignId")),
            _int(row.get("groupID") or row.get("groupId")), row.get("name"), row.get("status"),
            row.get("previewUrl"), Json(row),
        ) for row in creatives if _int(row.get("id") or row.get("creativeID"))]
        if creative_rows:
            execute_values(cur, """
                INSERT INTO public.avito_ads_creatives
                    (account_id, creative_id, campaign_id, group_id, name, status, preview_url, payload)
                VALUES %s
                ON CONFLICT (account_id, creative_id) DO UPDATE SET
                    campaign_id=EXCLUDED.campaign_id, group_id=EXCLUDED.group_id, name=EXCLUDED.name,
                    status=EXCLUDED.status, preview_url=EXCLUDED.preview_url,
                    payload=EXCLUDED.payload, synced_at=now()
            """, creative_rows)
    conn.commit()


def statistic_rows(account_id: int, campaign_id: int, payload: dict) -> list[tuple]:
    result: list[tuple] = []
    entities: list[tuple[str, dict, int, int]] = []
    campaign = payload.get("campaign")
    if isinstance(campaign, dict):
        entities.append(("campaign", campaign, 0, 0))
    for group in payload.get("groups") or []:
        if isinstance(group, dict):
            entities.append(("group", group, _int(group.get("id") or group.get("groupID")), 0))
    for creative in payload.get("creatives") or []:
        if isinstance(creative, dict):
            entities.append((
                "creative", creative,
                _int(creative.get("groupId") or creative.get("groupID")),
                _int(creative.get("id") or creative.get("creativeID")),
            ))
    for level, entity, group_id, creative_id in entities:
        for stats in entity.get("data") or []:
            if not isinstance(stats, dict):
                continue
            report_date = _date_from_timestamp(stats.get("timestamp"))
            if not report_date:
                continue
            result.append((
                account_id, report_date, level, campaign_id, group_id, creative_id,
                stats.get("views"), stats.get("clicks"), stats.get("ctr"),
                _kopeks(stats, "spendKopeks", "spend"),
                _kopeks(stats, "spendBonusKopeks", "spendBonus"),
                stats.get("cpm"), stats.get("cpc"),
                stats.get("videoViews25"), stats.get("videoViews50"),
                stats.get("videoViews75"), stats.get("videoViews100"),
                stats.get("q25"), stats.get("q50"), stats.get("q75"), stats.get("vtr"),
                Json(stats),
            ))
    return result


def stats_window_completed(conn, account_id: int, campaign_id: int, date_from: date, date_to: date) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM public.avito_ads_stat_windows
               WHERE account_id=%s AND campaign_id=%s AND date_from=%s AND date_to=%s
                 AND status='ok'""",
            (account_id, campaign_id, date_from, date_to),
        )
        return cur.fetchone() is not None


def store_stats_window(
    conn,
    account_id: int,
    campaign_id: int,
    date_from: date,
    date_to: date,
    rows: list[tuple],
    *,
    overwrite: bool,
    run_id: int,
) -> int:
    with conn.cursor() as cur:
        if overwrite:
            cur.execute(
                """DELETE FROM public.avito_ads_stats_daily
                   WHERE account_id=%s AND campaign_id=%s AND report_date BETWEEN %s AND %s""",
                (account_id, campaign_id, date_from, date_to),
            )
        if rows:
            execute_values(cur, """
                INSERT INTO public.avito_ads_stats_daily
                    (account_id, report_date, entity_level, campaign_id, group_id, creative_id,
                     views, clicks, ctr, spend_kopeks, spend_bonus_kopeks, cpm, cpc,
                     video_views_25, video_views_50, video_views_75, video_views_100,
                     q25, q50, q75, vtr, payload)
                VALUES %s
                ON CONFLICT (account_id, report_date, entity_level, campaign_id, group_id, creative_id)
                DO UPDATE SET views=EXCLUDED.views, clicks=EXCLUDED.clicks, ctr=EXCLUDED.ctr,
                    spend_kopeks=EXCLUDED.spend_kopeks, spend_bonus_kopeks=EXCLUDED.spend_bonus_kopeks,
                    cpm=EXCLUDED.cpm, cpc=EXCLUDED.cpc, video_views_25=EXCLUDED.video_views_25,
                    video_views_50=EXCLUDED.video_views_50, video_views_75=EXCLUDED.video_views_75,
                    video_views_100=EXCLUDED.video_views_100, q25=EXCLUDED.q25, q50=EXCLUDED.q50,
                    q75=EXCLUDED.q75, vtr=EXCLUDED.vtr, payload=EXCLUDED.payload, synced_at=now()
            """, rows)
        cur.execute(
            """INSERT INTO public.avito_ads_stat_windows
                   (account_id, campaign_id, date_from, date_to, status, rows_loaded, run_id)
               VALUES (%s, %s, %s, %s, 'ok', %s, %s)
               ON CONFLICT (account_id, campaign_id, date_from, date_to) DO UPDATE SET
                   status='ok', rows_loaded=EXCLUDED.rows_loaded, run_id=EXCLUDED.run_id,
                   updated_at=now()""",
            (account_id, campaign_id, date_from, date_to, len(rows), run_id),
        )
    conn.commit()
    return len(rows)


def resolve_credentials(client_key: str) -> tuple[int, str, str]:
    import app

    suffix = client_key.upper()
    values = {}
    for key, env_key in (
        ("avito_ads_account_id", f"AVITO_ADS_ACCOUNT_ID_{suffix}"),
        ("avito_ads_client_id", f"AVITO_ADS_CLIENT_ID_{suffix}"),
        ("avito_ads_client_secret", f"AVITO_ADS_CLIENT_SECRET_{suffix}"),
    ):
        values[key] = str(app.registered_client_credential(client_key, key) or os.environ.get(env_key) or "").strip()
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError("Не настроены реквизиты Avito Ads: " + ", ".join(missing))
    if not values["avito_ads_account_id"].isdigit():
        raise ValueError("Avito Ads Account ID должен состоять из цифр")
    return int(values["avito_ads_account_id"]), values["avito_ads_client_id"], values["avito_ads_client_secret"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.date_from > args.date_to:
        raise ValueError("Дата начала позже даты окончания")

    import app

    started = time.monotonic()
    account_id, client_id, client_secret = resolve_credentials(args.client_key)
    db_config = dict(app.read_db_config(args.client_key))
    db_config["database"] = args.database_name
    windows = list(iter_windows(args.date_from, args.date_to))
    print(
        f"ПЛАН: Avito Ads | клиент={args.client_key} | account_id={account_id} | "
        f"период={args.date_from}..{args.date_to} | окон={len(windows)} по <=100 дней | "
        f"resume={'yes' if args.resume else 'no'} | overwrite={'yes' if args.overwrite else 'no'} | "
        "сущности=account,balance,campaigns,groups,creatives,stats | API read-only | секреты не выводятся",
        flush=True,
    )

    with psycopg2.connect(**db_config) as conn:
        ensure_schema(conn, args.client_key, args.database_name)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.avito_ads_api_runs (step, date_from, date_to, details)
                   VALUES ('advertising', %s, %s, %s) RETURNING id""",
                (args.date_from, args.date_to, Json({
                    "client_key": args.client_key,
                    "account_id": account_id,
                    "resume": bool(args.resume),
                    "overwrite": bool(args.overwrite),
                })),
            )
            run_id = cur.fetchone()[0]
        conn.commit()

        client = AvitoAdsClient(client_id, client_secret)
        stats_loaded = 0
        errors_count = 0
        active_method_key = ""
        method_rows: dict[str, int] = {}
        try:
            client.authenticate()
            active_method_key = "avito_account"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            account = client.request("GET", f"/ads/v1/account/{account_id}")
            method_rows[active_method_key] = 1
            print_method_stage(active_method_key, "done", rows=1, requests=client.requests_made)
            active_method_key = "avito_balance"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            balance = client.request("GET", f"/ads/v1/account/{account_id}/balance")
            method_rows[active_method_key] = 1
            print_method_stage(active_method_key, "done", rows=1, requests=client.requests_made)
            active_method_key = "avito_campaigns"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            campaigns = client.paginated(account_id, "campaigns")
            method_rows[active_method_key] = len(campaigns)
            print_method_stage(active_method_key, "done", rows=len(campaigns), requests=client.requests_made)
            active_method_key = "avito_groups"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            groups = client.paginated(account_id, "groups")
            method_rows[active_method_key] = len(groups)
            print_method_stage(active_method_key, "done", rows=len(groups), requests=client.requests_made)
            active_method_key = "avito_creatives"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            creatives = client.paginated(account_id, "creatives")
            method_rows[active_method_key] = len(creatives)
            print_method_stage(active_method_key, "done", rows=len(creatives), requests=client.requests_made)
            save_catalogs(conn, account_id, account, balance, campaigns, groups, creatives)

            campaign_ids = [_int(row.get("id") or row.get("campaignID")) for row in campaigns]
            campaign_ids = [value for value in campaign_ids if value]
            total_requests = max(1, len(campaign_ids) * len(windows))
            completed_requests = 0
            skipped_windows = 0
            active_method_key = "avito_advertising"
            print_method_stage(active_method_key, "running", requests=client.requests_made)
            print(
                f"ПЛАН: статистика | campaigns={len(campaign_ids)} | windows={len(windows)} | "
                f"requests={total_requests} | дневная гранулярность",
                flush=True,
            )
            for campaign_index, campaign_id in enumerate(campaign_ids, start=1):
                for window_index, (window_from, window_to) in enumerate(windows, start=1):
                    if args.resume and stats_window_completed(
                        conn, account_id, campaign_id, window_from, window_to,
                    ):
                        completed_requests += 1
                        skipped_windows += 1
                        elapsed = time.monotonic() - started
                        eta = elapsed / completed_requests * (total_requests - completed_requests) if completed_requests else 0
                        print(
                            f"ПРОГРЕСС: {completed_requests}/{total_requests} "
                            f"({completed_requests / total_requests:.1%}) | campaign={campaign_id} "
                            f"({campaign_index}/{len(campaign_ids)}) | window={window_index}/{len(windows)} "
                            f"{window_from}..{window_to} | checkpoint=skip | rows={stats_loaded} | "
                            f"requests={client.requests_made} | elapsed={duration(elapsed)} | ETA={duration(eta)}",
                            flush=True,
                        )
                        continue
                    response = client.request(
                        "POST",
                        f"/ads/v1/account/{account_id}/campaigns/{campaign_id}/stats",
                        {"dateFrom": window_from.isoformat(), "dateTo": window_to.isoformat()},
                    )
                    rows = statistic_rows(account_id, campaign_id, response)
                    stats_loaded += store_stats_window(
                        conn,
                        account_id,
                        campaign_id,
                        window_from,
                        window_to,
                        rows,
                        overwrite=bool(args.overwrite),
                        run_id=run_id,
                    )
                    completed_requests += 1
                    elapsed = time.monotonic() - started
                    eta = elapsed / completed_requests * (total_requests - completed_requests) if completed_requests else 0
                    print(
                        f"ПРОГРЕСС: {completed_requests}/{total_requests} "
                        f"({completed_requests / total_requests:.1%}) | campaign={campaign_id} "
                        f"({campaign_index}/{len(campaign_ids)}) | window={window_index}/{len(windows)} "
                        f"{window_from}..{window_to} | rows={stats_loaded} | requests={client.requests_made} | "
                        f"api_points={client.api_point_balance if client.api_point_balance is not None else '—'} | "
                        f"elapsed={duration(elapsed)} | ETA={duration(eta)}",
                        flush=True,
                    )

            print_method_stage(active_method_key, "done", rows=stats_loaded, requests=client.requests_made)
            method_rows[active_method_key] = stats_loaded

            details = {
                "account_id": account_id,
                "campaigns": len(campaigns),
                "groups": len(groups),
                "creatives": len(creatives),
                "stats_rows": stats_loaded,
                "windows": len(windows),
                "overwrite": bool(args.overwrite),
                "resume": bool(args.resume),
                "skipped_windows": skipped_windows,
            }
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE public.avito_ads_api_runs
                       SET status='ok', requests_made=%s, rows_loaded=%s, api_point_balance=%s,
                           details=%s, finished_at=now() WHERE id=%s""",
                    (client.requests_made, stats_loaded, client.api_point_balance, Json(details), run_id),
                )
            conn.commit()
        except Exception as exc:
            errors_count = 1
            if active_method_key:
                print_method_stage(
                    active_method_key,
                    "failed",
                    rows=method_rows.get(active_method_key, stats_loaded),
                    requests=client.requests_made,
                )
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE public.avito_ads_api_runs
                       SET status='failed', requests_made=%s, rows_loaded=%s, errors_count=1,
                           api_point_balance=%s, details=details || %s::jsonb, finished_at=now()
                       WHERE id=%s""",
                    (client.requests_made, stats_loaded, client.api_point_balance, json.dumps({"error": str(exc)[:500]}), run_id),
                )
            conn.commit()
            raise

    print(
        f"ИТОГ: Avito Ads | campaigns={len(campaigns)} groups={len(groups)} creatives={len(creatives)} | "
        f"stats_rows={stats_loaded} | requests={client.requests_made} | errors={errors_count} | "
        f"skipped_windows={skipped_windows} | database={args.database_name} | "
        f"elapsed={duration(time.monotonic() - started)} | partial=no",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
