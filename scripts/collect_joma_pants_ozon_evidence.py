from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "019f9db9-38fc-7563-9294-704b706fbc17"
CACHE = OUTPUT / "mpstats_competitor_cache" / "ozon_detail"
ARTIFACT = ROOT / "artifacts" / "sportmaster" / "demix_pants_ozon"
PARSER_DIR = Path(r"D:\Codex\New project 2\scripts")
sys.path.insert(0, str(PARSER_DIR))

import ozon_search_items_parser as ozon  # type: ignore  # noqa: E402


NICHE_ID = 7552
DATE_FROM = "2026-04-29"
DATE_TO = "2026-07-28"
PRICE_LOW = 1700.0
PRICE_HIGH = 2900.0
CANDIDATES = ("2939814345", "2939818544", "2939814349")
TARGET = ARTIFACT / "joma_candidate_evidence.json"
VISUAL_AUDIT = {
    "2939814345": {
        "pass": True,
        "summary": "Темно-синие зауженные спортивные брюки; технический силуэт, без классических деталей.",
    },
    "2939818544": {
        "pass": True,
        "summary": "Темно-синие спортивные джоггеры с манжетами; визуально спортивные, но более теплый сезонный слой.",
    },
    "2939814349": {
        "pass": True,
        "summary": "Черные зауженные спортивные брюки; наиболее близкая к Demix цветовая база, без классических деталей.",
    },
}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_brand_rows(token: str) -> list[dict]:
    params = {
        "path": NICHE_ID,
        "d1": DATE_FROM,
        "d2": DATE_TO,
        "fbs": 0,
        "startRow": 0,
        "endRow": 500,
    }
    body = {
        "startRow": 0,
        "endRow": 500,
        "filterModel": {
            "brand": {"filterType": "text", "type": "contains", "filter": "Joma"}
        },
        "sortModel": [{"colId": "revenue", "sort": "desc"}],
    }
    response = ozon.api_post(f"{ozon.API_BASE}/niche/items", token, params, body) or {}
    return response.get("data") or []


def detail_payload(params: dict) -> dict:
    attrs = {
        str(key): value if isinstance(value, list) else [value]
        for key, value in params.items()
        if value not in (None, "", [])
    }
    return {
        "status": "ok",
        "source": "MPStats Ozon versions",
        "description": str(params.get("Аннотация") or params.get("Описание") or "").strip(),
        "attributes": attrs,
    }


def flatten(detail: dict) -> str:
    return " ".join(
        [
            str(detail.get("description") or ""),
            json.dumps(detail.get("attributes") or {}, ensure_ascii=False),
        ]
    ).casefold()


def attr(detail: dict, name: str) -> list[str]:
    value = (detail.get("attributes") or {}).get(name) or []
    if not isinstance(value, list):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def compact_market(row: dict) -> dict:
    return {
        "sku": str(row.get("id") or ""),
        "brand": row.get("brand"),
        "title": row.get("name"),
        "url": row.get("url") or f"https://www.ozon.ru/context/detail/id/{row.get('id')}/",
        "image": row.get("thumb_middle"),
        "seller": row.get("seller"),
        "final_price": row.get("final_price"),
        "price_median": row.get("final_price_median"),
        "price_average": row.get("final_price_average"),
        "price_min": row.get("final_price_min"),
        "price_max": row.get("final_price_max"),
        "sales": row.get("sales"),
        "revenue": row.get("revenue"),
        "rating": row.get("rating"),
        "comments": row.get("comments"),
        "days_in_stock": row.get("days_in_stock"),
        "days_with_sales": row.get("days_with_sales"),
    }


def assess(market: dict, detail: dict) -> dict:
    text = flatten(detail)
    gender = attr(detail, "Пол")
    audience_pass = any("муж" in item.casefold() for item in gender)
    youth_flag = bool(re.search(r"детск|мальчик|подрост|юнош", text))
    classic_flag = bool(re.search(r"делов|классич|офис|old money|банан|чинос", text))
    style = attr(detail, "Стиль")
    sport_flag = any("спорт" in item.casefold() for item in style) or bool(
        re.search(r"спорт|трениров|футбол|бег|фитнес|волейбол|training", text)
    )
    scenario_hits: list[str] = []
    for label, pattern in (
        ("тренировки", r"трениров|training"),
        ("футбол", r"футбол|football"),
        ("бег", r"\bбег\w*|running"),
        ("фитнес", r"фитнес|fitness"),
        ("волейбол", r"волейбол|volleyball"),
    ):
        if re.search(pattern, text):
            scenario_hits.append(label)
    functions: list[str] = []
    for label, pattern in (
        ("влагоотведение / быстрое высыхание", r"влаго|быстросох|quick.?dry|отвод.*влаг"),
        ("свобода движений / эластичность", r"эласт|спандекс|эластан|свобод.*движ"),
        ("вентиляция / воздухопроницаемость", r"вентил|дыша|воздухопрон"),
        ("регулируемая посадка", r"шнур|кулис|регулируем.*посад"),
        ("карманы", r"карман"),
    ):
        if re.search(pattern, text):
            functions.append(label)
    p50 = float(market.get("price_median") or 0)
    final_price = float(market.get("final_price") or 0)
    price_pass = PRICE_LOW <= p50 <= PRICE_HIGH
    final_price_pass = PRICE_LOW <= final_price <= PRICE_HIGH
    sales = float(market.get("sales") or 0)
    score = 0.0
    score += 30 if audience_pass and not youth_flag else 0
    score += 25 if sport_flag and not classic_flag else 0
    score += 20 if price_pass else 0
    score += min(10.0, math.log1p(sales) * 3.0)
    score += min(10.0, len(scenario_hits) * 3.0)
    score += min(5.0, len(functions))
    hard_pass = audience_pass and not youth_flag and sport_flag and not classic_flag and price_pass
    return {
        "adult_men_gate": {
            "pass": audience_pass and not youth_flag,
            "gender_attribute": gender,
            "youth_wording_detected": youth_flag,
        },
        "sport_semantic_gate": {
            "pass": sport_flag and not classic_flag,
            "style_attribute": style,
            "classic_or_business_wording_detected": classic_flag,
        },
        "price_gate": {
            "pass": price_pass,
            "corridor": [PRICE_LOW, PRICE_HIGH],
            "period_median": p50,
            "last_observed_price": final_price,
            "last_observed_price_pass": final_price_pass,
        },
        "scenarios": scenario_hits,
        "functions": functions,
        "material": attr(detail, "Состав материала") or attr(detail, "Материал"),
        "color": attr(detail, "Цвет товара") or attr(detail, "Название цвета"),
        "model": attr(detail, "Модель"),
        "fit": attr(detail, "Покрой"),
        "hard_pass": hard_pass,
        "score": round(score, 1),
    }


def main() -> int:
    started = time.monotonic()
    CACHE.mkdir(parents=True, exist_ok=True)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    total_steps = 1 + len(CANDIDATES)
    errors: list[dict] = []
    items: list[dict] = []
    completed = 0
    print(
        "ПЛАН: "
        f"1 брендовый MPStats-запрос + {len(CANDIDATES)} detail-проверки; "
        f"всего шагов={total_steps}; ниша={NICHE_ID}; период={DATE_FROM}..{DATE_TO}; "
        "пауза между API-вызовами=0.9 с; токен и сырые ответы в лог не выводятся.",
        flush=True,
    )
    token = ozon.load_token(None, ozon.DEFAULT_TOKEN_SOURCE)
    try:
        rows = fetch_brand_rows(token)
        by_sku = {str(row.get("id")): row for row in rows}
        status = f"ok rows={len(rows)}"
    except Exception as exc:  # noqa: BLE001
        rows = []
        by_sku = {}
        errors.append({"stage": "brand_query", "error": str(exc)})
        status = f"error:{type(exc).__name__}"
    completed += 1
    elapsed = time.monotonic() - started
    eta = elapsed / completed * (total_steps - completed)
    print(
        f"ПРОГРЕСС: {completed}/{total_steps} ({completed / total_steps:.0%}) | "
        f"Joma brand pool | {status} | accumulated=0 | errors={len(errors)} | "
        f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
        flush=True,
    )
    time.sleep(0.9)

    for sku in CANDIDATES:
        market_row = by_sku.get(sku)
        try:
            if market_row is None:
                raise RuntimeError(f"SKU {sku} не найден в Joma pool ({len(rows)} строк)")
            cache_file = CACHE / f"{sku}.json"
            if cache_file.exists():
                detail = json.loads(cache_file.read_text(encoding="utf-8"))
                detail_source = "cache"
            else:
                detail = detail_payload(ozon.fetch_params(int(sku), token))
                write_json(cache_file, detail)
                detail_source = "api"
            if detail.get("status") != "ok":
                raise RuntimeError(detail.get("error") or "detail status is not ok")
            market = compact_market(market_row)
            item = {
                "market": market,
                "detail": detail,
                "assessment": assess(market, detail),
                "visual_audit": VISUAL_AUDIT[sku],
            }
            items.append(item)
            status = f"ok source={detail_source} hard_pass={item['assessment']['hard_pass']}"
        except Exception as exc:  # noqa: BLE001
            errors.append({"stage": "detail", "sku": sku, "error": str(exc)})
            status = f"error:{type(exc).__name__}"
        completed += 1
        elapsed = time.monotonic() - started
        eta = elapsed / completed * (total_steps - completed)
        print(
            f"ПРОГРЕСС: {completed}/{total_steps} ({completed / total_steps:.0%}) | "
            f"Joma sku={sku} | {status} | accumulated={len(items)} | errors={len(errors)} | "
            f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
            flush=True,
        )
        if "source=api" in status:
            time.sleep(0.9)

    eligible = [item for item in items if item["assessment"]["hard_pass"]]
    eligible.sort(
        key=lambda item: (
            bool(item["assessment"]["price_gate"]["last_observed_price_pass"]),
            float(item["assessment"]["score"]),
            float(item["market"].get("sales") or 0),
            float(item["market"].get("revenue") or 0),
        ),
        reverse=True,
    )
    recommended = eligible[0] if eligible else None
    recommendation = None
    if recommended:
        market = recommended["market"]
        assessment = recommended["assessment"]
        recommendation = {
            "sku": market["sku"],
            "decision": "direct_strict_benchmark",
            "reason": (
                "Проходит все hard gates: взрослый мужской товар, спортивно-тренировочный сценарий, "
                "медианная и последняя наблюдаемая цены находятся в коридоре Demix; hero-изображение "
                "подтверждает технический спортивный силуэт без классических деталей. Приоритет отдан "
                "полной ценовой сопоставимости, а не максимальному объему продаж внутри одной модели."
            ),
            "score": assessment["score"],
        }
    payload = {
        "schemaVersion": "sportmaster_demix_pants_ozon_joma_v1",
        "status": "partial" if errors else "ok",
        "scope": "Demix / Брюки / Ozon / adult men",
        "source": {
            "provider": "MPStats",
            "dataset": "Ozon niche/items + Ozon versions",
            "niche_id": NICHE_ID,
            "date_from": DATE_FROM,
            "date_to": DATE_TO,
            "fbs": 0,
        },
        "price_corridor": {"low": PRICE_LOW, "high": PRICE_HIGH},
        "candidates": items,
        "recommendation": recommendation,
        "errors": errors,
    }
    write_json(TARGET, payload)
    elapsed = time.monotonic() - started
    print(
        "ИТОГО: "
        f"checked={len(items)}/{len(CANDIDATES)}; hard_pass={len(eligible)}; "
        f"recommended={recommendation['sku'] if recommendation else 'none'}; "
        f"errors={len(errors)}; файл={TARGET}; elapsed={elapsed:.1f}s; "
        f"run_status={'partial' if errors else 'complete'}.",
        flush=True,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
