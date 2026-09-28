#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Secret-safe read-only contract probe for KM Trade Ozon finance API."""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


BASE_URL = "https://api-seller.ozon.ru"
TARGET_CLIENT = "km_trade"
MIN_INTERVAL_SECONDS = 1.2
HTTP_TIMEOUT_SECONDS = 60


def credentials() -> tuple[str, str]:
    client_id, api_key = app.ozon_seo_credentials_value(TARGET_CLIENT)
    if not client_id or not api_key:
        raise RuntimeError("В UI не сохранены Seller API ключи KM Trade")
    return client_id, api_key


def safe_error(payload: bytes) -> str:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except Exception:
        return "не-JSON ошибка"
    if not isinstance(value, dict):
        return type(value).__name__
    parts = []
    for key in ("code", "message", "details"):
        item = value.get(key)
        if item not in (None, "", [], {}):
            parts.append(f"{key}={str(item)[:500]}")
    return " | ".join(parts) or "keys=" + ",".join(sorted(value))


def shape(value: Any, depth: int = 0) -> str:
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            child = value[key]
            if isinstance(child, dict):
                parts.append(f"{key}{{{shape(child, depth + 1)}}}")
            elif isinstance(child, list):
                nested = shape(child[0], depth + 1) if child else "empty"
                parts.append(f"{key}[{len(child)}]:{nested}")
            else:
                parts.append(f"{key}:{type(child).__name__}")
        return ", ".join(parts)
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def nested_key_inventory(payload: Any) -> str:
    accruals = payload.get("accruals", []) if isinstance(payload, dict) else []
    inventory: dict[str, set[str]] = {
        "accrual": set(),
        "posting": set(),
        "posting.delivery": set(),
        "posting.delivery.services[]": set(),
        "item_fees": set(),
        "item_fees.fees[]": set(),
        "non_item_fee": set(),
        "container_fees": set(),
    }
    categories: dict[str, int] = {}
    for item in accruals:
        if not isinstance(item, dict):
            continue
        inventory["accrual"].update(map(str, item))
        category = str(item.get("accrued_category") or "EMPTY")
        categories[category] = categories.get(category, 0) + 1
        posting = item.get("posting")
        if isinstance(posting, dict):
            inventory["posting"].update(map(str, posting))
            delivery = posting.get("delivery")
            if isinstance(delivery, dict):
                inventory["posting.delivery"].update(map(str, delivery))
                for service in delivery.get("services") or []:
                    if isinstance(service, dict):
                        inventory["posting.delivery.services[]"].update(map(str, service))
        item_fees = item.get("item_fees")
        if isinstance(item_fees, dict):
            inventory["item_fees"].update(map(str, item_fees))
            for fee in item_fees.get("fees") or []:
                if isinstance(fee, dict):
                    inventory["item_fees.fees[]"].update(map(str, fee))
        non_item = item.get("non_item_fee")
        if isinstance(non_item, dict):
            inventory["non_item_fee"].update(map(str, non_item))
        container = item.get("container_fees")
        if isinstance(container, dict):
            inventory["container_fees"].update(map(str, container))
    pieces = [
        "categories=" + ",".join(f"{key}:{value}" for key, value in sorted(categories.items()))
    ]
    pieces.extend(
        f"{name}=" + (",".join(sorted(keys)) if keys else "-")
        for name, keys in inventory.items()
    )
    if isinstance(payload, dict):
        pieces.append(f"has_more={bool(payload.get('last_id'))}")
    return " | ".join(pieces)


def request(path: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(
            Request(
                BASE_URL + path,
                data=body,
                headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            ),
            timeout=HTTP_TIMEOUT_SECONDS,
        ) as response:
            return response.status, json.loads(response.read().decode("utf-8-sig"))
    except HTTPError as exc:
        return exc.code, safe_error(exc.read())


def main() -> int:
    started = time.monotonic()
    probe_day = date.today() - timedelta(days=2)
    month_start = probe_day.replace(day=1)
    client_id, api_key = credentials()
    headers = {"Client-Id": client_id, "Api-Key": api_key}
    probes: list[tuple[str, str, list[dict[str, Any]]]] = [
        (
            "legacy-transactions",
            "/v3/finance/transaction/list",
            [
                {
                    "filter": {
                        "date": {
                            "from": f"{probe_day.isoformat()}T00:00:00.000Z",
                            "to": f"{probe_day.isoformat()}T23:59:59.999Z",
                        },
                        "operation_type": [],
                        "posting_number": "",
                        "transaction_type": "all",
                    },
                    "page": 1,
                    "page_size": 10,
                }
            ],
        ),
        (
            "accrual-by-day",
            "/v1/finance/accrual/by-day",
            [{"date": probe_day.isoformat()}],
        ),
        (
            "accrual-types",
            "/v1/finance/accrual/types",
            [{}, {"page": 1, "page_size": 10}],
        ),
        (
            "accrual-postings",
            "/v1/finance/accrual/postings",
            [
                {
                    "date": {"from": month_start.isoformat(), "to": probe_day.isoformat()},
                    "page": 1,
                    "page_size": 10,
                },
                {
                    "filter": {
                        "date": {"from": month_start.isoformat(), "to": probe_day.isoformat()}
                    },
                    "page": 1,
                    "page_size": 10,
                },
            ],
        ),
        (
            "product-prices",
            "/v5/product/info/prices",
            [{"filter": {"visibility": "ALL"}, "cursor": "", "limit": 10}],
        ),
    ]
    attempts_total = sum(len(items) for _, _, items in probes)
    print(
        f"ПЛАН: KM Trade | read-only POST | методов {len(probes)} | "
        f"до {attempts_total} запросов | пауза не менее {MIN_INTERVAL_SECONDS:.1f} сек. | "
        f"дата проверки {probe_day} | секреты и значения товаров не выводятся.",
        flush=True,
    )
    attempts = 0
    successes = 0
    last_request_at = 0.0
    for probe_index, (name, path, bodies) in enumerate(probes, start=1):
        succeeded = False
        for body_index, body in enumerate(bodies, start=1):
            remaining = MIN_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
            if remaining > 0:
                print(
                    f"ПРОГРЕСС: лимит API | {name} | пауза {remaining:.1f} сек. | "
                    f"выполнено запросов {attempts}",
                    flush=True,
                )
                time.sleep(remaining)
            attempts += 1
            print(
                f"ПРОГРЕСС: метод {probe_index}/{len(probes)} | {name} | "
                f"форма {body_index}/{len(bodies)} | запрос {attempts}/{attempts_total}",
                flush=True,
            )
            status, payload = request(path, body, headers)
            last_request_at = time.monotonic()
            if status == 200:
                print(f"CONTRACT {name}: HTTP 200 | {shape(payload)}", flush=True)
                if name == "accrual-by-day":
                    print(
                        f"CONTRACT {name} INVENTORY: {nested_key_inventory(payload)}",
                        flush=True,
                    )
                successes += 1
                succeeded = True
                break
            print(f"CONTRACT {name}: HTTP {status} | {payload}", flush=True)
        if not succeeded:
            print(f"CONTRACT {name}: подходящая форма запроса не подтверждена", flush=True)
    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: KM Trade | read_only=true | methods={len(probes)} | "
        f"successes={successes} | requests={attempts} | errors={len(probes) - successes} | "
        f"elapsed={elapsed:.1f}s | stopped=false | partial={successes != len(probes)}",
        flush=True,
    )
    return 0 if successes >= 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
