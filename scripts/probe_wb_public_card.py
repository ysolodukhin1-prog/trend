#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


def main() -> None:
    nm_id = int(sys.argv[1]) if len(sys.argv) > 1 else 473368082
    vol = nm_id // 100000
    part = nm_id // 1000
    hosts = list(range(1, 41))
    print(
        f"ПЛАН: SKU {nm_id} | CDN-хостов {len(hosts)} | "
        "поиск публичного card.json | таймаут 3 сек",
        flush=True,
    )
    started = time.monotonic()
    headers = {"User-Agent": "Mozilla/5.0"}
    for index, host in enumerate(hosts, start=1):
        url = (
            f"https://basket-{host:02d}.wbbasket.ru/"
            f"vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        )
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = response.read()
            data = json.loads(payload.decode("utf-8"))
            print(
                f"НАЙДЕНО: host={host:02d} | url={url} | "
                f"ключи={list(data)[:20]}",
                flush=True,
            )
            print(json.dumps(data, ensure_ascii=False)[:4000], flush=True)
            return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            pass
        if index % 5 == 0:
            elapsed = time.monotonic() - started
            print(
                f"ПРОГРЕСС: {index}/{len(hosts)} ({index / len(hosts):.0%}) | "
                f"прошло {elapsed:.1f} сек",
                flush=True,
            )
    print(
        f"ИТОГ: card.json не найден | проверено {len(hosts)} | "
        f"{time.monotonic() - started:.1f} сек",
        flush=True,
    )


if __name__ == "__main__":
    main()
