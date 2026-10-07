"""Authenticated TOPTOP VPS PULSE with scoped updates and user grants.

The runtime serves only TOPTOP and LERA NENA. Report reads use the SELECT-only
role; registry/session writes and child import processes use a separate writer.
Secrets are loaded from Docker secret files and are never persisted in code.
"""

from __future__ import annotations

import os
import threading
from contextvars import ContextVar
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg2
from psycopg2.extras import RealDictCursor

from pulse_laptop_scoped import ALLOWED_CLIENTS, READ_ONLY_API_PATHS, REPORTS, app


DB_HOST = "pulse_postgres"
READER_USER = "pulse_reader"
WRITER_USER = "pulse_writer"
ADMIN_GET_PATHS = frozenset({
    "/api/admin/auth/status",
    "/api/admin/users",
    "/api/admin/imports",
    "/api/admin/all-clients-daily",
    "/api/admin/clients",
    "/api/admin/connections",
    "/api/admin/client-onboarding",
    "/api/admin/client-onboarding/inspection",
})
ADMIN_POST_PATHS = frozenset({
    "/api/admin/users",
    "/api/admin/all-clients-daily/start",
    "/api/admin/all-clients-daily/resume",
    "/api/admin/all-clients-daily/stop",
    "/api/admin/all-clients-daily/client/start",
    "/api/admin/all-clients-daily/client/resume",
    "/api/admin/all-clients-daily/client/stop",
    "/api/admin/all-clients-daily/tasks/start",
    "/api/admin/all-clients-daily/tasks/resume",
    "/api/admin/all-clients-daily/tasks/stop",
    "/api/admin/clients",
    "/api/admin/connections",
    "/api/admin/yandex-market/discover",
    "/api/admin/client-onboarding/start",
    "/api/admin/client-onboarding/history",
    "/api/admin/client-onboarding/history/stop",
    "/api/admin/client-connections/disconnect",
})
PUBLIC_POST_PATHS = frozenset({"/api/access/login", "/api/access/logout", "/api/access/session/activity"})
REPORT_WRITE_GET_PATHS = frozenset({
    "/api/km-trade/unit-planner-inputs",
    "/api/reviews-workbench",
    "/api/reviews-reply",
    # The catalog endpoint builds transaction-local TEMP tables only; no
    # persistent marketplace data is changed.
    "/api/seo-project-candidates",
})
REPORT_WRITE_POST_PATHS = frozenset({
    "/api/km-trade/pl-monthly-budget",
    "/api/km-trade/unit-planner-inputs",
    "/api/reviews-workbench",
    "/api/reviews-reply/generate",
    "/api/reviews-reply/save",
    "/api/reviews-reply/publish",
    "/api/seo-projects",
    "/api/seo-projects/rename",
    "/api/seo-projects/skus",
    "/api/seo-projects/delete",
    "/api/seo-project-template/parse",
})
_USE_WRITER_CONFIG = ContextVar("pulse_vps_use_writer_config", default=False)
ROOT_ASSETS = frozenset({
    "/app.js", "/styles.css", "/compact_shell_v2.css", "/compact_shell_v2.js",
    "/admin_header_light.css", "/seo_projects.js", "/seo_projects.css",
})
MARKETPLACE_CREDENTIAL_KEYS = {
    "wb": ("wb_api_token", "wb_service_api_token"),
    "ozon": (
        "ozon_client_id", "ozon_api_key",
        "ozon_performance_client_id", "ozon_performance_client_secret",
    ),
    "avito": ("avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret"),
    "lamoda": ("lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id"),
    "yandex_market": (
        "yandex_market_api_key", "yandex_market_business_id", "yandex_market_campaign_id",
    ),
}


def _secret(env_name: str) -> str:
    path = Path(os.environ.get(env_name, ""))
    if not path.is_file():
        raise RuntimeError(f"{env_name} is required")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"{env_name} is empty")
    return value


def _strict_client(value: str | None) -> str:
    key = str(value or app.DEFAULT_CLIENT).strip().lower()
    if key not in ALLOWED_CLIENTS:
        raise ValueError("client outside VPS scope")
    return key


def _ensure_marketplace_storage(client_key, db_name, marketplaces):
    config=dict(app.read_db_config(client_key)); config["database"]=db_name
    with psycopg2.connect(**config) as conn:
        if "avito" in marketplaces:
            from scripts.sync_avito_ads import ensure_schema as ensure_avito
            ensure_avito(conn,client_key,db_name)
        if "lamoda" in marketplaces:
            from scripts.sync_lamoda import ensure_schema
            ensure_schema(conn)


def configure_scope() -> None:
    if getattr(app, "_vps_scope_configured", False):
        return
    reader_password = _secret("PULSE_DB_PASSWORD_FILE")
    writer_password = _secret("PULSE_WRITER_PASSWORD_FILE")
    session_secret = _secret("PULSE_SESSION_SECRET_FILE")
    credentials_key = _secret("PULSE_CREDENTIALS_MASTER_KEY_FILE")

    app.ADMIN_CLIENTS.clear()
    for key, label in (("toptop", "TOPTOP"), ("lera_nena", "LERA NENA")):
        app.ADMIN_CLIENTS[key] = {
            "label": label,
            "db_name": key,
            "status": "active",
            "description": "TOPTOP VPS",
            "show_in_dashboard": True,
            "root_path": f"/var/lib/pulse/clients/{key}",
            "reports": REPORTS + (["wbEntrance"] if key == "lera_nena" else []),
            "marketplaces": ["ozon", "wb", "yandex_market", "lamoda"],
        }
    app.DEFAULT_CLIENT = "toptop"
    app.normalize_client_key = _strict_client

    allowed_reports = {report for config in app.ADMIN_CLIENTS.values() for report in config["reports"]}
    app.ADMIN_REPORT_CATALOG[:] = [item for item in app.ADMIN_REPORT_CATALOG if item["id"] in allowed_reports]
    allowed_sections = {"allDaily", "client", "clientOnboarding", "users"}
    app.ADMIN_SECTION_CATALOG[:] = [item for item in app.ADMIN_SECTION_CATALOG if item["id"] in allowed_sections]
    app.ADMIN_SECTION_IDS = allowed_sections
    base_admin_section_ids = app.current_admin_section_ids

    def scoped_admin_section_ids():
        sections = set(base_admin_section_ids())
        if {"allDaily", "users"}.issubset(sections):
            sections.update({"client", "clientOnboarding"})
        return sections.intersection(allowed_sections)

    app.current_admin_section_ids = scoped_admin_section_ids

    os.environ["DASHBOARD_USER_SESSION_SECRET"] = session_secret
    os.environ["CLIENT_CREDENTIALS_MASTER_KEY"] = credentials_key
    os.environ["DASHBOARD_DB_HOST"] = DB_HOST
    os.environ["DASHBOARD_DB_PORT"] = "5432"
    os.environ["DASHBOARD_DB_USER"] = WRITER_USER
    os.environ["DASHBOARD_DB_PASSWORD"] = writer_password
    os.environ["DASHBOARD_DB_NAME"] = "toptop"
    os.environ.setdefault("DASHBOARD_STATEMENT_TIMEOUT_MS", "30000")
    os.environ.setdefault("PULSE_ACCESS_ALLOWED_ORIGINS", "http://127.0.0.1:8062,http://127.0.0.1:18062")
    # Keep the dashboard closed until the owner creates the first managed user.
    os.environ["PULSE_GALACTICA_STARTUP_FILE"] = "/run/pulse/managed-auth-required"

    def read_config(client: str | None = None) -> dict:
        key = _strict_client(client or app.CURRENT_CLIENT.get() or app.DEFAULT_CLIENT)
        worker_name = threading.current_thread().name
        use_writer = _USE_WRITER_CONFIG.get() or worker_name.startswith(("history-", "assortment-", "provision-"))
        return {
            "host": DB_HOST, "port": 5432,
            "user": WRITER_USER if use_writer else READER_USER,
            "password": writer_password if use_writer else reader_password,
            "database": key, "connect_timeout": 5,
            "options": "-c statement_timeout=30000",
        }

    def registry_connection():
        return psycopg2.connect(
            host=DB_HOST, port=5432, user=WRITER_USER, password=writer_password,
            database="toptop", connect_timeout=5, cursor_factory=RealDictCursor,
        )

    app.read_db_config = read_config
    app.client_registry_connection = registry_connection
    app.ADMIN_ALL_CLIENTS_DAILY_STATE_PATH = Path("/var/lib/pulse/admin_all_clients_daily_state.json")
    app.ADMIN_ALL_CLIENTS_ASSORTMENT_STATE_PATH = Path("/var/lib/pulse/admin_all_clients_assortment_state.json")
    app.ADMIN_ALL_CLIENTS_DAILY_RUNNER = None
    app.ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER = None

    os.environ["TREND_MARKETPLACE_ROWS"] = "1"

    # The base dashboard passes client_key to an older completeness helper
    # whose deployed signature does not accept it. Client databases are
    # already isolated, so omit the redundant argument and keep a genuinely
    # limited source from being reported as an unknown queued task.
    def compatible_api_completeness(client, client_config):
        from api_completeness import fallback_client_api_completeness, inspect_client_api_completeness

        try:
            config = dict(read_config(client))
            config["database"] = client_config["db_name"]
            with psycopg2.connect(**config) as conn:
                return inspect_client_api_completeness(
                    conn,
                    history_date_from=str(client_config.get("history_date_from") or ""),
                )
        except Exception as exc:
            return fallback_client_api_completeness(reason=f"{type(exc).__name__}: {exc}")

    app.admin_all_clients_api_completeness = compatible_api_completeness
    original_plan = app.build_admin_all_clients_daily_plan

    def scoped_daily_plan():
        plan = original_plan()
        client_keys = {item["key"] for item in plan.get("clients", [])}
        if client_keys - ALLOWED_CLIENTS:
            raise RuntimeError("daily plan escaped VPS scope")
        from yandex_daily import extend_plan
        from trend_daily import extend_plan as extend_trend
        from trend_monthly import extend_plan as extend_monthly
        from trend_freshness import extend_plan as extend_freshness
        monthly = extend_monthly(app, extend_trend(app, extend_plan(app, plan, ALLOWED_CLIENTS)))
        return extend_freshness(app, monthly)

    app.build_admin_all_clients_daily_plan = scoped_daily_plan

    original_client_action = app.handle_admin_all_clients_daily_client

    def scoped_client_action(action, client, task_ids=None):
        return original_client_action(action, _strict_client(client), task_ids)

    app.handle_admin_all_clients_daily_client = scoped_client_action
    original_save_client = app.save_admin_client
    def save_client_with_storage(payload):
        if any((payload.get("credentials") or {}).get(k) for k in MARKETPLACE_CREDENTIAL_KEYS["lamoda"]):
            raise ValueError("Добавьте отдельный аккаунт Lamoda с методом FBO/FBS")
        creds=payload.get("credentials") if isinstance(payload.get("credentials"),dict) else {}
        if creds.get("lamoda_client_id") and creds.get("lamoda_client_secret") and creds.get("lamoda_seller_id"):
            from scripts.sync_lamoda import verify_credentials
            verify_credentials(str(creds["lamoda_client_id"]),str(creds["lamoda_client_secret"]),str(creds["lamoda_seller_id"]))
        result=original_save_client(payload)
        key=str(result.get("saved_client") or payload.get("key") or payload.get("client") or "")
        markets=[str(x) for x in payload.get("marketplaces") or []]
        token=_USE_WRITER_CONFIG.set(True)
        try: _ensure_marketplace_storage(key,str(payload.get("db_name") or key),markets)
        finally: _USE_WRITER_CONFIG.reset(token)
        result["storage_ready"]=[x for x in markets if x in {"avito","lamoda"}]
        return result
    app.save_admin_client = save_client_with_storage
    from user_registry import ensure_schema
    with registry_connection() as connection:
        ensure_schema(connection)
    from trend_activity_log import ensure_schema as ensure_activity_schema
    ensure_activity_schema(app)
    app._vps_scope_configured = True

def disconnect_marketplace(payload: dict) -> dict:
    """Remove marketplace credentials without exposing secrets."""
    import json
    from client_registry import get_client
    client_key = _strict_client(payload.get("client"))
    marketplace = str(payload.get("marketplace") or "").strip().lower()
    if marketplace == "lamoda":
        raise ValueError("Отключайте нужный аккаунт в разделе Lamoda FBO/FBS")
    credential_keys = MARKETPLACE_CREDENTIAL_KEYS.get(marketplace)
    if credential_keys is None:
        raise ValueError("unknown marketplace")
    with app.client_registry_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT marketplaces FROM public.bi_client_registry WHERE client_key = %s FOR UPDATE", (client_key,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("client not found")
            current = row.get("marketplaces") if isinstance(row, dict) else row[0]
            current = json.loads(current) if isinstance(current, str) else current
            marketplaces = [value for value in (current or []) if str(value) != marketplace]
            cursor.execute("UPDATE public.bi_client_registry SET marketplaces = %s::jsonb, updated_at = now() WHERE client_key = %s", (json.dumps(marketplaces), client_key))
            cursor.execute("DELETE FROM public.bi_client_marketplace_stores WHERE client_key = %s AND marketplace = %s", (client_key, marketplace))
            cursor.execute("DELETE FROM public.bi_client_marketplace_accounts WHERE client_key = %s AND marketplace = %s", (client_key, marketplace))
            cursor.execute("DELETE FROM public.bi_client_credentials WHERE client_key = %s AND credential_key = ANY(%s)", (client_key, list(credential_keys)))
        connection.commit()
        client = get_client(connection, client_key)
    if client:
        app.apply_registered_client(client)
    return {"ok": True, "client": client, "disconnected_marketplace": marketplace}



class ActivityMixin:
    def do_GET(self):
        from trend_activity_log import begin, finish
        begin(app, self, 'GET')
        try: return self._activity_do_GET()
        finally: finish(app, self)

    def do_POST(self):
        from trend_activity_log import begin, finish
        begin(app, self, 'POST')
        try: return self._activity_do_POST()
        finally: finish(app, self)

    def send_response(self, code, message=None):
        if getattr(self, '_activity', None) is not None: self._activity['status'] = code
        return super().send_response(code, message)

    def read_json_body(self):
        from trend_activity_log import capture_body
        payload = super().read_json_body()
        capture_body(self, payload)
        return payload

    def send_json(self, payload, status=200, headers=None):
        from trend_activity_log import capture_response
        capture_response(app, self, payload, status, headers)
        return super().send_json(payload, status=status, headers=headers)


class VPSAdminHandler(ActivityMixin, app.DashboardHandler):
    def dashboard_access_default_path(self):
        return "/react/?client=toptop&dashboard=home&marketplace=ozon"

    def _redirect_entry(self) -> bool:
        from dashboard_access_navigation import scoped_react_entry
        target = scoped_react_entry(self.path, app.DEFAULT_CLIENT)
        if target is None:
            return False
        self.dashboard_access_redirect(target)
        return True

    def _foreign_query(self) -> bool:
        params = parse_qs(urlparse(self.path).query)
        return any(
            value.strip().lower() not in ALLOWED_CLIENTS
            for name in ("client", "client_key") for value in params.get(name, [])
        )

    def _allow_get(self) -> bool:
        path = urlparse(self.path).path
        if self._foreign_query():
            return False
        if path.startswith("/api/"):
            return path in ("/api/offline-sales","/api/channel-sales","/api/retail-bi") or path in READ_ONLY_API_PATHS or path in ADMIN_GET_PATHS or path in REPORT_WRITE_GET_PATHS or path in {"/api/access/session/status", "/api/access/session/activity.js"}
        return (
            path in {"/", "/react", "/login", "/login/"}
            or path in ROOT_ASSETS or path.startswith(("/react/", "/static/"))
            or (path.count("/") == 1 and (app.STATIC_DIR / path.lstrip("/")).is_file())
        )

    def _activity_do_GET(self) -> None:
        if urlparse(self.path).path == "/api/admin/lamoda-accounts":
            from lamoda_accounts import handle
            handle(app, self, "GET")
            return
        if urlparse(self.path).path == '/api/access/profile':
            identity = self.dashboard_access_identity()
            if not identity or not identity.get('username'):
                self.send_dashboard_access_required(urlparse(self.path))
                return
            self.send_json({
                'ok': True, 'authenticated': True,
                'user': {key: identity.get(key) or '' for key in ('username', 'display_name', 'email')}
                        | {'is_admin': bool(identity.get('is_admin'))},
            }, headers={'Cache-Control': 'no-store, max-age=0'})
            return

        if urlparse(self.path).path == "/api/admin/one-c-import":
            from one_c_import import handle
            handle(__import__(__name__, fromlist=['app']), self, "GET")
            return

        if urlparse(self.path).path == "/api/admin/activity-log":
            from trend_activity_log import handle_read
            handle_read(app, self)
            return

        if urlparse(self.path).path == '/api/galactica/data-entitlement':
            from data_access import handle_entitlement
            handle_entitlement(app, self, urlparse(self.path))
            return

        if self._redirect_entry():
            return
        if not self._allow_get():
            self.send_error(404)
            return
        path = urlparse(self.path).path
        token = _USE_WRITER_CONFIG.set(path in REPORT_WRITE_GET_PATHS or path == "/api/admin/connections")
        try:
            app.DashboardHandler.do_GET(self)
        finally:
            _USE_WRITER_CONFIG.reset(token)

    def do_HEAD(self) -> None:
        if self._redirect_entry():
            return
        if not self._allow_get():
            self.send_error(404)
            return
        app.DashboardHandler.do_HEAD(self)

    def _activity_do_POST(self) -> None:
        if urlparse(self.path).path == "/api/admin/lamoda-accounts":
            from lamoda_accounts import handle
            handle(app, self, "POST")
            return
        if urlparse(self.path).path == "/api/admin/one-c-import":
            from one_c_import import handle
            handle(__import__(__name__, fromlist=['app']), self, "POST")
            return

        if urlparse(self.path).path == "/api/access/password":
            from self_password import handle
            handle(app, self)
            return

        if urlparse(self.path).path == "/api/access/activity":
            from trend_activity_log import handle_page
            handle_page(app, self)
            return

        path = urlparse(self.path).path
        if path == "/api/galactica/read" and not self._foreign_query():
            from trend_reports import handle
            token = _USE_WRITER_CONFIG.set(False)
            try:
                handle(app, self)
            finally:
                _USE_WRITER_CONFIG.reset(token)
            return
        if path == "/api/galactica/database" and not self._foreign_query():
            from trend_database import handle
            token = _USE_WRITER_CONFIG.set(True)
            try:
                handle(app, self)
            finally:
                _USE_WRITER_CONFIG.reset(token)
            return
        if self._foreign_query() or path not in ADMIN_POST_PATHS | PUBLIC_POST_PATHS | REPORT_WRITE_POST_PATHS:
            self.send_error(404)
            return
        if parse_qs(urlparse(self.path).query).get("sales_channel") and path not in PUBLIC_POST_PATHS and not path.startswith("/api/admin/"):
            if not self.dashboard_access_granted():
                self.send_dashboard_access_required(urlparse(self.path))
            else:
                self.send_json({"ok": False, "error": "Запись планов для канала пока не подключена"}, status=422)
            return
        if path == "/api/admin/client-connections/disconnect":
            parsed = urlparse(self.path)
            if not self.dashboard_access_granted():
                self.send_dashboard_access_required(parsed)
                return
            identity = self.dashboard_access_identity() or {}
            granted = set(identity.get("admin_sections") or [])
            if not identity.get("is_admin") and not granted.intersection({"client", "clientOnboarding"}):
                self.send_json({"ok": False, "error": "Нет доступа к подключениям магазинов"}, status=403)
                return
            try:
                self.send_json(disconnect_marketplace(self.read_json_body()))
            except (ValueError, RuntimeError, TypeError, psycopg2.Error) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        task_action_prefix = "/api/admin/all-clients-daily/tasks/"
        if path.startswith(task_action_prefix):
            parsed = urlparse(self.path)
            if not self.dashboard_access_granted():
                self.send_dashboard_access_required(parsed)
                return
            identity = self.dashboard_access_identity() or {}
            if not identity.get("is_admin") and "allDaily" not in set(identity.get("admin_sections") or []):
                self.send_json({"ok": False, "error": "Нет доступа к разделу админки"}, status=403)
                return
            try:
                payload = self.read_json_body()
                task_ids = payload.get("task_ids") or []
                action = path.removeprefix(task_action_prefix)
                runner = app.admin_all_clients_daily_runner()
                if action == "stop":
                    result = runner.stop_tasks(task_ids)
                elif action == "resume":
                    result = runner.start_tasks(task_ids, resume=True)
                elif action == "start":
                    result = runner.start_tasks(task_ids, resume=False)
                else:
                    raise ValueError(f"Неизвестное действие: {action}")
                self.send_json(result, status=202)
            except (ValueError, RuntimeError, TypeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        token = _USE_WRITER_CONFIG.set(path in REPORT_WRITE_POST_PATHS or path == "/api/admin/connections")
        try:
            app.DashboardHandler.do_POST(self)
        finally:
            _USE_WRITER_CONFIG.reset(token)


def main() -> None:
    configure_scope()
    print("PULSE VPS | TOPTOP + LERA NENA | auth + scoped updates | port 8062", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8062), VPSAdminHandler)
    if os.environ.get("PULSE_DAILY_SCHEDULE_ENABLED", "1") == "1":
        from pulse_daily_scheduler import start_in_reader

        start_in_reader(app, Path("/var/lib/pulse/daily_schedule.json"))
    from km_trade_sales_planning import start_sales_forecast_prewarm
    start_sales_forecast_prewarm(app.read_db_config, clients=("toptop",))
    server.serve_forever()


if __name__ == "__main__":
    main()
