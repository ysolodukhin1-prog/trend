#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPORT_ENDPOINTS = {
    "abc": ["/api/summary", "/api/stats"],
    "product": ["/api/product-summary", "/api/product-stats"],
    "sku": ["/api/sku-summary", "/api/sku-stats"],
    "adv": ["/api/adv-summary", "/api/adv-daily", "/api/adv-stats", "/api/adv-waterfalls"],
    "mediaAdv": ["/api/media-adv-summary", "/api/media-adv-daily", "/api/media-adv-stats", "/api/media-adv-waterfalls"],
    "funnel": ["/api/funnel-summary", "/api/funnel-daily", "/api/funnel-stats"],
    "weeklyDynamics": ["/api/funnel-summary", "/api/funnel-daily", "/api/weekly-dynamics"],
    "planfact": ["/api/planfact-summary", "/api/planfact-daily", "/api/planfact-monthly", "/api/planfact-scorecard"],
    "seoMonitoring": [],
}

CHART_EXPORT_DASHBOARDS = {"abc", "product", "sku", "adv", "mediaAdv", "funnel"}


def iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def bounded_range(filters: dict) -> tuple[str, str]:
    available_from = iso_date(str(filters.get("date_from") or ""))
    available_to = iso_date(str(filters.get("date_to") or ""))
    if not available_to:
        return "", ""
    start = max(available_from or available_to, available_to - timedelta(days=29))
    return start.isoformat(), available_to.isoformat()


def request(base_url: str, path: str, params: dict | None, timeout: int, payload: dict | None = None):
    query = urlencode(params or {}, doseq=True)
    url = f"{base_url.rstrip('/')}{path}" + (f"?{query}" if query else "")
    body = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    started = time.monotonic()
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
            raw = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
        content_type = exc.headers.get("Content-Type", "")
    elapsed = time.monotonic() - started
    return url, status, content_type, raw, elapsed


def json_request(base_url: str, path: str, params: dict | None, timeout: int):
    url, status, content_type, raw, elapsed = request(base_url, path, params, timeout)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {raw[:300].decode('utf-8', errors='replace')}")
    if "json" not in content_type.lower():
        raise RuntimeError(f"content-type={content_type or 'missing'}")
    payload = json.loads(raw.decode("utf-8"))
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(str(payload.get("error") or "ok=false"))
    return payload, elapsed


def marketplace_ids(filters: dict) -> list[str]:
    values = [str(item.get("id")) for item in filters.get("marketplaces", []) if item.get("id")]
    return values or [str(filters.get("marketplace") or "ozon")]


def report_params(client: str, report: str, marketplace: str, filters: dict) -> dict:
    date_from, date_to = bounded_range(filters)
    params = {
        "client": client,
        "dashboard": report,
        "marketplace": marketplace,
        "page": "1",
        "limit": "2",
        "per_page": "2",
        "category_level": "category",
        "period_group": "day",
    }
    if date_from and date_to:
        params["date_from"] = date_from
        params["date_to"] = date_to
    if report == "mediaAdv" and marketplace == "wb":
        params["media_level"] = "campaign"
    return params


def detail(payload) -> str:
    if not isinstance(payload, dict):
        return type(payload).__name__
    if "rows" in payload:
        return f"rows={len(payload.get('rows') or [])}, total={payload.get('total', '-')}"
    if "category_names" in payload:
        return f"categories={len(payload.get('category_names') or [])}"
    keys = [key for key in ("days_count", "sku_count", "orders_qty", "expense_rub") if key in payload]
    return ", ".join(f"{key}={payload.get(key)}" for key in keys) or f"keys={len(payload)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Public legacy BI smoke matrix for all active clients and reports.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8052")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--skip-exports", action="store_true")
    parser.add_argument("--client", action="append", default=[], help="Limit checks to a client key; repeatable")
    args = parser.parse_args()

    started = time.monotonic()
    errors: list[str] = []
    checks = 0

    health, elapsed = json_request(args.base_url, "/api/health", {"client": "gloria_jeans"}, args.timeout)
    clients = [item for item in health.get("clients", []) if item.get("status") == "active"]
    requested_clients = {value.strip() for value in args.client if value.strip()}
    if requested_clients:
        available_client_keys = {str(item.get("key")) for item in clients}
        unknown_clients = sorted(requested_clients - available_client_keys)
        if unknown_clients:
            parser.error("unknown or inactive client(s): " + ", ".join(unknown_clients))
        clients = [item for item in clients if str(item.get("key")) in requested_clients]
    report_total = sum(len(item.get("reports") or []) for item in clients)
    print(
        f"ПЛАН: clients={len(clients)} | report configurations={report_total} | "
        f"phases=discovery, JSON API, read-only admin, exports={'off' if args.skip_exports else 'on'} | "
        f"timeout={args.timeout}s | pauses=none",
        flush=True,
    )
    print(f"ПРОГРЕСС: health | OK {elapsed:.2f}s | clients={len(clients)}", flush=True)

    combos: list[tuple[str, str, str, dict]] = []
    discovery_total = report_total
    discovery_index = 0
    for client in clients:
        client_key = str(client["key"])
        for report in client.get("reports") or []:
            discovery_index += 1
            try:
                filters, elapsed = json_request(
                    args.base_url,
                    "/api/filters",
                    {"client": client_key, "dashboard": report, "marketplace": "ozon"},
                    args.timeout,
                )
                markets = marketplace_ids(filters)
                for marketplace in markets:
                    exact_filters = filters
                    if marketplace != str(filters.get("marketplace") or "ozon"):
                        exact_filters, _ = json_request(
                            args.base_url,
                            "/api/filters",
                            {"client": client_key, "dashboard": report, "marketplace": marketplace},
                            args.timeout,
                        )
                    combos.append((client_key, report, marketplace, exact_filters))
                print(
                    f"ПРОГРЕСС: discovery {discovery_index}/{discovery_total} "
                    f"({discovery_index / discovery_total:.0%}) | {client_key}/{report} | "
                    f"marketplaces={','.join(markets)} | {elapsed:.2f}s",
                    flush=True,
                )
            except Exception as exc:
                message = f"discovery {client_key}/{report}: {exc}"
                errors.append(message)
                print(f"ПРОГРЕСС: discovery {discovery_index}/{discovery_total} | ОШИБКА {message}", flush=True)

    endpoint_total = sum(len(REPORT_ENDPOINTS.get(report, [])) for _, report, _, _ in combos)
    endpoint_index = 0
    for client_key, report, marketplace, filters in combos:
        params = report_params(client_key, report, marketplace, filters)
        for path in REPORT_ENDPOINTS.get(report, []):
            endpoint_index += 1
            checks += 1
            try:
                payload, elapsed = json_request(args.base_url, path, params, args.timeout)
                print(
                    f"ПРОГРЕСС: api {endpoint_index}/{endpoint_total} "
                    f"({endpoint_index / max(endpoint_total, 1):.0%}) | "
                    f"{client_key}/{report}/{marketplace} {path.removeprefix('/api/')} | "
                    f"OK {detail(payload)} | {elapsed:.2f}s",
                    flush=True,
                )
            except Exception as exc:
                message = f"{client_key}/{report}/{marketplace} {path}: {exc}"
                errors.append(message)
                print(f"ПРОГРЕСС: api {endpoint_index}/{endpoint_total} | ОШИБКА {message}", flush=True)

    for index, client in enumerate(clients, start=1):
        client_key = str(client["key"])
        checks += 1
        try:
            payload, elapsed = json_request(args.base_url, "/api/admin/imports", {"client": client_key}, args.timeout)
            print(
                f"ПРОГРЕСС: admin {index}/{len(clients)} | {client_key} | "
                f"OK jobs={len(payload.get('rows') or [])} | {elapsed:.2f}s",
                flush=True,
            )
        except Exception as exc:
            message = f"{client_key} /api/admin/imports: {exc}"
            errors.append(message)
            print(f"ПРОГРЕСС: admin {index}/{len(clients)} | ОШИБКА {message}", flush=True)

    if not args.skip_exports:
        export_combos = [combo for combo in combos if combo[1] in CHART_EXPORT_DASHBOARDS or combo[1] == "weeklyDynamics"]
        for index, (client_key, report, marketplace, filters) in enumerate(export_combos, start=1):
            params = report_params(client_key, report, marketplace, filters)
            params["dashboard"] = report
            path = "/api/weekly-dynamics-export" if report == "weeklyDynamics" else "/api/chart-export"
            payload = (
                {"query": urlencode(params), "kind": "trend"}
                if report == "weeklyDynamics"
                else {"query": urlencode({**params, "chart": "primary"}), "chart_image_data_url": ""}
            )
            checks += 1
            try:
                url, status, content_type, raw, elapsed = request(args.base_url, path, None, args.timeout, payload)
                if status != 200:
                    raise RuntimeError(f"HTTP {status}: {raw[:300].decode('utf-8', errors='replace')}")
                if not raw.startswith(b"PK"):
                    raise RuntimeError(f"not XLSX, content-type={content_type}, bytes={len(raw)}")
                print(
                    f"ПРОГРЕСС: export {index}/{len(export_combos)} | "
                    f"{client_key}/{report}/{marketplace} | OK xlsx={len(raw)} bytes | {elapsed:.2f}s",
                    flush=True,
                )
            except Exception as exc:
                message = f"export {client_key}/{report}/{marketplace}: {exc}"
                errors.append(message)
                print(f"ПРОГРЕСС: export {index}/{len(export_combos)} | ОШИБКА {message}", flush=True)

    elapsed_total = time.monotonic() - started
    print(
        f"ИТОГ: checks={checks} | combinations={len(combos)} | errors={len(errors)} | "
        f"elapsed={elapsed_total:.1f}s | stopped=no | partial={'yes' if errors else 'no'}",
        flush=True,
    )
    for message in errors:
        print(f"ОШИБКА: {message}", flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
