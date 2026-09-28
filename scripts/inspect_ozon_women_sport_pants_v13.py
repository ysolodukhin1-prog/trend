from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SOURCE = Path(r"D:\Codex\New project\outputs\019f9db9-38fc-7563-9294-704b706fbc17\mpstats_competitor_cache\ozon_niche\09c080da43fddec2e347384d594f8c0787b47724.json")
SPORT = re.compile(r"спорт|фитнес|йог|пилат|бег|тренир|gym|fitness|workout", re.I)
EXCLUDE = re.compile(r"девоч|детск|подрост|мужск|термо|утепл|начес|палацц|джоггер|карго|классич|домаш|пижам|беремен", re.I)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = []
    for item in payload.get("items") or []:
        title = str(item.get("name") or "")
        current = item.get("final_price")
        median = item.get("final_price_median")
        if not SPORT.search(title) or EXCLUDE.search(title):
            continue
        if not isinstance(current, (int, float)) or not 800 <= current <= 1500:
            continue
        if not isinstance(median, (int, float)) or not 800 <= median <= 1500:
            continue
        rows.append(
            {
                "sku": str(item.get("id") or ""),
                "brand": item.get("brand") or "",
                "title": title,
                "current": current,
                "median": median,
                "revenue": item.get("revenue"),
                "sales": item.get("sales"),
                "image": item.get("thumb_middle") or item.get("thumb") or "",
                "url": item.get("url") or "",
            }
        )
    rows.sort(key=lambda row: (-(row.get("revenue") or 0), row["sku"]))
    print(f"ПЛАН: 1 ниша, 500 top-SKU, критерии sport-title + взрослые + цена 800–1 500 ₽.")
    print(f"ПРОГРЕСС: 1/1 (100%) | найдено {len(rows)} | ошибок 0")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"ИТОГ: {len(rows)} потенциальных спортивных карточек до визуальной проверки.")


if __name__ == "__main__":
    main()
