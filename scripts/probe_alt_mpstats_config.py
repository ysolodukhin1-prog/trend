from __future__ import annotations

import re
import urllib.error
from pathlib import Path

from collect_demix_tshirts_ozon_competitors import fetch_niche_page


SOURCE = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Парсеры\Парсер позиций по ключам\WB\mpstats_parser.py"
)


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^\s*TOKEN\s*=\s*([\"'])(.+?)\1", text, re.MULTILINE)
    if not match:
        print("status=config_missing")
        return 2
    try:
        rows = fetch_niche_page(match.group(2), 7559, 500, 1000)
    except urllib.error.HTTPError as exc:
        print(f"status=http_{exc.code}")
        return 1
    print(f"status=ok rows={len(rows)} first_id={rows[0].get('id') if rows else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
