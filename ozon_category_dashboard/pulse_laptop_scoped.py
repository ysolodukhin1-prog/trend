"""Laptop-only, read-only PULSE for TOPTOP and LERA NENA.

This entry point leaves the existing PULSE service unchanged. It deliberately
does not start registry hydration, import workers or write/admin endpoints.
"""

from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["PULSE_HEALTH_CHECK_PROJECT_ROOT"] = str(
    Path(__file__).resolve().parent / "health_check_runtime"
)
import app


ALLOWED_CLIENTS = frozenset({"toptop", "lera_nena"})
REPORTS = [
    'assortmentProducts', 'assortmentPrices', 'assortmentABC', 'assortmentXYZ',
    "abc", "product", "sku", "adv", "mediaAdv", "funnel",
    "weeklyDynamics", "inventoryHistory", "planfact", "salesPlanning",
    "mediaPlan", "profitLoss", "unitEconomics", "seoMonitoring",
    "wbSearchQueries", "wbAdSearchQueries", "reviews", "commercialRadar",
    "yandexOverview", "yandexFunnel", "yandexFinance",
    "yandexPromotion", "yandexInventory",
    "lamodaSales", "lamodaReturns", "lamodaCatalog", "lamodaOperations",
]
READ_ONLY_API_PATHS = frozenset({
    "/api/assortment",
    "/api/health", "/api/database-status", "/api/service-status", "/api/filters", "/api/summary", "/api/stats",
    "/api/product-summary", "/api/product-stats", "/api/sku-summary",
    "/api/sku-stats", "/api/sku-card", "/api/adv-summary", "/api/adv-daily",
    "/api/adv-waterfalls", "/api/adv-stats", "/api/adv-campaigns",
    "/api/media-adv-summary", "/api/media-adv-daily", "/api/media-adv-waterfalls",
    "/api/media-adv-stats", "/api/funnel-summary", "/api/funnel-daily",
    "/api/funnel-waterfalls", "/api/funnel-stats", "/api/funnel-products",
    "/api/order-feed", "/api/weekly-sku-inventory", "/api/sales-order-days",
    "/api/inventory-history", "/api/inventory-history-summary",
    "/api/inventory-history-products", "/api/weekly-dynamics",
    "/api/cluster-supply/status",
    "/api/seo-monitoring-products", "/api/wb-search-query-products",
    "/api/wb-search-queries-dashboard", "/api/wb-entrance-products",
    "/api/wb-entrance-dashboard", "/api/planfact-summary",
    "/api/planfact-daily", "/api/planfact-monthly",
    "/api/planfact-scorecard", "/api/planfact-products",
    "/api/planfact-funnel-matrix", "/api/health-check-hypothesis-analysis",
    "/api/km-trade/sales-forecast", "/api/km-trade/media-plan",
    "/api/km-trade/pl", "/api/km-trade/pl-monthly-budget",
    "/api/km-trade/pl-cost-registry",
    "/api/km-trade/unit-workspace",
    "/api/km-trade/unit-economics",
    "/api/wb-ad-search-queries-dashboard", "/api/reviews-dashboard",
    "/api/reviews-insights", "/api/yandex-market/analytics",
    "/api/lamoda/dashboard",
    "/api/seo-projects", "/api/seo-project",
    "/api/seo-project-candidates", "/api/seo-project-full-run/status",
})
ROOT_STATIC_ASSETS = frozenset({"/seo_projects.js", "/seo_projects.css"})


def configure_scope() -> None:
    config_source = os.environ.get("DB_CONFIG_SOURCE")
    if not config_source:
        raise RuntimeError("DB_CONFIG_SOURCE is required for the scoped laptop instance")
    if os.environ.get("DASHBOARD_DB_HOST") or os.environ.get("DASHBOARD_DB_NAME"):
        raise RuntimeError("DB host/name overrides are forbidden in laptop scope")
    app.ADMIN_CLIENTS.clear()
    for key, label in (("toptop", "TOPTOP"), ("lera_nena", "LERA NENA")):
        app.ADMIN_CLIENTS[key] = {
            "label": label,
            "db_name": key,
            "status": "active",
            "description": "Local scoped PULSE copy",
            "show_in_dashboard": True,
            "root_path": "",
            "reports": REPORTS + (["wbEntrance"] if key == "lera_nena" else []),
            "marketplaces": ["ozon", "wb", "yandex_market", "lamoda"],
        }
    app.DEFAULT_CLIENT = "toptop"
    original_read_db_config = app.read_db_config

    def strict_client_key(value: str | None) -> str:
        key = str(value or app.DEFAULT_CLIENT).strip().lower()
        if key not in ALLOWED_CLIENTS:
            raise ValueError("client outside laptop scope")
        return key

    def scoped_read_db_config(client: str | None = None) -> dict:
        key = strict_client_key(client or app.CURRENT_CLIENT.get() or app.DEFAULT_CLIENT)
        config = original_read_db_config(key)
        if (
            config.get("database") != key
            or str(config.get("host", "")).lower() not in {"127.0.0.1", "localhost"}
            or int(config.get("port", 0)) != 55432
            or config.get("user") != "pulse_local"
        ):
            raise RuntimeError("database connection outside laptop scope")
        return config

    app.normalize_client_key = strict_client_key
    app.read_db_config = scoped_read_db_config


class ScopedHandler(app.DashboardHandler):
    def _redirect_react_entry(self) -> bool:
        if urlparse(self.path).path not in {"/", "/react"}:
            return False
        self.send_response(308)
        self.send_header("Location", "/react/")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _allowed_get(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and parsed.path not in READ_ONLY_API_PATHS:
            self.send_error(404)
            return False
        if not parsed.path.startswith(("/api/", "/react/", "/static/")) and parsed.path not in ROOT_STATIC_ASSETS:
            self.send_error(404)
            return False
        if parse_qs(parsed.query).get("refresh") == ["1"]:
            self.send_error(405)
            return False
        params = parse_qs(parsed.query)
        for parameter in ("client", "client_key"):
            if parameter in params and any(value.strip().lower() not in ALLOWED_CLIENTS for value in params[parameter]):
                self.send_error(404)
                return False
        return True

    def do_GET(self) -> None:
        if self._redirect_react_entry():
            return
        if self._allowed_get():
            try:
                super().do_GET()
            except Exception as exc:
                # Keep the diagnostic limited to the exception type: request data
                # and database payloads must never enter the laptop log.
                print(f"scoped GET failed: {type(exc).__name__}", flush=True)
                try:
                    self.send_json({"ok": False, "error": "scoped_get_failed"}, status=500)
                except OSError:
                    pass

    def do_HEAD(self) -> None:
        if self._redirect_react_entry():
            return
        if self._allowed_get():
            super().do_HEAD()

    def do_POST(self) -> None:
        self.send_error(405)


def main() -> None:
    configure_scope()
    for key in ALLOWED_CLIENTS:
        app.read_db_config(key)
    host = "127.0.0.1"
    port = int(os.environ.get("PULSE_LAPTOP_PORT", "8062"))
    print(f"PULSE scoped laptop: http://{host}:{port} | TOPTOP,LERA NENA | read-only", flush=True)
    ThreadingHTTPServer((host, port), ScopedHandler).serve_forever()


if __name__ == "__main__":
    main()
