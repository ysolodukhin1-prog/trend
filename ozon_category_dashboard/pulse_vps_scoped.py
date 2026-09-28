"""Closed VPS PULSE reader for only TOPTOP and LERA NENA.

No registry hydration, import workers, source credentials, or write endpoints.
Publish only on a host-loopback Docker port until full report/auth QA passes.
"""

from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from pathlib import Path

from pulse_laptop_scoped import ALLOWED_CLIENTS, REPORTS, ScopedHandler, app


DB_HOST = "pulse_postgres"
DB_USER = "pulse_reader"


def configure_scope() -> None:
    secret_file = Path(os.environ.get("PULSE_DB_PASSWORD_FILE", ""))
    if not secret_file.is_file():
        raise RuntimeError("PULSE_DB_PASSWORD_FILE is required")
    password = secret_file.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("PULSE database credential is empty")
    if any(os.environ.get(name) for name in ("DB_CONFIG_SOURCE", "DASHBOARD_DB_HOST", "DASHBOARD_DB_NAME")):
        raise RuntimeError("legacy database overrides are forbidden")

    app.ADMIN_CLIENTS.clear()
    for key, label in (("toptop", "TOPTOP"), ("lera_nena", "LERA NENA")):
        app.ADMIN_CLIENTS[key] = {
            "label": label,
            "db_name": key,
            "status": "active",
            "description": "Scoped TOPTOP VPS reader",
            "show_in_dashboard": True,
            "root_path": "",
            "reports": REPORTS + (["wbEntrance"] if key == "lera_nena" else []),
            "marketplaces": ["ozon", "wb", "yandex_market"],
        }
    app.DEFAULT_CLIENT = "toptop"

    def strict_client_key(value: str | None) -> str:
        key = str(value or app.DEFAULT_CLIENT).strip().lower()
        if key not in ALLOWED_CLIENTS:
            raise ValueError("client outside VPS scope")
        return key

    def scoped_read_db_config(client: str | None = None) -> dict:
        key = strict_client_key(client or app.CURRENT_CLIENT.get() or app.DEFAULT_CLIENT)
        return {
            "host": DB_HOST,
            "port": 5432,
            "user": DB_USER,
            "password": password,
            "database": key,
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000",
        }

    app.normalize_client_key = strict_client_key
    app.read_db_config = scoped_read_db_config


class VPSHandler(ScopedHandler):
    def _allowed_get(self) -> bool:
        # The laptop allowlist handles APIs and client params. Exclude its
        # unrelated /static/ branch: this package serves only /react/* assets.
        if self.path.split("?", 1)[0].startswith("/static/"):
            self.send_error(404)
            return False
        return super()._allowed_get()


def main() -> None:
    configure_scope()
    host = "0.0.0.0"  # Host mapping must remain 127.0.0.1-only in Compose.
    port = 8062
    print("PULSE VPS scoped reader | 2 clients | read-only | container port 8062", flush=True)
    ThreadingHTTPServer((host, port), VPSHandler).serve_forever()


if __name__ == "__main__":
    main()
