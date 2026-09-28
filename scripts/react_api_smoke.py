import json
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


def resolve_sku_card_url():
    stats_url = "/api/sku-stats?dashboard=sku&marketplace=ozon&page=1&limit=1&sort_col=total_stock_qty&sort_dir=desc"
    payload = app.handle_sku_stats(urlparse(stats_url))
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    if not rows:
        raise AssertionError("sku_card_smoke: /api/sku-stats returned no rows")

    row = rows[0]
    sku = row.get("artikul_wb") or row.get("sku") or row.get("ozon_sku")
    if not sku:
        raise AssertionError("sku_card_smoke: first /api/sku-stats row has no SKU field")

    return f"/api/sku-card?dashboard=sku&marketplace=ozon&sku={quote(str(sku))}"


def react_api_smoke_checks():
    return [
        ("filters_funnel_ozon", "/api/filters?dashboard=funnel&marketplace=ozon", app.handle_funnel_filters),
        ("funnel_summary_ozon", "/api/funnel-summary?dashboard=funnel&marketplace=ozon", app.handle_funnel_summary),
        ("funnel_daily_ozon", "/api/funnel-daily?dashboard=funnel&marketplace=ozon", app.handle_funnel_daily),
        (
            "funnel_stats_ozon",
            "/api/funnel-stats?dashboard=funnel&marketplace=ozon&page=1&limit=50&sort_col=report_date&sort_dir=asc",
            app.handle_funnel_stats,
        ),
        ("funnel_waterfalls_ozon", "/api/funnel-waterfalls?dashboard=funnel&marketplace=ozon", app.handle_funnel_waterfalls),
        ("funnel_products_ozon", "/api/funnel-products?dashboard=funnel&marketplace=ozon&q=", app.handle_funnel_products),
        ("adv_daily_ozon", "/api/adv-daily?dashboard=adv&marketplace=ozon", app.handle_adv_daily),
        ("media_adv_daily_ozon", "/api/media-adv-daily?dashboard=mediaAdv&marketplace=ozon", app.handle_media_adv_daily),
        (
            "planfact_monthly_ozon",
            "/api/planfact-monthly?dashboard=planfact&marketplace=ozon&date_from=2026-05-01&date_to=2026-05-28",
            app.handle_planfact_monthly,
        ),
        (
            "planfact_scorecard_ozon",
            "/api/planfact-scorecard?dashboard=planfact&marketplace=ozon&date_from=2026-05-01&date_to=2026-05-28",
            app.handle_planfact_scorecard,
        ),
        ("sku_card_smoke", resolve_sku_card_url, app.handle_sku_card),
    ]


def assert_payload_has_signal(name, payload):
    if not isinstance(payload, dict):
        raise AssertionError(f"{name}: payload is not an object")
    if payload.get("ok") is False:
        raise AssertionError(f"{name}: {payload.get('error') or 'ok=false'}")
    if "rows" in payload and isinstance(payload["rows"], list):
        return
    if "product_names" in payload and isinstance(payload["product_names"], list):
        return
    if payload:
        return
    raise AssertionError(f"{name}: empty payload")


def run_check(name, url, handler):
    started = time.perf_counter()
    resolved_url = "<dynamic>"
    try:
        resolved_url = url() if callable(url) else url
        payload = handler(urlparse(resolved_url))
        assert_payload_has_signal(name, payload)
        ok = True
        return {
            "name": name,
            "url": resolved_url,
            "ok": ok,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "rows": len(payload.get("rows", [])) if isinstance(payload, dict) else None,
        }
    except Exception as exc:
        ok = False
        return {
            "name": name,
            "url": resolved_url,
            "ok": ok,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def main():
    results = [run_check(name, url, handler) for name, url, handler in react_api_smoke_checks()]
    for item in results:
        print(json.dumps(item, ensure_ascii=False), flush=True)
    sys.exit(1 if any(not item["ok"] for item in results) else 0)


if __name__ == "__main__":
    main()
