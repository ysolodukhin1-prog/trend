#!/usr/bin/env python3
"""Run a registered client's read-only marketplace API pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from api_completeness import marketplace_today


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def calendar_months_ago(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def is_historical_backfill(date_to: date, *, today: date | None = None) -> bool:
    """Return true when a range ends before the current daily refresh window."""

    current_day = today or marketplace_today()
    return date_to < current_day - timedelta(days=1)


def configure_runtime_scope():
    import app

    # CLI workers do not pass through pulse_vps_admin.main(). Configure the
    # same scoped DB credentials and registry before touching client secrets.
    if os.environ.get("PULSE_DB_PASSWORD_FILE"):
        import pulse_vps_admin
        pulse_vps_admin.configure_scope()
    return app


def credentials_to_env(client_key: str) -> dict[str, str]:
    app = configure_runtime_scope()

    # Registered clients are loaded by the web server at startup, but this
    # command runs independently.  Hydrating here makes the environment-name
    # resolution agree with the child synchronizers and prevents a fallback to
    # Gloria's profile for newly registered accounts.
    hydrate = getattr(app, "hydrate_registered_clients", None)
    if callable(hydrate):
        hydrate()
    env_values = app.read_app_env_file()
    registered = {
        key: app.registered_client_credential(client_key, key)
        for key in (
            "wb_api_token", "wb_service_api_token",
            "ozon_client_id",
            "ozon_api_key",
            "ozon_performance_client_id",
            "ozon_performance_client_secret",
            "avito_ads_account_id",
            "avito_ads_client_id",
            "avito_ads_client_secret",
        )
    }
    # A fresh child process initially knows only static ADMIN_CLIENTS. Calling
    # normalized wrappers for a registered client would silently fall back to
    # gloria_jeans and resolve credentials for the wrong tenant.
    if client_key in app.ADMIN_CLIENTS:
        fallback_client_id, fallback_api_key = app.ozon_seo_credentials_value(client_key)
        fallback_performance_id, fallback_performance_secret = app.ozon_performance_credentials_value(client_key)
        wb_token_name = app.wb_api_token_env(client_key)
        fallback_wb_token = os.environ.get(wb_token_name) or env_values.get(wb_token_name)
    else:
        fallback_client_id = fallback_api_key = ""
        fallback_performance_id = fallback_performance_secret = ""
        fallback_wb_token = ""
        wb_token_name = f"WB_API_TOKEN_{client_key.upper()}"

    ozon_client_id = registered["ozon_client_id"] or fallback_client_id
    ozon_api_key = registered["ozon_api_key"] or fallback_api_key
    performance_client_id = registered["ozon_performance_client_id"] or fallback_performance_id
    performance_client_secret = registered["ozon_performance_client_secret"] or fallback_performance_secret
    wb_token = registered["wb_api_token"] or fallback_wb_token
    wb_service_token_name = f"WB_SERVICE_API_TOKEN_{client_key.upper()}"
    wb_service_token = registered["wb_service_api_token"] or os.environ.get(wb_service_token_name) or env_values.get(wb_service_token_name, "")
    avito_account_id = registered["avito_ads_account_id"]
    avito_client_id = registered["avito_ads_client_id"]
    avito_client_secret = registered["avito_ads_client_secret"]
    values = {
        "WB_API_TOKEN_ENV": wb_token_name,
        wb_token_name: wb_token,
        "WB_SERVICE_API_TOKEN_ENV": wb_service_token_name,
        wb_service_token_name: wb_service_token,
        f"OZON_SELLER_CLIENT_ID_{client_key.upper()}": ozon_client_id,
        f"OZON_SELLER_API_KEY_{client_key.upper()}": ozon_api_key,
        f"OZON_PERFORMANCE_CLIENT_ID_{client_key.upper()}": performance_client_id,
        f"OZON_PERFORMANCE_CLIENT_SECRET_{client_key.upper()}": performance_client_secret,
        f"AVITO_ADS_ACCOUNT_ID_{client_key.upper()}": avito_account_id,
        f"AVITO_ADS_CLIENT_ID_{client_key.upper()}": avito_client_id,
        f"AVITO_ADS_CLIENT_SECRET_{client_key.upper()}": avito_client_secret,
    }
    return {key: value for key, value in values.items() if value}


OZON_PERFORMANCE_ACCESS_PATTERNS = (
    "у организации нет доступа к ozon performance api",
    "ozon api http 403 for get https://api-performance.ozon.ru/api/client/campaign",
)
OZON_REVIEWS_ACCESS_PATTERNS = (
    "not available with existing subscription",
    "permissiondenied",
)


def _is_ozon_performance_access_limit(label: str, command: list[str], output: str) -> bool:
    normalized_command = [str(value).casefold() for value in command]
    is_advertising_step = "ozon реклама" in label.casefold()
    if "--step" in normalized_command:
        step_index = normalized_command.index("--step")
        is_advertising_step = is_advertising_step or (
            step_index + 1 < len(normalized_command)
            and normalized_command[step_index + 1] == "advertising"
        )
    normalized_output = output.casefold()
    return is_advertising_step and any(
        pattern in normalized_output for pattern in OZON_PERFORMANCE_ACCESS_PATTERNS
    )


def _is_ozon_reviews_access_limit(label: str, command: list[str], output: str) -> bool:
    normalized_command = [str(value).casefold() for value in command]
    is_review_step = "ozon отзывы" in label.casefold()
    is_review_step = is_review_step or "sync_km_marketplace_reviews.py" in normalized_command
    normalized_output = output.casefold()
    return is_review_step and any(
        pattern in normalized_output for pattern in OZON_REVIEWS_ACCESS_PATTERNS
    )


def run_step(label: str, command: list[str], env: dict[str, str]) -> str:
    print(f"ПРОГРЕСС: запуск | {label} | command_args={len(command)} | secrets_logged=0", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_tail: list[str] = []
    if process.stdout:
        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.rstrip("\r\n")
            print(line, flush=True)
            output_tail.append(line)
            if len(output_tail) > 80:
                output_tail = output_tail[-80:]
        process.stdout.close()
    returncode = process.wait()
    output = "\n".join(output_tail)
    if returncode == 3 and any(str(value).endswith("sync_yandex_market.py") for value in command):
        print("SOURCE_LIMITED: Яндекс | отдельные магазины или разделы недоступны | очередь продолжена", flush=True)
        return "limited"
    if returncode == 3 and any(str(value).endswith("sync_lamoda.py") for value in command):
        print("SOURCE_LIMITED: Lamoda | финансовый источник требует отдельного экспорта Seller/LAB | очередь продолжена", flush=True)
        return "limited"
    if returncode and _is_ozon_performance_access_limit(label, command, output):
        print(
            "ОГРАНИЧЕНИЕ: Ozon Performance API недоступен для организации | "
            "SOURCE_LIMITED | очередь продолжена",
            flush=True,
        )
        return "limited"
    if returncode and _is_ozon_reviews_access_limit(label, command, output):
        print(
            "ОГРАНИЧЕНИЕ: Ozon отзывы недоступны по текущей подписке | "
            "SOURCE_LIMITED | очередь продолжена",
            flush=True,
        )
        return "limited"
    if returncode:
        raise RuntimeError(f"{label}: завершено с кодом {returncode}")
    return "ok"


def detect_wb_jam(env: dict[str, str]) -> dict[str, str]:
    """Return the verified WB Jam state without ever exposing the API token."""
    service_token_name = str(env.get("WB_SERVICE_API_TOKEN_ENV") or "").strip()
    token = str(env.get(service_token_name) or "").strip() if service_token_name else ""
    if not token:
        return {
            "status": "unknown",
            "reason": "подписка будет проверена фактическим вызовом поисковой аналитики",
        }
    request = Request(
        "https://common-api.wildberries.ru/api/common/v1/subscriptions",
        headers={"Authorization": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace").strip()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 403 and "personal token is not allowed" in detail.lower():
            return {
                "status": "unknown",
                "reason": "для проверки подписки WB требуется отдельный service-токен",
            }
        return {"status": "unknown", "reason": f"WB HTTP {exc.code}"}
    except (TimeoutError, URLError) as exc:
        return {"status": "unknown", "reason": f"сетевая ошибка {type(exc).__name__}"}

    try:
        import json
        payload = json.loads(raw) if raw else {}
    except ValueError:
        return {"status": "unknown", "reason": "некорректный ответ WB"}
    if not isinstance(payload, dict):
        return {"status": "unknown", "reason": "некорректный формат ответа WB"}
    if str(payload.get("state") or "").strip().lower() == "active":
        return {
            "status": "active",
            "level": str(payload.get("level") or "не указан"),
            "till": str(payload.get("till") or "не указана"),
        }
    return {"status": "inactive", "reason": "активная подписка Jam не найдена"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--marketplaces", required=True)
    parser.add_argument("--mode", choices=("daily", "history", "views"), default="daily")
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument("--wb-stock-history-date-from", type=date.fromisoformat)
    parser.add_argument("--wb-stock-history-date-to", type=date.fromisoformat)
    parser.add_argument(
        "--steps", default="",
        help="Необязательный список ключей шагов через запятую; пусто означает все доступные шаги",
    )
    parser.add_argument(
        "--defer-views",
        action="store_true",
        help="Не пересобирать зависимые витрины внутри отдельного шага; они будут запущены своей строкой плана",
    )
    parser.add_argument(
        "--force-assortment-replace",
        action="store_true",
        help="Явно разрешить полную перезапись Ozon ассортимента и характеристик",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Заново скачать и заменить API-данные выбранного периода",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Продолжить историческую загрузку с сохранённых checkpoint-окон",
    )
    parser.add_argument(
        "--include-inactive-campaigns",
        action="store_true",
        help="Для ручной истории рекламы включить завершённые и приостановленные WB-кампании",
    )
    args = parser.parse_args()
    if args.date_from > args.date_to:
        raise ValueError("Дата начала позже даты окончания")
    if bool(args.wb_stock_history_date_from) != bool(args.wb_stock_history_date_to):
        raise ValueError("Для истории остатков WB укажите обе даты")
    if args.wb_stock_history_date_from and args.wb_stock_history_date_to:
        if args.wb_stock_history_date_from > args.wb_stock_history_date_to:
            raise ValueError("В периоде истории остатков WB дата начала позже даты окончания")
        earliest_stock_date = calendar_months_ago(marketplace_today(), 3)
        if args.wb_stock_history_date_from < earliest_stock_date:
            raise ValueError(
                "История остатков WB доступна максимум за 3 месяца: "
                f"дата начала должна быть не раньше {earliest_stock_date.isoformat()}"
            )
    marketplaces = [value.strip().lower() for value in args.marketplaces.split(",") if value.strip()]
    if not marketplaces or set(marketplaces) - {"ozon", "wb", "avito", "lamoda", "yandex_market"}:
        raise ValueError("Поддерживаются ozon, wb, avito и yandex_market")

    started = time.monotonic()
    credential_env = credentials_to_env(args.client_key)
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "DASHBOARD_CLIENT": args.client_key,
        "DASHBOARD_DB_NAME": args.database_name,
        "KM_DB_NAME": args.database_name,
    }
    env.update(credential_env)
    steps: list[tuple[str, str, list[str]]] = []
    if args.mode != "views" and "ozon" in marketplaces:
        ozon_script = str(PROJECT_ROOT / "scripts" / "sync_km_ozon_api.py")
        review_script = str(PROJECT_ROOT / "scripts" / "sync_km_marketplace_reviews.py")
        common = [
            "--date-from", args.date_from.isoformat(),
            "--date-to", args.date_to.isoformat(),
        ]
        assortment_command = [
            sys.executable, "-X", "utf8", "-u",
            str(PROJECT_ROOT / "scripts" / "sync_ozon_assortment.py"),
        ]
        if args.force_assortment_replace or args.overwrite:
            assortment_command.append("--force-replace")
        steps.append(("ozon_assortment", "Ozon ассортимент", assortment_command))
        steps.append(("ozon_funnel", "Ozon воронка", [
            sys.executable, "-X", "utf8", "-u", ozon_script, "--step", "funnel", *common,
            *(["--overwrite"] if args.overwrite else []),
        ]))
        steps.append(("ozon_stock", "Ozon остатки", [
            sys.executable, "-X", "utf8", "-u", ozon_script, "--step", "stock", *common,
        ]))
        if env.get(f"OZON_SELLER_CLIENT_ID_{args.client_key.upper()}") and env.get(
            f"OZON_SELLER_API_KEY_{args.client_key.upper()}"
        ):
            steps.append(("ozon_feedbacks", "Ozon отзывы", [
                sys.executable, "-X", "utf8", "-u", review_script,
                "--client-key", args.client_key,
                "--database-name", args.database_name,
                "--marketplace", "ozon",
            ]))
        else:
            print("ПРОГРЕСС: Ozon отзывы | пропуск: Seller API не подключён", flush=True)
        if env.get(f"OZON_PERFORMANCE_CLIENT_ID_{args.client_key.upper()}") and env.get(
            f"OZON_PERFORMANCE_CLIENT_SECRET_{args.client_key.upper()}"
        ):
            steps.append(("ozon_advertising", "Ozon реклама", [
                sys.executable, "-X", "utf8", "-u", ozon_script, "--step", "advertising", *common,
                *(["--overwrite"] if args.overwrite else []),
            ]))
        else:
            print("ПРОГРЕСС: Ozon реклама | пропуск: Performance API не подключён", flush=True)
        steps.append(("ozon_finance", "Ozon финансы", [
            sys.executable, "-X", "utf8", "-u",
            str(PROJECT_ROOT / "scripts" / "sync_km_ozon_finance.py"),
            "--source", "api", *common,
            *(
                ["--skip-catalog-refresh"]
                if args.mode == "history" or is_historical_backfill(args.date_to)
                else []
            ),
        ]))
        steps.append(("ozon_views", "Ozon BI views", [
            sys.executable, "-X", "utf8", "-u", ozon_script, "--step", "views", *common,
        ]))
    if args.mode != "views" and "wb" in marketplaces:
        wb_extended_script = str(PROJECT_ROOT / "scripts" / "sync_km_wb_extended_api.py")
        wb_script = str(PROJECT_ROOT / "scripts" / "sync_km_wb_api.py")
        wb_period = ["--date-from", args.date_from.isoformat(), "--date-to", args.date_to.isoformat()]
        wb_stock_history_period = [
            "--date-from", (args.wb_stock_history_date_from or args.date_from).isoformat(),
            "--date-to", (args.wb_stock_history_date_to or args.date_to).isoformat(),
        ]
        wb_jam = detect_wb_jam(env)
        env["WB_JAM_STATUS"] = wb_jam["status"]
        if wb_jam["status"] == "active":
            print(
                f"ПРОГРЕСС: WB preflight | Jam активен | уровень={wb_jam['level']} | до={wb_jam['till']} | сценарий=расширенный",
                flush=True,
            )
        elif wb_jam["status"] == "inactive":
            print("ПРОГРЕСС: WB preflight | Jam неактивен | сценарий=базовый без поисковой аналитики", flush=True)
        else:
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: WB preflight | статус Jam не определён: {wb_jam['reason']} | "
                "поисковая аналитика будет проверена отдельно",
                flush=True,
            )
        wb_extended_steps = [
            ("wb_catalog", "WB ассортимент и характеристики", "content"),
            ("wb_orders_sales", "WB заказы и продажи", "statistics"),
        ]
        for key, label, script_step in wb_extended_steps:
            steps.append((key, label, [
                sys.executable, "-X", "utf8", "-u", wb_extended_script,
                "--step", script_step, *wb_period,
                *(["--overwrite"] if args.overwrite else []),
            ]))
        steps.append(("wb_stock_current", "WB текущие остатки", [
            sys.executable, "-X", "utf8", "-u", wb_script,
            "--step", "stock", *wb_period,
        ]))
        steps.append(("wb_stock_history", "WB история остатков", [
            sys.executable, "-X", "utf8", "-u", wb_extended_script,
            "--step", "stock_history", *wb_stock_history_period,
            *(["--overwrite"] if args.overwrite else []),
        ]))
        steps.append(("wb_funnel", "WB воронка продаж", [
            sys.executable, "-X", "utf8", "-u", wb_script,
            "--step", "funnel", "--date-from", args.date_from.isoformat(),
            "--date-to", args.date_to.isoformat(),
        ]))
        wb_optional_steps = (
            ("wb_advertising", "WB рекламные кампании", "promotion"),
            ("wb_search", "WB поисковая аналитика", "analytics"),
            ("wb_feedbacks_questions", "WB отзывы и вопросы", "communication"),
            ("wb_finance", "WB финансовые отчёты", "finance"),
        )
        for key, label, script_step in wb_optional_steps:
            if key == "wb_search" and wb_jam["status"] == "inactive":
                print("ПРОГРЕСС: WB поисковая аналитика | сценарий=пропуск, Jam неактивен | requests=0", flush=True)
            steps.append((key, label, [
                sys.executable, "-X", "utf8", "-u", wb_extended_script,
                "--step", script_step, *wb_period,
                *(["--finance-period", "daily"] if key == "wb_finance" else []),
                *(["--overwrite"] if args.overwrite else []),
                *(["--resume"] if args.resume else []),
                *(["--include-inactive-campaigns"] if key == "wb_advertising" and args.include_inactive_campaigns else []),
            ]))
        steps.append(("wb_views", "WB витрины BI", [
            sys.executable, "-X", "utf8", "-u", wb_script,
            "--step", "views", *wb_period,
        ]))
    avito_credentials = (
        env.get(f"AVITO_ADS_ACCOUNT_ID_{args.client_key.upper()}"),
        env.get(f"AVITO_ADS_CLIENT_ID_{args.client_key.upper()}"),
        env.get(f"AVITO_ADS_CLIENT_SECRET_{args.client_key.upper()}"),
    )
    if args.mode != "views" and "yandex_market" in marketplaces:
        from yandex_market_history import SOURCES
        for source, (label, _, _) in SOURCES.items():
            steps.append((source, label, [
                sys.executable, "-X", "utf8", "-u", str(DASHBOARD_ROOT / "scripts" / "sync_yandex_market.py"),
                "--client-key", args.client_key, "--database-name", args.database_name,
                "--step", source, "--date-from", args.date_from.isoformat(), "--date-to", args.date_to.isoformat(),
                *(["--overwrite"] if args.overwrite else []), *(["--resume"] if args.resume else []),
            ]))
    if args.mode != "views" and "avito" in marketplaces and all(avito_credentials):
        steps.append(("avito_advertising", "Avito Ads · рекламная статистика", [
            sys.executable, "-X", "utf8", "-u",
            str(DASHBOARD_ROOT / "scripts" / "sync_avito_ads.py"),
            "--client-key", args.client_key,
            "--database-name", args.database_name,
            "--date-from", args.date_from.isoformat(),
            "--date-to", args.date_to.isoformat(),
            *(["--overwrite"] if args.overwrite else []),
            *(["--resume"] if args.resume else []),
        ]))
    elif args.mode != "views" and "avito" in marketplaces and any(avito_credentials):
        print("ПРЕДУПРЕЖДЕНИЕ: Avito Ads | пропуск: сохранён неполный комплект Account ID / Client ID / Client Secret", flush=True)
    if args.mode != "views" and "lamoda" in marketplaces:
        for lamoda_step, lamoda_label in (
            ("orders", "Lamoda · Заказы"),
            ("stock", "Lamoda · Остатки"),
            ("catalog", "Lamoda · Каталог"),
            ("prices", "Lamoda · Цены и скидки"),
            ("promotions", "Lamoda · Продвижение и акции"),
            ("fbo_shipments", "Lamoda · Поставки и приёмка FBO"),
            ("fbs_returns", "Lamoda · Возвраты FBS"),
        ):
            command=[sys.executable,"-X","utf8","-u",str(DASHBOARD_ROOT/"scripts"/"sync_lamoda.py"),"--client-key",args.client_key,"--database-name",args.database_name,"--date-from",args.date_from.isoformat(),"--date-to",args.date_to.isoformat(),"--step",lamoda_step]
            if args.resume: command.append("--resume")
            if args.overwrite: command.append("--overwrite")
            steps.append((f"lamoda_{lamoda_step}",lamoda_label,command))
        if "lamoda_finance" in {value.strip() for value in args.steps.split(",") if value.strip()}:
            steps.append(("lamoda_finance", "Lamoda · Расходы и финансовые документы", [
                sys.executable, "-X", "utf8", "-u", str(DASHBOARD_ROOT / "scripts" / "sync_lamoda.py"),
                "--client-key", args.client_key, "--database-name", args.database_name,
                "--date-from", args.date_from.isoformat(), "--date-to", args.date_to.isoformat(),
                "--step", "finance",
            ]))
    if args.mode == "views":
        steps.append(("all_views", "BI materialized views", [
            sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "scripts" / "rebuild_km_dashboard_views.py"),
        ]))

    selected_steps = {value.strip() for value in args.steps.split(",") if value.strip()}
    available_steps = {key for key, _, _ in steps}
    unknown_steps = selected_steps - available_steps
    if unknown_steps:
        raise ValueError("Неизвестные шаги: " + ", ".join(sorted(unknown_steps)))
    if selected_steps:
        steps = [item for item in steps if item[0] in selected_steps]
        if not args.defer_views and "ozon_funnel" in selected_steps and "ozon_views" not in selected_steps:
            steps.append((
                "ozon_funnel_views",
                "Ozon витрины воронки",
                [
                    sys.executable, "-X", "utf8", "-u",
                    str(PROJECT_ROOT / "scripts" / "rebuild_km_dashboard_views.py"),
                ],
            ))
        if not args.defer_views and "ozon_advertising" in selected_steps and "ozon_views" not in selected_steps:
            steps.append((
                "ozon_advertising_view",
                "Ozon рекламная витрина",
                [
                    sys.executable, "-X", "utf8", "-u",
                    str(PROJECT_ROOT / "Скрипты" / "rebuild_ozon_adv_daily_category_view.py"),
                ],
            ))
        has_ozon_seller = bool(
            env.get(f"OZON_SELLER_CLIENT_ID_{args.client_key.upper()}")
            and env.get(f"OZON_SELLER_API_KEY_{args.client_key.upper()}")
        )
        needs_planfact = bool(selected_steps.intersection({"ozon_funnel", "ozon_finance"}))
        needs_planfact = needs_planfact or (
            "ozon_advertising" in selected_steps and has_ozon_seller
        )
        if not args.defer_views and needs_planfact and "ozon_views" not in selected_steps:
            steps.append((
                "ozon_planfact_view",
                "Ozon План/факт",
                [
                    sys.executable, "-X", "utf8", "-u",
                    str(PROJECT_ROOT / "scripts" / "rebuild_api_planfact_views.py"),
                ],
            ))
    if "lamoda" in marketplaces:
        selected_lamoda = any(value.startswith("lamoda_") and value not in {"lamoda_finance", "lamoda_views"} for value in selected_steps)
        if args.mode == "views" or (not args.defer_views and (selected_lamoda or not selected_steps)):
            steps.append(("lamoda_views", "Lamoda · Витрины и отчёты", [
                sys.executable, "-X", "utf8", "-u", str(DASHBOARD_ROOT / "scripts" / "rebuild_lamoda_views.py"),
                "--client", args.client_key,
            ]))
    if not steps:
        raise ValueError("Не выбран ни один доступный шаг загрузки")

    wb_stock_plan = ""
    if "wb" in marketplaces:
        wb_stock_plan = (
            " | история остатков WB="
            f"{(args.wb_stock_history_date_from or args.date_from)}.."
            f"{(args.wb_stock_history_date_to or args.date_to)}"
        )
    print(
        f"ПЛАН: клиент={args.client_key} | БД={args.database_name} | режим={args.mode} | "
        f"период={args.date_from}..{args.date_to}{wb_stock_plan} | шагов={len(steps)} | "
        f"режим={'ПЕРЕЗАПИСЬ' if args.overwrite else 'обычный'} | "
        "API read-only | токены не выводятся | WB funnel: дневные данные products API за запрошенный период, глубина до 365 дней",
        flush=True,
    )
    completed_count = 0
    limited_count = 0
    try:
        for index, (_, label, command) in enumerate(steps, start=1):
            elapsed = time.monotonic() - started
            eta = elapsed / completed_count * (len(steps) - completed_count) if completed_count else 0
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({(index - 1) / max(len(steps), 1):.0%}) | "
                f"{label} | completed={completed_count} errors=0 | elapsed={duration(elapsed)} | ETA={duration(eta)}",
                flush=True,
            )
            step_status = run_step(label, command, env)
            completed_count += 1
            if step_status == "limited":
                limited_count += 1
            print(
                f"[{index}/{len(steps)}] {label}: completed | errors=0 limitations={limited_count}",
                flush=True,
            )
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({index / max(len(steps), 1) * 100:.1f}%) | "
                f"{label} завершён | completed={completed_count} errors=0 limitations={limited_count} | "
                f"elapsed={duration(time.monotonic() - started)}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"ИТОГ: completed={completed_count}/{len(steps)} | errors=1 limitations={limited_count} | partial=yes | "
            f"elapsed={duration(time.monotonic() - started)} | error={exc}",
            flush=True,
        )
        raise
    print(
        f"ИТОГ: completed={completed_count}/{len(steps)} | errors=0 limitations={limited_count} | partial={'yes' if limited_count else 'no'} | "
        f"elapsed={duration(time.monotonic() - started)} | output_db={args.database_name}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
