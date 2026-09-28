"""Bounded live smoke test for one representative API per registered PULSE report."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import base64
import hashlib
import hmac
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8052"
TARGET_FROM = os.environ.get("PULSE_QA_DATE_FROM", "2026-09-01")
TARGET_TO = os.environ.get("PULSE_QA_DATE_TO", "2026-09-06")
MAX_WORKERS = max(1, int(os.environ.get("PULSE_QA_MAX_WORKERS", "2")))
TIMEOUT_SECONDS = max(10, int(os.environ.get("PULSE_QA_TIMEOUT_SECONDS", "30")))
RETRY_TIMEOUT_SECONDS = max(
    TIMEOUT_SECONDS,
    int(os.environ.get("PULSE_QA_RETRY_TIMEOUT_SECONDS", "120")),
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPORT_ENDPOINTS = {
    "abc": "/api/summary",
    "product": "/api/product-summary",
    "sku": "/api/sku-summary",
    "adv": "/api/adv-summary",
    "mediaAdv": "/api/media-adv-summary",
    "funnel": "/api/funnel-summary",
    "weeklyDynamics": "/api/weekly-dynamics",
    "inventoryHistory": "/api/inventory-history-summary",
    "planfact": "/api/planfact-summary",
    "salesPlanning": "/api/km-trade/sales-forecast",
    "mediaPlan": "/api/km-trade/media-plan",
    "profitLoss": "/api/km-trade/pl",
    "unitEconomics": "/api/km-trade/unit-economics",
    "seoMonitoring": "/api/seo-monitoring-products",
    "wbSearchQueries": "/api/wb-search-queries-dashboard",
    "wbAdSearchQueries": "/api/wb-ad-search-queries-dashboard",
    "wbEntrance": "/api/wb-entrance-dashboard",
    "reviews": "/api/reviews-dashboard",
    "commercialRadar": "/api/planfact-funnel-matrix",
    "avitoOverview": "/api/avito-ads-dashboard",
    "avitoCampaigns": "/api/avito-ads-dashboard",
    "avitoGroups": "/api/avito-ads-dashboard",
    "avitoCreatives": "/api/avito-ads-dashboard",
    "avitoDaily": "/api/avito-ads-dashboard",
    "yandexOverview": "/api/yandex-market/analytics",
    "yandexFunnel": "/api/yandex-market/analytics",
    "yandexFinance": "/api/yandex-market/analytics",
    "yandexPromotion": "/api/yandex-market/analytics",
    "yandexInventory": "/api/yandex-market/analytics",
}

OUTPUT_PATH = Path(
    os.environ.get(
        "PULSE_QA_OUTPUT",
        "reports/pulse_all_registered_report_apis_latest.json",
    )
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def local_admin_headers() -> dict[str, str]:
    """Create a short-lived local admin session without exposing credentials."""
    env_path = Path(__file__).resolve().parents[1] / "ozon_category_dashboard" / ".env.local"
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key.strip()] = value
    username = os.environ.get("ADMIN_AUTH_USERNAME") or values.get("ADMIN_AUTH_USERNAME", "")
    secret = os.environ.get("ADMIN_AUTH_SESSION_SECRET") or values.get("ADMIN_AUTH_SESSION_SECRET", "")
    if not username or not secret:
        return {}
    issued_at = int(time.time())
    payload = {
        "username": username,
        "issued_at": issued_at,
        "expires_at": issued_at + 3600,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = _b64url(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64url(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return {"Cookie": f"kokoc_admin_session={encoded}.{signature}"}


AUTH_HEADERS = local_admin_headers()


def get_json(
    path: str,
    params: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> tuple[int, dict]:
    query = f"?{urlencode(params or {})}" if params else ""
    request = Request(
        f"{BASE_URL}{path}{query}",
        headers={"Accept": "application/json", **AUTH_HEADERS},
    )
    with urlopen(request, timeout=timeout_seconds or TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def run_case(case: dict[str, str], timeout_seconds: int | None = None) -> dict:
    started = time.monotonic()
    params = {
        "client": case["client"],
        "dashboard": case["report"],
        "marketplace": case["marketplace"],
        "date_from": TARGET_FROM,
        "date_to": TARGET_TO,
    }
    try:
        http_status, payload = get_json(case["endpoint"], params, timeout_seconds)
        semantic_status = str(payload.get("data_status") or payload.get("status") or "").lower()
        limitation = payload.get("available") is False or semantic_status in {
            "partial",
            "blocked",
            "unavailable",
            "missing",
            "not_available",
        }
        note = str(payload.get("data_note") or payload.get("message") or "")
        if any(marker in note.lower() for marker in ("недоступ", "не подключ", "не выдали доступ")):
            limitation = True
        ok = http_status == 200 and payload.get("ok") is not False
        return {
            **case,
            "ok": ok,
            "limitation": limitation,
            "http": http_status,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "available": payload.get("available"),
            "data_status": payload.get("data_status"),
            "status": payload.get("status"),
            "note": note[:300],
            "payload_keys": sorted(payload.keys())[:20],
        }
    except Exception as exc:
        return {
            **case,
            "ok": False,
            "limitation": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    _, health = get_json("/api/health")
    cases = []
    for client in health.get("clients", []):
        marketplaces = client.get("marketplaces") or ["ozon"]
        for report in client.get("reports", []):
            endpoint = REPORT_ENDPOINTS.get(report)
            if not endpoint:
                continue
            if report.startswith("avito"):
                marketplace = "avito"
            elif report.startswith("yandex"):
                marketplace = "yandex_market"
            elif report.startswith("wb"):
                marketplace = "wb"
            else:
                marketplace = marketplaces[0]
            cases.append(
                {
                    "client": client["key"],
                    "report": report,
                    "marketplace": marketplace,
                    "endpoint": endpoint,
                }
            )

    total = len(cases)
    started = time.monotonic()
    print(
        f"ПЛАН: {total} API-проверок; до {MAX_WORKERS} параллельно; "
        f"таймаут {TIMEOUT_SECONDS}s; период {TARGET_FROM}—{TARGET_TO}",
        flush=True,
    )
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run_case, case): case for case in cases}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            elapsed = time.monotonic() - started
            eta = (elapsed / index * (total - index)) if index else 0
            state = "OK" if result["ok"] else "ERROR"
            if result.get("limitation"):
                state = "LIMITATION"
            print(
                f"ПРОГРЕСС: {index}/{total} ({index / total * 100:.1f}%) | "
                f"{result['client']}:{result['report']} | {state} | "
                f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

    first_pass_failures = [item for item in results if not item["ok"]]
    if first_pass_failures:
        print(
            f"ПОВТОР: {len(first_pass_failures)} ошибок проверяются последовательно; "
            f"таймаут {RETRY_TIMEOUT_SECONDS}s",
            flush=True,
        )
        for failed in first_pass_failures:
            retry_case = {
                key: failed[key]
                for key in ("client", "report", "marketplace", "endpoint")
            }
            retried = run_case(retry_case, RETRY_TIMEOUT_SECONDS)
            retried["retried"] = True
            results[results.index(failed)] = retried
            print(
                f"ПОВТОР: {retry_case['client']}:{retry_case['report']} | "
                f"{'OK' if retried['ok'] else 'ERROR'} | "
                f"elapsed={retried['elapsed_ms'] / 1000:.1f}s",
                flush=True,
            )

    failures = [item for item in results if not item["ok"]]
    limitations = [item for item in results if item.get("limitation")]
    summary = {
        "total": total,
        "ok": total - len(failures),
        "failed": len(failures),
        "limitations": len(limitations),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "failures": failures,
        "limitation_items": limitations,
        "results": sorted(results, key=lambda item: (item["client"], item["report"])),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ИТОГО: " + json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"АРТЕФАКТ: {OUTPUT_PATH.resolve()}", flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
