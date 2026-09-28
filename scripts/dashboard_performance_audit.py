import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


def default_checks():
    return [
        {
            "name": "filters_abc_ozon",
            "url": "/api/filters?dashboard=abc&marketplace=ozon",
            "handler": app.handle_filters,
        },
        {
            "name": "filters_abc_ozon_compact",
            "url": "/api/filters?dashboard=abc&marketplace=ozon&compact=1",
            "handler": app.handle_filters,
        },
        {
            "name": "abc_summary_ozon",
            "url": "/api/summary?dashboard=abc&marketplace=ozon",
            "handler": app.handle_summary,
        },
        {
            "name": "abc_stats_ozon",
            "url": "/api/stats?dashboard=abc&marketplace=ozon&limit=50&page=1&sort=total_stock_qty&dir=desc",
            "handler": app.handle_stats,
        },
        {
            "name": "product_stats_ozon_30d",
            "url": "/api/product-stats?dashboard=product&marketplace=ozon&limit=50&page=1&sort=zakazano_rub&dir=desc&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_product_stats,
        },
        {
            "name": "sku_stats_ozon_30d",
            "url": "/api/sku-stats?dashboard=sku&marketplace=ozon&limit=50&page=1&sort=zakazano_rub&dir=desc&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_sku_stats,
        },
        {
            "name": "funnel_filters_ozon",
            "url": "/api/filters?dashboard=funnel&marketplace=ozon",
            "handler": app.handle_funnel_filters,
        },
        {
            "name": "funnel_filters_ozon_compact",
            "url": "/api/filters?dashboard=funnel&marketplace=ozon&compact=1",
            "handler": app.handle_funnel_filters,
        },
        {
            "name": "funnel_products_ozon_empty",
            "url": "/api/funnel-products?dashboard=funnel&marketplace=ozon",
            "handler": app.handle_funnel_products,
        },
        {
            "name": "funnel_summary_ozon_30d",
            "url": "/api/funnel-summary?dashboard=funnel&marketplace=ozon&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_funnel_summary,
        },
        {
            "name": "funnel_daily_ozon_30d",
            "url": "/api/funnel-daily?dashboard=funnel&marketplace=ozon&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_funnel_daily,
        },
        {
            "name": "funnel_stats_ozon_30d",
            "url": "/api/funnel-stats?dashboard=funnel&marketplace=ozon&limit=50&page=1&sort=report_date&dir=asc&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_funnel_stats,
        },
        {
            "name": "adv_summary_30d",
            "url": "/api/adv-summary?dashboard=adv&marketplace=ozon&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_adv_summary,
        },
        {
            "name": "media_adv_summary_30d",
            "url": "/api/media-adv-summary?dashboard=mediaAdv&marketplace=ozon&date_from=2026-04-29&date_to=2026-05-28",
            "handler": app.handle_media_adv_summary,
        },
        {
            "name": "planfact_summary_may",
            "url": "/api/planfact-summary?dashboard=planfact&marketplace=ozon&date_from=2026-05-01&date_to=2026-05-28",
            "handler": app.handle_planfact_summary,
        },
    ]


def measure_check(check):
    started = time.perf_counter()
    try:
        payload = check["handler"](urlparse(check["url"]))
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return {
            "name": check["name"],
            "url": check["url"],
            "ok": True,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "bytes": len(body),
            "rows": len(payload.get("rows", [])) if isinstance(payload, dict) else None,
        }
    except Exception as exc:
        return {
            "name": check["name"],
            "url": check["url"],
            "ok": False,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "bytes": 0,
            "rows": None,
            "error": str(exc),
            "kind": app.api_error_payload(exc).get("kind"),
        }


def run_audit(timeout_ms):
    os.environ["DASHBOARD_STATEMENT_TIMEOUT_MS"] = str(timeout_ms)
    return [measure_check(check) for check in default_checks()]


def write_report(results, timeout_ms, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"performance_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "statement_timeout_ms": timeout_ms,
        "results": results,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Measure KOKOC BI dashboard API endpoint latency.")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs")
    args = parser.parse_args()

    results = run_audit(args.timeout_ms)
    for result in results:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    path = write_report(results, args.timeout_ms, args.output_dir)
    print(f"Saved performance audit: {path}")


if __name__ == "__main__":
    main()
