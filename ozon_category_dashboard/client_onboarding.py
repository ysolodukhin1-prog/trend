"""Provision KOKOC BI clients and run their API backfills.

The module never receives credentials in command-line arguments and never
prints them. Credentials are read from the encrypted control registry only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLIENTS_ROOT = Path(
    os.environ.get(
        "BI_CLIENTS_ROOT",
        r"G:\Общие диски\Kokoc Marketplaces\Clients",
    )
)
DEFAULT_SCHEMA_TEMPLATE_DB = os.environ.get(
    "BI_CLIENT_SCHEMA_TEMPLATE_DB", "km_trade_products"
)
POSTGRES_BIN = Path(
    os.environ.get("POSTGRES_BIN", r"C:\Program Files\PostgreSQL\18\bin")
)

ProgressCallback = Callable[[str, int, str], None]
AVITO_ADS_CREDENTIALS = (
    "avito_ads_account_id",
    "avito_ads_client_id",
    "avito_ads_client_secret",
)
LAMODA_CREDENTIALS = ("lamoda_client_id", "lamoda_client_secret")
YANDEX_MARKET_CREDENTIALS = ("yandex_market_api_key",)


class HistoryStopped(RuntimeError):
    """Raised when an operator stops a historical backfill."""


def required_credentials(marketplaces: list[str]) -> list[str]:
    required: list[str] = []
    if "wb" in marketplaces:
        required.append("wb_api_token")
    if "ozon" in marketplaces:
        required.extend(("ozon_client_id", "ozon_api_key"))
    if "avito" in marketplaces:
        required.extend(AVITO_ADS_CREDENTIALS)
    if "lamoda" in marketplaces:
        required.extend(LAMODA_CREDENTIALS)
    if "yandex_market" in marketplaces:
        required.extend(YANDEX_MARKET_CREDENTIALS)
    return required


def default_reports(marketplaces: list[str]) -> list[str]:
    connected = set(marketplaces or [])
    reports = [] if connected == {"avito"} else ["commercialRadar"]
    if connected.intersection({"ozon", "wb"}) or "avito" not in connected:
        reports[0:0] = [
            "abc", "product", "sku", "funnel", "weeklyDynamics",
            "inventoryHistory", "planfact", "profitLoss", "unitEconomics",
        ]
    if "ozon" in marketplaces:
        reports.extend(("adv", "mediaAdv", "seoMonitoring"))
    if "wb" in marketplaces:
        reports.extend(("adv", "mediaAdv", "wbSearchQueries", "wbEntrance"))
    if "avito" in marketplaces:
        reports.extend(("avitoOverview", "avitoCampaigns", "avitoGroups", "avitoCreatives", "avitoDaily"))
    return list(dict.fromkeys(reports))


def _safe_http_json(request: Request, *, timeout: int = 30) -> tuple[int, bytes]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"API отклонил ключ: HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Не удалось проверить API: {exc}") from exc


def discover_yandex_market_accounts(api_key: str) -> list[dict]:
    """Return every Yandex Market business and store available to an API key."""
    clean_key = str(api_key or "").strip()
    if not clean_key:
        raise ValueError("Введите API-ключ Яндекс Маркета")
    try:
        clean_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Вставьте API-ключ Яндекс Маркета без дополнительного текста") from exc
    campaigns = []
    page_token = ""
    seen_tokens = set()
    for _page in range(1000):
        params = {"limit": 100}
        if page_token:
            params["pageToken"] = page_token
        request = Request(
            "https://api.partner.market.yandex.ru/v2/campaigns?" + urlencode(params),
            headers={"Api-Key": clean_key, "Accept": "application/json"},
            method="GET",
        )
        _status, body = _safe_http_json(request)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Яндекс Маркет вернул некорректный ответ") from exc
        page = payload.get("campaigns") if isinstance(payload, dict) else None
        if not isinstance(page, list):
            raise RuntimeError("Яндекс Маркет не вернул список магазинов")
        campaigns.extend(page)
        paging = payload.get("paging") or {}
        page_token = str(paging.get("nextPageToken") or "")
        if not page_token:
            break
        if page_token in seen_tokens:
            raise RuntimeError("Яндекс Маркет повторил страницу магазинов. Повторите проверку ключа")
        seen_tokens.add(page_token)
    else:
        raise RuntimeError("Не удалось получить полный список магазинов Яндекс Маркета")

    grouped: dict[str, dict] = {}
    seen_stores = set()
    for campaign in campaigns:
        if not isinstance(campaign, dict):
            continue
        business = campaign.get("business") if isinstance(campaign.get("business"), dict) else {}
        business_id = str(business.get("id") or campaign.get("businessId") or "").strip()
        campaign_id = str(campaign.get("id") or campaign.get("campaignId") or "").strip()
        if not business_id or not campaign_id:
            raise RuntimeError("Яндекс Маркет вернул магазин без идентификатора бизнеса или магазина")
        identity = (business_id, campaign_id)
        if identity in seen_stores:
            continue
        seen_stores.add(identity)
        account = grouped.setdefault(
            business_id,
            {
                "business_id": business_id,
                "name": str(business.get("name") or f"Бизнес {business_id}").strip(),
                "stores": [],
            },
        )
        availability = str(campaign.get("apiAvailability") or "AVAILABLE").upper()
        account["stores"].append({
            "campaign_id": campaign_id,
            "name": str(campaign.get("domain") or campaign.get("name") or f"Магазин {campaign_id}").strip(),
            "domain": str(campaign.get("domain") or "").strip(),
            "placement_type": str(campaign.get("placementType") or campaign.get("placement_type") or "").strip(),
            "status": availability.lower(),
            "is_accessible": availability == "AVAILABLE",
            "import_enabled": availability == "AVAILABLE",
        })
    accounts = list(grouped.values())
    for account in accounts:
        account["stores"].sort(key=lambda item: (item["name"].lower(), item["campaign_id"]))
    accounts.sort(key=lambda item: (item["name"].lower(), item["business_id"]))
    if not accounts:
        raise RuntimeError("По этому API-ключу не найдено доступных магазинов")
    return accounts


def validate_marketplace_credentials(
    marketplaces: list[str], credentials: dict[str, str]
) -> None:
    missing = [key for key in required_credentials(marketplaces) if not credentials.get(key)]
    if missing:
        raise ValueError("Не заполнены обязательные ключи: " + ", ".join(missing))
    if "wb" in marketplaces:
        request = Request(
            "https://common-api.wildberries.ru/ping",
            headers={"Authorization": credentials["wb_api_token"], "Accept": "application/json"},
            method="GET",
        )
        _safe_http_json(request)
    if "ozon" in marketplaces:
        payload = json.dumps({"filter": {}, "last_id": "", "limit": 1}).encode("utf-8")
        request = Request(
            "https://api-seller.ozon.ru/v3/product/list",
            data=payload,
            headers={
                "Client-Id": credentials["ozon_client_id"],
                "Api-Key": credentials["ozon_api_key"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        _safe_http_json(request)
    if "yandex_market" in marketplaces:
        discover_yandex_market_accounts(credentials["yandex_market_api_key"])
    avito_values = {key: str(credentials.get(key) or "").strip() for key in AVITO_ADS_CREDENTIALS}
    if any(avito_values.values()):
        missing_avito = [key for key, value in avito_values.items() if not value]
        if missing_avito:
            raise ValueError("Для Avito Ads заполните Account ID, Client ID и Client Secret")
        if not avito_values["avito_ads_account_id"].isdigit():
            raise ValueError("Avito Ads Account ID должен состоять из цифр")
        token_payload = urlencode({
            "grant_type": "client_credentials",
            "client_id": avito_values["avito_ads_client_id"],
            "client_secret": avito_values["avito_ads_client_secret"],
        }).encode("utf-8")
        token_request = Request(
            "https://api.avito.ru/token",
            data=token_payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        _status, token_body = _safe_http_json(token_request)
        try:
            access_token = str(json.loads(token_body.decode("utf-8")).get("access_token") or "")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Avito Ads вернул некорректный ответ авторизации") from exc
        if not access_token:
            raise RuntimeError("Avito Ads не вернул access token")
        account_request = Request(
            f"https://api.avito.ru/ads/v1/account/{avito_values['avito_ads_account_id']}",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            method="GET",
        )
        _safe_http_json(account_request)


def ensure_database(db_config: dict, db_name: str) -> bool:
    admin_config = dict(db_config)
    admin_config["database"] = os.environ.get("PGMAINTENANCE_DB", "postgres")
    admin_config.pop("options", None)
    conn = psycopg2.connect(**admin_config)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                return False
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(db_name)))
            return True
    finally:
        conn.close()


def database_has_user_tables(db_config: dict, db_name: str) -> bool:
    config = dict(db_config)
    config["database"] = db_name
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE')"
        )
        return bool(cur.fetchone()[0])


def _postgres_tool(name: str) -> Path:
    candidate = POSTGRES_BIN / f"{name}.exe"
    if candidate.exists():
        return candidate
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(f"Не найден PostgreSQL tool: {name}")


def clone_schema(db_config: dict, target_db: str, template_db: str) -> None:
    if database_has_user_tables(db_config, target_db):
        return
    pg_dump = _postgres_tool("pg_dump")
    psql = _postgres_tool("psql")
    temp_fd, temp_name = tempfile.mkstemp(prefix="kokoc_bi_schema_", suffix=".sql")
    os.close(temp_fd)
    temp_path = Path(temp_name)
    env = os.environ.copy()
    if db_config.get("password"):
        env["PGPASSWORD"] = str(db_config["password"])
    common = [
        "--host", str(db_config.get("host") or "localhost"),
        "--port", str(db_config.get("port") or 5432),
        "--username", str(db_config.get("user") or "postgres"),
    ]
    try:
        dumped = subprocess.run(
            [str(pg_dump), *common, "--schema-only", "--no-owner", "--no-privileges",
             "--file", str(temp_path), "--dbname", template_db],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60 * 20, check=False,
        )
        if dumped.returncode:
            raise RuntimeError(f"Не удалось подготовить schema-only шаблон: {dumped.stderr[-1000:]}")
        restored = subprocess.run(
            [str(psql), *common, "--single-transaction", "--set", "ON_ERROR_STOP=1", "--dbname", target_db,
             "--file", str(temp_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60 * 30, check=False,
        )
        if restored.returncode:
            raise RuntimeError(f"Не удалось применить схему BI: {restored.stderr[-1200:]}")
    finally:
        temp_path.unlink(missing_ok=True)


def apply_bootstrap(db_config: dict, db_name: str, bootstrap_sql: str) -> None:
    config = dict(db_config)
    config["database"] = db_name
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(bootstrap_sql)
        conn.commit()


def refresh_materialized_views(db_config: dict, db_name: str) -> int:
    config = dict(db_config)
    config["database"] = db_name
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SELECT schemaname, matviewname FROM pg_matviews ORDER BY schemaname, matviewname")
        pending = [(row[0], row[1]) for row in cur.fetchall()]
    total = len(pending)
    print(f"ПЛАН: materialized views {total} | БД={db_name} | dependency-aware refresh", flush=True)
    refreshed = 0
    last_errors: dict[tuple[str, str], str] = {}
    while pending:
        progressed = False
        for item in list(pending):
            schema_name, view_name = item
            try:
                with psycopg2.connect(**config) as refresh_conn, refresh_conn.cursor() as refresh_cur:
                    refresh_cur.execute(
                        sql.SQL("REFRESH MATERIALIZED VIEW {}.{}").format(
                            sql.Identifier(schema_name), sql.Identifier(view_name)
                        )
                    )
            except psycopg2.Error as exc:
                last_errors[item] = str(exc).splitlines()[0][:300]
                continue
            pending.remove(item)
            last_errors.pop(item, None)
            refreshed += 1
            progressed = True
            print(
                f"ПРОГРЕСС: {refreshed}/{total} ({refreshed / max(total, 1):.0%}) | "
                f"{schema_name}.{view_name} | pending={len(pending)} errors=0",
                flush=True,
            )
        if not progressed:
            details = "; ".join(
                f"{schema}.{view}: {last_errors.get((schema, view), 'unknown')}"
                for schema, view in pending[:5]
            )
            raise RuntimeError(
                f"Не удалось инициализировать {len(pending)} materialized views: {details}"
            )
    print(f"ИТОГ: refreshed={refreshed} errors=0 database={db_name}", flush=True)
    return refreshed

def provision_client(
    client: dict,
    credentials: dict[str, str],
    db_config: dict,
    progress: ProgressCallback,
    *,
    validate_api: bool = True,
) -> dict:
    try:
        from scripts.scaffold_client import BOOTSTRAP_SQL, apply_plan, build_plan
    except ImportError:
        from ozon_category_dashboard.scripts.scaffold_client import BOOTSTRAP_SQL, apply_plan, build_plan

    started = time.monotonic()
    marketplaces = list(client["marketplaces"])
    stages = [
        ("credentials", 8, "Проверяю доступ к API"),
        ("folders", 22, "Создаю структуру папок клиента"),
        ("database", 38, "Создаю отдельную базу PostgreSQL"),
        ("schema", 62, "Разворачиваю таблицы и materialized views BI"),
        ("pipelines", 82, "Создаю API-pipelines и ежедневный процесс"),
        ("ready", 100, "Клиент готов к исторической загрузке"),
    ]
    progress(*stages[0])
    if validate_api:
        validate_marketplace_credentials(marketplaces, credentials)
    progress(*stages[1])
    root_path = Path(client["root_path"])
    plan = build_plan(
        client["key"], client["label"], client["db_name"], root_path.parent,
        marketplaces=marketplaces,
    )
    plan_result = apply_plan(plan, apply=True)
    if plan_result["errors"]:
        raise RuntimeError(f"Не удалось создать {plan_result['errors']} объектов файловой структуры")
    progress(*stages[2])
    database_created = ensure_database(db_config, client["db_name"])
    progress(*stages[3])
    clone_schema(db_config, client["db_name"], DEFAULT_SCHEMA_TEMPLATE_DB)
    apply_bootstrap(
        db_config,
        client["db_name"],
        BOOTSTRAP_SQL.format(key=client["key"], label=client["label"], db_name=client["db_name"]),
    )
    refresh_materialized_views(db_config, client["db_name"])
    progress(*stages[4])
    manifest_path = PROJECT_ROOT / "scripts" / "clients" / client["key"] / "client_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("Не создан manifest клиентского pipeline")
    progress(*stages[5])
    return {
        "ok": True,
        "database_created": database_created,
        "files_created": plan_result["created"],
        "files_existing": plan_result["skipped"],
        "manifest": str(manifest_path),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def validate_history_range(date_from: str, date_to: str) -> tuple[date, date]:
    start = date.fromisoformat(str(date_from))
    end = date.fromisoformat(str(date_to))
    if start > end:
        raise ValueError("Дата начала позже даты окончания")
    if end >= date.today():
        raise ValueError("Историческую загрузку можно выполнять только по вчерашний день включительно")
    return start, end


def calendar_months_ago(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def validate_wb_stock_history_range(date_from: str, date_to: str) -> tuple[date, date]:
    start, end = validate_history_range(date_from, date_to)
    earliest = calendar_months_ago(date.today(), 3)
    if start < earliest:
        raise ValueError(
            "История остатков WB доступна максимум за 3 месяца: "
            f"дата начала должна быть не раньше {earliest.isoformat()}"
        )
    return start, end


def pipeline_command(
    client: dict,
    date_from: str,
    date_to: str,
    mode: str,
    selected_steps: list[str] | None = None,
    overwrite: bool = False,
    marketplaces: list[str] | None = None,
    wb_stock_history_date_from: str | None = None,
    wb_stock_history_date_to: str | None = None,
    include_inactive_campaigns: bool = False,
    resume: bool = False,
) -> list[str]:
    selected_marketplaces = marketplaces if marketplaces is not None else client["marketplaces"]
    command = [
        os.environ.get("PYTHON_EXECUTABLE", os.sys.executable), "-X", "utf8", "-u",
        str(PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "run_client_pipeline.py"),
        "--client-key", client["key"],
        "--database-name", client["db_name"],
        "--marketplaces", ",".join(selected_marketplaces),
        "--mode", mode,
        "--date-from", date_from,
        "--date-to", date_to,
    ]
    if selected_steps:
        command.extend(("--steps", ",".join(selected_steps)))
    if overwrite:
        command.append("--overwrite")
    if resume:
        command.append("--resume")
    if wb_stock_history_date_from and wb_stock_history_date_to:
        command.extend((
            "--wb-stock-history-date-from", wb_stock_history_date_from,
            "--wb-stock-history-date-to", wb_stock_history_date_to,
        ))
    if include_inactive_campaigns:
        command.append("--include-inactive-campaigns")
    return command


def history_pipeline_progress(
    line: str,
    current: int,
    step_index: int,
    step_total: int,
) -> tuple[int, int, int]:
    stage = re.search(
        r"^ПРОГРЕСС:\s+(\d+)/(\d+)\s+\((\d{1,3}(?:\.\d+)?)%\).*\bcompleted=",
        line,
    )
    if stage:
        step_index = max(1, int(stage.group(1)))
        step_total = max(step_index, int(stage.group(2)))
        pipeline_pct = max(0.0, min(100.0, float(stage.group(3))))
        current = max(current, 5 + round(pipeline_pct * 0.9))
    live = re.search(r"\bstep_pct=(\d{1,3}(?:\.\d+)?)\b", line)
    if live and step_total:
        step_pct = max(0.0, min(100.0, float(live.group(1))))
        pipeline_pct = ((step_index - 1) + step_pct / 100) / step_total * 100
        current = max(current, 5 + round(pipeline_pct * 0.9))
    return min(95, current), step_index, step_total


def run_history(
    client: dict,
    date_from: str,
    date_to: str,
    progress: ProgressCallback,
    *,
    process_hook: Callable[[subprocess.Popen], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    selected_steps: list[str] | None = None,
    overwrite: bool = False,
    marketplaces: list[str] | None = None,
    wb_stock_history_date_from: str | None = None,
    wb_stock_history_date_to: str | None = None,
    include_inactive_campaigns: bool = False,
    resume: bool = False,
) -> dict:
    validate_history_range(date_from, date_to)
    if wb_stock_history_date_from or wb_stock_history_date_to:
        if not (wb_stock_history_date_from and wb_stock_history_date_to):
            raise ValueError("Для истории остатков WB укажите обе даты")
        validate_wb_stock_history_range(wb_stock_history_date_from, wb_stock_history_date_to)
    progress("history", 3, f"Историческая загрузка {date_from} — {date_to}")
    command = pipeline_command(
        client,
        date_from,
        date_to,
        "history",
        selected_steps,
        overwrite,
        marketplaces,
        wb_stock_history_date_from,
        wb_stock_history_date_to,
        include_inactive_campaigns,
        resume,
    )
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "DASHBOARD_CLIENT": client["key"],
            "DASHBOARD_CLIENT_LABEL": client["label"],
            "DASHBOARD_DB_NAME": client["db_name"],
        },
    )
    if process_hook:
        process_hook(process)
    tail: list[str] = []
    pipeline_progress = 5
    pipeline_step_index = 1
    pipeline_step_total = 1
    assert process.stdout is not None
    for raw_line in process.stdout:
        if should_stop and should_stop():
            raise HistoryStopped("Историческая загрузка остановлена пользователем")
        line = raw_line.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-80:]
        if "ПРОГРЕСС:" in line:
            pipeline_progress, pipeline_step_index, pipeline_step_total = history_pipeline_progress(
                line,
                pipeline_progress,
                pipeline_step_index,
                pipeline_step_total,
            )
        progress("history", pipeline_progress, line[-500:])
    return_code = process.wait()
    if should_stop and should_stop():
        raise HistoryStopped("Историческая загрузка остановлена пользователем")
    if return_code:
        raise RuntimeError(("\n".join(tail) or "Исторический pipeline завершился с ошибкой")[-1600:])
    partial = any(line.startswith("ИТОГ:") and "partial=yes" in line for line in tail)
    success_message = "Данные Яндекс Маркета загружены" if marketplaces == ["yandex_market"] else "Выбранные разделы и витрины обновлены"
    progress("history_ready", 100, "Загрузка завершена с ограничениями источника" if partial else success_message)
    return {
        "ok": True,
        "partial": partial,
        "date_from": date_from,
        "date_to": date_to,
        "tail": "\n".join(tail)[-2000:],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
