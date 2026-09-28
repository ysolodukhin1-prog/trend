#!/usr/bin/env python3
"""Import the current Ozon assortment and product attributes via Seller API."""

from __future__ import annotations

import argparse
from http.client import IncompleteRead, RemoteDisconnected
import gzip
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
CLIENT_KEY = os.environ.get("DASHBOARD_CLIENT", "km_trade").strip().lower()
TARGET_DB = os.environ.get("DASHBOARD_DB_NAME", "km_trade_products").strip()
CLIENT_LABEL = os.environ.get("DASHBOARD_CLIENT_LABEL", CLIENT_KEY).strip()
CLIENT_SUFFIX = re.sub(r"[^A-Z0-9]+", "_", CLIENT_KEY.upper()).strip("_")
API_BASE_URL = os.environ.get("OZON_SELLER_API_BASE_URL", "https://api-seller.ozon.ru").rstrip("/")
PAGE_SIZE = 1000
MAX_RETRIES = 6
HTTP_TIMEOUT_SECONDS = 60
REQUEST_INTERVAL_SECONDS = float(os.environ.get("OZON_ASSORTMENT_API_INTERVAL_SECONDS", "1.0"))

os.environ["DASHBOARD_CLIENT"] = CLIENT_KEY
os.environ["DASHBOARD_DB_NAME"] = TARGET_DB
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

import app  # noqa: E402


class AssortmentPipelineError(RuntimeError):
    pass


COMMON_COLUMNS = [
    ("#Хештеги", "heshtegi"),
    ("Rich-контент JSON", "rich_kontent_json"),
    ("SKU", "sku"),
    ("Аннотация", "annotatsiya"),
    ("Артикул", "artikul"),
    ("Вес в упаковке, г", "ves_v_upakovke_g"),
    ("Высота упаковки, мм", "vysota_upakovki_mm"),
    ("Длина упаковки, мм", "dlina_upakovki_mm"),
    ("Количество заводских упаковок", "kolichestvo_zavodskih_upakovok"),
    ("Количество товара в УЕИ", "kolichestvo_tovara_v_uei"),
    ("Минимальное количество оптом", "minimalnoe_kolichestvo_optom"),
    ("НДС, %", "nds_pct"),
    ("Название товара", "nazvanie_tovara"),
    ("Объединить в похожие товары", "obedinit_v_pohozhie_tovary"),
    ("Рассрочка", "rassrochka"),
    ("Ссылка на главное фото", "ssylka_na_glavnoe_foto"),
    ("Ссылки на дополнительные фото", "ssylki_na_dopolnitelnye_foto"),
    ("Страна-изготовитель", "strana_izgotovitel"),
    ("Тип", "tip"),
    ("Ускоренный сбор отзывов", "uskorennyy_sbor_otzyvov"),
    ("Цена до скидки, руб.", "tsena_do_skidki_rub"),
    ("Цена, руб.", "tsena_rub"),
    ("Ширина упаковки, мм", "shirina_upakovki_mm"),
    ("Штрихкод (Серийный номер / EAN)", "shtrihkod_seriynyy_nomer_ean"),
]


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ").strip().rstrip("*")).casefold()


COMMON_FIELD_BY_ATTRIBUTE = {
    normalize_name(name): column for name, column in COMMON_COLUMNS
}
COMMON_FIELD_BY_ATTRIBUTE.update(
    {
        normalize_name("Название"): "nazvanie_tovara",
        normalize_name("Вес с упаковкой, г"): "ves_v_upakovke_g",
        normalize_name("Barcode"): "shtrihkod_seriynyy_nomer_ean",
    }
)


def text(value: Any) -> str:
    return str(value or "").strip()


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def seller_credentials() -> tuple[str, str]:
    env_values = app.read_app_env_file()
    client_id = os.environ.get(f"OZON_SELLER_CLIENT_ID_{CLIENT_SUFFIX}") or env_values.get(
        f"OZON_SELLER_CLIENT_ID_{CLIENT_SUFFIX}"
    )
    api_key = os.environ.get(f"OZON_SELLER_API_KEY_{CLIENT_SUFFIX}") or env_values.get(
        f"OZON_SELLER_API_KEY_{CLIENT_SUFFIX}"
    )
    if not client_id or not api_key:
        try:
            app.hydrate_registered_clients()
            client_id, api_key = app.ozon_seo_credentials_value(CLIENT_KEY)
        except Exception:
            client_id = client_id or ""
            api_key = api_key or ""
    if not client_id or not api_key:
        raise AssortmentPipelineError(f"Для {CLIENT_LABEL} не сохранены Seller API Client-Id/Api-Key")
    return client_id, api_key


def connect_target():
    try:
        app.hydrate_registered_clients()
    except Exception:
        pass
    config = dict(app.read_db_config(CLIENT_KEY))
    config["database"] = TARGET_DB
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    if actual != TARGET_DB:
        conn.close()
        raise AssortmentPipelineError(f"Ожидалась БД {TARGET_DB}, подключена {actual}")
    return conn


class SellerApiClient:
    def __init__(self) -> None:
        client_id, api_key = seller_credentials()
        self.headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
        }
        self.request_count = 0
        self.last_request_at = 0.0

    def _wait(self, label: str) -> None:
        remaining = REQUEST_INTERVAL_SECONDS - (time.monotonic() - self.last_request_at)
        if remaining > 0:
            print(
                f"ПРОГРЕСС: лимит API | {label} | пауза {remaining:.1f} сек. | "
                f"запросов {self.request_count}",
                flush=True,
            )
            time.sleep(remaining)

    def post(self, path: str, payload: dict[str, Any], *, label: str) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait(label)
            self.request_count += 1
            try:
                with urlopen(
                    Request(API_BASE_URL + path, data=body, headers=self.headers, method="POST"),
                    timeout=HTTP_TIMEOUT_SECONDS,
                ) as response:
                    raw = response.read()
                    content_encoding = str(response.headers.get("Content-Encoding") or "").lower()
                if content_encoding == "gzip":
                    raw = gzip.decompress(raw)
                self.last_request_at = time.monotonic()
                parsed = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(parsed, dict):
                    raise AssortmentPipelineError(f"{path} вернул не объект JSON")
                return parsed
            except HTTPError as exc:
                self.last_request_at = time.monotonic()
                retryable = exc.code == 429 or 500 <= exc.code < 600
                detail = exc.read().decode("utf-8", errors="replace")[:600]
                if not retryable or attempt >= MAX_RETRIES:
                    raise AssortmentPipelineError(f"Ozon API HTTP {exc.code} для {path}: {detail}") from exc
                try:
                    wait_seconds = max(float(exc.headers.get("Retry-After") or 0), 60 if exc.code == 429 else 2**attempt)
                except ValueError:
                    wait_seconds = 60 if exc.code == 429 else 2**attempt
                print(
                    f"ПРОГРЕСС: повтор API {attempt}/{MAX_RETRIES} | HTTP {exc.code} | "
                    f"{label} | ожидание {wait_seconds:.0f} сек. | запросов {self.request_count}",
                    flush=True,
                )
                time.sleep(min(wait_seconds, 120))
            except (
                URLError,
                TimeoutError,
                json.JSONDecodeError,
                IncompleteRead,
                RemoteDisconnected,
                gzip.BadGzipFile,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
            ) as exc:
                self.last_request_at = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise AssortmentPipelineError(f"Ошибка запроса {path}: {exc}") from exc
                wait_seconds = min(2**attempt, 60)
                print(
                    f"ПРОГРЕСС: повтор API {attempt}/{MAX_RETRIES} | сеть/JSON | "
                    f"{label} | ожидание {wait_seconds} сек.",
                    flush=True,
                )
                time.sleep(wait_seconds)
        raise AssortmentPipelineError(f"Исчерпаны повторы {path}")


def fetch_products(api: SellerApiClient, started: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_id = ""
    page = 0
    total = 0
    while True:
        page += 1
        payload = api.post(
            "/v4/product/info/attributes",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PAGE_SIZE, "sort_dir": "ASC"},
            label="ассортимент",
        )
        items = [item for item in (payload.get("result") or []) if isinstance(item, dict)]
        rows.extend(items)
        total = int(payload.get("total") or len(rows))
        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        elapsed = time.monotonic() - started
        eta = elapsed / page * max(total_pages - page, 0)
        print(
            f"ПРОГРЕСС: ассортимент | страница {page}/{total_pages} | получено {len(items):,} | "
            f"накоплено {len(rows):,}/{total:,} | запросов {api.request_count} | "
            f"step_pct={min(100, len(rows) / max(total, 1) * 100):.1f} | "
            f"elapsed={duration(elapsed)} | ETA={duration(eta)}",
            flush=True,
        )
        new_last_id = text(payload.get("last_id"))
        if not items or len(rows) >= total or not new_last_id or new_last_id == last_id:
            break
        last_id = new_last_id
    if total and len(rows) != total:
        raise AssortmentPipelineError(f"Получено {len(rows):,} товаров из ожидаемых {total:,}")
    if not rows:
        raise AssortmentPipelineError("Ozon вернул пустой ассортимент; существующий справочник не заменён")
    return rows


def fetch_prices(api: SellerApiClient, started: float) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    cursor = ""
    page = 0
    loaded = 0
    while True:
        page += 1
        payload = api.post(
            "/v5/product/info/prices",
            {"filter": {"visibility": "ALL"}, "cursor": cursor, "limit": PAGE_SIZE},
            label="цены ассортимента",
        )
        items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        loaded += len(items)
        for item in items:
            for key in (text(item.get("product_id")), text(item.get("offer_id"))):
                if key:
                    by_key[key] = item
        total = int(payload.get("total") or loaded)
        print(
            f"ПРОГРЕСС: цены ассортимента | страница {page} | получено {len(items):,} | "
            f"накоплено {loaded:,}/{total:,} | запросов {api.request_count} | "
            f"elapsed={duration(time.monotonic() - started)}",
            flush=True,
        )
        new_cursor = text(payload.get("cursor"))
        if not items or loaded >= total or not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor
    return by_key


def flatten_category_tree(nodes: Iterable[dict[str, Any]]) -> tuple[dict[int, str], dict[tuple[int, int], str]]:
    categories: dict[int, str] = {}
    types: dict[tuple[int, int], str] = {}

    def walk(items: Iterable[dict[str, Any]], current_category_id: int | None = None) -> None:
        for node in items:
            if not isinstance(node, dict):
                continue
            category_id = node.get("description_category_id") or current_category_id
            category_name = text(node.get("category_name"))
            if node.get("description_category_id") and category_name:
                categories[int(node["description_category_id"])] = category_name
            type_id = node.get("type_id")
            type_name = text(node.get("type_name"))
            if category_id and type_id and type_name:
                types[(int(category_id), int(type_id))] = type_name
            walk(node.get("children") or [], int(category_id) if category_id else current_category_id)

    walk(nodes)
    return categories, types


def fetch_category_tree(api: SellerApiClient) -> tuple[dict[int, str], dict[tuple[int, int], str]]:
    payload = api.post(
        "/v1/description-category/tree", {"language": "DEFAULT"}, label="дерево категорий"
    )
    return flatten_category_tree(payload.get("result") or [])


def fetch_attribute_definitions(
    api: SellerApiClient,
    pairs: list[tuple[int, int]],
    started: float,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    result: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for index, (category_id, type_id) in enumerate(pairs, start=1):
        payload = api.post(
            "/v1/description-category/attribute",
            {"description_category_id": category_id, "type_id": type_id, "language": "DEFAULT"},
            label=f"характеристики категории {category_id}/{type_id}",
        )
        result[(category_id, type_id)] = [
            item for item in (payload.get("result") or []) if isinstance(item, dict)
        ]
        elapsed = time.monotonic() - started
        eta = elapsed / index * (len(pairs) - index) if index else 0
        print(
            f"ПРОГРЕСС: справочники характеристик | {index}/{len(pairs)} | "
            f"атрибутов {len(result[(category_id, type_id)]):,} | запросов {api.request_count} | "
            f"step_pct={index / max(len(pairs), 1) * 100:.1f} | elapsed={duration(elapsed)} | ETA={duration(eta)}",
            flush=True,
        )
    return result


def attribute_value(attribute: dict[str, Any]) -> str:
    values: list[str] = []
    for value in attribute.get("values") or []:
        if isinstance(value, dict):
            clean = text(value.get("value"))
        else:
            clean = text(value)
        if clean and clean not in values:
            values.append(clean)
    return "; ".join(values)


def complex_attributes(items: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for item in items:
        if isinstance(item, dict):
            if item.get("id") is not None and isinstance(item.get("values"), list):
                yield item
            for value in item.values():
                if isinstance(value, (dict, list)):
                    yield from complex_attributes([value] if isinstance(value, dict) else value)
        elif isinstance(item, list):
            yield from complex_attributes(item)


def dimension_value(value: Any, unit: Any, *, weight: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    unit_name = text(unit).casefold()
    if weight:
        factors = {"kg": 1000, "кг": 1000, "g": 1, "г": 1, "mg": 0.001, "мг": 0.001}
    else:
        factors = {"m": 1000, "м": 1000, "cm": 10, "см": 10, "mm": 1, "мм": 1}
    result = number * factors.get(unit_name, 1)
    return str(int(result)) if result.is_integer() else f"{result:.3f}".rstrip("0").rstrip(".")


def image_urls(value: Any) -> list[str]:
    result: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            candidate = text(item.get("file_name") or item.get("url") or item.get("image_url"))
        else:
            candidate = text(item)
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def prepare_rows(
    products: list[dict[str, Any]],
    prices: dict[str, dict[str, Any]],
    categories: dict[int, str],
    types: dict[tuple[int, int], str],
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    product_rows: list[tuple[Any, ...]] = []
    attribute_rows: list[tuple[Any, ...]] = []
    category_attribute_map: dict[tuple[int, int], str] = {}
    name_by_pair: dict[tuple[int, int], dict[int, str]] = {}
    for pair, items in definitions.items():
        name_by_pair[pair] = {int(item["id"]): text(item.get("name")) for item in items if item.get("id") is not None}
        for item in items:
            if item.get("id") is None:
                continue
            name = text(item.get("name")) or str(item["id"])
            if normalize_name(name) in COMMON_FIELD_BY_ATTRIBUTE:
                continue
            category_attribute_map[(pair[0], int(item["id"]))] = name

    imported_at = datetime.now(timezone.utc)
    source_ref = f"api://seller/v4/product/info/attributes/{imported_at.isoformat()}"
    for row_number, item in enumerate(products, start=1):
        product_id = int(item["id"])
        category_id = int(item.get("description_category_id") or 0) or None
        type_id = int(item.get("type_id") or 0) or None
        pair = (category_id or 0, type_id or 0)
        attribute_names = name_by_pair.get(pair, {})
        fields = {column: "" for _name, column in COMMON_COLUMNS}
        fields.update(
            {
                "sku": text(item.get("sku")),
                "artikul": text(item.get("offer_id")),
                "nazvanie_tovara": text(item.get("name")),
                "ves_v_upakovke_g": dimension_value(item.get("weight"), item.get("weight_unit"), weight=True),
                "vysota_upakovki_mm": dimension_value(item.get("height"), item.get("dimension_unit")),
                "dlina_upakovki_mm": dimension_value(item.get("depth"), item.get("dimension_unit")),
                "shirina_upakovki_mm": dimension_value(item.get("width"), item.get("dimension_unit")),
                "tip": types.get(pair, str(type_id or "")),
            }
        )
        barcodes = image_urls(item.get("barcodes") or item.get("barcode"))
        fields["shtrihkod_seriynyy_nomer_ean"] = text(item.get("barcode")) or (barcodes[0] if barcodes else "")
        primary_image = text(item.get("primary_image"))
        images = image_urls(item.get("images"))
        fields["ssylka_na_glavnoe_foto"] = primary_image or (images[0] if images else "")
        fields["ssylki_na_dopolnitelnye_foto"] = "; ".join(url for url in images if url != fields["ssylka_na_glavnoe_foto"])

        values_by_attribute: dict[int, list[str]] = defaultdict(list)
        source_attributes = list(item.get("attributes") or []) + list(complex_attributes(item.get("complex_attributes") or []))
        for attribute in source_attributes:
            if not isinstance(attribute, dict) or attribute.get("id") is None:
                continue
            attribute_id = int(attribute["id"])
            value = attribute_value(attribute)
            if not value:
                continue
            name = attribute_names.get(attribute_id, str(attribute_id))
            common_field = COMMON_FIELD_BY_ATTRIBUTE.get(normalize_name(name))
            if common_field:
                if not fields.get(common_field):
                    fields[common_field] = value
                continue
            for part in (piece.strip() for piece in value.split(";")):
                if part and part not in values_by_attribute[attribute_id]:
                    values_by_attribute[attribute_id].append(part)
        for attribute_id, values in values_by_attribute.items():
            attribute_rows.append((product_id, attribute_id, "; ".join(values)))

        price_item = prices.get(str(product_id)) or prices.get(fields["artikul"]) or {}
        price = price_item.get("price") or {}
        fields["tsena_rub"] = text(price.get("price"))
        fields["tsena_do_skidki_rub"] = text(price.get("old_price"))
        fields["nds_pct"] = text(price.get("vat"))
        raw_json = {"attributes": item, "price": price_item}
        product_rows.append(
            (
                product_id,
                *[fields[column] or None for _name, column in COMMON_COLUMNS],
                category_id,
                categories.get(category_id or 0) or None,
                source_ref,
                row_number,
                imported_at,
                imported_at,
                product_id,
                category_id,
                type_id,
                Json(barcodes),
                Json(images),
                Json(item.get("model_info") or {}),
                Json(item.get("attributes") or []),
                Json(item.get("complex_attributes") or []),
                Json(raw_json),
                imported_at,
            )
        )

    common_rows = [(-index, name, column) for index, (name, column) in enumerate(COMMON_COLUMNS, start=1)]
    category_rows = [
        (category_id, attribute_id, name)
        for (category_id, attribute_id), name in sorted(category_attribute_map.items())
    ]
    return product_rows, attribute_rows, common_rows, category_rows


def ensure_schema(cur) -> None:
    required = (
        "ozon_cat_products",
        "ozon_cat_product_attributes",
        "ozon_cat_common_attributes",
        "ozon_cat_category_attributes",
    )
    for table in required:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            raise AssortmentPipelineError(f"В БД {TARGET_DB} отсутствует таблица public.{table}")
    cur.execute(
        """
        ALTER TABLE public.ozon_cat_products
            ADD COLUMN IF NOT EXISTS ozon_product_id bigint,
            ADD COLUMN IF NOT EXISTS description_category_id bigint,
            ADD COLUMN IF NOT EXISTS type_id bigint,
            ADD COLUMN IF NOT EXISTS barcodes_json jsonb,
            ADD COLUMN IF NOT EXISTS images_json jsonb,
            ADD COLUMN IF NOT EXISTS model_info_json jsonb,
            ADD COLUMN IF NOT EXISTS attributes_json jsonb,
            ADD COLUMN IF NOT EXISTS complex_attributes_json jsonb,
            ADD COLUMN IF NOT EXISTS raw_json jsonb,
            ADD COLUMN IF NOT EXISTS api_updated_at timestamptz
        """
    )


def existing_assortment_rows(conn) -> int:
    """Return the durable assortment size without changing schema or data."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.ozon_cat_products')")
        if cur.fetchone()[0] is None:
            return 0
        cur.execute("SELECT COUNT(*) FROM public.ozon_cat_products")
        return int(cur.fetchone()[0] or 0)


def store_rows(
    conn,
    product_rows: list[tuple[Any, ...]],
    attribute_rows: list[tuple[Any, ...]],
    common_rows: list[tuple[Any, ...]],
    category_rows: list[tuple[Any, ...]],
) -> None:
    product_columns = ["product_id", *[column for _name, column in COMMON_COLUMNS]]
    product_columns.extend(
        [
            "category_id", "category_name", "import_file", "source_row_num", "imported_at", "updated_at",
            "ozon_product_id", "description_category_id", "type_id", "barcodes_json", "images_json",
            "model_info_json", "attributes_json", "complex_attributes_json", "raw_json", "api_updated_at",
        ]
    )
    try:
        with conn.cursor() as cur:
            ensure_schema(cur)
            cur.execute(
                """
                TRUNCATE TABLE
                    public.ozon_cat_product_attributes,
                    public.ozon_cat_category_attributes,
                    public.ozon_cat_common_attributes,
                    public.ozon_cat_products
                RESTART IDENTITY
                """
            )
            execute_values(
                cur,
                "INSERT INTO public.ozon_cat_common_attributes (attribute_id, attribute_name, db_column) VALUES %s",
                common_rows,
                page_size=1000,
            )
            if category_rows:
                execute_values(
                    cur,
                    "INSERT INTO public.ozon_cat_category_attributes (category_id, attribute_id, attribute_name) VALUES %s",
                    category_rows,
                    page_size=5000,
                )
            execute_values(
                cur,
                f"INSERT INTO public.ozon_cat_products ({', '.join(product_columns)}) VALUES %s",
                product_rows,
                page_size=1000,
            )
            if attribute_rows:
                execute_values(
                    cur,
                    "INSERT INTO public.ozon_cat_product_attributes (product_id, attribute_id, value_text) VALUES %s",
                    attribute_rows,
                    page_size=10000,
                )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_ozon_product_id ON public.ozon_cat_products(ozon_product_id)"
            )
            cur.execute("ANALYZE public.ozon_cat_products")
            cur.execute("ANALYZE public.ozon_cat_product_attributes")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Получить и проверить API без записи в БД")
    parser.add_argument(
        "--force-replace",
        action="store_true",
        help="Явно разрешить полную атомарную перезапись уже загруженного ассортимента",
    )
    return parser.parse_args(argv)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    args = parse_args()
    started = time.monotonic()
    conn = None
    if not args.dry_run:
        conn = connect_target()
        try:
            existing_rows = existing_assortment_rows(conn)
        except Exception:
            conn.close()
            raise
        if existing_rows and not args.force_replace:
            conn.close()
            print(
                f"ИТОГ: ассортимент Ozon | клиент={CLIENT_KEY} | БД={TARGET_DB} | "
                f"status=skipped | причина=в БД уже есть {existing_rows:,} товаров | "
                "данные и прогресс сохранены | для полной перезаписи требуется --force-replace | "
                f"requests=0 | errors=0 | partial=no | elapsed={duration(time.monotonic() - started)}",
                flush=True,
            )
            return 0
    print(
        f"ПЛАН: ассортимент Ozon | клиент={CLIENT_KEY} | БД={TARGET_DB} | "
        f"страницы до {PAGE_SIZE} товаров | цены отдельными страницами | "
        f"справочник для каждой пары категория/тип | режим={'force-replace' if args.force_replace else 'initial-load'} | "
        "запись одним атомарным replace только для пустой БД или по явному флагу | "
        "API read-only | секреты не выводятся | 429=Retry-After/пауза",
        flush=True,
    )
    api = SellerApiClient()
    products = fetch_products(api, started)
    prices = fetch_prices(api, started)
    categories, types = fetch_category_tree(api)
    pairs = sorted(
        {
            (int(item.get("description_category_id") or 0), int(item.get("type_id") or 0))
            for item in products
            if item.get("description_category_id") and item.get("type_id")
        }
    )
    definitions = fetch_attribute_definitions(api, pairs, started)
    product_rows, attribute_rows, common_rows, category_rows = prepare_rows(
        products, prices, categories, types, definitions
    )
    print(
        f"ПРОГРЕСС: подготовка БД | товаров {len(product_rows):,} | "
        f"значений характеристик {len(attribute_rows):,} | "
        f"категорийных определений {len(category_rows):,} | запросов {api.request_count} | step_pct=100.0",
        flush=True,
    )
    if not args.dry_run:
        assert conn is not None
        try:
            store_rows(conn, product_rows, attribute_rows, common_rows, category_rows)
        finally:
            conn.close()
    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: ассортимент Ozon | клиент={CLIENT_KEY} | БД={TARGET_DB} | "
        f"товаров={len(product_rows):,} | характеристик={len(attribute_rows):,} | "
        f"определений={len(category_rows):,} | запросов={api.request_count} | "
        f"errors=0 | partial=no | dry_run={str(args.dry_run).lower()} | elapsed={duration(elapsed)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
