"""Authenticated TOPTOP VPS PULSE with scoped updates and user grants.

The runtime serves only TOPTOP and LERA NENA. Report reads use the SELECT-only
role; registry/session writes and child import processes use a separate writer.
Secrets are loaded from Docker secret files and are never persisted in code.
"""

from __future__ import annotations

import os
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
})
ADMIN_POST_PATHS = frozenset({
    "/api/admin/users",
    "/api/admin/all-clients-daily/start",
    "/api/admin/all-clients-daily/resume",
    "/api/admin/all-clients-daily/stop",
    "/api/admin/all-clients-daily/client/start",
    "/api/admin/all-clients-daily/client/resume",
    "/api/admin/all-clients-daily/client/stop",
})
PUBLIC_POST_PATHS = frozenset({"/api/access/login", "/api/access/logout"})
REPORT_WRITE_POST_PATHS = frozenset({"/api/km-trade/pl-monthly-budget"})
_USE_WRITER_CONFIG = ContextVar("pulse_vps_use_writer_config", default=False)
ROOT_ASSETS = frozenset({
    "/app.js", "/styles.css", "/compact_shell_v2.css", "/compact_shell_v2.js",
    "/admin_header_light.css", "/seo_projects.js", "/seo_projects.css",
})


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


def configure_scope() -> None:
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
            "marketplaces": ["ozon", "wb", "yandex_market"],
        }
    app.DEFAULT_CLIENT = "toptop"
    app.normalize_client_key = _strict_client

    allowed_reports = {report for config in app.ADMIN_CLIENTS.values() for report in config["reports"]}
    app.ADMIN_REPORT_CATALOG[:] = [item for item in app.ADMIN_REPORT_CATALOG if item["id"] in allowed_reports]
    allowed_sections = {"allDaily", "users"}
    app.ADMIN_SECTION_CATALOG[:] = [item for item in app.ADMIN_SECTION_CATALOG if item["id"] in allowed_sections]
    app.ADMIN_SECTION_IDS = allowed_sections

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
        use_writer = _USE_WRITER_CONFIG.get()
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

    original_plan = app.build_admin_all_clients_daily_plan

    def scoped_daily_plan():
        plan = original_plan()
        client_keys = {item["key"] for item in plan.get("clients", [])}
        if client_keys - ALLOWED_CLIENTS:
            raise RuntimeError("daily plan escaped VPS scope")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for client in sorted(ALLOWED_CLIENTS):
            config = app.ADMIN_CLIENTS[client]
            if "yandex_market" not in config.get("marketplaces", []):
                continue
            for order, (step, label) in enumerate((
                ("yandex_orders", "Заказы Яндекс Маркета"),
                ("yandex_order_stats", "Детализация заказов Яндекс Маркета"),
                ("yandex_returns", "Возвраты Яндекс Маркета"),
            ), 1):
                plan["tasks"].append({
                    "id": f"{client}:api:{step}:{yesterday}",
                    "row_id": f"imports:{step}", "row_label": label,
                    "row_order": 700 + order, "stage": "imports",
                    "source_kind": "api", "source_label": "API · все кабинеты",
                    "client": client, "client_label": config["label"],
                    "key": f"api_{step}", "report": label,
                    "initial_status": "queued", "initial_detail": f"Обновить {yesterday}",
                    "initial_progress_text": f"Обновить {yesterday}",
                    "command": [
                        os.sys.executable, "-X", "utf8", "-u",
                        str(app.PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "run_client_pipeline.py"),
                        "--client-key", client, "--database-name", config["db_name"],
                        "--marketplaces", "yandex_market", "--mode", "daily",
                        "--date-from", yesterday, "--date-to", yesterday,
                        "--steps", step, "--defer-views",
                    ],
                    "cwd": str(app.PROJECT_ROOT),
                    "env": {**os.environ, "DASHBOARD_CLIENT": client, "DASHBOARD_DB_NAME": config["db_name"], "KM_DB_NAME": config["db_name"]},
                    "failure_policy": "retry:2:60", "supports_resume": True,
                })
        return plan

    app.build_admin_all_clients_daily_plan = scoped_daily_plan

    original_client_action = app.handle_admin_all_clients_daily_client

    def scoped_client_action(action, client, task_ids=None):
        return original_client_action(action, _strict_client(client), task_ids)

    app.handle_admin_all_clients_daily_client = scoped_client_action


class VPSAdminHandler(app.DashboardHandler):
    def _redirect_entry(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path == "/react":
            self.send_response(308)
            self.send_header("Location", "/react/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if parsed.path == "/" and parse_qs(parsed.query).get("dashboard") != ["admin"]:
            self.send_response(308)
            self.send_header("Location", "/react/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

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
            return path in READ_ONLY_API_PATHS or path in ADMIN_GET_PATHS
        return (
            path in {"/", "/react", "/login", "/login/"}
            or path in ROOT_ASSETS or path.startswith(("/react/", "/static/"))
            or (path.count("/") == 1 and (app.STATIC_DIR / path.lstrip("/")).is_file())
        )

    def do_GET(self) -> None:
        if self._redirect_entry():
            return
        if not self._allow_get():
            self.send_error(404)
            return
        app.DashboardHandler.do_GET(self)

    def do_HEAD(self) -> None:
        if self._redirect_entry():
            return
        if not self._allow_get():
            self.send_error(404)
            return
        app.DashboardHandler.do_HEAD(self)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if self._foreign_query() or path not in ADMIN_POST_PATHS | PUBLIC_POST_PATHS | REPORT_WRITE_POST_PATHS:
            self.send_error(404)
            return
        token = _USE_WRITER_CONFIG.set(path in REPORT_WRITE_POST_PATHS)
        try:
            app.DashboardHandler.do_POST(self)
        finally:
            _USE_WRITER_CONFIG.reset(token)


def main() -> None:
    configure_scope()
    print("PULSE VPS | TOPTOP + LERA NENA | auth + scoped updates | port 8062", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8062), VPSAdminHandler)
    from km_trade_sales_planning import start_sales_forecast_prewarm
    start_sales_forecast_prewarm(app.read_db_config, clients=("toptop",))
    server.serve_forever()


if __name__ == "__main__":
    main()
