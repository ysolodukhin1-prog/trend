from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import ast
import base64
import contextvars
import hashlib
import hmac
import html
import importlib.util
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookies import CookieError, SimpleCookie
from decimal import Decimal
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import sql
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"
PLANFACT_MATRIX_PROJECT_ROOT = Path(
    os.environ.get("PULSE_HEALTH_CHECK_PROJECT_ROOT")
    or r"C:\Users\Solod\Documents\Память _Прорыв_"
)
PLANFACT_MATRIX_RUNTIME_PATH = (
    PLANFACT_MATRIX_PROJECT_ROOT
    / "artifacts"
    / "bi-planfact-integration"
    / "planfact_funnel_matrix_runtime.py"
)
_PLANFACT_MATRIX_RUNTIME_MODULE = None
_PLANFACT_MATRIX_RUNTIME_LOCK = threading.RLock()
GLORIA_JEANS_PROPOSAL_PATH = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\КП\Approved\KokocMarketplaces_GloriaJeans_Proposal_28042026.xlsx"
)
GLORIA_JEANS_POST_MEETING_REPORTS_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Post Meeting Reports"
)
ADMIN_IMPORT_STREAM_HEARTBEAT_SECONDS = 15
ADMIN_IMPORT_STOP_TIMEOUT_SECONDS = 5
ADMIN_IMPORT_RUNNING_LOCK = threading.Lock()
ADMIN_IMPORT_RUNNING = {}
ADMIN_ALL_CLIENTS_DAILY_LOCK = threading.Lock()
ADMIN_ALL_CLIENTS_DAILY_RUNNER = None
ADMIN_ALL_CLIENTS_DAILY_STATE_PATH = PROJECT_ROOT / ".admin_all_clients_daily_state.json"
ADMIN_ALL_CLIENTS_ASSORTMENT_LOCK = threading.Lock()
ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER = None
ADMIN_ALL_CLIENTS_ASSORTMENT_STATE_PATH = PROJECT_ROOT / ".admin_all_clients_assortment_state.json"
ADMIN_AUTH_FAILURES_LOCK = threading.Lock()
ADMIN_AUTH_FAILURES = {}
MARKETPLACE_TIMEZONE = ZoneInfo("Europe/Moscow")


def marketplace_today():
    """Return the Moscow business date used by marketplace imports."""
    return datetime.now(MARKETPLACE_TIMEZONE).date()


WB_API_RUNNING_LOCK = threading.Lock()
WB_API_RUNNING = {}
WB_API_RUN_CONTEXT = threading.local()
WB_API_STREAM_CONTEXT = threading.local()
SEO_COMPETITOR_JOB_THREADS_LOCK = threading.Lock()
SEO_COMPETITOR_JOB_THREADS = {}
SEO_FULL_RUN_THREADS_LOCK = threading.Lock()
SEO_FULL_RUN_THREADS = {}
WB_API_METHOD_LABELS = {
    "media_count": "GET /adv/v1/count",
    "media_adverts": "GET /adv/v1/adverts",
    "media_stats": "POST /adv/v1/stats",
    "promotion_count": "GET /adv/v1/promotion/count",
    "promotion_adverts": "GET /api/advert/v2/adverts",
    "promotion_fullstats": "GET /adv/v3/fullstats",
    "content_categories": "GET /content/v2/object/all",
    "content_cards": "POST /content/v2/get/cards/list",
    "content_characteristics": "GET /content/v2/object/charcs/{subjectId}",
}
OZON_SELLER_API_BASE_URL = "https://api-seller.ozon.ru"
KOKOC_EXCEL_FONT = "Montserrat"
KOKOC_EXCEL_TEXT = "142327"
KOKOC_EXCEL_TIFFANY = "00AFAA"
KOKOC_EXCEL_TIFFANY_DARK = "007E7A"
KOKOC_EXCEL_TIFFANY_LIGHT = "DDF7F6"
KOKOC_EXCEL_LINE = "BFECEA"


def open_path_in_file_manager(path):
    target = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def load_planfact_funnel_matrix_runtime():
    """Load the read-only Health Check matrix adapter on first request.

    The matrix calculation is maintained with the screening engine.  Keeping
    the import lazy lets the ordinary PULSE dashboard start even if that
    optional reporting workspace is unavailable, while the endpoint reports a
    controlled JSON error instead of a static HTML 404.
    """

    global _PLANFACT_MATRIX_RUNTIME_MODULE
    with _PLANFACT_MATRIX_RUNTIME_LOCK:
        if _PLANFACT_MATRIX_RUNTIME_MODULE is not None:
            return _PLANFACT_MATRIX_RUNTIME_MODULE
        if not PLANFACT_MATRIX_RUNTIME_PATH.is_file():
            raise RuntimeError(
                "Не найден адаптер матрицы Health Check: "
                f"{PLANFACT_MATRIX_RUNTIME_PATH}"
            )
        module_name = "_pulse_planfact_funnel_matrix_runtime"
        spec = importlib.util.spec_from_file_location(
            module_name,
            PLANFACT_MATRIX_RUNTIME_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось подготовить адаптер матрицы Health Check.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if not callable(getattr(module, "handle", None)):
            raise RuntimeError("Адаптер матрицы Health Check не экспортирует handle.")
        _PLANFACT_MATRIX_RUNTIME_MODULE = module
        return module


def handle_review_proposal_open_folder():
    proposal_folder = GLORIA_JEANS_PROPOSAL_PATH.parent
    if not proposal_folder.exists():
        return {"ok": False, "error": "proposal folder not found"}
    open_path_in_file_manager(proposal_folder)
    return {"ok": True, "status": "proposal_folder_opened"}


def is_path_inside(child, parent):
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def review_post_meeting_report_row(path, reports_dir):
    stat = path.stat()
    relative_path = path.relative_to(reports_dir)
    return {
        "name": path.name,
        "path": str(path),
        "relative_path": relative_path.as_posix(),
        "folder": relative_path.parent.as_posix() if str(relative_path.parent) != "." else "",
        "extension": path.suffix.lstrip(".").upper() or "FILE",
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
        "size_bytes": stat.st_size,
    }


def handle_review_post_meeting_reports(_parsed=None):
    reports_dir = GLORIA_JEANS_POST_MEETING_REPORTS_DIR
    if not reports_dir.exists() or not reports_dir.is_dir():
        return {
            "ok": True,
            "folder": str(reports_dir),
            "exists": False,
            "rows": [],
            "error": "post meeting reports folder not found",
        }
    rows = [
        review_post_meeting_report_row(path, reports_dir)
        for path in reports_dir.rglob("*")
        if path.is_file() and not path.name.startswith("~$")
    ]
    rows.sort(key=lambda row: row["modified_at"], reverse=True)
    return {"ok": True, "folder": str(reports_dir), "exists": True, "rows": rows}


def handle_review_post_meeting_reports_open_folder():
    reports_dir = GLORIA_JEANS_POST_MEETING_REPORTS_DIR
    if not reports_dir.exists() or not reports_dir.is_dir():
        return {"ok": False, "error": "post meeting reports folder not found"}
    open_path_in_file_manager(reports_dir)
    return {"ok": True, "status": "post_meeting_reports_folder_opened"}


def handle_review_post_meeting_report_open(parsed):
    reports_dir = GLORIA_JEANS_POST_MEETING_REPORTS_DIR
    params = parse_qs(parsed.query)
    target_value = params.get("path", [""])[0].strip()
    if not target_value:
        return {"ok": False, "error": "report path is required"}
    target_path = Path(target_value)
    if not reports_dir.exists() or not reports_dir.is_dir():
        return {"ok": False, "error": "post meeting reports folder not found"}
    if not target_path.exists() or not target_path.is_file() or not is_path_inside(target_path, reports_dir):
        return {"ok": False, "error": "report file not found"}
    open_path_in_file_manager(target_path)
    return {"ok": True, "status": "post_meeting_report_opened"}


REVIEW_KPI_PLAN_TABLE = "review_kpi_plan_monthly"
REVIEW_KPI_PLAN_MONTHS = [
    {"key": f"2026-{month:02d}", "label": label}
    for month, label in enumerate(
        ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"],
        start=1,
    )
]
REVIEW_KPI_PLAN_METRICS = [
    {"key": "gmv_plan_rub", "label": "GMV", "unit": "money"},
    {"key": "sales_plan_rub", "label": "Продажи", "unit": "money"},
    {"key": "ad_spend_plan_rub", "label": "Расходы на рекламу", "unit": "money"},
    {"key": "tacos_plan_pct", "label": "TACoS", "unit": "pct"},
    {"key": "acos_plan_pct", "label": "ACoS", "unit": "pct"},
    {"key": "organic_share_plan_pct", "label": "Organic Share", "unit": "pct"},
]
REVIEW_KPI_PLAN_METRICS_BY_KEY = {metric["key"]: metric for metric in REVIEW_KPI_PLAN_METRICS}
REVIEW_KPI_PLAN_MARKETPLACES = [
    {"key": "total", "label": "Итого"},
    {"key": "ozon", "label": "Ozon"},
    {"key": "wb", "label": "WB"},
]
REVIEW_KPI_PLAN_MARKETPLACE_KEYS = {marketplace["key"] for marketplace in REVIEW_KPI_PLAN_MARKETPLACES}


def review_kpi_plan_month_key(value):
    raw_value = str(value or "").strip()
    return raw_value[:7] if len(raw_value) >= 7 else ""


def review_kpi_plan_month_date(month_key):
    return f"{month_key}-01"


def normalize_review_kpi_plan_marketplace(value):
    marketplace = str(value or "total").strip().lower()
    return marketplace if marketplace in REVIEW_KPI_PLAN_MARKETPLACE_KEYS else "total"


def ensure_review_kpi_plan_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.review_kpi_plan_monthly (
            client_key text NOT NULL,
            marketplace text NOT NULL DEFAULT 'total',
            plan_month date NOT NULL,
            metric_key text NOT NULL,
            metric_label text NOT NULL,
            metric_unit text NOT NULL,
            plan_value numeric NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (client_key, marketplace, plan_month, metric_key)
        )
        """
    )
    cur.execute("ALTER TABLE public.review_kpi_plan_monthly ADD COLUMN IF NOT EXISTS marketplace text NOT NULL DEFAULT 'total'")
    cur.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public'
                    AND t.relname = 'review_kpi_plan_monthly'
                    AND c.conname = 'review_kpi_plan_monthly_pkey'
                    AND pg_get_constraintdef(c.oid) <> 'PRIMARY KEY (client_key, marketplace, plan_month, metric_key)'
            ) THEN
                ALTER TABLE public.review_kpi_plan_monthly DROP CONSTRAINT review_kpi_plan_monthly_pkey;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public'
                    AND t.relname = 'review_kpi_plan_monthly'
                    AND c.conname = 'review_kpi_plan_monthly_pkey'
            ) THEN
                ALTER TABLE public.review_kpi_plan_monthly
                ADD CONSTRAINT review_kpi_plan_monthly_pkey
                PRIMARY KEY (client_key, marketplace, plan_month, metric_key);
            END IF;
        END $$;
        """
    )


def empty_review_kpi_plan_rows():
    return [
        {
            "metric_key": metric["key"],
            "label": metric["label"],
            "unit": metric["unit"],
            "values": {month["key"]: 0 for month in REVIEW_KPI_PLAN_MONTHS},
        }
        for metric in REVIEW_KPI_PLAN_METRICS
    ]


def handle_review_kpi_plan(parsed):
    params = parse_qs(parsed.query)
    client = normalize_client_key(params.get("client", [current_client_key()])[0])
    marketplace = normalize_review_kpi_plan_marketplace(params.get("marketplace", ["total"])[0])
    rows = empty_review_kpi_plan_rows()
    rows_by_key = {row["metric_key"]: row for row in rows}
    with get_conn() as conn, conn.cursor() as cur:
        ensure_review_kpi_plan_table(cur)
        cur.execute(
            """
            SELECT marketplace, metric_key, metric_label, metric_unit, plan_month, plan_value
            FROM public.review_kpi_plan_monthly
            WHERE client_key = %s AND marketplace = %s
            ORDER BY plan_month, metric_key
            """,
            (client, marketplace),
        )
        for row in cur.fetchall():
            metric_key = str(row.get("metric_key") or "")
            month_key = review_kpi_plan_month_key(row.get("plan_month"))
            if metric_key in rows_by_key and month_key in rows_by_key[metric_key]["values"]:
                rows_by_key[metric_key]["values"][month_key] = normalize_value(row.get("plan_value"))
    return {
        "ok": True,
        "client": client,
        "marketplace": marketplace,
        "marketplaces": REVIEW_KPI_PLAN_MARKETPLACES,
        "months": REVIEW_KPI_PLAN_MONTHS,
        "rows": rows,
    }


def handle_review_kpi_plan_save(payload):
    client = normalize_client_key((payload or {}).get("client", current_client_key()))
    marketplace = normalize_review_kpi_plan_marketplace((payload or {}).get("marketplace", "total"))
    incoming_rows = (payload or {}).get("rows") or []
    records = []
    for row in incoming_rows:
        metric_key = str((row or {}).get("metric_key") or "").strip()
        metric = REVIEW_KPI_PLAN_METRICS_BY_KEY.get(metric_key)
        values = (row or {}).get("values") or {}
        if not metric or not isinstance(values, dict):
            continue
        allowed_months = {month["key"] for month in REVIEW_KPI_PLAN_MONTHS}
        for month_key, value in values.items():
            month_key = review_kpi_plan_month_key(month_key)
            if month_key not in allowed_months:
                continue
            records.append(
                (
                    client,
                    marketplace,
                    review_kpi_plan_month_date(month_key),
                    metric["key"],
                    metric["label"],
                    metric["unit"],
                    to_float(value),
                )
            )
    with get_conn() as conn, conn.cursor() as cur:
        ensure_review_kpi_plan_table(cur)
        for record in records:
            cur.execute(
                """
                INSERT INTO public.review_kpi_plan_monthly (
                    client_key,
                    marketplace,
                    plan_month,
                    metric_key,
                    metric_label,
                    metric_unit,
                    plan_value
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_key, marketplace, plan_month, metric_key) DO UPDATE
                SET
                    metric_label = EXCLUDED.metric_label,
                    metric_unit = EXCLUDED.metric_unit,
                    plan_value = EXCLUDED.plan_value,
                    updated_at = now()
                """,
                record,
            )
    return {"ok": True, "client": client, "marketplace": marketplace, "saved": len(records)}


MARKETPLACES = {
    "ozon": {
        "label": "Ozon",
        "view": "mv_ozon_category_stock_sku_attribute_stats",
        "sku_view": "mv_sku_card_scoring_ozon",
        "product_view": "mv_product_abc_ozon",
    },
    "wb": {
        "label": "WB",
        "view": "mv_category_stock_sku_attribute_stats",
        "sku_view": "mv_sku_card_scoring_wb",
        "product_view": "mv_product_abc_wb",
    },
}
DEFAULT_MARKETPLACE = "ozon"
DEFAULT_SORT_COLUMN = "total_stock_qty"
DEFAULT_SORT_DIRECTION = "desc"
MAX_PAGE_SIZE = 300
ADV_VIEW = "mv_ozon_adv_daily_by_article_category"
ADV_VIEWS = {
    "ozon": "mv_ozon_adv_daily_by_article_category",
    "wb": "mv_wb_adv_daily_by_article_category",
}
MEDIA_ADV_VIEW = "mv_ozon_media_adv_daily"
MEDIA_ADV_VIEWS = {
    "ozon": {
        "campaign": MEDIA_ADV_VIEW,
        "group": MEDIA_ADV_VIEW,
        "creative": MEDIA_ADV_VIEW,
    },
    "wb": {
        "campaign": "mv_wb_media_adv_campaign_daily",
        "group": "mv_wb_media_adv_group_daily",
        "creative": "mv_wb_media_adv_creative_daily",
    },
}
FUNNEL_VIEW = "mv_ozon_funnel_daily_by_article_category"
FUNNEL_FILTERS_VIEW = "mv_ozon_funnel_filter_options"
FUNNEL_PRODUCTS_VIEW = "mv_ozon_funnel_product_options"
FUNNEL_VIEWS = {
    "ozon": {
        "daily": "mv_ozon_funnel_daily_by_article_category",
        "filters": "mv_ozon_funnel_filter_options",
        "products": "mv_ozon_funnel_product_options",
    },
    "wb": {
        "daily": "mv_wb_funnel_daily_by_article_category",
        "filters": "mv_wb_funnel_filter_options",
        "products": "mv_wb_funnel_product_options",
    },
}
FUNNEL_ROLLUP_VIEWS = {
    "ozon": "mv_ozon_funnel_daily_product_rollup",
    "wb": "mv_wb_funnel_daily_product_rollup",
}
FUNNEL_DAILY_ROLLUP_VIEWS = {
    "ozon": "mv_ozon_funnel_daily_summary_rollup",
    "wb": "mv_wb_funnel_daily_summary_rollup",
}
ADV_CHART_METRICS = {
    "orders_amount_rub": "Продажи",
    "direct_orders_amount_rub": "Продажи · прямая атрибуция",
    "indirect_orders_amount_rub": "Продажи · косвенная атрибуция",
    "expense_rub": "Расходы",
    "orders_qty": "Заказы с рекламы",
    "direct_orders_qty": "Заказы · прямая атрибуция",
    "indirect_orders_qty": "Заказы · косвенная атрибуция",
    "total_orders_qty": "Заказы итого",
    "total_orders_amount_rub": "Продажи итого",
    "avg_ad_order_value_rub": "Средний чек, руб",
    "impressions": "Показы",
    "promoted_sku_count": "Товаров в продвижении",
    "ordered_sku_count": "Товаров с заказами",
    "total_sku_count": "Товаров итого",
    "clicks": "Клики",
    "added_to_cart": "Корзины",
    "adv_orders_to_total_orders_pct": "Рекл. заказы / итого, %",
    "drr_pct": "ДРР рекламный, %",
    "direct_drr_pct": "ДРР · прямая атрибуция, %",
    "indirect_drr_pct": "ДРР · косвенная атрибуция, %",
    "total_drr_pct": "ДРР общий, %",
    "ctr_calc_pct": "CTR, %",
    "click_to_cart_pct": "Клик -> корзина, %",
    "cart_to_order_pct": "Корзина -> заказ, %",
    "impression_to_order_pct": "Показы -> заказы, %",
    "click_to_order_pct": "Клик -> заказ, %",
    "cpc_calc_rub": "CPC, руб",
    "cpa_calc_rub": "CPA, руб",
    "cpm_calc_rub": "CPM, руб",
}
MEDIA_ADV_CHART_METRICS = {
    "attributed_revenue_rub": "Атриб. выручка",
    "post_view_revenue_rub": "Post-view выручка",
    "orders_amount_rub": "Прямая выручка",
    "expense_rub": "Расходы",
    "impressions": "Показы",
    "clicks": "Клики",
    "attributed_orders_qty": "Атриб. заказы",
    "post_view_orders_qty": "Post-view заказы",
    "orders_qty": "Прямые заказы",
    "ctr_calc_pct": "CTR, %",
    "click_to_order_pct": "Клик -> заказ, %",
    "drr_direct_pct": "ДРР прямой, %",
    "drr_attributed_pct": "ДРР post-view, %",
    "attributed_roas": "ROAS post-view",
    "post_view_revenue_share_pct": "Доля post-view, %",
    "cpc_calc_rub": "CPC, руб",
    "cpm_calc_rub": "CPM, руб",
    "post_view_orders_per_1000_impressions": "Post-view заказы / 1000 показов",
}
FUNNEL_CHART_METRICS = {
    "impressions_total": "Показы всего",
    "impressions_search_catalog": "Показы поиск/каталог",
    "impressions_card": "Показы карточки",
    "sessions_total": "Сессии всего",
    "sessions_search_catalog": "Сессии поиск/каталог",
    "sessions_card": "Сессии карточки",
    "card_visits": "Переходы в карточку",
    "cart_adds": "Корзины",
    "cart_adds_search_catalog": "Корзины поиск/каталог",
    "cart_adds_card": "Корзины карточки",
    "returned_units": "Возвращено, шт",
    "delivered_units": "Доставлено, шт",
    "ordered_units": "Заказы, шт",
    "ordered_amount_rub": "Заказы, руб",
    "bought_units": "Выкуплено, шт",
    "bought_amount_rub": "Выкупы минус возвраты по дате операции, руб",
    "cohort_bought_units": "Выкуплено по дате заказа, шт",
    "cohort_bought_amount_rub": "Выкуплено по дате заказа, руб",
    "favorites_adds": "Добавили в отложенные",
    "cancelled_units": "Отменено, шт",
    "cancelled_amount_rub": "Отменено, руб",
    "wb_club_ordered_units": "Заказы WB Клуб, шт",
    "wb_club_bought_units": "Выкуплено WB Клуб, шт",
    "wb_club_ordered_amount_rub": "Заказы WB Клуб, руб",
    "wb_club_bought_amount_rub": "Выкуплено WB Клуб, руб",
    "favorite_to_card_visit_pct": "Карточка -> отложенные, %",
    "buyout_pct": "Заказ -> выкуп, %",
    "cancellation_pct": "Заказ -> отмена, %",
    "wb_club_order_share_pct": "Доля заказов WB Клуб, %",
    "adv_impressions": "Рекламные показы",
    "adv_clicks": "Рекламные клики",
    "adv_cart_adds": "Рекламные корзины",
    "adv_orders": "Рекламные заказы",
    "adv_orders_amount_rub": "Рекламные заказы, руб",
    "adv_expense_rub": "Расходы на рекламу",
    "adv_ctr_pct": "Рекламный CTR, %",
    "adv_click_to_cart_pct": "Реклама: клик -> корзина, %",
    "adv_cart_to_order_pct": "Реклама: корзина -> заказ, %",
    "adv_click_to_order_pct": "Реклама: клик -> заказ, %",
    "adv_cpc_rub": "Рекламный CPC, руб",
    "adv_cpa_rub": "Рекламный CPA, руб",
    "adv_cpm_rub": "Рекламный CPM, руб",
    "organic_impressions": "Органические показы",
    "organic_card_visits": "Органические переходы",
    "organic_cart_adds": "Органические корзины",
    "organic_orders": "Органические заказы",
    "search_to_card_visit_pct": "Поиск -> карточка, %",
    "total_impression_to_card_visit_pct": "Показы -> карточка, %",
    "card_visit_to_cart_pct": "Карточка -> корзина, %",
    "cart_to_order_pct": "Корзина -> заказ, %",
    "card_visit_to_order_pct": "Карточка -> заказ, %",
    "ordered_amount_per_unit_rub": "Средний заказ, руб/шт",
    "acos_pct": "ACOS, %",
    "tacos_pct": "TACOS, %",
}
CHART_METRIC_CATALOGS = {
    "abc": {
        "total_stock_qty": "Остаток, шт",
        "sku_count": "SKU",
        "zakazano_sht": "Заказано, шт",
        "zakazano_rub": "Заказано, руб",
        "category_attribute_count": "Характеристики",
    },
    "product": {
        "total_stock_qty": "Остаток, шт",
        "sku_count": "SKU",
        "zakazano_sht": "Заказано, шт",
        "zakazano_rub": "Заказано, руб",
        "category_attribute_count": "Характеристики",
    },
    "sku": {
        "total_stock_qty": "Остаток, шт",
        "zakazano_sht": "Заказано, шт",
        "zakazano_rub": "Заказано, руб",
    },
    "adv": ADV_CHART_METRICS,
    "mediaAdv": MEDIA_ADV_CHART_METRICS,
    "funnel": FUNNEL_CHART_METRICS,
}
CHART_DEFAULT_METRICS = {
    "abc": ["total_stock_qty"],
    "product": ["total_stock_qty"],
    "sku": ["total_stock_qty"],
    "adv": ["orders_amount_rub", "expense_rub", "drr_pct", "total_drr_pct"],
    "mediaAdv": ["attributed_revenue_rub", "post_view_revenue_rub", "expense_rub", "drr_attributed_pct"],
    "funnel": ["impressions_total", "adv_impressions", "organic_impressions", "card_visit_to_order_pct"],
}
CHART_SECONDARY_METRICS = {
    "abc": ["total_stock_qty"],
    "product": ["total_stock_qty"],
    "sku": ["zakazano_sht"],
    "adv": [
        "orders_qty",
        "direct_orders_qty",
        "indirect_orders_qty",
        "orders_amount_rub",
        "avg_ad_order_value_rub",
        "direct_orders_amount_rub",
        "indirect_orders_amount_rub",
        "impressions",
        "promoted_sku_count",
        "ordered_sku_count",
        "total_sku_count",
        "clicks",
        "added_to_cart",
        "expense_rub",
        "adv_orders_to_total_orders_pct",
        "ctr_calc_pct",
        "click_to_cart_pct",
        "cart_to_order_pct",
        "impression_to_order_pct",
        "click_to_order_pct",
        "cpc_calc_rub",
        "cpa_calc_rub",
        "cpm_calc_rub",
        "direct_drr_pct",
        "indirect_drr_pct",
    ],
    "mediaAdv": [
        "attributed_revenue_rub",
        "post_view_revenue_rub",
        "expense_rub",
        "impressions",
        "clicks",
        "attributed_orders_qty",
        "ctr_calc_pct",
        "click_to_order_pct",
        "drr_direct_pct",
        "drr_attributed_pct",
        "attributed_roas",
        "post_view_revenue_share_pct",
        "cpc_calc_rub",
        "cpm_calc_rub",
        "post_view_orders_per_1000_impressions",
    ],
    "funnel": [
        "impressions_total",
        "impressions_search_catalog",
        "card_visits",
        "cart_adds",
        "ordered_units",
        "ordered_amount_rub",
        "bought_units",
        "bought_amount_rub",
            "cohort_bought_units",
            "cohort_bought_amount_rub",
        "favorites_adds",
        "cancelled_units",
        "cancelled_amount_rub",
        "wb_club_ordered_units",
        "wb_club_bought_units",
        "wb_club_ordered_amount_rub",
        "wb_club_bought_amount_rub",
        "adv_impressions",
        "adv_clicks",
        "adv_cart_adds",
        "adv_orders",
        "adv_orders_amount_rub",
        "adv_expense_rub",
        "organic_impressions",
        "organic_card_visits",
        "organic_cart_adds",
        "organic_orders",
        "search_to_card_visit_pct",
        "total_impression_to_card_visit_pct",
        "card_visit_to_cart_pct",
        "cart_to_order_pct",
        "card_visit_to_order_pct",
        "ordered_amount_per_unit_rub",
        "favorite_to_card_visit_pct",
        "buyout_pct",
        "cancellation_pct",
        "wb_club_order_share_pct",
        "adv_ctr_pct",
        "adv_click_to_cart_pct",
        "adv_cart_to_order_pct",
        "adv_click_to_order_pct",
        "adv_cpc_rub",
        "adv_cpa_rub",
        "adv_cpm_rub",
        "acos_pct",
        "tacos_pct",
    ],
}
ABC_BASE_VIEWS = {
    "ozon": {
        "orders": "mv_ozon_abc_product_orders_base",
        "stock": "mv_ozon_abc_product_stock_base",
    },
    "wb": {
        "orders": "mv_wb_abc_product_orders_base",
        "stock": "mv_wb_abc_product_stock_base",
    },
}
PLANFACT_DAILY_VIEW = "mv_planfact_daily"
PLANFACT_MONTHLY_VIEW = "mv_planfact_monthly"
PLANFACT_HISTORY_START_BY_CLIENT = {
    "gloria_jeans": date(2026, 4, 1),
}
OZON_ABC_COLUMNS = [
    "category_name",
    "sku_count",
    "abc_combined",
    "zakazano_rub",
    "avg_price_rub",
    "zakazano_sht",
    "total_stock_qty",
    "category_attribute_count",
    "orders_share_pct",
    "orders_cumulative_pct",
    "abc_orders",
    "sales_share_pct",
    "sales_cumulative_pct",
    "abc_sales",
    "stock_share_pct",
    "stock_cumulative_pct",
    "abc_stock",
    "abc_combined",
]
OZON_ABC_PRODUCT_COLUMNS = [
    "category_name",
    "sku_wb",
    "sku_ozon",
    "naimenovanie",
    "abc_combined",
    "zakazano_rub",
    "avg_price_rub",
    "zakazano_sht",
    "total_stock_qty",
    "adv_impressions",
    "adv_clicks",
    "adv_sales_rub",
    "adv_acos_pct",
    "adv_tacos_pct",
    "orders_share_pct",
    "orders_cumulative_pct",
    "abc_orders",
    "sales_share_pct",
    "sales_cumulative_pct",
    "abc_sales",
    "stock_share_pct",
    "stock_cumulative_pct",
    "abc_stock",
    "reyting_kartochki",
    "reyting_po_otzyvam",
]
ABC_CLASS_VALUES = ["A", "B", "C", "Без ABC"]
ABC_COMBINED_VALUES = [
    f"{orders}{sales}{stock}"
    for orders in ("A", "B", "C")
    for sales in ("A", "B", "C")
    for stock in ("A", "B", "C")
] + ["Без ABC"]
MAPPING_FILTERS = [
    ("gj_model", "gj_model"),
    ("assortment_bia", "assortment_bia"),
    ("tg", "tg"),
    ("tg_plus", "tg_plus"),
    ("cg", "cg"),
    ("season", "season"),
]
MAPPING_COLUMNS = [
    "gj_wb_article",
    "gj_ozon_sku",
    "gj_model",
    "assortment_bia",
    "tg",
    "tg_plus",
    "cg",
    "season",
]
ABC_PRODUCT_MAPPING_COLUMNS = [column for column in MAPPING_COLUMNS if column != "gj_ozon_sku"]
MAPPINGLESS_CLIENTS = {"konstex"}
SPORTMASTER_FILTERS = {
    "sm_subcategory": {
        "label": "Подкатегория",
        "kind": "subcategory",
    },
    "sm_brand": {
        "label": "Бренд",
        "kind": "brand",
        "attribute_names": ("Бренд", "Бренд в одежде и обуви"),
    },
    "sm_model": {
        "label": "Модель",
        "kind": "attribute_search",
        "attribute_names": (
            "Модель",
            "Название модели (для объединения в одну карточку)",
            "Название модели для шаблона наименования",
            "Модель спортивная",
            "Модель ботинок",
            "Модель брюк",
            "Модель плавок",
            "Модель лифа",
            "Модель шапки",
        ),
    },
    "sm_gender": {
        "label": "Пол",
        "kind": "attribute_exact",
        "attribute_names": ("Пол",),
    },
    "sm_age": {
        "label": "Возраст",
        "kind": "attribute_exact",
        "attribute_names": ("Возрастная группа", "Целевая аудитория", "Возраст"),
    },
    "sm_collection": {
        "label": "Коллекция",
        "kind": "attribute_exact",
        "attribute_names": ("Коллекция",),
    },
    "sm_season": {
        "label": "Сезон",
        "kind": "attribute_exact",
        "attribute_names": ("Сезон",),
    },
    "sm_sport": {
        "label": "Назначение / спорт",
        "kind": "attribute_exact",
        "attribute_names": ("Спортивное назначение", "Назначение", "Назначение обуви", "Вид спорта"),
    },
}
OZON_PRODUCT_ATTRIBUTE_FILTERS = {
    "ozon_collection": {
        "label": "Коллекция карточки",
        "attribute_names": ("Коллекция",),
    },
    "ozon_gender": {
        "label": "Пол карточки",
        "attribute_names": ("Пол",),
    },
    "ozon_season": {
        "label": "Сезон карточки",
        "attribute_names": ("Сезон",),
    },
    "ozon_style": {
        "label": "Стиль",
        "attribute_names": ("Стиль",),
    },
    "ozon_color": {
        "label": "Цвет",
        "attribute_names": ("Название цвета", "Цвет товара"),
    },
    "ozon_material": {
        "label": "Материал",
        "attribute_names": ("Материал",),
    },
    "ozon_material_composition": {
        "label": "Состав материала",
        "attribute_names": ("Состав материала",),
    },
    "ozon_russian_size": {
        "label": "Российский размер",
        "attribute_names": ("Российский размер",),
    },
    "ozon_manufacturer_size": {
        "label": "Размер производителя",
        "attribute_names": ("Размер производителя",),
    },
    "ozon_target_audience": {
        "label": "Целевая аудитория",
        "attribute_names": ("Целевая аудитория",),
    },
}
OZON_PRODUCT_ATTRIBUTE_OPTIONS_TTL_SECONDS = 300
OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE = {}
OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE_LOCK = threading.Lock()
ABC_SPORTMASTER_GROUP_DIMENSIONS = {
    "category": {"label": "Категория", "noun": "категорий"},
    "subcategory": {"label": "Подкатегория", "noun": "подкатегорий"},
    "brand": {"label": "Бренд", "noun": "брендов", "filter": "sm_brand", "fallback": "Без бренда"},
    "model": {"label": "Модель", "noun": "моделей", "filter": "sm_model", "fallback": "Без модели"},
    "gender": {"label": "Пол", "noun": "значений пола", "filter": "sm_gender", "fallback": "Без пола"},
    "age": {"label": "Возраст", "noun": "возрастных групп", "filter": "sm_age", "fallback": "Без возраста"},
    "collection": {"label": "Коллекция", "noun": "коллекций", "filter": "sm_collection", "fallback": "Без коллекции"},
    "season_sm": {"label": "Сезон", "noun": "сезонов", "filter": "sm_season", "fallback": "Без сезона"},
    "sport": {"label": "Назначение / спорт", "noun": "назначений", "filter": "sm_sport", "fallback": "Без назначения"},
}
SEO_STATUS_PARAM = "seo_status"
SEO_STATUS_COLUMN = "seo_status"
COLLECTION_STATUS_PARAM = "collection_status"
COLLECTION_STATUS_COLUMN = "priority_collection"
SEO_TAGS_TABLE = "marketplace_sku_tags"
SEO_STATUS_NO_TAG = "__no_tag__"
COLLECTION_STATUS_NO_TAG = "__no_tag__"
NO_TAG_LABEL = "Без тега"
COLLECTION_PRIORITY = "priority_collection"
COLLECTION_PRIORITY_LABEL = "Приоритетная коллекция"
PRIORITY_COLLECTION_SOURCE_TAG = "GJ лето 2026"
PRIORITY_COLLECTION_DISPLAY_TAG = "Glory Jeans Лето 2026"
BACK_TO_SCHOOL_COLLECTION_TAG = "школа"
NEAR_SCHOOL_COLLECTION_TAG = "околошкола"
COLLECTION_TAG_LABELS = {
    PRIORITY_COLLECTION_SOURCE_TAG: PRIORITY_COLLECTION_DISPLAY_TAG,
    BACK_TO_SCHOOL_COLLECTION_TAG: BACK_TO_SCHOOL_COLLECTION_TAG,
    NEAR_SCHOOL_COLLECTION_TAG: NEAR_SCHOOL_COLLECTION_TAG,
}
COLLECTION_TAG_PREFIXES = ("GJ ", "Glory Jeans", "Gloria Jeans")
COLLECTION_SOURCE_TAGS = set(COLLECTION_TAG_LABELS)
ADMIN_IMPORTS = {
    "sku_mapping_gj": {
        "report": "Справочник SKU WB/Ozon",
        "description": "Связки штрихкодов, WB-артикулов, Ozon SKU и классификаторов Gloria Jeans.",
        "policy": "Полная замена справочника при запуске.",
        "script": PROJECT_ROOT / "scripts" / "import_sku_mapping_gj.py",
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\SKU_mapping_Gj.xlsx"
        ),
        "destination": "sku_mapping_gj, mv_sku_mapping_gj_*",
    },
    "planfact": {
        "report": "План/факт",
        "description": "Ежедневные продажи, заказы, расходы и месячные планы по WB/Ozon.",
        "policy": "Ежедневно атомарно заменяется текущий месяц WB, а Ozon обновляется по датам с полной связкой Spend + воронка + реклама; месячные планы не меняются.",
        "script": PROJECT_ROOT / "scripts" / "import_planfact_reports.py",
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Pl_F"
        ),
        "destination": "planfact_daily, planfact_plan, mv_planfact_daily, mv_planfact_monthly",
    },
    "ozon_stock": {
        "report": "Остатки Ozon",
        "description": "Текущие остатки Ozon по товарам, кластерам и складам; обновляет Ozon ABC/SKU витрины.",
        "policy": "Текущий снимок остатков заменяется целиком.",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_stock_reports.py",
        "defer_views_args": ["--no-refresh"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Stock\Stock.xlsx"
        ),
        "destination": "ozon_stock_*, vw_ozon_current_stock_by_sku, mv_ozon_category_stock_sku_attribute_stats, mv_sku_card_scoring_ozon, mv_product_abc_ozon",
    },
    "ozon_funnel": {
        "report": "Воронка OZON",
        "description": "Ежедневная товарная воронка Ozon: показы, карточки, корзины, заказы и органика/реклама.",
        "policy": "Инкрементально: уже загруженные XLSX пропускаются; полный перезапуск через --force-reimport.",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_funnel_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Funel"
        ),
        "destination": "ozon_funnel_daily, ozon_funnel_import_files, mv_ozon_funnel_daily_by_article_category, mv_ozon_funnel_filter_options, mv_ozon_funnel_product_options, Ozon ABC base views",
    },
    "ozon_funnel_views": {
        "report": "Витрины воронки Ozon",
        "description": "Пересобирает Ozon funnel materialized views после загрузки всех новых файлов.",
        "policy": "Только пересборка витрин, без чтения Excel.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_funnel_reports.py",
        "args": ["--skip-import", "--skip-dependent-views"],
        "source": "Только БД: ozon_funnel_daily, SKU mapping, Ozon cards, Ozon stock, Ozon adv",
        "destination": "mv_ozon_funnel_*, mv_ozon_funnel_daily_*_rollup",
    },
    "wb_funnel": {
        "report": "Воронка WB",
        "description": "Ежедневная товарная воронка Wildberries: показы, карточки, корзины, заказы, остатки WB/свой склад и WB ABC base views.",
        "policy": "Инкрементально: дни, которые уже есть в wb_funnel_daily, пропускаются; полный перезапуск через --force-reimport.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_funnel_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Funel"
        ),
        "destination": "wb_funnel_daily, wb_funnel_import_files, mv_wb_funnel_daily_by_article_category, mv_wb_funnel_filter_options, mv_wb_funnel_product_options, WB ABC base views",
    },
    "wb_stock": {
        "report": "Остатки WB",
        "description": "Текущий детальный снимок остатков Wildberries из ежедневного Stock.zip.",
        "policy": "Инкрементально: неизменённый ZIP пропускается; изменённый снимок заменяет только текущую детальную таблицу остатков WB.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_stock_reports.py",
        "args": [
            "--source",
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика\Дашборды\Data\Wb\Stock\Stock.zip",
            "--client-label",
            "Gloria Jeans",
        ],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Stock\Stock.zip"
        ),
        "destination": "wb_stock_detail_raw, wb_stock_import_files, vw_wb_current_stock_by_article",
    },
    "wb_search_queries": {
        "report": "Поисковые запросы WB",
        "description": "Дневные товаро-запросные выгрузки WB: спрос, видимость, позиции, переходы, корзины и заказы.",
        "policy": "Инкрементально по ZIP: неизмененные файлы пропускаются; полный перезапуск через --force-reimport.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_search_queries.py",
        "defer_views_args": ["--skip-views", "--skip-classification"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\SEO"
        ),
        "destination": "wb_search_queries_daily, wb_search_queries_import_files, wb_search_query_classification, mv_wb_search_query_daily, mv_wb_search_product_daily, mv_wb_search_queries_daily_summary, mv_wb_search_query_classification_summary",
    },
    "wb_search_query_views": {
        "report": "Витрины поисковых запросов WB",
        "description": "Пересобирает дневные витрины и обновляет семантическую классификацию запросов без повторного чтения Excel.",
        "policy": "Только пересборка витрин, без загрузки ZIP/XLSX.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_wb_search_queries.py",
        "args": ["--skip-import"],
        "source": "Только БД: wb_search_queries_daily",
        "destination": "wb_search_query_classification, mv_wb_search_query_daily, mv_wb_search_product_daily, mv_wb_search_queries_daily_summary, mv_wb_search_query_classification_summary",
    },
    "wb_products_characteristics": {
        "report": "Ассортимент WB",
        "description": "Ручное обновление списка товаров WB, категорий продавца и характеристик из новых Prodacts XLSX.",
        "policy": "Только ручной запуск: перед replace-импортом скрипт делает обязательный бэкап БД; в ежедневную цепочку не входит.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_products_with_characteristics.py",
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Prodacts"
        ),
        "destination": "categories, attribute_definitions, common_attributes, category_attributes, products, product_attributes, WB assortment scoring views",
    },
    "ozon_product_categories": {
        "report": "Ассортимент Ozon",
        "description": "Ручное обновление списка товаров Ozon, категорий и характеристик из новых Prodacts ZIP/XLSX.",
        "policy": "Только ручной запуск: перед replace-импортом скрипт делает обязательный бэкап БД; в ежедневную цепочку не входит.",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_product_categories.py",
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Prodacts"
        ),
        "destination": "ozon_cat_categories, ozon_cat_attribute_definitions, ozon_cat_common_attributes, ozon_cat_category_attributes, ozon_cat_products, ozon_cat_product_attributes, Ozon SKU/ABC scoring views",
    },
    "ozon_adv_daily": {
        "report": "Товарная реклама Ozon",
        "description": "Ежедневные рекламные показатели по товарам и артикулам из старых Adv и новых Prod_adv выгрузок.",
        "policy": "Инкрементально: уже загруженные файлы/дни Prod_adv пропускаются; полный перезапуск через --force-reimport.",
        "script": PROJECT_ROOT / "Скрипты" / "import_ozon_adv_daily_reports.py",
        "defer_views_args": ["--skip-views"],
        "env": {
            "OZON_PROD_ADV_START_DATE": "2026-03-01",
            "OZON_PROD_ADV_CUTOFF_DATE": "2026-02-28",
        },
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Adv; "
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Prod_adv"
        ),
        "destination": "ozon_adv_daily_raw, ozon_adv_daily_import_files, mv_ozon_adv_daily_by_article_category",
    },
    "wb_adv_daily": {
        "report": "Товарная реклама WB",
        "description": "Ежедневные рекламные показатели WB по товарам из месячных Adv XLSX.",
        "policy": "Инкрементально: неизмененные месячные XLSX пропускаются; измененный файл месяца переимпортируется целиком.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_adv_daily_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Adv"
        ),
        "destination": "wb_adv_daily_raw, wb_adv_daily_import_files, mv_wb_adv_daily_by_article_category",
    },
    "ozon_media_adv": {
        "report": "Медийная реклама Ozon",
        "description": "Ежедневные кампании медийной рекламы Ozon: бюджеты, расход, показы, клики, post-view и ДРР.",
        "policy": "Инкрементально: уже загруженные XLSX пропускаются; полный перезапуск через --force-reimport.",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_media_adv_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Ozon\Adv"
        ),
        "destination": "ozon_media_adv_daily_raw, ozon_media_adv_import_files, mv_ozon_media_adv_daily",
    },
    "wb_media_adv": {
        "report": "Медийная реклама WB",
        "description": "Ежедневные показатели медийной рекламы WB на уровнях кампаний, групп объявлений и креативов.",
        "policy": "Инкрементально: неизмененные XLSX пропускаются; измененный файл переимпортируется целиком.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_media_adv_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Media_adv"
        ),
        "destination": "wb_media_adv_daily_raw, wb_media_adv_import_files, mv_wb_media_adv_*",
    },
    "ozon_adv_daily_view": {
        "report": "Витрина товарной рекламы Ozon",
        "description": "Пересобирает категории и индексы рекламной витрины без повторного чтения Excel.",
        "policy": "Только пересборка витрины, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "Скрипты" / "rebuild_ozon_adv_daily_category_view.py",
        "source": (
            "Только БД: ozon_adv_daily_raw + справочники Ozon"
        ),
        "destination": "mv_ozon_adv_daily_by_article_category",
    },
    "ozon_media_adv_view": {
        "report": "Витрина медийной рекламы Ozon",
        "description": "Пересобирает медийную рекламную витрину Ozon без повторного чтения Excel.",
        "policy": "Только пересборка витрины, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_ozon_media_adv_reports.py",
        "args": ["--skip-import"],
        "source": "Только БД: ozon_media_adv_daily_raw",
        "destination": "mv_ozon_media_adv_daily",
    },
    "wb_adv_daily_view": {
        "report": "Витрина товарной рекламы WB",
        "description": "Пересобирает товарную рекламную витрину WB без повторного чтения Excel.",
        "policy": "Только пересборка витрины, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_wb_adv_daily_reports.py",
        "args": ["--skip-import"],
        "source": "Только БД: wb_adv_daily_raw + mv_wb_funnel_daily_by_article_category",
        "destination": "mv_wb_adv_daily_by_article_category",
    },
    "wb_media_adv_views": {
        "report": "Витрины медийной рекламы WB",
        "description": "Пересобирает медийные рекламные витрины WB по кампаниям, группам и креативам без повторного чтения Excel.",
        "policy": "Только пересборка витрин, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_wb_media_adv_reports.py",
        "args": ["--skip-import"],
        "source": "Только БД: wb_media_adv_daily_raw",
        "destination": "mv_wb_media_adv_campaign_daily, mv_wb_media_adv_group_daily, mv_wb_media_adv_creative_daily",
    },
    "ozon_abc_base_views": {
        "report": "Базовые витрины Ozon ABC",
        "description": "Пересобирает быстрые продуктовые основы ABC после обновления воронки, остатков или маппинга.",
        "policy": "Только пересборка витрин, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "build_ozon_abc_materialized_views.py",
        "args": ["--skip-dependents"],
        "source": (
            "Только БД: воронка Ozon, остатки Ozon, карточки Ozon, SKU mapping"
        ),
        "destination": "mv_ozon_abc_product_orders_base, mv_ozon_abc_product_stock_base",
    },
    "ozon_sku_scoring_view": {
        "report": "SKU-скоринг Ozon",
        "description": "Пересобирает SKU-скоринг Ozon с заполненностью общих/категорийных атрибутов, фото и зависимой продуктовой ABC-витриной.",
        "policy": "Только пересборка витрин, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "rebuild_ozon_sku_scoring_view.py",
        "source": (
            "Только БД: vw_ozon_sku_sales_90d, ozon_cat_products, "
            "ozon_cat_product_attributes, ozon_cat_common_attributes, ozon_cat_category_attributes"
        ),
        "destination": "mv_sku_card_scoring_ozon, mv_product_abc_ozon",
    },
    "wb_dashboard_views": {
        "report": "Витрины WB",
        "description": "Пересобирает WB-витрины воронки, ABC, SKU-скоринга и продуктовой ABC без повторного чтения файлов.",
        "policy": "Только пересборка витрин, без загрузки файлов.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "rebuild_wb_dashboard_views.py",
        "source": (
            "Только БД: wb_funnel_daily, stock_history_import, sales_flat_import, "
            "products, product_attributes, SKU mapping"
        ),
        "destination": (
            "mv_wb_funnel_*, mv_wb_abc_product_*, "
            "mv_sku_card_scoring_wb, mv_product_abc_wb, mv_category_stock_sku_attribute_stats"
        ),
    },
}

DAILY_IMPORT_KEYS = [
    "planfact",
    "ozon_stock",
    "ozon_funnel",
    "wb_funnel",
    "wb_stock",
    "wb_search_queries",
    "ozon_adv_daily",
    "wb_adv_daily",
    "ozon_media_adv",
    "wb_media_adv",
    "ozon_funnel_views",
    "ozon_adv_daily_view",
    "ozon_media_adv_view",
    "wb_search_query_views",
    "wb_dashboard_views",
    "wb_adv_daily_view",
    "wb_media_adv_views",
    "ozon_abc_base_views",
    "ozon_sku_scoring_view",
]
ADMIN_CLIENTS = {
    "gloria_jeans": {
        "label": "Gloria Jeans",
        "db_name": "wb_products",
        "status": "active",
        "description": "Рабочий клиент: импорты, витрины и БД подключены.",
        "show_in_dashboard": True,
        "reports": ["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics", "planfact", "seoMonitoring", "wbSearchQueries", "commercialRadar"],
        "marketplaces": ["ozon", "wb"],
    },
    "sportmaster": {
        "label": "Спортмастер",
        "db_name": "sportmaster",
        "status": "active",
        "description": (
            r"Отдельная БД и выгрузки: "
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Спортмастер\Аналитика\Дашборды"
        ),
        "show_in_dashboard": True,
        "reports": ["abc", "product", "sku", "adv", "mediaAdv", "funnel", "weeklyDynamics", "planfact", "seoMonitoring", "wbSearchQueries", "wbEntrance", "commercialRadar"],
        "marketplaces": ["ozon", "wb"],
    },
    "boiron": {
        "label": "Boiron",
        "db_name": "boiron",
        "status": "active",
        "description": (
            r"Отдельная БД и выгрузки товарной рекламы Ozon: "
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Boiron\Отчёты"
        ),
        "show_in_dashboard": True,
        "reports": ["adv", "commercialRadar"],
        "marketplaces": ["ozon"],
    },
    "km_trade": {
        "label": "KM Trade",
        "db_name": "km_trade_products",
        "status": "active",
        "description": (
            r"Отдельная БД, Ozon и WB API-импорты и витрины подключены. Источник: "
            r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data"
        ),
        "show_in_dashboard": True,
        "reports": ["abc", "product", "sku", "adv", "funnel", "weeklyDynamics", "planfact", "salesPlanning", "mediaPlan", "profitLoss", "unitEconomics", "inventoryHistory", "seoMonitoring", "reviews", "commercialRadar"],
        "marketplaces": ["ozon", "wb"],
    },
}
ADMIN_REPORT_CATALOG = [
    {'id': 'assortmentProducts', 'label': 'Товары', 'group': 'Ассортимент'},
    {'id': 'assortmentPrices', 'label': 'Цены', 'group': 'Ассортимент'},
    {'id': 'assortmentABC', 'label': 'ABC', 'group': 'Ассортимент'},
    {'id': 'assortmentXYZ', 'label': 'XYZ', 'group': 'Ассортимент'},

    {"id": "abc", "label": "ABC по категориям"},
    {"id": "product", "label": "ABC по продуктам"},
    {"id": "sku", "label": "SKU-скоринг"},
    {"id": "adv", "label": "Товарная реклама"},
    {"id": "mediaAdv", "label": "Медийная реклама"},
    {"id": "funnel", "label": "Воронка продаж"},
    {"id": "weeklyDynamics", "label": "Еженедельная динамика"},
    {"id": "inventoryHistory", "label": "История запасов"},
    {"id": "planfact", "label": "План/факт"},
    {"id": "salesPlanning", "label": "План продаж"},
    {"id": "mediaPlan", "label": "Медиаплан"},
    {"id": "profitLoss", "label": "P&L"},
    {"id": "unitEconomics", "label": "Юнит-экономика"},
    {"id": "seoMonitoring", "label": "SEO-мониторинг"},
    {"id": "wbSearchQueries", "label": "Поисковые запросы WB"},
    {"id": "wbAdSearchQueries", "label": "Запросы рекламы WB"},
    {"id": "wbEntrance", "label": "Точки входа WB"},
    {"id": "reviews", "label": "Работа с отзывами"},
    {"id": "avitoOverview", "label": "Avito Ads · Обзор"},
    {"id": "avitoCampaigns", "label": "Avito Ads · Кампании"},
    {"id": "avitoGroups", "label": "Avito Ads · Группы"},
    {"id": "avitoCreatives", "label": "Avito Ads · Креативы"},
    {"id": "avitoDaily", "label": "Avito Ads · По дням"},
    {"id": "yandexOverview", "label": "Яндекс Маркет · Обзор"},
    {"id": "yandexFunnel", "label": "Яндекс Маркет · Воронка"},
    {"id": "yandexFinance", "label": "Яндекс Маркет · Финансы"},
    {"id": "yandexPromotion", "label": "Яндекс Маркет · Продвижение"},
    {"id": "yandexInventory", "label": "Яндекс Маркет · Остатки"},
    {"id": "lamodaSales", "label": "Lamoda · Продажи"},
    {"id": "lamodaReturns", "label": "Lamoda · Возвраты"},
    {"id": "lamodaCatalog", "label": "Lamoda · Ассортимент"},
    {"id": "lamodaOperations", "label": "Lamoda · Операции"},
    {"id": "commercialRadar", "label": "Health Check"},
]
AVITO_REPORT_IDS = ("avitoOverview", "avitoCampaigns", "avitoGroups", "avitoCreatives", "avitoDaily")
YANDEX_REPORT_IDS = ("yandexOverview", "yandexFunnel", "yandexFinance", "yandexPromotion", "yandexInventory")
LAMODA_REPORT_IDS = ("lamodaSales", "lamodaReturns", "lamodaCatalog", "lamodaOperations")
WB_ONLY_REPORT_IDS = {"wbSearchQueries", "wbAdSearchQueries", "wbEntrance"}
WB_SEARCH_QUERY_CLIENT_IDS = {"gloria_jeans", "sportmaster", "konstex"}
MEDIA_ADV_CLIENT_IDS = {"gloria_jeans", "sportmaster"}
ADMIN_SECTION_CATALOG = [
    {"id": "manual", "label": "Ручные загрузки", "caption": "Ручные"},
    {"id": "daily", "label": "Ежедневный импорт", "caption": "Ежедн."},
    {"id": "allDaily", "label": "Обновление всех аккаунтов", "caption": "Все"},
    {"id": "apiDaily", "label": "Ежедневная API-цепочка", "caption": "API день"},
    {"id": "api", "label": "Выгрузка API", "caption": "API"},
    {"id": "client", "label": "Клиенты", "caption": "Клиенты"},
    {"id": "clientOnboarding", "label": "Добавление магазина", "caption": "Добавление магазина"},
    {"id": "database", "label": "Структура БД", "caption": "БД"},
    {"id": "integrations", "label": "Интеграции", "caption": "Интеграции"},
    {"id": "users", "label": "Пользователи и доступы", "caption": "Права"},
]
ADMIN_SECTION_IDS = {item["id"] for item in ADMIN_SECTION_CATALOG}
DATABASE_REPORT_KEYWORDS = {
    "abc": ("abc", "category", "categories", "cat_", "катег"),
    "product": ("product", "products", "goods", "cards", "assort", "nomenc", "sku_sales"),
    "sku": ("sku", "scoring", "score", "article", "barcode"),
    "adv": ("adv_daily", "advert", "campaign", "cpc", "cpo", "promotion", "реклам"),
    "mediaAdv": ("media_adv", "banner", "media", "creative"),
    "funnel": ("funnel", "sales_funnel", "orders", "sessions", "cart", "ворон"),
    "weeklyDynamics": ("weekly", "week", "dynamics", "dynamic"),
    "inventoryHistory": ("stock", "inventory", "остат", "warehouse"),
    "planfact": ("planfact", "plan_fact", "plan", "fact"),
    "salesPlanning": ("sales_plan", "salesplanning", "planning"),
    "mediaPlan": ("media_plan", "mediaplan"),
    "profitLoss": ("profit", "loss", "pnl", "p_l", "finance"),
    "unitEconomics": ("unit", "economics", "cogs", "margin", "tariff"),
    "seoMonitoring": ("seo", "search", "query", "queries", "position"),
    "wbSearchQueries": ("wb_search", "search_queries", "query"),
    "wbEntrance": ("entrance", "entry"),
    "reviews": ("review", "feedback", "comment", "rating", "отзыв"),
    "commercialRadar": ("health", "radar", "monitor", "oos"),
}
DEFAULT_CLIENT = "gloria_jeans"
CURRENT_CLIENT = contextvars.ContextVar("dashboard_client", default=None)
CURRENT_GALACTICA_REPORT_CONNECTION = contextvars.ContextVar("galactica_report_connection", default=None)
BOIRON_ADV_DAILY_DIR = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Boiron\Отчёты"
    r"\Анализ РК Daily\Статистика\2026"
)
CURRENT_ACCESS_USER = contextvars.ContextVar("dashboard_access_user", default=None)
BOIRON_ADV_WORKBOOK = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Boiron\Отчёты"
    r"\Отчёты по товарной рекламе\Kokoc Group_Boiron_Анализ рекламных кампаний.xlsb"
)
BOIRON_BRAND_PARAM = "boiron_brand"
BOIRON_ADMIN_IMPORTS = {
    "boiron_ozon_adv_daily": {
        "report": "Boiron: товарная реклама Ozon + PF",
        "description": "Ежедневные XLSX выгрузки товарной рекламы Ozon и план/факт с листа PF в отдельную БД Boiron.",
        "policy": "Полная замена только таблиц Boiron; базы Gloria Jeans и Спортмастер не затрагиваются.",
        "script": PROJECT_ROOT / "scripts" / "import_boiron_ozon_adv_reports.py",
        "supports_resume": True,
        "source": f"{BOIRON_ADV_DAILY_DIR}; {BOIRON_ADV_WORKBOOK}",
        "destination": "boiron.public.ozon_adv_daily_raw, mv_ozon_adv_daily_by_article_category, boiron_adv_planfact_brand",
    },
}
BOIRON_DAILY_IMPORT_KEYS = ["boiron_ozon_adv_daily"]
KM_TRADE_DATA_ROOT = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data"
)
KM_TRADE_ADMIN_IMPORTS = {
    "km_initial_load": {
        "report": "Первичная/полная загрузка KM Trade",
        "description": "Резервная копия БД, полный импорт Ozon и пересборка всех витрин KM Trade.",
        "policy": "Ручной запуск. Перед изменениями существующей БД обязательно делает pg_dump.",
        "script": PROJECT_ROOT / "scripts" / "import_km_initial_load.py",
        "source": KM_TRADE_DATA_ROOT,
        "destination": "km_trade_products.public.* + dashboard materialized views",
    },
    "km_ozon_product_categories": {
        "report": "Справочник Ozon: товары, категории и характеристики",
        "description": "Месячная загрузка карточек, категорий и характеристик KM Trade из Ozon Product_catigories.",
        "policy": "Полная замена справочника. Запускать вручную примерно раз в месяц, не входит в ежедневную цепочку.",
        "script": PROJECT_ROOT / "scripts" / "import_km_ozon_product_categories.py",
        "source": rf"{KM_TRADE_DATA_ROOT}\Ozon\Product_catigories",
        "destination": "km_trade_products.public.ozon_cat_products, ozon_cat_product_attributes, ozon_cat_common_attributes, ozon_cat_category_attributes",
    },
    "km_ozon_fin_sales": {
        "report": "Финансовые начисления Ozon",
        "description": "Полная загрузка 25 финансовых полей: продажи, комиссии, логистика, возвраты, локализация и итог.",
        "policy": "Полная пересборка только XLSX-части финансового контура KM Trade; API-часть не затрагивается.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_finance.py",
        "args": ["--source", "manual"],
        "source": rf"{KM_TRADE_DATA_ROOT}\Ozon\Fin",
        "destination": "km_trade_products.public.ozon_finance_events, ozon_finance_lines",
    },
    "km_ozon_stock": {
        "report": "Остатки Ozon",
        "description": "Текущие остатки Ozon по товарам, кластерам и складам для KM Trade.",
        "policy": "Полная замена текущего снимка остатков в отдельной БД KM.",
        "script": PROJECT_ROOT / "scripts" / "import_km_ozon_stock_reports.py",
        "defer_views_args": ["--no-refresh"],
        "source": rf"{KM_TRADE_DATA_ROOT}\Ozon\Stock",
        "destination": "km_trade_products.public.ozon_stock_*, vw_ozon_current_stock_by_sku, mv_ozon_category_stock_sku_attribute_stats",
    },
    "km_ozon_funnel": {
        "report": "Воронка Ozon",
        "description": "Ежедневная товарная воронка Ozon для KM Trade.",
        "policy": "Инкрементальная загрузка новых/измененных файлов воронки в отдельную БД KM.",
        "script": PROJECT_ROOT / "scripts" / "import_km_ozon_funnel_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{KM_TRADE_DATA_ROOT}\Ozon\Funel",
        "destination": "km_trade_products.public.ozon_funnel_daily, mv_ozon_funnel_*",
    },
    "km_ozon_adv_daily": {
        "report": "Товарная реклама Ozon",
        "description": "Ежедневные рекламные показатели Ozon по товарам и артикулам для KM Trade.",
        "policy": "Инкрементальная загрузка новых/измененных Prod_adv в отдельную БД KM.",
        "script": PROJECT_ROOT / "scripts" / "import_km_ozon_adv_daily_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{KM_TRADE_DATA_ROOT}\Ozon\Adv; {KM_TRADE_DATA_ROOT}\Ozon\Prod_adv",
        "destination": "km_trade_products.public.ozon_adv_daily_raw, mv_ozon_adv_daily_by_article_category",
    },
    "km_dashboard_views": {
        "report": "Витрины KM Trade",
        "description": "Пересборка ABC, воронки, товарной рекламы, остатков и SKU-витрин KM Trade.",
        "policy": "Пересборка materialized views в отдельной БД KM.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "rebuild_km_dashboard_views.py",
        "source": "Только БД KM Trade",
        "destination": "km_trade_products.public.mv_* dashboard views",
    },
    "km_api_funnel": {
        "report": "API: воронка Ozon",
        "description": "Дозагрузка ежедневной товарной воронки KM Trade через Seller API.",
        "policy": "Только read-only POST /v1/analytics/data; запись разрешена только в km_trade_products.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_api.py",
        "args": ["--step", "funnel"],
        "date_args": True,
        "api_daily": True,
        "api_daily_order": 0,
        "source": "Ozon Seller API /v1/analytics/data",
        "destination": "km_trade_products.public.ozon_funnel_daily",
    },
    "km_api_stock": {
        "report": "API: остатки Ozon",
        "description": "Полный текущий снимок остатков KM Trade по товарам и складам.",
        "policy": "Только read-only POST /v2/analytics/stock_on_warehouses; каждый календарный день добавляется в историю, повторный запуск заменяет только тот же день, пустой ответ не заменяет снимок.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_api.py",
        "args": ["--step", "stock"],
        "date_args": False,
        "api_daily": True,
        "api_daily_order": 1,
        "source": "Ozon Seller API /v2/analytics/stock_on_warehouses",
        "destination": "km_trade_products.public.ozon_stock_*",
    },
    "km_api_advertising": {
        "report": "API: реклама Ozon",
        "description": "Товарная статистика рекламы KM Trade через отдельный Performance API.",
        "policy": "Только OAuth и read-only GET статистики; Seller API key не подменяет Performance credentials.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_api.py",
        "args": ["--step", "advertising"],
        "date_args": True,
        "api_daily": True,
        "api_daily_order": 2,
        "source": "Ozon Performance API /api/client/statistics/campaign/product",
        "destination": "km_trade_products.public.ozon_adv_daily_raw",
    },
    "km_api_finance": {
        "report": "API: финансы, тарифы и индексы Ozon",
        "description": "Ежедневные начисления Ozon по дням плюс текущие комиссии, тарифы и ценовые индексы для Юнитки и P&L.",
        "policy": "Только read-only POST; запросы строго последовательно с паузой 7 секунд, 429 — ожидание не менее 60 секунд; запись только в km_trade_products.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_finance.py",
        "args": ["--source", "api"],
        "date_args": True,
        "api_daily": True,
        "api_daily_order": 3,
        "source": "Ozon Seller API /v1/finance/accrual/by-day, /types, /v5/product/info/prices",
        "destination": "km_trade_products.public.ozon_finance_*, ozon_product_price_snapshots",
    },
    "km_api_views": {
        "report": "API: витрины KM Trade",
        "description": "Пересборка KM-витрин после завершения API-загрузок.",
        "policy": "Работает только с materialized views базы km_trade_products.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "sync_km_ozon_api.py",
        "args": ["--step", "views"],
        "api_daily": True,
        "api_daily_order": 4,
        "source": "Только БД KM Trade",
        "destination": "km_trade_products.public.mv_* dashboard views",
    },
    "km_wb_api_funnel": {
        "report": "API: воронка WB",
        "description": "Дневная воронка карточек KM Trade через отдельный токен WB категории «Аналитика».",
        "policy": "Read-only POST /api/analytics/v3/sales-funnel/products: отдельный запрос на каждый день, все страницы, включая удалённые карточки; повтор обновляет поздние выкупы.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_wb_api.py",
        "args": ["--step", "funnel"],
        "date_args": True,
        "api_daily": True,
        "api_daily_order": 5,
        "source": "WB Seller Analytics API /api/analytics/v3/sales-funnel/products",
        "destination": "km_trade_products.public.wb_funnel_daily",
    },
    "km_wb_api_stock": {
        "report": "API: остатки WB",
        "description": "Текущий снимок остатков и товарный справочник KM Trade из WB Analytics.",
        "policy": "Только read-only POST /api/v2/stocks-report/products/products; пустой ответ не заменяет снимок.",
        "script": PROJECT_ROOT / "scripts" / "sync_km_wb_api.py",
        "args": ["--step", "stock"],
        "date_args": True,
        "api_daily": True,
        "api_daily_order": 6,
        "source": "WB Seller Analytics API /api/v2/stocks-report/products/products",
        "destination": "km_trade_products.public.wb_stock_api_current, products",
    },
    "km_wb_api_views": {
        "report": "API: витрины WB",
        "description": "Пересборка воронки, ABC, SKU, остатков и недельной динамики WB для KM Trade.",
        "policy": "Без внешних запросов; работает только с WB-таблицами базы km_trade_products.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "sync_km_wb_api.py",
        "args": ["--step", "views"],
        "api_daily": True,
        "api_daily_order": 7,
        "source": "Только БД KM Trade",
        "destination": "km_trade_products.public.mv_wb_* + mv_product_abc_wb + mv_sku_card_scoring_wb",
    },
}
KM_TRADE_DAILY_IMPORT_KEYS = [
    "km_ozon_fin_sales",
    "km_ozon_stock",
    "km_ozon_funnel",
    "km_ozon_adv_daily",
    "km_dashboard_views",
]
KM_TRADE_API_DAILY_IMPORT_KEYS = [
    "km_api_funnel",
    "km_api_stock",
    "km_api_advertising",
    "km_api_finance",
    "km_api_views",
    "km_wb_api_funnel",
    "km_wb_api_stock",
    "km_wb_api_views",
]
SPORTMASTER_DATA_ROOT = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Спортмастер\Аналитика\Дашборды"
)
SPORTMASTER_OZON_ROOT = rf"{SPORTMASTER_DATA_ROOT}\ozon"
SPORTMASTER_WB_ROOT = rf"{SPORTMASTER_DATA_ROOT}\WB"
SPORTMASTER_ADMIN_IMPORTS = {
    "sportmaster_initial_load": {
        "report": "Первичная загрузка Спортмастер",
        "description": "Создает совместимую БД и запускает первичные импорты справочников и ежедневных выгрузок.",
        "policy": "Запускать вручную при создании/пересоздании клиентской базы.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_initial_load.py",
        "source": SPORTMASTER_DATA_ROOT,
        "destination": "sportmaster.public.* dashboard tables and materialized views",
    },
    "sportmaster_ozon_product_categories": {
        "report": "Ozon: товары, категории и характеристики",
        "description": "Загружает Ozon Case Cat/Prod в ozon_cat_* таблицы Спортмастера.",
        "policy": "Полная замена справочника.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_ozon_product_categories.py",
        "source": rf"{SPORTMASTER_OZON_ROOT}\Case\Cat; {SPORTMASTER_OZON_ROOT}\Case\Prod",
        "destination": "sportmaster.public.ozon_cat_products, ozon_cat_product_attributes, ozon_cat_common_attributes, ozon_cat_category_attributes",
    },
    "sportmaster_wb_product_categories": {
        "report": "WB: товары, категории и характеристики",
        "description": "Подготавливает WB Case Cat/Prod к загрузке в совместимые WB справочники.",
        "policy": "Первичная загрузка; ZIP выгрузки требуют распаковки/адаптации формата.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_product_categories.py",
        "source": rf"{SPORTMASTER_WB_ROOT}\Case\Cat; {SPORTMASTER_WB_ROOT}\Case\Prod",
        "destination": "sportmaster.public.products, product_attributes, categories, category_attributes, common_attributes",
    },
    "sportmaster_ozon_stock": {
        "report": "Остатки Ozon",
        "description": "Берет самый свежий файл управления остатками Ozon и обновляет stock-витрины.",
        "policy": "Последний снимок; неизмененный XLSX пропускается, измененный заменяет текущие остатки.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_ozon_stock_reports.py",
        "source": rf"{SPORTMASTER_OZON_ROOT}\Stok",
        "destination": "sportmaster.public.ozon_stock_*, vw_ozon_current_stock_by_sku",
    },
    "sportmaster_wb_stock": {
        "report": "Остатки WB",
        "description": "Берет самый свежий Stock.zip WB и обновляет текущие остатки для WB SKU/ABC.",
        "policy": "Последний снимок; неизмененный ZIP пропускается, измененный заменяет текущие остатки.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_stock_reports.py",
        "source": rf"{SPORTMASTER_WB_ROOT}\Stock",
        "destination": "sportmaster.public.wb_stock_detail_raw, vw_wb_current_stock_by_article",
    },
    "sportmaster_wb_funnel": {
        "report": "Воронка WB",
        "description": "Ежедневная воронка WB для Спортмастера.",
        "policy": "Инкрементально: успешно обработанные неизмененные файлы пропускаются; --force-reimport только вручную.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_funnel_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_WB_ROOT}\Funel",
        "destination": "sportmaster.public.wb_funnel_daily, mv_wb_funnel_*",
    },
    "sportmaster_wb_entrance": {
        "report": "Точки входа WB",
        "description": "Дневная эффективность разделов и точек входа WB по SKU Спортмастера.",
        "policy": "Инкрементально по ZIP; неизмененные файлы пропускаются; детальный лист сверяется с агрегированным.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_entrance_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_WB_ROOT}\Entrance (включая месячные подпапки)",
        "destination": "sportmaster.public.wb_entrance_daily, wb_entrance_import_files, mv_wb_entrance_*",
    },
    "sportmaster_wb_entrance_views": {
        "report": "Витрины точек входа WB",
        "description": "Пересобирает дневные, товарные и точечные витрины Entrance без чтения Excel.",
        "policy": "Только пересборка витрин.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_entrance_reports.py",
        "args": ["--skip-import"],
        "source": "Только БД sportmaster.public.wb_entrance_daily",
        "destination": "sportmaster.public.mv_wb_entrance_daily_summary, mv_wb_entrance_entry_daily, mv_wb_entrance_product_daily",
    },
    "sportmaster_wb_market_search_queries": {
        "report": "Поисковый спрос площадки WB",
        "description": "Периодические Serp-выгрузки WB: ключи и частотность площадки для сравнения с товарами Спортмастера.",
        "policy": "Инкрементально по ZIP; неизмененные файлы пропускаются, предмет WB точно сопоставляется с категориями SEO-отчета.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_market_search_queries.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_WB_ROOT}\Serp",
        "destination": "sportmaster.public.wb_market_search_queries_period, wb_market_search_import_files, mv_wb_market_search_category_query_period",
    },
    "sportmaster_wb_market_search_query_views": {
        "report": "Витрина поискового спроса площадки WB",
        "description": "Сопоставляет Serp-ключи с категориями Спортмастера и пересчитывает ВЧ/СЧ/НЧ.",
        "policy": "Только пересборка витрины без повторного чтения ZIP/XLSX.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_market_search_queries.py",
        "args": ["--skip-import"],
        "source": "Только БД sportmaster.public.wb_market_search_queries_period и wb_search_queries_daily",
        "destination": "sportmaster.public.mv_wb_market_search_category_query_period",
    },
    "sportmaster_wb_search_queries": {
        "report": "Поисковые запросы WB",
        "description": "Дневные товаро-запросные выгрузки WB Спортмастера: спрос, видимость, позиции и воронка.",
        "policy": "Инкрементально по ZIP: успешно загруженные неизмененные файлы пропускаются; --force-reimport только вручную.",
        "script": PROJECT_ROOT / "scripts" / "import_wb_search_queries.py",
        "args": ["--client", "sportmaster"],
        "defer_views_args": ["--skip-views", "--skip-classification"],
        "source": rf"{SPORTMASTER_WB_ROOT}\SEO (включая месячные подпапки)",
        "destination": "sportmaster.public.wb_search_queries_daily, wb_search_queries_import_files, wb_search_query_classification, mv_wb_search_query_*",
    },
    "sportmaster_wb_search_query_views": {
        "report": "Витрины поисковых запросов WB",
        "description": "Пересобирает поисковые витрины, ВЧ/СЧ/НЧ и семантическую классификацию Спортмастера.",
        "policy": "Только пересборка витрин и классификации, без повторного чтения ZIP/XLSX.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_wb_search_queries.py",
        "args": ["--client", "sportmaster", "--skip-import"],
        "source": "Только БД sportmaster.public.wb_search_queries_daily",
        "destination": "sportmaster.public.wb_search_query_classification, mv_wb_search_query_daily, mv_wb_search_category_query_daily, mv_wb_search_queries_daily_summary",
    },
    "sportmaster_ozon_funnel": {
        "report": "Воронка Ozon",
        "description": "Ежедневная воронка Ozon для Спортмастера.",
        "policy": "Инкрементально: успешно обработанные неизмененные файлы пропускаются; --force-reimport только вручную.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_ozon_funnel_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_OZON_ROOT}\Funel",
        "destination": "sportmaster.public.ozon_funnel_daily, mv_ozon_funnel_*",
    },
    "sportmaster_ozon_adv_daily": {
        "report": "Товарная реклама Ozon",
        "description": "Товарная реклама Ozon из папки Prod_adv.",
        "policy": "Инкрементально: успешно обработанные неизмененные файлы пропускаются; --force-reimport только вручную.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_ozon_adv_daily_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_OZON_ROOT}\Prod_adv",
        "destination": "sportmaster.public.ozon_adv_daily_raw, mv_ozon_adv_daily_by_article_category",
    },
    "sportmaster_ozon_media_adv": {
        "report": "Медийная реклама Ozon",
        "description": "Медийная реклама Ozon из папки Media_adv.",
        "policy": "Инкрементально: обработанные неизмененные XLSX пропускаются; файлы ошибок повторяются.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_ozon_media_adv_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_OZON_ROOT}\Media_adv",
        "destination": "sportmaster.public.ozon_media_adv_daily_raw, mv_ozon_media_adv_daily",
    },
    "sportmaster_wb_adv_daily": {
        "report": "Товарная реклама WB",
        "description": "Ежедневная статистика товарных рекламных кампаний WB из папки Adv.",
        "policy": "Инкрементально: успешно загруженные неизмененные кампании пропускаются; ID кампании не подменяет отсутствующий SKU.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_adv_campaign_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_WB_ROOT}\Adv",
        "destination": "sportmaster.public.wb_adv_campaign_daily_raw, wb_adv_campaign_import_files, mv_wb_adv_daily_by_article_category",
    },
    "sportmaster_wb_media_adv": {
        "report": "Медийная реклама WB",
        "description": "Кампании, группы объявлений и креативы WB из папки Media_adv.",
        "policy": "Инкрементально: успешно обработанные неизмененные файлы пропускаются; папка Comp распознается как уровень кампаний.",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_media_adv_reports.py",
        "defer_views_args": ["--skip-views"],
        "source": rf"{SPORTMASTER_WB_ROOT}\Media_adv",
        "destination": "sportmaster.public.wb_media_adv_daily_raw, wb_media_adv_import_files",
    },
    "sportmaster_wb_media_adv_views": {
        "report": "Витрины медийной рекламы WB",
        "description": "Пересобирает WB media-витрины кампаний, групп и креативов.",
        "policy": "Только пересборка витрин, без чтения Excel.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "import_sportmaster_wb_media_adv_reports.py",
        "args": ["--skip-import"],
        "source": "Только БД sportmaster.public.wb_media_adv_daily_raw",
        "destination": "sportmaster.public.mv_wb_media_adv_campaign_daily, mv_wb_media_adv_group_daily, mv_wb_media_adv_creative_daily",
    },
    "sportmaster_dashboard_views": {
        "report": "Витрины Спортмастер",
        "description": "Пересобирает materialized views после импортов.",
        "policy": "Только пересборка витрин, без чтения Excel.",
        "daily_stage": "views",
        "script": PROJECT_ROOT / "scripts" / "rebuild_sportmaster_dashboard_views.py",
        "source": "Только БД sportmaster",
        "destination": "sportmaster.public.mv_* dashboard views",
    },
}
SPORTMASTER_DAILY_IMPORT_KEYS = [
    "sportmaster_ozon_stock",
    "sportmaster_wb_stock",
    "sportmaster_wb_funnel",
    "sportmaster_wb_search_queries",
    "sportmaster_wb_market_search_queries",
    "sportmaster_wb_entrance",
    "sportmaster_ozon_funnel",
    "sportmaster_ozon_adv_daily",
    "sportmaster_ozon_media_adv",
    "sportmaster_wb_adv_daily",
    "sportmaster_wb_media_adv",
    "sportmaster_wb_search_query_views",
    "sportmaster_wb_market_search_query_views",
    "sportmaster_wb_entrance_views",
    "sportmaster_wb_media_adv_views",
    "sportmaster_dashboard_views",
]
COLUMN_LABELS = {
    "category_name": "Категория",
    "total_stock_qty": "Остаток, шт",
    "sku_count": "SKU",
    "category_attribute_count": "Характеристик",
    "zakazano_sht": "Заказано, шт",
    "zakazano_rub": "Заказано, руб",
    "avg_price_rub": "Средняя цена, руб",
    "vykupleno_sht": "Выкуплено, шт",
    "vykupleno_rub": "Выкуплено, руб",
    "vykup_pct_sht": "Выкуп, % шт",
    "vykup_pct_rub": "Выкуп, % руб",
    "orders_share_pct": "Доля заказов, %",
    "orders_cumulative_pct": "Накоп. доля заказов, %",
    "abc_orders": "ABC заказов",
    "sales_share_pct": "Доля продаж, %",
    "sales_cumulative_pct": "Накоп. доля продаж, %",
    "abc_sales": "ABC продаж",
    "stock_share_pct": "Доля остатков, %",
    "stock_cumulative_pct": "Накоп. доля остатков, %",
    "abc_stock": "ABC остатков",
    "abc_combined": "ABC итог",
    "artikul_wb": "SKU",
    "sku_wb": "SKU WB",
    "sku_ozon": "SKU Ozon",
    "naimenovanie": "Наименование",
    "gj_wb_article": "Артикул WB",
    "gj_ozon_sku": "SKU Ozon",
    "gj_model": "Модель GJ",
    "assortment_bia": "Осн. Ассорт / БиА",
    "tg": "ТГ",
    "tg_plus": "ТГ+",
    "cg": "Пол / ЦГ",
        "season": "Сезон",
        "priority_collection": "Приоритетная коллекция",
        "naimenovanie_len": "Длина наименования",
    "opisanie_len": "Длина описания",
    "common_attrs_total": "Общих атрибутов",
    "common_attrs_filled": "Заполнено общих",
    "category_attrs_total": "Категорийных атрибутов",
    "category_attrs_filled": "Заполнено категорийных",
    "foto_count": "Фото, шт",
    "reyting_kartochki": "Рейтинг карточки",
    "reyting_po_otzyvam": "Рейтинг по отзывам",
}
NUMERIC_FIELDS = {
    "total_stock_qty",
    "sku_count",
    "category_attribute_count",
    "zakazano_sht",
    "zakazano_rub",
    "stock_value_rub",
    "avg_price_rub",
    "vykupleno_sht",
    "vykupleno_rub",
    "vykup_pct_sht",
    "vykup_pct_rub",
    "orders_share_pct",
    "orders_cumulative_pct",
    "sales_share_pct",
    "sales_cumulative_pct",
    "stock_share_pct",
    "stock_cumulative_pct",
    "naimenovanie_len",
    "opisanie_len",
    "common_attrs_total",
    "common_attrs_filled",
    "category_attrs_total",
    "category_attrs_filled",
    "foto_count",
    "reyting_kartochki",
    "reyting_po_otzyvam",
}
COLUMN_LABELS.update(
    {
        "orders_qty_share_pct": "Доля заказов, шт %",
        "orders_qty_cumulative_pct": "Накоп. доля заказов, шт %",
    }
)
NUMERIC_FIELDS.update(
    {
        "orders_qty_share_pct",
        "orders_qty_cumulative_pct",
    }
)

OZON_SKU_CARD_COMMON_ATTRIBUTES = [
    ("#Хештеги", "heshtegi"),
    ("Rich-контент JSON", "rich_kontent_json"),
    ("SKU", "card_sku"),
    ("Аннотация", "annotatsiya"),
    ("Артикул*", "artikul_prodavtsa"),
    ("Вес в упаковке, г*", "ves_v_upakovke_g"),
    ("Высота упаковки, мм*", "vysota_upakovki_mm"),
    ("Длина упаковки, мм*", "dlina_upakovki_mm"),
    ("Количество заводских упаковок", "kolichestvo_zavodskih_upakovok"),
    ("Количество товара в УЕИ", "kolichestvo_tovara_v_uei"),
    ("Минимальное количество оптом", "minimalnoe_kolichestvo_optom"),
    ("НДС, %*", "nds_pct"),
    ("Название товара", "card_name"),
    ("Объединить в похожие товары", "obedinit_v_pohozhie_tovary"),
    ("Рассрочка", "rassrochka"),
    ("Ссылка на главное фото*", "ssylka_na_glavnoe_foto"),
    ("Ссылки на дополнительные фото", "ssylki_na_dopolnitelnye_foto"),
    ("Страна-изготовитель", "strana_izgotovitel"),
    ("Тип*", "card_tip"),
    ("Ускоренный сбор отзывов", "uskorennyy_sbor_otzyvov"),
    ("Цена до скидки, руб.", "tsena_do_skidki_rub"),
    ("Цена, руб.*", "tsena_rub"),
    ("Ширина упаковки, мм*", "shirina_upakovki_mm"),
    ("Штрихкод (Серийный номер / EAN)", "shtrihkod_seriynyy_nomer_ean"),
]

WB_SKU_CARD_COMMON_ATTRIBUTES = [
    ("Артикул OZON", "artikul_ozon"),
    ("Артикул продавца", "artikul_prodavtsa"),
    ("Баркод", "barkod"),
    ("Бренд", "brend"),
    ("Вес с упаковкой, кг*", "ves_s_upakovkoy_kg"),
    ("Видео", "video"),
    ("Высота упаковки", "vysota_upakovki"),
    ("Группа", "gruppa"),
    ("Длина упаковки", "dlina_upakovki"),
    ("Категория продавца", "kategoriya_prodavtsa"),
    ("Комплектация", "komplektatsiya"),
    ("Ставка НДС", "stavka_nds"),
    ("Страна производства", "strana_proizvodstva"),
    ("Фото", "foto"),
    ("Ширина упаковки", "shirina_upakovki"),
]

OZON_SKU_CARD_PRIMARY_NAMES = {
    "Наименование",
    "Описание",
    "Артикул*",
    "Вес в упаковке, г*",
    "Высота упаковки, мм*",
    "Длина упаковки, мм*",
    "НДС, %*",
    "Название товара",
    "Ссылка на главное фото*",
    "Тип*",
    "Цена, руб.*",
    "Ширина упаковки, мм*",
    "Штрихкод (Серийный номер / EAN)",
    "Вес с упаковкой, кг*",
}
COLUMN_LABELS.update(
    {
        "report_date": "Дата",
        "ozon_marketplace_article": "Артикул Ozon",
        "seller_article": "Артикул продавца",
        "match_type": "Тип сопоставления",
        "product_id": "ID товара",
        "sku": "SKU",
        "product_artikul": "Артикул товара",
        "product_name": "Наименование",
        "seo_status": "Тег Seo",
        "category_id": "ID категории",
        "impressions": "Показы",
        "promoted_sku": "SKU в продвижении",
        "promoted_sku_count": "Товаров в продвижении",
        "ordered_sku_count": "Товаров с заказами",
        "total_sku_count": "Товаров итого",
        "clicks": "Клики",
        "ctr_pct": "CTR, %",
        "expense_rub": "Расход, руб",
        "fact_expense_rub": "Факт. расход, руб",
        "cpc_rub": "CPC, руб",
        "cpm_rub": "CPM, руб",
        "added_to_cart": "В корзину",
        "orders_qty": "Заказы с рекламы, шт",
        "direct_orders_qty": "Заказы · прямая атрибуция, шт",
        "indirect_orders_qty": "Заказы · косвенная атрибуция, шт",
        "cr_pct": "CR, %",
        "orders_amount_rub": "Продажи с рекламы, руб",
        "direct_orders_amount_rub": "Продажи · прямая атрибуция, руб",
        "indirect_orders_amount_rub": "Продажи · косвенная атрибуция, руб",
        "cpa_rub": "CPA, руб",
        "drr_pct": "ДРР рекламы, %",
        "direct_drr_pct": "ДРР · прямая атрибуция, %",
        "indirect_drr_pct": "ДРР · косвенная атрибуция, %",
        "total_orders_qty": "Общие заказы, шт",
        "total_orders_amount_rub": "Общие продажи, руб",
        "total_drr_pct": "Общий ДРР, %",
        "total_cpa_rub": "Общий CPA, руб",
        "adv_sales_to_total_sales_pct": "Продажи рекламы / общие продажи, %",
        "period_from": "Период от",
        "period_to": "Период до",
        "seller_article": "Артикул продавца",
        "barcode": "Штрихкод",
        "category_level_1": "Категория 1 уровня",
        "category_level_2": "Категория 2 уровня",
        "category_level_3": "Категория 3 уровня",
        "brand": "Бренд",
        "model": "Модель",
        "work_schema": "Схема работы",
        "abc_orders_amount": "ABC по сумме заказов",
        "abc_orders_qty": "ABC по количеству заказов",
        "ordered_amount_rub": "Заказано, руб",
        "bought_units": "Выкуплено, шт",
        "bought_amount_rub": "Выкупы минус возвраты по дате операции, руб",
    "cohort_bought_units": "Выкуплено по дате заказа, шт",
    "cohort_bought_amount_rub": "Выкуплено по дате заказа, руб",
        "ordered_amount_dynamic": "Динамика заказов, руб",
        "search_catalog_position": "Позиция в поиске и каталоге",
        "search_catalog_position_dynamic": "Динамика позиции",
        "impressions_total": "Показы всего",
        "impressions_total_dynamic": "Динамика показов всего",
        "impressions_search_catalog": "Показы в поиске и каталоге",
        "impressions_search_catalog_dynamic": "Динамика показов в поиске",
        "card_visits": "Посещения карточки",
        "card_visits_dynamic": "Динамика посещений",
        "cart_adds": "Добавления в корзину",
        "cart_adds_dynamic": "Динамика корзин",
        "ordered_units": "Заказано товаров",
        "ordered_units_dynamic": "Динамика заказанных товаров",
        "search_to_card_visit_pct": "Поиск -> карточка, %",
        "total_impression_to_card_visit_pct": "Показы -> карточка, %",
        "card_visit_to_cart_pct": "Карточка -> корзина, %",
        "cart_to_order_pct": "Корзина -> заказ, %",
        "card_visit_to_order_pct": "Карточка -> заказ, %",
        "ordered_amount_per_unit_rub": "Средний заказ, руб/шт",
        "source_file": "Файл-источник",
        "source_sheet": "Лист",
        "source_row_num": "Строка источника",
        "file_size_bytes": "Размер файла, байт",
        "file_mtime": "Дата файла",
        "imported_at": "Дата импорта",
        "marketplace_label": "Маркетплейс",
        "orders_rub": "Заказы, руб",
        "sales_rub": "Факт продаж, руб",
        "ad_spend_rub": "Расходы, руб",
        "sales_plan_rub": "План продаж, руб",
        "ad_spend_plan_rub": "Бюджет, руб",
        "sales_month_plan_fact_pct": "План/факт продаж, %",
        "sales_elapsed_plan_fact_pct": "План/факт к ранрейту, %",
        "ad_spend_budget_used_pct": "Использование бюджета, %",
        "tacos_pct": "TACOS, %",
        "tacos_cum_pct": "TACOS накоп., %",
        "adv_impressions": "Показы рекламы",
        "adv_clicks": "Клики рекламы",
        "adv_sales_rub": "Продажи с рекламы, руб",
        "adv_acos_pct": "ACOS, %",
        "adv_tacos_pct": "TACOS, %",
        "campaign_id": "ID кампании",
        "campaign_name": "Кампания",
        "campaign_section": "Раздел кампании",
        "bid_type": "Тип ставки",
        "campaign_brand": "Бренд кампании",
        "started_at": "Старт кампании",
        "finished_at": "Финиш кампании",
        "frequency": "Частота",
        "placement": "Место",
        "duration_text": "Длительность",
        "currency": "Валюта",
        "media_level": "Уровень медийной рекламы",
        "group_id": "ID группы объявлений",
        "creative_id": "ID креатива",
        "entity_id": "ID объекта",
        "entity_name": "Объект",
        "campaign_format": "Формат кампании",
        "campaign_status": "Статус кампании",
        "campaign_segment": "Сегмент",
        "promoted_category": "Продвигаемая категория",
        "reach": "Охват",
        "budget_rub": "Бюджет, руб",
        "balance_rub": "Баланс, руб",
        "views": "Просмотры",
        "vr_pct": "VR, %",
        "vp25": "VP25",
        "vp50": "VP50",
        "vp75": "VP75",
        "video_started": "Запуски видео",
        "video_completed": "Досмотры видео",
        "video_paused": "Паузы видео",
        "video_resumed": "Возобновления видео",
        "baskets": "Корзины",
        "orders": "Заказы всего",
        "orders_post_click": "Заказы post-click",
        "orders_qty_post_click": "Товары post-click, шт",
        "orders_sum_post_click": "Выручка post-click, руб",
        "baskets_post_click": "Корзины post-click",
        "baskets_qty_post_click": "Товары в корзинах post-click, шт",
        "baskets_sum_post_click": "Сумма корзин post-click, руб",
        "orders_post_view": "Заказы post-view",
        "orders_qty_post_view": "Товары post-view, шт",
        "orders_sum_post_view": "Выручка post-view, руб",
        "baskets_post_view": "Корзины post-view",
        "baskets_qty_post_view": "Товары в корзинах post-view, шт",
        "baskets_sum_post_view": "Сумма корзин post-view, руб",
        "daily_budget_rub": "Дневной бюджет, руб",
        "campaign_budget_rub": "Бюджет кампании, руб",
        "budget_type": "Тип бюджета",
        "payment_model": "Способ оплаты",
        "ctr_calc_pct": "CTR расчетный, %",
        "cpm_calc_rub": "CPM расчетный, руб",
        "cpc_calc_rub": "CPC расчетный, руб",
        "post_view_orders_qty": "Заказы post-view, шт",
        "post_view_revenue_rub": "Выручка post-view, руб",
        "attributed_orders_qty": "Заказы атрибутированные, шт",
        "attributed_revenue_rub": "Выручка атрибутированная, руб",
        "drr_direct_pct": "ДРР прямой, %",
        "drr_attributed_pct": "ДРР с post-view, %",
        "direct_roas": "ROAS прямой",
        "attributed_roas": "ROAS с post-view",
        "click_to_order_pct": "Клик -> заказ, %",
        "post_view_orders_per_1000_impressions": "Post-view заказы / 1000 показов",
        "revenue_per_1000_impressions": "Выручка / 1000 показов",
        "post_view_revenue_share_pct": "Доля post-view выручки, %",
    }
)
NUMERIC_FIELDS.update(
    {
        "product_id",
        "category_id",
        "impressions",
        "promoted_sku_count",
        "ordered_sku_count",
        "total_sku_count",
        "clicks",
        "ctr_pct",
        "expense_rub",
        "fact_expense_rub",
        "cpc_rub",
        "cpm_rub",
        "reach",
        "budget_rub",
        "balance_rub",
        "views",
        "vr_pct",
        "vp25",
        "vp50",
        "vp75",
        "video_started",
        "video_completed",
        "video_paused",
        "video_resumed",
        "baskets",
        "orders",
        "orders_post_click",
        "orders_qty_post_click",
        "orders_sum_post_click",
        "baskets_post_click",
        "baskets_qty_post_click",
        "baskets_sum_post_click",
        "orders_post_view",
        "orders_qty_post_view",
        "orders_sum_post_view",
        "baskets_post_view",
        "baskets_qty_post_view",
        "baskets_sum_post_view",
        "added_to_cart",
        "orders_qty",
        "direct_orders_qty",
        "indirect_orders_qty",
        "cr_pct",
        "orders_amount_rub",
        "direct_orders_amount_rub",
        "indirect_orders_amount_rub",
        "direct_drr_pct",
        "indirect_drr_pct",
        "cpa_rub",
        "drr_pct",
        "total_orders_qty",
        "total_orders_amount_rub",
        "total_drr_pct",
        "total_cpa_rub",
        "adv_sales_to_total_sales_pct",
        "ordered_amount_rub",
        "bought_units",
        "bought_amount_rub",
            "cohort_bought_units",
            "cohort_bought_amount_rub",
        "favorites_adds",
        "cancelled_units",
        "cancelled_amount_rub",
        "wb_club_ordered_units",
        "wb_club_bought_units",
        "wb_club_cancelled_units",
        "wb_club_ordered_amount_rub",
        "wb_club_bought_amount_rub",
        "wb_club_cancelled_amount_rub",
        "favorite_to_card_visit_pct",
        "buyout_pct",
        "cancellation_pct",
        "wb_club_order_share_pct",
        "adv_ctr_pct",
        "adv_click_to_cart_pct",
        "adv_cart_to_order_pct",
        "adv_click_to_order_pct",
        "adv_cpc_rub",
        "adv_cpa_rub",
        "adv_cpm_rub",
        "ordered_amount_dynamic",
        "search_catalog_position",
        "search_catalog_position_dynamic",
        "impressions_total",
        "impressions_total_dynamic",
        "impressions_search_catalog",
        "impressions_search_catalog_dynamic",
        "card_visits",
        "card_visits_dynamic",
        "cart_adds",
        "cart_adds_dynamic",
        "ordered_units",
        "ordered_units_dynamic",
        "search_to_card_visit_pct",
        "total_impression_to_card_visit_pct",
        "card_visit_to_cart_pct",
        "cart_to_order_pct",
        "card_visit_to_order_pct",
        "ordered_amount_per_unit_rub",
        "source_row_num",
        "file_size_bytes",
        "orders_rub",
        "sales_rub",
        "ad_spend_rub",
        "sales_plan_rub",
        "ad_spend_plan_rub",
        "sales_month_plan_fact_pct",
        "sales_elapsed_plan_fact_pct",
        "ad_spend_budget_used_pct",
        "tacos_pct",
        "tacos_cum_pct",
        "adv_impressions",
        "adv_clicks",
        "adv_sales_rub",
        "adv_acos_pct",
        "adv_tacos_pct",
        "daily_budget_rub",
        "campaign_budget_rub",
        "ctr_calc_pct",
        "cpm_calc_rub",
        "cpc_calc_rub",
        "post_view_orders_qty",
        "post_view_revenue_rub",
        "attributed_orders_qty",
        "attributed_revenue_rub",
        "drr_direct_pct",
        "drr_attributed_pct",
        "direct_roas",
        "attributed_roas",
        "click_to_order_pct",
        "post_view_orders_per_1000_impressions",
        "revenue_per_1000_impressions",
        "post_view_revenue_share_pct",
    }
)
DEFAULT_CONFIG_SOURCE = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Скоринг ассортимнетной матрицы\Выгрузки\Ассортиментная матрица\WB\wb_data_import.py"
)
WB_API_TOKEN_ENV = "WB_API_TOKEN"
WB_API_ENV_FILE = ROOT / ".env.local"
WB_API_RATE_LIMITS_FILE = ROOT / ".wb_api_rate_limits.json"
OZON_SELLER_CLIENT_ID_ENV = "OZON_SELLER_CLIENT_ID"
OZON_SELLER_API_KEY_ENV = "OZON_SELLER_API_KEY"
OZON_PERFORMANCE_CLIENT_ID_ENV = "OZON_PERFORMANCE_CLIENT_ID"
OZON_PERFORMANCE_CLIENT_SECRET_ENV = "OZON_PERFORMANCE_CLIENT_SECRET"
WB_MEDIA_API_BASE_URL = "https://advert-media-api.wildberries.ru"
WB_PROMOTION_API_BASE_URL = "https://advert-api.wildberries.ru"
WB_CONTENT_API_BASE_URL = "https://content-api.wildberries.ru"
WB_ANALYTICS_API_BASE_URL = "https://seller-analytics-api.wildberries.ru"
WB_MEDIA_COUNT_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_MEDIA_COUNT_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Count"
        ),
    )
)
WB_MEDIA_ADVERTS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_MEDIA_ADVERTS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Adverts"
        ),
    )
)
WB_MEDIA_STATS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_MEDIA_STATS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Stats"
        ),
    )
)
WB_PROMOTION_COUNT_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_PROMOTION_COUNT_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Promotion\Count"
        ),
    )
)
WB_PROMOTION_ADVERTS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_PROMOTION_ADVERTS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Promotion\Adverts"
        ),
    )
)
WB_PROMOTION_STATS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_PROMOTION_STATS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Promotion\Stats"
        ),
    )
)
WB_CONTENT_CATEGORIES_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_CONTENT_CATEGORIES_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Content\Categories"
        ),
    )
)
WB_CONTENT_CARDS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_CONTENT_CARDS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Content\Cards"
        ),
    )
)
WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR",
        (
            r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
            r"\Дашборды\Data\Wb\Api\Content\Characteristics"
        ),
    )
)
WB_API_LOG_OUTPUT_DIR = Path(
    os.environ.get(
        "WB_API_LOG_OUTPUT_DIR",
        str(Path(WB_PROMOTION_STATS_OUTPUT_DIR).parent / "Logs"),
    )
)
WB_API_LAST_LOG_FILES = {}
WB_API_LOG_LOCK = threading.Lock()
WB_API_READ_ONLY_METHODS = {"GET"}
WB_API_READ_ONLY_PATHS = {
    "/adv/v1/count",
    "/adv/v1/adverts",
    "/adv/v1/promotion/count",
    "/api/advert/v2/adverts",
    "/adv/v3/fullstats",
    "/content/v2/object/parent/all",
    "/content/v2/object/all",
}
WB_API_READ_ONLY_PATH_PREFIXES = (
    "/content/v2/object/charcs/",
)
WB_API_READ_ONLY_POST_PATHS = {
    "/adv/v1/stats",
    "/content/v2/get/cards/list",
}
WB_MEDIA_STATS_CHUNK_SIZE = 100
WB_MEDIA_STATS_CHUNK_PAUSE_SECONDS = 1.0
WB_MEDIA_STATS_EXCLUDED_STATUSES = {
    1: "черновик",
    2: "модерация",
    3: "отклонена",
    4: "готова",
    5: "запланирована",
    8: "отменена",
}
WB_MEDIA_STATS_FINISHED_STATUS = 7
WB_PROMOTION_ADVERTS_CHUNK_SIZE = 50
WB_PROMOTION_FULLSTATS_CHUNK_SIZE = 50
WB_PROMOTION_FULLSTATS_MAX_DAYS = 31
WB_PROMOTION_API_PAUSE_SECONDS = 20.0
WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_RETRY_SECONDS = 60
WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES = 3
WB_PROMOTION_STATS_STATUSES = {7, 9, 11}
WB_PROMOTION_FULLSTATS_AUTO_MODE_STRICT_STARTED = "strict_started"
WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD = "active_period"
WB_PROMOTION_FULLSTATS_AUTO_MODE_ALL_STATUS = "all_status"
WB_PROMOTION_FULLSTATS_WINDOW_PERIOD = "period"
WB_PROMOTION_FULLSTATS_WINDOW_DAY = "day"
WB_PROMOTION_FULLSTATS_WINDOW_CAMPAIGN_LIFETIME = "campaign_lifetime"
WB_PROMOTION_FULLSTATS_RESULT_DETAILS = "details"
WB_PROMOTION_FULLSTATS_RESULT_CAMPAIGNS = "campaign_totals"
WB_PROMOTION_FULLSTATS_CAMPAIGN_BATCH_SIZES = {1, 5, 10, 20, 25, 30, 35, 50}


def read_app_env_file(path=None):
    env_path = Path(path or WB_API_ENV_FILE)
    if not env_path.exists():
        return {}
    values = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_app_env():
    for key, value in read_app_env_file().items():
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def save_env_value(key, value, path=None):
    env_path = Path(path or WB_API_ENV_FILE)
    lines = []
    replaced = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    next_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            next_lines.append(f"{key}={value}")
            replaced = True
        else:
            next_lines.append(line)
    if not replaced:
        next_lines.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(next_lines).strip() + "\n", encoding="utf-8")
    os.environ[key] = value
    return env_path


CLIENT_CREDENTIALS_MASTER_KEY_ENV = "CLIENT_CREDENTIALS_MASTER_KEY"


def client_registry_connection():
    config = read_db_config(DEFAULT_CLIENT)
    return psycopg2.connect(**config, cursor_factory=RealDictCursor)


def client_credentials_master_key(create=False):
    value = str(
        os.environ.get(CLIENT_CREDENTIALS_MASTER_KEY_ENV)
        or read_app_env_file().get(CLIENT_CREDENTIALS_MASTER_KEY_ENV)
        or ""
    ).strip()
    if not value and create:
        value = secrets.token_urlsafe(48)
        save_env_value(CLIENT_CREDENTIALS_MASTER_KEY_ENV, value)
    return value


def apply_registered_client(row):
    key = str(row.get("key") or "").strip().lower()
    if not key:
        return
    ADMIN_CLIENTS[key] = {
        "label": row["label"],
        "db_name": row["db_name"],
        "status": row.get("status") or "active",
        "description": f"Управляется через реестр клиентов. Источник: {row.get('root_path') or ''}",
        "root_path": row.get("root_path") or rf"G:\Clients\{row['label']}",
        "show_in_dashboard": (row.get("status") or "active") == "active",
        "reports": list(row.get("reports") or []),
        "marketplaces": list(row.get("marketplaces") or []),
        "history_date_from": row.get("history_date_from") or "",
        "history_date_to": row.get("history_date_to") or "",
    }


def hydrate_registered_clients():
    from client_registry import list_clients

    with client_registry_connection() as conn:
        rows = list_clients(conn)
    for row in rows:
        apply_registered_client(row)
    return len(rows)


def admin_clients_payload():
    from client_onboarding_runtime import reconcile_orphaned_rows
    from client_registry import list_clients

    with client_registry_connection() as conn:
        stored_rows = list_clients(conn)
    stored_rows = reconcile_orphaned_rows(stored_rows)
    stored = {row["key"]: row for row in stored_rows}
    rows = []
    for key, config in ADMIN_CLIENTS.items():
        row = stored.pop(key, None) or {
            "key": key,
            "label": config["label"],
            "db_name": config["db_name"],
            "status": config["status"],
            "root_path": config.get("root_path") or rf"G:\Общие диски\Kokoc Marketplaces\Clients\{config['label']}",
            "marketplaces": list(config.get("marketplaces") or []),
            "reports": list(config.get("reports") or []),
            "credentials": {},
            "created_at": "",
            "updated_at": "",
        }
        row["source"] = "database" if key in {item["key"] for item in stored_rows} else "configuration"
        rows.append(row)
    rows.extend(stored.values())
    rows.sort(key=lambda row: str(row.get("label") or "").lower())
    return {
        "ok": True,
        "clients": rows,
        "reports": ADMIN_REPORT_CATALOG,
        "credentials_encrypted": True,
        "credential_storage": "PostgreSQL pgcrypto / AES-256",
        "scaffold_script": str(PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "scaffold_client.py"),
        "clients_root": r"G:\Общие диски\Kokoc Marketplaces\Clients",
    }


def save_admin_client(payload):
    from client_onboarding import discover_yandex_market_accounts
    from client_registry import normalize_client_payload, save_client, save_marketplace_accounts

    client = normalize_client_payload(payload, {item["id"] for item in ADMIN_REPORT_CATALOG})
    raw_credentials = payload.get("credentials") if isinstance(payload.get("credentials"), dict) else {}
    credentials = {key: str(value or "").strip() for key, value in raw_credentials.items()}
    avito_keys = ("avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret")
    avito_values = {
        key: credentials.get(key) or registered_client_credential(client["key"], key)
        for key in avito_keys
    }
    if any(avito_values.values()) and not all(avito_values.values()):
        raise ValueError("Для Avito Ads заполните Account ID, Client ID и Client Secret")
    if avito_values["avito_ads_account_id"] and not str(avito_values["avito_ads_account_id"]).isdigit():
        raise ValueError("Avito Ads Account ID должен состоять из цифр")
    for marketplace_label, credential_keys in (
        ("Lamoda", ("lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id")),
    ):
        values = {
            key: credentials.get(key) or registered_client_credential(client["key"], key)
            for key in credential_keys
        }
        if any(values.values()) and not all(values.values()):
            raise ValueError(f"Для {marketplace_label} заполните все обязательные поля доступа")
    yandex_api_key = ""
    if "yandex_market" in client["marketplaces"]:
        yandex_api_key = credentials.get("yandex_market_api_key") or registered_client_credential(
            client["key"], "yandex_market_api_key"
        )
        if not yandex_api_key:
            raise ValueError("Для Яндекс Маркета заполните API-ключ")
    # IDs and accessibility are authoritative only when returned by Yandex.
    # The form supplies import choices, never trusted account/store identities.
    yandex_accounts = []
    # Updating Lamoda, Avito, WB or Ozon must not depend on an unrelated
    # Yandex API call. Rediscover Yandex accounts only when its key is part
    # of this request; otherwise keep the already persisted account mapping.
    if "yandex_market" in client["marketplaces"] and credentials.get("yandex_market_api_key"):
        try:
            yandex_accounts = discover_yandex_market_accounts(yandex_api_key)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
    master_key = client_credentials_master_key(create=any(credentials.values()))
    with client_registry_connection() as conn:
        save_client(conn, client, credentials, master_key)
        if yandex_accounts:
            raw_enabled_store_ids = payload.get("yandex_market_enabled_store_ids")
            enabled_store_ids = {
                str(value).strip()
                for value in raw_enabled_store_ids or []
                if str(value).strip()
            } if isinstance(raw_enabled_store_ids, list) else None
            save_marketplace_accounts(
                conn, client["key"], "yandex_market", yandex_accounts, enabled_store_ids
            )
    apply_registered_client(client)
    result = admin_clients_payload()
    result["saved_client"] = client["key"]
    return result


def discover_admin_yandex_market(payload):
    from client_onboarding import discover_yandex_market_accounts

    client_key = normalize_client_key(payload.get("client"))
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key and client_key:
        api_key = registered_client_credential(client_key, "yandex_market_api_key")
    accounts = discover_yandex_market_accounts(api_key)
    return {
        "ok": True,
        "accounts": accounts,
        "business_count": len(accounts),
        "store_count": sum(len(account.get("stores") or []) for account in accounts),
    }


SERVICE_INTEGRATION_CATALOG = {
    "1c": {
        "label": "1С",
        "description": "Параметры SQL Server 1С. Сетевой доступ через VPN TOPTOP настраивается отдельно.",
        "credentials": [
            {"key": "host", "label": "Адрес SQL Server", "placeholder": "Имя хоста или IP-адрес"},
            {"key": "port", "label": "Порт", "placeholder": "1433"},
            {"key": "database", "label": "База данных", "placeholder": "Имя базы 1С"},
            {"key": "username", "label": "Пользователь SQL Server", "placeholder": "Логин"},
            {"key": "password", "label": "Пароль SQL Server", "placeholder": "Пароль"},
        ],
    },
    "mpstats": {
        "label": "MPStats",
        "description": "Поисковые запросы, частотность, конкуренты и семантика для Ozon и WB.",
        "credential_key": "api_key",
        "credential_label": "API-ключ MPStats",
        "placeholder": "Вставьте API-ключ MPStats",
    },
    "openrouter": {
        "label": "OpenRouter",
        "description": "AI-классификация семантики и аналитические сценарии PULSE.",
        "credential_key": "api_key",
        "credential_label": "API-ключ OpenRouter",
        "placeholder": "Вставьте API-ключ OpenRouter",
    },
    "openai": {
        "label": "Cloud API",
        "description": "Облачные мультимодальные модели для доказательного скоринга контента.",
        "credential_key": "api_key",
        "credential_label": "API-ключ Cloud API",
        "placeholder": "Вставьте API-ключ Cloud API",
    },
    "avito": {
        "label": "Avito",
        "description": "API Avito для объявлений, статистики и рабочих процессов PULSE.",
        "credentials": [
            {"key": "client_id", "label": "Client ID", "placeholder": "Вставьте Client ID Avito"},
            {"key": "client_secret", "label": "Client Secret", "placeholder": "Вставьте Client Secret Avito"},
        ],
    },
}


def service_credential_fields(config):
    if config.get("credentials"):
        return config["credentials"]
    return [{"key": config["credential_key"], "label": config["credential_label"], "placeholder": config["placeholder"]}]


def ensure_service_credentials_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_service_credentials (
                service_key text NOT NULL,
                credential_key text NOT NULL,
                encrypted_value bytea NOT NULL,
                fingerprint text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (service_key, credential_key)
            )
            """
        )
    conn.commit()


def is_1c_connection_key(key):
    import re
    return key == "1c" or key == "1c:new" or bool(re.fullmatch(r"1c:[0-9a-f]{32}", key))


def connection_catalog(saved_rows):
    catalog = dict(SERVICE_INTEGRATION_CATALOG)
    for row in saved_rows:
        key = str(row["service_key"])
        if is_1c_connection_key(key) and key != "1c:new":
            catalog[key] = SERVICE_INTEGRATION_CATALOG["1c"]
    return catalog


def admin_integrations_payload():
    with client_registry_connection() as conn:
        ensure_service_credentials_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT service_key, credential_key, fingerprint, updated_at FROM public.bi_service_credentials"
            )
            saved_rows = cur.fetchall()
    saved = {
        (str(row["service_key"]), str(row["credential_key"])): row
        for row in saved_rows
    }
    services = []
    for service_key, config in connection_catalog(saved_rows).items():
        credentials = []
        for field in service_credential_fields(config):
            row = saved.get((service_key, field["key"]))
            credentials.append({
                **field, "saved": bool(row), "fingerprint": str(row["fingerprint"] if row else ""),
                "updated_at": row["updated_at"].isoformat() if row and row.get("updated_at") else "",
            })
        services.append({
            "key": service_key,
            "label": config["label"],
            "description": config["description"],
            "credentials": credentials,
            "saved": all(field["saved"] for field in credentials),
        })
    return {"ok": True, "services": services, "credentials_encrypted": True}


def save_admin_integration(payload):
    service_key = str(payload.get("service") or "").strip().lower()
    config = SERVICE_INTEGRATION_CATALOG.get("1c" if is_1c_connection_key(service_key) else service_key)
    if not config:
        raise ValueError("Неизвестная интеграция")
    action = str(payload.get("action") or "save").strip().lower()
    fields = service_credential_fields(config)
    with client_registry_connection() as conn:
        ensure_service_credentials_schema(conn)
        with conn.cursor() as cur:
            if action == "delete":
                cur.execute(
                    "DELETE FROM public.bi_service_credentials WHERE service_key = %s",
                    (service_key,),
                )
            else:
                incoming = payload.get("credentials") if isinstance(payload.get("credentials"), dict) else {}
                if payload.get("value") and len(fields) == 1:
                    incoming[fields[0]["key"]] = payload.get("value")
                values = {}
                for field in fields:
                    key = field["key"]
                    value = str(incoming.get(key) or "")
                    if key != "password":
                        value = value.strip()
                    if value:
                        values[key] = value
                saved_keys = set()
                if payload.get("partial_update"):
                    cur.execute(
                        "SELECT credential_key FROM public.bi_service_credentials WHERE service_key=%s",
                        (service_key,),
                    )
                    saved_keys = {row["credential_key"] for row in cur.fetchall()}
                if any(field["key"] not in values and field["key"] not in saved_keys for field in fields):
                    raise ValueError("Заполните все поля подключения")
                if not values:
                    raise ValueError("Введите новые параметры подключения")
                master_key = client_credentials_master_key(create=True)
                for field in fields:
                    if field["key"] not in values:
                        continue
                    value = values[field["key"]]
                    fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
                    cur.execute(
                        """INSERT INTO public.bi_service_credentials
                            (service_key, credential_key, encrypted_value, fingerprint)
                        VALUES (%s, %s, pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256'), %s)
                        ON CONFLICT (service_key, credential_key) DO UPDATE SET
                            encrypted_value=EXCLUDED.encrypted_value, fingerprint=EXCLUDED.fingerprint,
                            updated_at=now()""",
                        (service_key, field["key"], value, master_key, fingerprint),
                    )
        conn.commit()
    return admin_integrations_payload()


def ensure_admin_connection_events_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.bi_service_connection_events (
                id bigserial PRIMARY KEY,
                service_key text NOT NULL,
                action text NOT NULL,
                status text NOT NULL,
                message text NOT NULL,
                http_status integer,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """)
    conn.commit()


def record_admin_connection_event(service_key, action, status, message, http_status=None):
    with client_registry_connection() as conn:
        ensure_admin_connection_events_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.bi_service_connection_events
                    (service_key, action, status, message, http_status)
                VALUES (%s, %s, %s, %s, %s)
            """, (service_key, action, status, message, http_status))
        conn.commit()


def probe_1c_connection(service_key="1c"):
    """Check the configured 1C database over the dedicated local SQL tunnel."""
    import pymssql
    import re

    values = {key: service_credential(service_key, key) for key in
              ("host", "port", "database", "username", "password")}
    if not all(values.values()):
        return "not_configured", "1С: сначала сохраните все параметры подключения", None
    if values["host"].strip() != "172.19.0.1" or values["port"].strip() != "11433":
        return "config_error", "1С: для настроенного SQL-туннеля укажите адрес 172.19.0.1 и порт 11433", None
    if values["database"].strip() not in {"1c_ut_prod", "1c_retail_prod", "1c_erp_prod"}:
        return "config_error", "1С: выберите одну базу: 1c_ut_prod, 1c_retail_prod или 1c_erp_prod", None
    conn = None
    try:
        conn = pymssql.connect(
            server="172.19.0.1", port=11433,
            user=values["username"], password=values["password"],
            database=values["database"].strip(), login_timeout=5, timeout=5,
            appname="TREND connection check", autocommit=True,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        if row and row[0] == 1:
            return "connected", "1С: вход в выбранную базу выполнен, SELECT 1 выполнен успешно", None
        return "unavailable", "1С: SQL Server не подтвердил проверочный запрос", None
    except pymssql.Error as exc:
        # Inspect only SQL error codes; never return/log driver text or credentials.
        codes = set(re.findall(r"\b(?:18456|18452|4060|916)\b", str(exc.args)))
        if codes & {"4060", "916"}:
            return "access_denied", "1С: выбранная база недоступна или у пользователя нет доступа", None
        if codes & {"18456", "18452"}:
            return "auth_failed", "1С: SQL Server отклонил логин или пароль", None
        return "unavailable", "1С: соединение или запрос не выполнены; проверьте VPN и SQL-туннель", None
    except (OSError, TimeoutError):
        return "unavailable", "1С: истекло время ожидания; проверьте VPN и SQL-туннель", None
    finally:
        if conn is not None:
            conn.close()


def probe_mpstats_connection():
    """Use one documented, fixed MPStats read endpoint; never expose the token."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    token = service_credential("mpstats")
    if not token:
        raise ValueError("Сначала сохраните API-ключ MPStats")
    request = Request(
        "https://mpstats.io/api/analytics/v1/oz/items/1786874757/full?d1=2026-09-01&d2=2026-09-02",
        headers={"X-Mpstats-TOKEN": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=8) as response:
            http_status = response.status
            if http_status == 200 and "json" not in response.headers.get("Content-Type", "").lower():
                return "unavailable", "MPStats ответил не в формате API", http_status
    except HTTPError as exc:
        http_status = exc.code
        exc.close()
    except (URLError, TimeoutError, OSError):
        return "unavailable", "MPStats не ответил; проверьте сеть и повторите запрос", None
    statuses = {
        200: ("connected", "MPStats ответил: API-ключ принят"),
        202: ("pending", "MPStats принял запрос, результат ещё обрабатывается"),
        401: ("auth_failed", "MPStats отклонил API-ключ"),
        403: ("access_denied", "MPStats не разрешил доступ к этому методу"),
        429: ("rate_limited", "MPStats ограничил запросы по тарифу"),
    }
    status, message = statuses.get(http_status, ("unavailable", "MPStats не подтвердил соединение"))
    return status, message, http_status


_SERVICE_STATUS_LOCK = threading.Lock()
_SERVICE_STATUS_CACHE = {"expires": 0.0, "payload": None}


def probe_atlas_connection():
    """Check that the Atlas API can read its canonical PostgreSQL store."""
    try:
        with urlopen("http://galactica_ui_api:8080/readyz", timeout=2) as response:
            payload = json.load(response)
            return "connected" if response.status == 200 and payload.get("status") == "ok" else "unavailable"
    except Exception:
        return "unavailable"


def service_connection_status_payload():
    """Bounded, cached checks for the TOPTOP header; never expose credentials."""
    import socket

    with _SERVICE_STATUS_LOCK:
        if _SERVICE_STATUS_CACHE["payload"] and time.monotonic() < _SERVICE_STATUS_CACHE["expires"]:
            return _SERVICE_STATUS_CACHE["payload"]

        vpn_connected = False
        try:
            # This private 1C host is reachable from the VPS only over its VPN route.
            with socket.create_connection(("192.168.90.228", 40300), timeout=2):
                vpn_connected = True
        except (OSError, TimeoutError):
            pass

        keys = []
        registry_failed = False
        try:
            with client_registry_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT service_key FROM public.bi_service_credentials
                        WHERE service_key = '1c' OR service_key LIKE '1c:%'
                        ORDER BY service_key
                    """)
                    keys = [row["service_key"] for row in cur.fetchall()]
        except Exception:
            # A registry failure must not be interpreted as healthy 1C.
            registry_failed = True

        statuses = {}
        with ThreadPoolExecutor(max_workers=min(len(keys) + 2, 6)) as pool:
            futures = {pool.submit(probe_1c_connection, key): key for key in keys}
            mpstats_future = pool.submit(probe_mpstats_connection)
            atlas_future = pool.submit(probe_atlas_connection)
            for future, key in futures.items():
                try:
                    statuses[key] = future.result()[0]
                except Exception:
                    statuses[key] = "unavailable"
            try:
                mpstats_status = mpstats_future.result()[0]
            except ValueError:
                mpstats_status = "not_configured"
            except Exception:
                mpstats_status = "unavailable"
            try:
                atlas_status = atlas_future.result()
            except Exception:
                atlas_status = "unavailable"

        connected_count = sum(status == "connected" for status in statuses.values())
        one_c_status = (
            "unavailable" if registry_failed else
            "not_configured" if not keys else
            "connected" if connected_count == len(keys) else
            "partial" if connected_count else "unavailable"
        )
        payload = {
            "ok": True,
            "checked_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "services": [
                {"key": "1c", "label": "1С", "status": one_c_status,
                 "connected": connected_count, "total": len(keys)},
                {"key": "mpstats", "label": "MPStats", "status": mpstats_status},
                {"key": "vpn", "label": "VPN 1С", "status": "connected" if vpn_connected else "unavailable"},
                {"key": "atlas", "label": "Атлас", "status": atlas_status,
                 "detail": "API и каноническая БД Атласа отвечают" if atlas_status == "connected"
                 else "API или каноническая БД Атласа недоступны"},
            ],
        }
        # Retry transient VPN/1C failures promptly; healthy checks stay cached.
        ttl = 30 if not vpn_connected or one_c_status in {"unavailable", "partial"} or atlas_status != "connected" else 300
        _SERVICE_STATUS_CACHE.update(payload=payload, expires=time.monotonic() + ttl)
        return payload


def admin_connections_payload():
    """Only MPStats and 1C metadata; never return decrypted credentials."""
    payload = admin_integrations_payload()
    payload["services"] = [
        service for service in payload["services"]
        if service["key"] == "mpstats" or is_1c_connection_key(service["key"])
    ]
    with client_registry_connection() as conn:
        ensure_admin_connection_events_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT service_key, action, status, message, http_status, created_at
                FROM public.bi_service_connection_events
                WHERE service_key IN ('mpstats', '1c') OR service_key LIKE '1c:%'
                ORDER BY id DESC LIMIT 50
            """)
            events = cur.fetchall()
    for service in payload["services"]:
        own_events = [row for row in events if row["service_key"] == service["key"]]
        service["history"] = [{
            "action": row["action"], "status": row["status"],
            "message": row["message"], "http_status": row["http_status"],
            "created_at": row["created_at"].isoformat(),
        } for row in own_events[:10]]
        saved_at = max((field["updated_at"] for field in service["credentials"] if field["saved"]), default="")
        service["updated_at"] = saved_at
        if is_1c_connection_key(service["key"]):
            database = service_credential(service["key"], "database") if service["saved"] else ""
            service["database_name"] = database
            service["label"] = "1С · " + database if database else "1С"

        if saved_at and not any(row["action"] == "save" for row in own_events):
            service["history"].append({
                "action": "saved_existing", "status": "saved",
                "message": "Параметры сохранены до включения журнала",
                "http_status": None, "created_at": saved_at,
            })
        last_check = next((row for row in own_events
            if row["action"] == "check" and row["created_at"].isoformat() >= saved_at), None) if own_events and own_events[0]["action"] == "check" else None
        service["connection_status"] = (
            last_check["status"] if service["saved"] and last_check else
            "untested" if service["saved"] else "not_configured"
        )
    payload["services"] = [x for x in payload["services"] if x["key"] != "1c" or x["saved"] or x.get("history")]
    payload["services"].append({
        "key": "1c:new", "label": "Добавить базу 1С", "description": "Каждая база сохраняется как отдельное подключение.",
        "credentials": [{**f, "saved": False} for f in service_credential_fields(SERVICE_INTEGRATION_CATALOG["1c"])],
        "saved": False, "history": [], "connection_status": "not_configured",
    })

    labels = {x["key"]: x["label"] for x in payload["services"]}
    payload["history"] = [{"service_label": labels.get(row["service_key"], "1С (удалено)"),
        "action": row["action"], "status": row["status"], "message": row["message"],
        "http_status": row["http_status"], "created_at": row["created_at"].isoformat()}
        for row in events]

    return payload


def save_admin_connection(payload):
    service_key = str(payload.get("service") or "").strip().lower()
    if service_key != "mpstats" and not is_1c_connection_key(service_key):
        raise ValueError("В этом разделе доступны только MPStats и 1С")
    action = str(payload.get("action") or "save").strip().lower()
    if service_key == "1c:new":
        if action != "save":
            raise ValueError("Сначала сохраните базу")
        import uuid
        service_key = "1c:" + uuid.uuid4().hex
        payload = {**payload, "service": service_key}
    elif service_key.startswith("1c:"):
        with client_registry_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM public.bi_service_credentials WHERE service_key=%s LIMIT 1", (service_key,))
                if not cur.fetchone():
                    raise ValueError("Подключение уже удалено; обновите список")

    if action == "check":
        status, message, http_status = probe_mpstats_connection() if service_key == "mpstats" else probe_1c_connection(service_key)
        record_admin_connection_event(service_key, "check", status, message, http_status)
    elif action in {"save", "delete"}:
        save_admin_integration({**payload, "partial_update": action == "save"})
        record_admin_connection_event(
            service_key, action, "saved" if action == "save" else "deleted",
            "Параметры подключения сохранены" if action == "save" else "Подключение удалено",
        )
    else:
        raise ValueError("Неизвестное действие с подключением")
    return admin_connections_payload()


def service_credential(service_key, credential_key="api_key"):
    master_key = client_credentials_master_key(create=False)
    if not master_key:
        return ""
    with client_registry_connection() as conn:
        ensure_service_credentials_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pgp_sym_decrypt(encrypted_value, %s) AS value
                FROM public.bi_service_credentials
                WHERE service_key = %s AND credential_key = %s
                """,
                (master_key, str(service_key), str(credential_key)),
            )
            row = cur.fetchone()
    return str(row.get("value") or "") if row else ""


def resolve_claude_cli():
    candidates = [
        os.environ.get("CLAUDE_CLI_PATH"),
        str(Path(os.environ.get("PULSE_TOOLS_DIR") or r"D:\Codex\Tools") / "Claude" / "claude.exe"),
        str(Path.home() / ".local" / "bin" / "claude.exe"),
        shutil.which("claude"),
        shutil.which("claude.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return ""


def seo_ai_provider_catalog():
    try:
        from ozon_category_dashboard.content_scoring import resolve_codex_cli, _probe_local_models
    except ModuleNotFoundError:
        from content_scoring import resolve_codex_cli, _probe_local_models
    local_ready, local_models = _probe_local_models()
    claude_cli = resolve_claude_cli()
    openrouter_ready = bool(service_credential("openrouter"))
    if not openrouter_ready:
        try:
            openrouter_ready = bool(json.loads((Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json").read_text(encoding="utf-8")).get("OPENROUTER_API_KEY"))
        except (OSError, ValueError, TypeError):
            openrouter_ready = False
    return [
        {"id": "local", "label": "Local", "available": local_ready, "models": local_models, "message": "" if local_ready else "LM Studio не отвечает."},
        {"id": "codex", "label": "Codex CLI", "available": bool(resolve_codex_cli()), "models": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.5"], "message": "" if resolve_codex_cli() else "Codex CLI не найден."},
        {"id": "claude", "label": "Claude Code", "available": bool(claude_cli), "models": ["sonnet", "haiku", "opus"], "message": "" if claude_cli else "Claude Code CLI не найден."},
        {"id": "openrouter", "label": "OpenRouter", "available": openrouter_ready, "models": ["deepseek/deepseek-v4-flash", "openai/gpt-4.1-mini"], "message": "" if openrouter_ready else "OpenRouter не подключён."},
    ]


def _seo_ai_content(provider, model, prompt, system, api_key="", reasoning_effort=None):
    if provider in {"openrouter", "local"}:
        if provider == "openrouter" and not api_key:
            raise RuntimeError("OpenRouter не подключён")
        endpoint = "https://openrouter.ai/api/v1/chat/completions" if provider == "openrouter" else f"{str(os.environ.get('LM_STUDIO_BASE_URL') or 'http://127.0.0.1:1234/v1').rstrip('/')}/chat/completions"
        body = {"model": model, "temperature": 0.1, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        headers = {"Content-Type": "application/json"}
        if provider == "openrouter":
            headers["Authorization"] = f"Bearer {api_key}"
        with urlopen(
            Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"),
            timeout=max(20, int(os.environ.get("SEO_OPENROUTER_TIMEOUT_SECONDS", "60"))),
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        return ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    if provider == "codex":
        try:
            from ozon_category_dashboard.content_scoring import resolve_codex_cli
        except ModuleNotFoundError:
            from content_scoring import resolve_codex_cli
        cli = resolve_codex_cli()
        if not cli:
            raise RuntimeError("Codex CLI не найден")
        reasoning_effort = str(reasoning_effort or os.environ.get("SEO_CODEX_REASONING_EFFORT") or "low").strip().lower()
        if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            reasoning_effort = "low"
        with tempfile.TemporaryDirectory(prefix="pulse-seo-ai-") as tmp:
            output = Path(tmp) / "result.json"
            command = [
                cli, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "-C", tmp, "-s", "read-only", "-m", model,
                "-c", f'model_reasoning_effort="{reasoning_effort}"',
                "--output-last-message", str(output), "--color", "never", "-",
            ]
            completed = subprocess.run(
                command,
                input=f"{system}\n\n{prompt}",
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(30, int(os.environ.get("SEO_CODEX_CLI_TIMEOUT_SECONDS", "120"))),
                cwd=tmp,
                check=False,
            )
            if completed.returncode != 0 or not output.is_file():
                raise RuntimeError("Codex CLI не вернул результат")
            return output.read_text(encoding="utf-8")
    cli = resolve_claude_cli()
    if not cli:
        raise RuntimeError("Claude Code CLI не найден")
    completed = subprocess.run([cli, "-p", "--model", model, "--output-format", "text"], input=f"{system}\n\n{prompt}", text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError("Claude Code CLI не вернул результат")
    return completed.stdout


def analyze_seo_keywords_with_openrouter(
    rows, model_settings=None, batch_size=60, max_workers=None, compact_response=False
):
    api_key = service_credential("openrouter")
    if not api_key:
        seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
        try:
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    primary_model = model_settings.get("primary_model") or os.environ.get("SEO_KEYWORD_RELEVANCE_MODEL") or os.environ.get(
        "SEO_KEYWORD_ANALYSIS_MODEL", "deepseek/deepseek-v4-flash"
    )
    fallback_model = model_settings.get("fallback_model") or "openai/gpt-4.1-mini"
    configurations = list({(item["provider"], item["model"]): item for item in [
        {"provider": model_settings.get("primary_provider") or "openrouter", "model": primary_model},
        {"provider": model_settings.get("fallback_provider") or "openrouter", "model": fallback_model},
    ]}.values())
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    batch_size = max(20, min(int(batch_size or 60), 250))
    batches = [(offset, rows[offset:offset + batch_size]) for offset in range(0, len(rows), batch_size)]

    def analyze_batch_once(offset, batch, provider, model):
        compact_batch = [{
            "analysis_id": str(row.get("analysis_id") or offset + index),
            "sku": row.get("sku"),
            "query": row.get("search_query"),
            "product_context": row.get("product_context"),
            "source": row.get("source_group"),
            "frequency": row.get("frequency_value"),
            "traffic": row.get("traffic"),
            "orders": row.get("orders"),
        } for index, row in enumerate(batch)]
        if compact_response:
            # Product context is large and identical for many queries of one SKU.
            # Send it once per SKU instead of repeating it in every row: this cuts
            # Codex/OpenRouter input by an order of magnitude on competitor runs.
            products = {}
            queries = []
            for item in compact_batch:
                sku = str(item.get("sku") or "")
                products.setdefault(sku, item.get("product_context") or "")
                queries.append({
                    "analysis_id": item.get("analysis_id"),
                    "sku": sku,
                    "query": item.get("query"),
                })
            compact_payload = {
                "products": [{"sku": sku, "context": context} for sku, context in products.items()],
                "queries": queries,
            }
            # Same compact contract as the working SEO Bot checker: classification
            # only. Semantic type and the auditable reason stay deterministic.
            prompt = (
                "Проверь релевантность поисковых запросов Ozon конкретной карточке товара. "
                "Контекст каждой карточки передан один раз в products; свяжи запрос с карточкой по sku. "
                "Верни только компактный JSON {\"keep\":[analysis_id...],\"reject\":[analysis_id...],"
                "\"review\":[analysis_id...]}. Каждый analysis_id должен встретиться ровно один раз. "
                "Не повторяй тексты запросов и не добавляй объяснения. "
                "keep — товар, близкий синоним, подходящая аудитория/характеристика/сценарий; "
                "reject — другой товар, конфликт пола или возраста, чужой бренд, аксессуар/услуга или мусор; "
                "review — только если контекста реально недостаточно. Не выдумывай свойства карточки.\n"
                + json.dumps(compact_payload, ensure_ascii=False, default=str, separators=(",", ":"))
            )
        else:
            prompt = (
                "Проверь релевантность поисковых запросов Ozon конкретной карточке товара. Верни только JSON "
                "{\"items\":[...]}. Для каждой строки верни ровно один объект: analysis_id; "
                "relevance_decision (keep|reject|review); clean_query (нормализованный запрос только для keep, иначе пусто); "
                "semantic_type (brand|category|product|attribute|audience|use_case|problem|competitor|other); "
                "rationale (краткая проверяемая причина до 140 символов). "
                "keep — товар, близкий синоним, подходящая аудитория/характеристика/сценарий; "
                "reject — другой товар, конфликт пола или возраста, чужой бренд, аксессуар/услуга или мусор; "
                "review — только если контекста реально недостаточно. Не выдумывай свойства карточки.\n"
                + json.dumps(compact_batch, ensure_ascii=False, default=str, separators=(",", ":"))
            )
        try:
            content = _seo_ai_content(provider, model, prompt, "Ты строгий классификатор релевантности SEO-бота. Сохраняй только смысловые ключи конкретного товара.", api_key)
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            decoded = json.loads(content)
            if compact_response and isinstance(decoded, dict) and any(key in decoded for key in ("keep", "reject", "review")):
                source_by_id = {str(item.get("analysis_id")): item for item in compact_batch}
                compact_items = []
                seen_ids = set()
                for decision in ("keep", "reject", "review"):
                    for raw_id in (decoded.get(decision) or []):
                        analysis_id = str(raw_id)
                        source = source_by_id.get(analysis_id)
                        if source is None or analysis_id in seen_ids:
                            continue
                        seen_ids.add(analysis_id)
                        compact_items.append({
                            "analysis_id": analysis_id,
                            "relevance_decision": decision,
                            "clean_query": " ".join(str(source.get("query") or "").lower().split()) if decision == "keep" else "",
                        })
                missing_ids = [analysis_id for analysis_id in source_by_id if analysis_id not in seen_ids]
                compact_items.extend({
                    "analysis_id": analysis_id,
                    "relevance_decision": "review",
                    "clean_query": "",
                } for analysis_id in missing_ids)
                return compact_items
            if isinstance(decoded, dict):
                return decoded.get("items") or []
            if isinstance(decoded, list):
                return decoded
            raise ValueError("AI вернул JSON неподдерживаемого типа")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"AI-анализ не выполнен на пакете {offset // batch_size + 1}: {exc}") from exc

    def analyze_batch(offset, batch):
        last_error = None
        for config in configurations:
            try:
                return analyze_batch_once(offset, batch, config["provider"], config["model"]), f'{config["provider"]}:{config["model"]}'
            except RuntimeError as exc:
                # One bounded attempt per configured provider. The next item is
                # the explicit fallback; repeating the same 120-300s timeout
                # three times made a simple relevance check look hung.
                last_error = exc
        raise last_error

    def analyze_batch_resilient(offset, batch):
        """SEO Bot fallback: split a bad response instead of losing the full package."""
        try:
            return analyze_batch(offset, batch)
        except RuntimeError:
            if compact_response:
                # Compact competitor batches already have a deterministic
                # review fallback at the job level. Recursive provider retries
                # multiplied one failed package into minutes of hidden work.
                raise
            if len(batch) <= 20:
                raise
            midpoint = len(batch) // 2
            left_items, left_model = analyze_batch_resilient(offset, batch[:midpoint])
            right_items, right_model = analyze_batch_resilient(offset + midpoint, batch[midpoint:])
            models = sorted({value for value in (left_model, right_model) if value})
            return left_items + right_items, ",".join(models)
    all_items = []
    used_models = set()
    requested_workers = max_workers if max_workers is not None else int(os.environ.get("SEO_KEYWORD_AI_WORKERS", "2"))
    workers = max(1, min(int(requested_workers), 4, len(batches) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze_batch_resilient, offset, batch): offset for offset, batch in batches}
        for future in as_completed(futures):
            items, used_model = future.result()
            all_items.extend(items)
            used_models.add(used_model)
    return {"items": all_items, "model": ",".join(sorted(used_models)) or f'{configurations[0]["provider"]}:{primary_model}'}


def analyze_seo_competitor_keywords(rows, model_settings=None):
    """Run the isolated competitor-key relevance scenario against our own card context."""
    prepared = []
    for row in rows:
        item = dict(row)
        item["product_context"] = (
            "НАША КАРТОЧКА, для которой проверяется ключ конкурента: "
            + str(row.get("product_context") or "")
            + f" | ключ встречается у {int(row.get('competitor_coverage') or 0)} конкурентов из топ-10"
        )[:1500]
        prepared.append(item)
    return analyze_seo_keywords_with_openrouter(
        prepared, model_settings, batch_size=250, max_workers=4, compact_response=True
    )


def ensure_seo_competitor_analysis_job_thread(client, job_id):
    """Attach one in-process worker to a durable PostgreSQL job."""
    client = normalize_client_key(client)
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("job_id не передан")
    key = (client, job_id)
    with SEO_COMPETITOR_JOB_THREADS_LOCK:
        current = SEO_COMPETITOR_JOB_THREADS.get(key)
        if current and current.is_alive():
            return False

        def run_job():
            try:
                try:
                    from ozon_category_dashboard.seo_projects import run_competitor_analysis_job
                except ModuleNotFoundError:
                    from seo_projects import run_competitor_analysis_job
                run_competitor_analysis_job(
                    read_db_config(client), job_id, analyze_seo_competitor_keywords
                )
            finally:
                with SEO_COMPETITOR_JOB_THREADS_LOCK:
                    SEO_COMPETITOR_JOB_THREADS.pop(key, None)

        worker = threading.Thread(
            target=run_job,
            name=f"seo-competitor-ai-{job_id[:8]}",
            daemon=True,
        )
        SEO_COMPETITOR_JOB_THREADS[key] = worker
        worker.start()
        return True


def ensure_seo_full_run_job_thread(client, job_id):
    """Attach one worker to the durable full SEO pipeline job."""
    client = normalize_client_key(client)
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("job_id не передан")
    key = (client, job_id)
    with SEO_FULL_RUN_THREADS_LOCK:
        current = SEO_FULL_RUN_THREADS.get(key)
        if current and current.is_alive():
            return False

        def run_job():
            try:
                try:
                    from ozon_category_dashboard.seo_projects import (
                        analyze_project_competitor_keywords, analyze_project_customer_voice,
                        analyze_project_keywords, collect_project_competitor_keywords,
                        collect_project_customer_messages, collect_project_niche_competitors,
                        generate_project_content_allocation, generate_project_content_draft,
                        generate_project_content_review, generate_project_intents,
                        prepare_project_semantic_context, refresh_project, refresh_project_mpstats,
                        run_full_seo_run_job,
                    )
                except ModuleNotFoundError:
                    from seo_projects import (
                        analyze_project_competitor_keywords, analyze_project_customer_voice,
                        analyze_project_keywords, collect_project_competitor_keywords,
                        collect_project_customer_messages, collect_project_niche_competitors,
                        generate_project_content_allocation, generate_project_content_draft,
                        generate_project_content_review, generate_project_intents,
                        prepare_project_semantic_context, refresh_project, refresh_project_mpstats,
                        run_full_seo_run_job,
                    )
                config = read_db_config(client)

                def one_sku(payload):
                    return str((payload.get("skus") or [""])[0])

                def fetch_ozon_project_sku(request_payload):
                    return handle_ozon_seo_product_queries_details({"client": client, **request_payload})

                handlers = {
                    "seller_keywords": lambda payload: refresh_project(config, payload, fetch_ozon_project_sku),
                    "mpstats": lambda payload: refresh_project_mpstats(config, payload),
                    "keyword_analysis": lambda payload: analyze_project_keywords(
                        config, payload, analyze_seo_keywords_with_openrouter
                    ),
                    "intents": lambda payload: generate_project_intents(
                        config, payload, infer_seo_product_intents_with_openrouter
                    ),
                    "competitors": lambda payload: collect_project_niche_competitors(
                        config, {**payload, "competitor_limit": 5}
                    ),
                    "competitor_keywords": lambda payload: collect_project_competitor_keywords(
                        config, {**payload, "keyword_limit_per_competitor": 50}
                    ),
                    "competitor_analysis": lambda payload: analyze_project_competitor_keywords(
                        config, payload, analyze_seo_competitor_keywords
                    ),
                    "customer_messages": lambda payload: collect_project_customer_messages(
                        config, {**payload, "period_days": 90},
                        lambda skus, date_from, date_to: fetch_ozon_project_questions(
                            client, skus, date_from, date_to
                        ),
                    ),
                    "customer_voice": lambda payload: analyze_project_customer_voice(
                        config, payload, analyze_seo_customer_voice_claims
                    ),
                    "semantic_context": lambda payload: prepare_project_semantic_context(config, payload),
                    "allocation": lambda payload: generate_project_content_allocation(
                        config, {**payload, "sku": one_sku(payload)}, analyze_seo_content_allocation
                    ),
                    "draft": lambda payload: generate_project_content_draft(
                        config, {**payload, "sku": one_sku(payload)}, generate_seo_content_draft
                    ),
                    "review": lambda payload: generate_project_content_review(
                        config, {**payload, "sku": one_sku(payload), "apply_fixes": True},
                        review_seo_content_draft, revise_seo_content_draft
                    ),
                }
                run_full_seo_run_job(config, job_id, handlers)
            finally:
                with SEO_FULL_RUN_THREADS_LOCK:
                    SEO_FULL_RUN_THREADS.pop(key, None)

        worker = threading.Thread(
            target=run_job, name=f"seo-full-run-{job_id[:8]}", daemon=True,
        )
        SEO_FULL_RUN_THREADS[key] = worker
        worker.start()
        return True


def _intent_ai_payload_row(row):
    queries = {"api": [], "mpstats": []}
    for item in (row.get("keywords") or [])[:12]:
        source = str(item.get("source") or "").strip().lower()
        query = str(item.get("query") or "").strip()
        if source in queries and query and query not in queries[source]:
            queries[source].append(query)
    overlap = [query for query in queries["api"] if query in set(queries["mpstats"])]
    audience = row.get("audience") or {}
    return {
        "sku": row.get("sku"),
        "title": str(row.get("product_name") or "")[:350],
        "audience": {
            "gender": audience.get("gender"),
            "age_group": audience.get("age_group"),
            "evidence": (audience.get("evidence") or [])[:4],
        },
        "description": str(row.get("description") or "")[:1600],
        "category": str(row.get("category_name") or "")[:250],
        "marketplace_type": str(row.get("type_name") or "")[:180],
        "characteristics": str(row.get("attributes_text") or "")[:1800],
        "cleaned_queries": {**queries, "overlap": overlap},
    }


def infer_seo_product_intents_with_openrouter(rows, marketplace, model_settings=None):
    api_key = service_credential("openrouter")
    if not api_key:
        seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
        try:
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    primary_model = model_settings.get("primary_model") or os.environ.get("SEO_PRODUCT_INTENT_MODEL", "deepseek/deepseek-v4-flash")
    fallback_model = model_settings.get("fallback_model") or "openai/gpt-4.1-mini"
    configurations = list({(item["provider"], item["model"]): item for item in [
        {"provider": model_settings.get("primary_provider") or "openrouter", "model": primary_model},
        {"provider": model_settings.get("fallback_provider") or "openrouter", "model": fallback_model},
    ]}.values())
    payload_rows = [_intent_ai_payload_row(row) for row in rows]
    prompt = (
        "Определи один главный поисковый интент для каждого товара. Это ёмкая сущность, по которой товар точно найдут: "
        "например 'рубашка женская', 'худи для девочки', 'тостер', 'диван угловой', 'диван маленький'. "
        "Сначала установи реальный тип товара по названию, описанию и характеристикам; очищенные запросы API и MPStats "
        "используй как подтверждение, но не позволяй частотному шуму менять сущность товара. Блок audience передан отдельно: "
        "пол и возрастную группу из него считай приоритетными фактами и обязательно учитывай в интенте, когда они уточняют товар. "
        "Не заменяй их предположением из поисковых запросов; при явном конфликте опирайся на название, описание и характеристики. "
        "Интент содержит 1-5 слов в нижнем регистре, без бренда, артикула, цвета, размера, рекламных слов и неподтверждённых свойств. "
        "Верни только JSON {\"items\":[{\"sku\":\"...\",\"intent\":\"...\",\"confidence\":0.0,\"rationale\":\"краткое основание\"}]} "
        "и ровно один объект на каждый SKU.\n" + json.dumps(payload_rows, ensure_ascii=False, separators=(",", ":"))
    )
    request_body = {
        "model": primary_model, "temperature": 0.1, "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Ты товарный SEO-классификатор. Возвращай только проверяемый краткий поисковый интент."},
            {"role": "user", "content": prompt},
        ],
    }
    last_error = None
    for config in configurations:
      model, provider = config["model"], config["provider"]
      for attempt in range(1, 4):
        try:
            content = _seo_ai_content(provider, model, prompt, "Ты товарный SEO-классификатор. Возвращай только проверяемый краткий поисковый интент.", api_key)
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            decoded = json.loads(content)
            items = decoded.get("items", []) if isinstance(decoded, dict) else decoded
            if not isinstance(items, list):
                raise ValueError("AI вернул JSON неподдерживаемого типа")
            return {"items": items, "model": f"{provider}:{model}"}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * attempt)
    raise RuntimeError(f"AI-интенты не сформированы основной и резервной моделью: {last_error}")


def analyze_seo_customer_voice_claims(rows, model_settings=None):
    """Extract source-backed SEO claims without turning competitor voice into own-product facts."""
    api_key = service_credential("openrouter")
    if not api_key:
        seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
        try:
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    configurations = list({(item["provider"], item["model"]): item for item in [
        {"provider": model_settings.get("primary_provider") or "codex",
         "model": model_settings.get("primary_model") or "gpt-5.6-luna"},
        {"provider": model_settings.get("fallback_provider") or "openrouter",
         "model": model_settings.get("fallback_model") or "deepseek/deepseek-v4-flash"},
    ]}.values())
    compact_rows = []
    for row in rows:
        context = row.get("product_context") or {}
        compact_rows.append({
            "sku": row.get("sku"),
            "claim_limits": row.get("claim_limits") or {},
            "source_counts": row.get("source_counts") or {},
            "product_context": {
                "title": str(context.get("product_name") or "")[:350],
                "intent": str(context.get("search_intent") or "")[:180],
                "category": str(context.get("category") or "")[:220],
                "type": str(context.get("type") or "")[:160],
                "description": str(context.get("description") or "")[:1400],
                "attributes": str(context.get("attributes") or "")[:1800],
            },
            "evidence": [{
                "evidence_id": item.get("evidence_id"),
                "source_type": item.get("source_type"),
                "text": str(item.get("text") or "")[:500],
                "rating": item.get("rating"),
                "competitor_sku": item.get("competitor_sku"),
            } for item in (row.get("evidence") or [])[:80]],
        })
    prompt = (
        "Синтезируй из отзывов и вопросов несколько итоговых клеймов/тем, которые полезно перенести в SEO карточки. "
        "Верни только JSON {\"items\":[{\"sku\":\"...\",\"claims\":[...]}]}; ровно один item на SKU, "
        "количество claims держи строго внутри переданного claim_limits для каждого SKU. Формат claim: claim, claim_type "
        "(benefit|use_case|audience|fit|material|quality|care|problem|faq|other), seo_target "
        "(title|description|characteristics|faq), verification_status (safe|review), confidence 0..1, "
        "source_refs (массив evidence_id и при необходимости context:product), rationale. "
        "Не пересказывай каждое сообщение отдельным клеймом. Сначала сгруппируй синонимы и повторяющиеся смыслы, затем верни только сильнейшие разные темы. "
        "Ранжируй темы так: повторяется у своих и конкурентов; повторяется у своих; повторяется у конкурентов; одиночный FAQ-пробел. "
        "Для benefit/use_case/audience/fit/material/quality/care/problem при наличии 6 и более сообщений используй минимум два разных evidence_id; "
        "одиночное подтверждение допустимо только для faq или когда всего сообщений меньше 6. Не более 6 representative source_refs на клейм. "
        "Каждый клейм обязан ссылаться хотя бы на одно customer-voice evidence_id. "
        "safe ставь только когда свойство подтверждено product_context: тогда добавь context:product в source_refs. "
        "Отзывы конкурентов показывают язык ниши и потребности, но сами по себе не доказывают свойство нашего товара: "
        "для competitor-only клейма ставь review. Вопрос без ответа означает информационный пробел, а не факт: "
        "формулируй 'указать/уточнить ...', claim_type=faq и verification_status=review. "
        "Не выдумывай свойства, числа, состав, посадку, пол или возраст. Клеймы должны быть непересекающимися, короткими и сохранять конкретную лексику покупателей.\n"
        + json.dumps(compact_rows, ensure_ascii=False, default=str, separators=(",", ":"))
    )
    last_error = None
    for config in configurations:
        try:
            content = _seo_ai_content(
                config["provider"], config["model"], prompt,
                "Ты строгий аналитик customer voice. Каждый SEO-клейм должен иметь проверяемые ссылки на переданные источники.",
                api_key,
            )
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            decoded = json.loads(content)
            items = decoded.get("items") if isinstance(decoded, dict) else decoded
            if not isinstance(items, list):
                raise ValueError("AI вернул JSON неподдерживаемого типа")
            return {"items": items, "model": f'{config["provider"]}:{config["model"]}'}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError,
                RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
    raise RuntimeError(f"AI-клеймы не сформированы основной и резервной моделью: {last_error}")


def analyze_seo_content_allocation(context, model_settings=None):
    """Choose source-backed phrases for marketplace fields; persistence performs the final allow-list check."""
    api_key = service_credential("openrouter")
    if not api_key:
        seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
        try:
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    configurations = [
        {"provider": model_settings.get("primary_provider") or "codex",
         "model": model_settings.get("primary_model") or "gpt-5.6-luna"},
        {"provider": model_settings.get("fallback_provider") or "openrouter",
         "model": model_settings.get("fallback_model") or "deepseek/deepseek-v4-flash"},
    ]
    marketplace = "wb" if str(context.get("marketplace") or "").lower() == "wb" else "ozon"
    marketplace_label = "Wildberries" if marketplace == "wb" else "Ozon"
    title_limit = 60 if marketplace == "wb" else 200
    compact = {
        "marketplace": marketplace,
        "sku": context.get("sku"), "product": context.get("product") or {},
        "own_keywords": (context.get("own_keywords") or [])[:80],
        "competitor_keywords": (context.get("competitor_keywords") or [])[:80],
        "customer_voice_claims": (context.get("customer_voice_claims") or [])[:12],
        "coverage": context.get("coverage") or {}, "warnings": context.get("warnings") or [],
    }
    prompt = (
        f"Разметь готовый SEO-контекст товара {marketplace_label} по полям карточки. Это план, не публикация и не генерация новых фактов. "
        "Название: сначала реальный тип/интент товара, затем бренд/модель и только важные различающие характеристики; "
        f"не превращай название в перечень синонимов, не дублируй один смысл и уложи title_outline в {title_limit} символов. "
        "В title_keywords возьми ровно одну фразу, которая ближе всего к product.intent по смыслу; это служебная связь с поисковой статистикой, "
        "но канонический основной интент берётся из product.intent. Все остальные релевантные запросы перенеси в description_keywords "
        "как смысловую поддержку, сценарии использования и формулировки аудитории. "
        "Клеймы разрешены только из customer_voice_claims; safe можно рекомендовать прямо, review только пометить как требующий проверки. "
        "Отдельно выбери SEO-чувствительные характеристики из product.seo_characteristics: для названия только самые "
        "различающие и реально искомые (обычно тип, пол/возраст, цвет, материал, размер или конструктивная особенность), "
        "для описания — остальные полезные для поиска и выбора. Не дублируй одну характеристику в двух полях. "
        "name и value верни дословно из входа, ничего не переименовывай и не нормализуй. "
        "Нельзя создавать новые ключи, менять слова внутри переданных query или придумывать свойства товара. "
        "Каждый выбранный ключ верни точной строкой query из входа. role: primary|secondary|supporting. "
        "Верни только JSON: {\"title_outline\":\"...\",\"description_outline\":\"...\","
        "\"title_keywords\":[{\"query\":\"...\",\"role\":\"primary\",\"reason\":\"...\"}],"
        "\"description_keywords\":[...],"
        "\"title_characteristics\":[{\"name\":\"точное имя\",\"value\":\"точное значение\",\"reason\":\"...\"}],"
        "\"description_characteristics\":[{\"name\":\"точное имя\",\"value\":\"точное значение\",\"reason\":\"...\"}],"
        "\"description_claims\":[{\"claim\":\"точная строка из входа\",\"reason\":\"...\"}],"
        "\"excluded\":[{\"text\":\"...\",\"reason\":\"...\"}]}\n" +
        json.dumps(compact, ensure_ascii=False, default=str, separators=(",", ":"))
    )
    system = (
        f"Ты редактор товарного SEO для {marketplace_label}. Выбирай только переданные факты и фразы. "
        "Ответ должен быть проверяемым JSON без markdown и без скрытых рассуждений."
    )
    last_error = None
    seen = set()
    for config in configurations:
        key = (config["provider"], config["model"])
        if key in seen:
            continue
        seen.add(key)
        try:
            content = _seo_ai_content(config["provider"], config["model"], prompt, system, api_key)
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            decoded = json.loads(content)
            if not isinstance(decoded, dict):
                raise ValueError("AI вернул JSON неподдерживаемого типа")
            return {"allocation": decoded, "model": f'{config["provider"]}:{config["model"]}'}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError,
                RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
    raise RuntimeError(f"SEO-разметка не сформирована основной и резервной моделью: {last_error}")


def build_seo_content_draft_request(context, allocation):
    """Build the exact secret-free model request used for one marketplace SEO draft."""
    try:
        from ozon_category_dashboard.seo_projects import (
            _build_keyword_strategy, _seo_normalize, _source_model_identifier, _source_warranty_claims,
        )
    except ModuleNotFoundError:
        from seo_projects import _build_keyword_strategy, _seo_normalize, _source_model_identifier, _source_warranty_claims
    product = context.get("product") or {}
    marketplace = "wb" if str(context.get("marketplace") or "").lower() == "wb" else "ozon"
    marketplace_label = "Wildberries" if marketplace == "wb" else "Ozon"
    title_limit = 60 if marketplace == "wb" else 200
    description_target = {"min": 1000, "target": 1800, "max": 2000, "soft_max": 5000} if marketplace == "wb" else {"min": 1300, "target": 1550, "max": 1800, "soft_max": 2200}
    required_model = _source_model_identifier(context)
    required_warranties = _source_warranty_claims(context)
    keyword_candidates = {}
    for placement, field in (("title", "title_keywords"), ("description", "description_keywords")):
        for row in allocation.get(field) or []:
            query = str(row.get("query") or "").strip()
            normalized = _seo_normalize(query)
            if normalized:
                keyword_candidates[normalized] = {**row, "query": query, "placement": placement}
    keyword_strategy = _build_keyword_strategy(keyword_candidates, product.get("intent") or "")
    compact = {
        "sku": context.get("sku"),
        "marketplace": marketplace,
        "product": {
            "current_title": product.get("title") or "",
            "current_description": product.get("description") or "",
            "category": product.get("category") or "",
            "product_type": product.get("product_type") or "",
            "intent": product.get("intent") or "",
        },
        "title_outline": allocation.get("title_outline") or "",
        "description_outline": allocation.get("description_outline") or "",
        "title_keywords": allocation.get("title_keywords") or [],
        "description_keywords": allocation.get("description_keywords") or [],
        "keyword_strategy": keyword_strategy,
        "title_characteristics": allocation.get("title_characteristics") or [],
        "description_characteristics": allocation.get("description_characteristics") or [],
        "description_claims": [row for row in (allocation.get("description_claims") or [])
                               if str(row.get("verification_status") or "").lower() == "safe"],
        "required_title_suffix": required_model,
        "required_description_claims": required_warranties,
        "generation_feedback": allocation.get("generation_feedback") or None,
        "seo_writing_policy": {
            "selection_order": [
                "сначала смысловая точность для конкретного товара и соответствие исходным фактам",
                "затем роль фразы: primary, secondary, supporting",
                "priority_score, search_demand и best_position использовать только как вспомогательные сигналы между одинаково релевантными фразами",
                "не брать широкую или неточную фразу только из-за высокой частотности",
            ],
            "title": [
                "начать с точной строки product.intent; это единственное обязательное точное SEO-вхождение",
                "не менять порядок слов внутри product.intent и поместить его в начало названия",
                "после главной фразы добавить только различающие характеристики и бренд; обязательную модель оставить точным суффиксом в конце",
                "не перечислять синонимы, не повторять один корень и не превращать название в набор запросов",
            ],
            "description": [
                "первый смысловой блок естественно называет товар, аудиторию или назначение; первую фразу из description_keywords использовать только если она читается органично",
                "ровно одна строка keyword_strategy с exact_required=true — канонический product.intent; включить её в название дословно один раз",
                "все строки source_type=search_query являются смысловой поддержкой без требования точного вхождения",
                "для поисковых запросов использовать morphological_bases и semantic_links естественными словоформами, не копируя запрос целиком специально",
                "каждую обязательную точную фразу использовать не более одного раза; не начинать соседние абзацы одинаковой сущностью товара",
                "покрыть максимально возможное число уникальных морфологических основ без потери естественности, фактической точности и смысла связок",
                "в used_keywords перечислять только фактические непрерывные точные вхождения; морфологическое покрытие сервер определит самостоятельно",
                "не писать канцелярские пояснения о самом описании или характеристиках: 'эта характеристика помогает', 'описание остаётся понятным', 'важно учитывать обозначения' и подобные заполнители",
                "heading всегда оставлять пустым: названия смысловых блоков нужны только для аудита и не входят в итоговое описание",
                "не добавлять отдельные SEO-перечни, бессвязные синонимы, искусственные повторы и неподтверждённые свойства",
            ],
            "audit": {
                "title_prefix_chars": 80,
                "description_opening_chars": 420,
                "exact_keyword_target": 1 if keyword_strategy else 0,
                "minimum_morphological_base_coverage": 0.8,
            },
        },
        "title_policy": {
            "dynamic": True,
            "max_chars": title_limit,
            "required_components": ["primary_intent", "required_title_suffix"],
            "component_candidates": [
                {"kind": "audience_or_use_case", "purpose": "аудитория или главный сценарий, если это различает товар"},
                {"kind": "material_or_composition", "purpose": "материал или состав, если важен для выбора"},
                {"kind": "function_or_construction", "purpose": "функция, конструкция или комплектация"},
                {"kind": "format_dimensions_compatibility", "purpose": "формат, размер, габариты или совместимость"},
                {"kind": "style_color_variant", "purpose": "стиль, цвет или вариант, если это поисково и покупательски значимо"},
                {"kind": "brand", "purpose": "бренд, только когда он подтверждён входом"},
            ],
            "rules": [
                "после основного интента выбрать только те компоненты, которые различают именно этот товар",
                "число, набор и порядок компонентов определять по категории и фактам товара, а не по фиксированному шаблону",
                "не включать слабый компонент только ради заполнения названия",
                "обязательную модель сохранить точным последним компонентом",
            ],
        },
        "hashtag_policy": {
            "purpose": "внутренние SEO-метки для мониторинга; не поле карточки WB" if marketplace == "wb" else "вспомогательные хештеги",
            "target_count": {"min": 8, "max": 12},
            "rules": [
                "каждый тег должен описывать товар через сущность плюс уточнение, обычно 2-5 слов",
                "покрыть разные стороны спроса: основной интент, аудиторию, сценарий, материал, конструкцию и бренд только при наличии брендового ключа",
                "не использовать одиночные общие слова, слишком широкие категории, повторы и перестановки одних и тех же слов",
                "основа тега должна происходить из точного разрешённого ключа; не придумывать новые свойства и синонимы",
            ],
        },
        "description_policy": {
            "target_chars": description_target,
            "section_count": {"min": 3, "max": 6},
            "section_candidates": [
                {"kind": "product_identity", "purpose": "что это за товар, его основной интент, аудитория и назначение"},
                {"kind": "material_and_construction", "purpose": "материалы, состав, конструкция и связанные с ними свойства"},
                {"kind": "fit_dimensions_compatibility", "purpose": "посадка, размеры, габариты, совместимость или подбор варианта"},
                {"kind": "usage_scenarios", "purpose": "конкретные сценарии использования, сезонность и контекст применения"},
                {"kind": "functional_details", "purpose": "важные функции, детали, комплектация и практические особенности"},
                {"kind": "care_operation_storage", "purpose": "уход, эксплуатация или хранение, только когда это подтверждено входом"},
                {"kind": "style_audience_context", "purpose": "стиль, образ, целевая аудитория и сочетаемость, когда это уместно"},
                {"kind": "source_backed_benefits", "purpose": "проверенные преимущества, safe-клеймы и обязательные факты исходника"},
            ],
            "rules": [
                "выбрать только уместные для конкретного товара блоки",
                "не использовать один и тот же набор и порядок для всех категорий",
                "объединять слабые блоки и пропускать блоки без подтверждённых фактов",
                "heading всегда возвращать пустым; kind служит только внутренней разметкой для аудита",
                "не дополнять текст типовыми советами, если их нет во входных данных",
            ],
        },
    }
    user_prompt = (
        f"Сформируй черновик SEO-контента карточки {marketplace_label} по уже проверенной разметке. "
        "Следуй seo_writing_policy и title_policy. В названии начни с product.intent в точной исходной формулировке и используй его ровно один раз. "
        "Это единственное обязательное точное вхождение. Затем добавляй "
        "только уместные для этой категории различающие компоненты из title_policy; их набор и порядок после интента должны быть динамическими. Не делай перечень синонимов, не повторяй слова и "
        f"уложись в {title_limit} символов. Не выбирай широкую фразу только из-за search_demand: смысловая релевантность и "
        "роль primary/secondary/supporting важнее числовых метрик. "
        "Описание сформируй из 4-6 связанных абзацев. Выбери их внутренние типы из description_policy.section_candidates "
        "по фактам именно этого товара: набор и порядок не должны быть типовым шаблоном для категории. "
        "Каждый блок должен решать отдельную покупательскую задачу; пустые и слабые блоки объедини или пропусти. "
        f"Целевой объём описания — {description_target['min']}-{description_target['max']} символов, ориентир {description_target['target']}. Не добивай объём повторами, общими советами "
        "или неподтверждёнными фразами: при недостатке фактов напиши короче. Раскрой назначение, характеристики и "
        "преимущества естественным русским текстом без keyword stuffing. В keyword_strategy первые строки с "
        "exact_required=true — это только основной product.intent, уже предназначенный для названия. "
        "Не собирай поисковые фразы через запятую и не превращай название в SEO-перечень. "
        "Все поисковые запросы source_type=search_query не нужно вставлять дословно: используй их morphological_bases в естественных словоформах, "
        "сохраняя semantic_links и смысл товара. Стремись покрыть не менее 80% уникальных основ. Перед ответом сверь "
        "used_keywords с title и description и не заявляй отсутствующие точные фразы. Не начинай соседние "
        "абзацы повтором названия товара. Избегай канцелярских заполнителей о самом тексте и характеристиках. "
        "Если generation_feedback передан, это контролируемая редакторская доработка: сохрани удачные факты и "
        "структуру текущего черновика, но добавь missing_exact_keywords дословно, а missing_morphological_bases — "
        "естественными словоформами в уместных смысловых связках. Не превышай density_limit_per_1000_chars. "
        "Используй только переданные ключи, характеристики и safe-клеймы; не придумывай свойства, числа, обещания, "
        "сертификацию, пол или возраст. review-клеймы не переданы и использовать их нельзя. "
        "ЖЁСТКИЕ ПРАВИЛА СОХРАНЕНИЯ ИСХОДНИКА: если required_title_suffix непустой, название обязано "
        "заканчиваться этой точной строкой без изменения; заранее оставь для неё место в лимите 200 символов. "
        "Каждую строку required_description_claims обязательно перенеси в описание дословно. Эти строки взяты "
        "из исходной карточки, их нельзя перефразировать или опускать. "
        "Хештеги — отдельные внутренние SEO-метки для аналитики, не часть описания и не команда публикации в карточку. Следуй hashtag_policy и верни 8-12 "
        "разнообразных точных тегов, если во входе достаточно ключей: сущность товара плюс уточнение, обычно 2-5 "
        "слов. Не возвращай одиночные общие слова и перестановки одного набора слов; слова разделяй подчёркиванием. "
        "Для аудита перечисли точные исходные строки ВСЕХ фраз, которые "
        "фактически использованы в названии или описании, а не только несколько примеров, а также всех использованных "
        "ключей, клеймов и характеристик. description должен совпадать с текстами description_sections, соединёнными "
        "двумя переводами строки. heading всегда верни пустым: видимых заголовков в описании быть не должно. Верни только JSON: "
        "{\"title\":\"...\",\"description\":\"...\","
        "\"description_sections\":[{\"kind\":\"точный kind из description_policy\",\"heading\":\"\",\"text\":\"...\"}],"
        "\"hashtags\":[\"#...\"],"
        "\"used_keywords\":[{\"query\":\"точная строка из входа\"}],"
        "\"used_claims\":[{\"claim\":\"точная строка из входа\"}],"
        "\"used_characteristics\":[{\"name\":\"точное имя\",\"value\":\"точное значение\"}]}.\n"
        + json.dumps(compact, ensure_ascii=False, default=str, separators=(",", ":"))
    )
    system_prompt = (
        f"Ты строгий редактор SEO-контента для {marketplace_label}. Пиши полезно и естественно, но используй только разрешённые "
        "входные факты. Ответ — валидный JSON без markdown и скрытых рассуждений."
    )
    return {"input_context": compact, "system_prompt": system_prompt, "user_prompt": user_prompt}


def generate_seo_content_draft(context, allocation, model_settings=None):
    """Generate a compact, source-backed draft; persistence performs the exact allow-list validation."""
    api_key = service_credential("openrouter")
    if not api_key:
        seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
        try:
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    configurations = [
        {"provider": model_settings.get("primary_provider") or "codex",
         "model": model_settings.get("primary_model") or "gpt-5.6-luna"},
        {"provider": model_settings.get("fallback_provider") or "openrouter",
         "model": model_settings.get("fallback_model") or "deepseek/deepseek-v4-flash"},
    ]
    request_audit = build_seo_content_draft_request(context, allocation)
    prompt = request_audit["user_prompt"]
    system = request_audit["system_prompt"]
    last_error = None
    seen = set()
    for config in configurations:
        key = (config["provider"], config["model"])
        if key in seen:
            continue
        seen.add(key)
        try:
            raw_content = _seo_ai_content(config["provider"], config["model"], prompt, system, api_key)
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content.strip(), flags=re.I)
            decoded = json.loads(content)
            if not isinstance(decoded, dict):
                raise ValueError("AI вернул JSON неподдерживаемого типа")
            reasoning_effort = None
            if config["provider"] == "codex":
                reasoning_effort = str(os.environ.get("SEO_CODEX_REASONING_EFFORT") or "low").strip().lower()
                if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                    reasoning_effort = "low"
            return {
                "draft": decoded,
                "model": f'{config["provider"]}:{config["model"]}',
                "audit": {
                    **request_audit,
                    "provider": config["provider"],
                    "model": config["model"],
                    "reasoning_effort": reasoning_effort,
                    "raw_response": raw_content,
                    "response_json": decoded,
                },
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError,
                RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
    raise RuntimeError(f"SEO-текст не сформирован основной и резервной моделью: {last_error}")


def build_seo_content_review_request(context, draft):
    """Build the exact secret-free request for the post-generation expert gate."""
    product = context.get("product") or {}
    source_facts = []
    for row in product.get("seo_characteristics") or []:
        if isinstance(row, dict) and str(row.get("name") or "").strip() and str(row.get("value") or "").strip():
            source_facts.append({"name": str(row["name"]).strip(), "value": str(row["value"]).strip()})
    marketplace = "wb" if str(context.get("marketplace") or "").lower() == "wb" else "ozon"
    marketplace_label = "Wildberries" if marketplace == "wb" else "Ozon"
    compact = {
        "marketplace": marketplace,
        "sku": context.get("sku"),
        "source_product": {
            "title": product.get("title") or "",
            "description": product.get("description") or "",
            "category": product.get("category") or "",
            "product_type": product.get("product_type") or "",
            "intent": product.get("intent") or "",
            "characteristics": source_facts,
        },
        "generated_content": {
            "title": draft.get("title") or "",
            "description": draft.get("description") or "",
            "hashtags": draft.get("hashtags") or [],
            "used_claims": draft.get("used_claims") or [],
            "used_characteristics": draft.get("used_characteristics") or [],
        },
        "review_policy": {
            "blocking_fact_checks": [
                "пол и возрастная аудитория",
                "материал и состав материала",
                "цвет и оттенок",
                "любая другая явно названная характеристика или числовой факт",
            ],
            "language_checks": [
                "естественный современный русский язык",
                "грамматика, согласование и управление",
                "логика фраз и связность абзацев",
                "отсутствие топорных повторов, переспама, канцелярита и бессмысленных SEO-конструкций",
            ],
            "prohibited_information_checks": [
                "контакты, ссылки, призывы уйти с площадки",
                "цены, скидки, доставка и условия покупки, которых нет в подтвержденном источнике",
                "неподтвержденные гарантии, сертификаты, медицинские или абсолютные обещания",
                "чужие бренды, сравнения с конкурентами, превосходная степень без доказательств",
                "оскорбительная, дискриминационная, сексуальная или иная явно запрещенная информация",
            ],
            "brand_alias_policy": (
                "латинское и общеупотребительное русское написание одного подтвержденного бренда "
                "эквивалентны, например Gloria Jeans и Глория Джинс"
            ),
            "grammatical_gender_policy": (
                "ошибка согласования с неизменяемым названием товара, например «худи мужская» вместо "
                "«худи мужское», является языковой ошибкой, а не конфликтом пола аудитории"
            ),
            "decision": {
                "approved": "текст приемлем, понятен и не противоречит подтвержденным фактам; мелкие вкусовые замечания допустимы",
                "needs_revision": "есть конкретный заметный дефект языка или логики, который реально мешает чтению",
                "blocked": "есть фактический конфликт, запрещенная информация или критическая языковая ошибка",
            },
        },
    }
    system_prompt = (
        f"Ты старший русскоязычный редактор и факт-чекер карточек {marketplace_label}. Проведи независимую экспертную оценку "
        "готового SEO-черновика. Особо строго ищи ошибки типа неверного пола, возраста, материала, состава, цвета "
        "или свойств товара — такие ошибки всегда блокируют готовность. Не переписывай текст целиком и не меняй "
        "источник. Не считай отсутствие необязательной характеристики ошибкой; ошибкой является противоречие или "
        "выдуманный факт. Для каждой исправимой проблемы предложи только локальную замену: точный уникальный "
        "фрагмент из generated_content и его короткую замену. Не переписывай абзацы или весь текст. "
        "Разрешай точечные замены и удаления отдельных хештегов. Считай латинское и однозначное русское "
        "написание одного подтвержденного бренда эквивалентными, например Gloria Jeans и Глория Джинс. "
        "Не путай грамматический род слова с полом "
        "аудитории: «худи мужская» — ошибка согласования, но не женский товар. "
        "Не придирайся к допустимым вариантам стиля и не требуй идеального литературного текста: minor-вкусовщина, "
        "не мешающая смыслу и чтению, должна оставаться approved без правок. "
        "Возвращай только валидный JSON без markdown и скрытых рассуждений."
    )
    user_prompt = (
        "Проверь generated_content относительно source_product и review_policy. Для каждой проверки верни status "
        "pass, review, fail или not_applicable. gender/material/color не могут быть pass при конфликте с исходными "
        "характеристиками. verdict=blocked при любом фактическом конфликте или запрещенной информации; "
        "needs_revision при языковых и логических недостатках; approved только если критичных и редакционных "
        "замечаний, реально требующих исправления, нет. Фрагменты, рекомендации и точечные замены должны быть "
        "короткими и проверяемыми. Не создавай edit для необязательной стилистической полировки. "
        "Поле find копируй из generated_content дословно; одна замена должна исправлять одну проблему. JSON-контракт: "
        "{\"verdict\":\"approved|needs_revision|blocked\",\"score\":0,"
        "\"summary\":\"...\",\"checks\":{"
        "\"russian_language\":{\"status\":\"pass|review|fail|not_applicable\",\"summary\":\"...\",\"evidence\":[\"...\"]},"
        "\"phrase_logic\":{...},\"prohibited_information\":{...},\"gender\":{...},\"age_audience\":{...},"
        "\"material\":{...},\"color\":{...},\"other_characteristics\":{...}},"
        "\"issues\":[{\"severity\":\"critical|major|minor\",\"field\":\"title|description|hashtags|all\","
        "\"category\":\"...\",\"fragment\":\"...\",\"message\":\"...\",\"suggestion\":\"...\"}],"
        "\"recommended_changes\":[\"...\"],"
        "\"edits\":[{\"field\":\"title|description|hashtags\",\"find\":\"точный фрагмент\","
        "\"replace\":\"исправленный фрагмент\",\"reason\":\"...\"}]}.\n"
        + json.dumps(compact, ensure_ascii=False, default=str, separators=(",", ":"))
    )
    return {"input_context": compact, "system_prompt": system_prompt, "user_prompt": user_prompt}


def build_seo_content_revision_request(context, draft, expert_review):
    """Build a narrow edit task: the reviser may change only expert-anchored fragments."""
    product = context.get("product") or {}
    compact = {
        "sku": context.get("sku"),
        "source_product": {
            "title": product.get("title") or "",
            "intent": product.get("intent") or "",
            "characteristics": product.get("seo_characteristics") or [],
        },
        "current_content": {
            "title": draft.get("title") or "",
            "description": draft.get("description") or "",
            "description_sections": draft.get("description_sections") or [],
            "hashtags": draft.get("hashtags") or [],
        },
        "expert_task": {
            "summary": expert_review.get("summary") or "",
            "issues": expert_review.get("issues") or [],
            "exact_edits": expert_review.get("suggested_edits") or [],
        },
    }
    system_prompt = (
        "Ты аккуратный редактор-исполнитель. Эксперт уже закончил проверку и передал точечное задание. "
        "Исправь только перечисленные фрагменты, остальной текст не трогай. Не проводи новую экспертизу, "
        "не переписывай блоки целиком и не добавляй факты. Верни только JSON без markdown."
    )
    user_prompt = (
        "Для каждого элемента expert_task.exact_edits верни одну точечную замену. field и find скопируй "
        "дословно из задания эксперта; replace можешь слегка улучшить, но он должен решать только указанную "
        "проблему и не менять остальные факты. Для hashtags разрешено заменить один хештег или удалить его, "
        "вернув пустой replace. Ничего вне find заменено не будет. Если безопасной замены нет, "
        "помести задание в skipped. JSON-контракт: "
        "{\"edits\":[{\"field\":\"title|description|hashtags\",\"find\":\"точный исходный фрагмент\","
        "\"replace\":\"исправленный фрагмент\",\"reason\":\"кратко\"}],"
        "\"skipped\":[{\"field\":\"title|description|hashtags\",\"find\":\"...\",\"reason\":\"...\"}]}\n"
        + json.dumps(compact, ensure_ascii=False, default=str, separators=(",", ":"))
    )
    return {"input_context": compact, "system_prompt": system_prompt, "user_prompt": user_prompt}


def revise_seo_content_draft(context, draft, expert_review, model_settings=None):
    """Execute the expert's narrow edit task with the fast generation model."""
    api_key = service_credential("openrouter")
    if not api_key:
        try:
            seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    configurations = [
        {"provider": model_settings.get("primary_provider") or "codex",
         "model": model_settings.get("primary_model") or "gpt-5.6-luna"},
        {"provider": model_settings.get("fallback_provider") or "openrouter",
         "model": model_settings.get("fallback_model") or "deepseek/deepseek-v4-flash"},
    ]
    request_audit = build_seo_content_revision_request(context, draft, expert_review)
    started = time.monotonic()
    attempts, seen, last_error = [], set(), None
    for config in configurations:
        key = (config["provider"], config["model"])
        if key in seen:
            continue
        seen.add(key)
        attempt_started = time.monotonic()
        try:
            raw_content = _seo_ai_content(
                config["provider"], config["model"], request_audit["user_prompt"],
                request_audit["system_prompt"], api_key, reasoning_effort="low",
            )
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content.strip(), flags=re.I)
            decoded = json.loads(content)
            if not isinstance(decoded, dict) or not isinstance(decoded.get("edits"), list):
                raise ValueError("Редактор не вернул список точечных замен")
            attempts.append({"provider": config["provider"], "model": config["model"], "status": "ok",
                             "duration_ms": round((time.monotonic() - attempt_started) * 1000)})
            return {
                "edits": decoded.get("edits") or [],
                "skipped": decoded.get("skipped") or [],
                "model": f'{config["provider"]}:{config["model"]}',
                "audit": {**request_audit, "provider": config["provider"], "model": config["model"],
                          "reasoning_effort": "low", "raw_response": raw_content,
                          "response_json": decoded,
                          "duration_ms": round((time.monotonic() - started) * 1000),
                          "attempt_count": len(attempts), "fallback_used": len(attempts) > 1,
                          "provider_attempts": attempts},
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError,
                RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            attempts.append({"provider": config["provider"], "model": config["model"], "status": "error",
                             "error_type": type(exc).__name__,
                             "duration_ms": round((time.monotonic() - attempt_started) * 1000)})
    raise RuntimeError(f"Точечная редакция не выполнена основной и резервной моделью: {last_error}")


def review_seo_content_draft(context, draft, model_settings=None):
    """Run the stronger Terra/medium expert gate with fallback only on a real failure."""
    api_key = service_credential("openrouter")
    if not api_key:
        try:
            seo_bot_config = Path(os.environ.get("APPDATA", str(Path.home()))) / "SEO_Bot" / "config.json"
            api_key = str(json.loads(seo_bot_config.read_text(encoding="utf-8")).get("OPENROUTER_API_KEY") or "").strip()
        except (OSError, ValueError, TypeError):
            api_key = ""
    model_settings = model_settings or {}
    configurations = [
        {"provider": model_settings.get("primary_provider") or "codex",
         "model": model_settings.get("primary_model") or "gpt-5.6-terra"},
        {"provider": model_settings.get("fallback_provider") or "openrouter",
         "model": model_settings.get("fallback_model") or "deepseek/deepseek-v4-flash"},
    ]
    request_audit = build_seo_content_review_request(context, draft)
    last_error, seen = None, set()
    review_started = time.monotonic()
    provider_attempts = []
    for config in configurations:
        key = (config["provider"], config["model"])
        if key in seen:
            continue
        seen.add(key)
        attempt_started = time.monotonic()
        try:
            effort = "medium" if config["provider"] == "codex" else None
            raw_content = _seo_ai_content(
                config["provider"], config["model"], request_audit["user_prompt"],
                request_audit["system_prompt"], api_key, reasoning_effort=effort,
            )
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content.strip(), flags=re.I)
            decoded = json.loads(content)
            if not isinstance(decoded, dict):
                raise ValueError("Экспертная модель вернула JSON неподдерживаемого типа")
            provider_attempts.append({
                "provider": config["provider"],
                "model": config["model"],
                "status": "ok",
                "duration_ms": round((time.monotonic() - attempt_started) * 1000),
            })
            return {
                "review": decoded,
                "model": f'{config["provider"]}:{config["model"]}',
                "audit": {
                    **request_audit,
                    "provider": config["provider"],
                    "model": config["model"],
                    "reasoning_effort": effort,
                    "raw_response": raw_content,
                    "response_json": decoded,
                    "duration_ms": round((time.monotonic() - review_started) * 1000),
                    "attempt_count": len(provider_attempts),
                    "fallback_used": len(provider_attempts) > 1,
                    "provider_attempts": provider_attempts,
                },
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError,
                RuntimeError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            provider_attempts.append({
                "provider": config["provider"],
                "model": config["model"],
                "status": "error",
                "error_type": type(exc).__name__,
                "duration_ms": round((time.monotonic() - attempt_started) * 1000),
            })
    raise RuntimeError(f"Экспертная оценка не выполнена основной и резервной моделью: {last_error}")


def admin_users_payload():
    from user_registry import list_users

    with client_registry_connection() as conn:
        users = list_users(conn)
    return {
        "ok": True,
        "users": users,
        "clients": [
            {"key": key, "label": value["label"]}
            for key, value in ADMIN_CLIENTS.items()
            if value.get("show_in_dashboard")
        ],
        "reports": ADMIN_REPORT_CATALOG,
        "admin_sections": ADMIN_SECTION_CATALOG,
        "allowed_admin_sections": ordered_admin_sections(current_admin_section_ids()),
        "access_enforced": bool(users),
        "password_storage": "PBKDF2-SHA256",
        "data_sources": __import__("data_access").catalog(sys.modules[__name__]),
    }


def database_report_labels_for_object(schema_name, object_name, object_comment=""):
    text = f"{schema_name}.{object_name} {object_comment or ''}".lower()
    labels = []
    label_by_id = {item["id"]: item["label"] for item in ADMIN_REPORT_CATALOG}
    for report_id, keywords in DATABASE_REPORT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            labels.append(label_by_id.get(report_id, report_id))
    return list(dict.fromkeys(labels))


def admin_database_clients_by_db():
    clients_by_db = {}
    for key, config in ADMIN_CLIENTS.items():
        db_name = str(config.get("db_name") or "").strip()
        if not db_name:
            continue
        clients_by_db.setdefault(db_name, []).append({
            "key": key,
            "label": config.get("label") or key,
            "status": config.get("status") or "",
            "reports": list(config.get("reports") or []),
        })
    return clients_by_db


def admin_database_catalog_rows():
    try:
        with psycopg2.connect(**read_db_config(DEFAULT_CLIENT), cursor_factory=RealDictCursor) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT datname AS db_name,
                       datallowconn AS allow_connections,
                       pg_database_size(datname) AS size_bytes
                FROM pg_database
                WHERE NOT datistemplate
                ORDER BY datname
                """
            )
            return list(cur.fetchall()), ""
    except Exception as exc:
        return [], str(exc)


def admin_database_objects_for_client(client_key):
    object_rows = []
    field_rows = []
    with psycopg2.connect(**read_db_config(client_key), cursor_factory=RealDictCursor) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname AS schema_name,
                   c.relname AS object_name,
                   c.relkind,
                   CASE c.relkind
                       WHEN 'm' THEN 'materialized_view'
                       WHEN 'v' THEN 'view'
                       WHEN 'p' THEN 'partitioned_table'
                       ELSE 'table'
                   END AS object_type,
                   obj_description(c.oid, 'pg_class') AS object_comment,
                   GREATEST(c.reltuples::bigint, 0) AS estimated_rows,
                   COALESCE(s.n_live_tup, 0) AS live_rows,
                   COALESCE(s.n_dead_tup, 0) AS dead_rows,
                   pg_relation_size(c.oid) AS table_bytes,
                   pg_total_relation_size(c.oid) AS total_bytes
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE c.relkind IN ('r', 'p', 'm', 'v')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%'
            ORDER BY pg_total_relation_size(c.oid) DESC, n.nspname, c.relname
            """
        )
        object_rows = list(cur.fetchall())
        cur.execute(
            """
            SELECT n.nspname AS schema_name,
                   c.relname AS object_name,
                   a.attname AS column_name,
                   a.attnum AS ordinal_position,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                   NOT a.attnotnull AS is_nullable,
                   pg_get_expr(ad.adbin, ad.adrelid) AS column_default,
                   col_description(c.oid, a.attnum) AS column_comment
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attnum > 0
              AND NOT a.attisdropped
              AND c.relkind IN ('r', 'p', 'm', 'v')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND n.nspname NOT LIKE 'pg_toast%'
            ORDER BY n.nspname, c.relname, a.attnum
            """
        )
        field_rows = list(cur.fetchall())
    return object_rows, field_rows


def admin_database_overview_payload():
    clients_by_db = admin_database_clients_by_db()
    db_catalog_rows, catalog_error = admin_database_catalog_rows()
    catalog_by_name = {row["db_name"]: row for row in db_catalog_rows}
    report_labels_by_id = {item["id"]: item["label"] for item in ADMIN_REPORT_CATALOG}
    databases = []
    objects = []
    totals = {"databases": 0, "objects": 0, "tables": 0, "materialized_views": 0, "fields": 0, "rows_estimate": 0, "size_bytes": 0}

    for db_name in sorted(clients_by_db):
        clients = clients_by_db[db_name]
        client_key = clients[0]["key"]
        catalog = catalog_by_name.get(db_name, {})
        db_summary = {
            "db_name": db_name,
            "clients": clients,
            "client_labels": [client["label"] for client in clients],
            "reports": sorted({report_labels_by_id.get(report, report) for client in clients for report in client.get("reports") or []}),
            "size_bytes": int(catalog.get("size_bytes") or 0),
            "allow_connections": bool(catalog.get("allow_connections", True)),
            "object_count": 0,
            "table_count": 0,
            "materialized_view_count": 0,
            "view_count": 0,
            "field_count": 0,
            "rows_estimate": 0,
            "error": "",
        }
        try:
            object_rows, field_rows = admin_database_objects_for_client(client_key)
            field_map = {}
            for field in field_rows:
                key = (field["schema_name"], field["object_name"])
                field_map.setdefault(key, []).append({
                    "name": field["column_name"],
                    "type": field["data_type"],
                    "nullable": bool(field["is_nullable"]),
                    "default": field.get("column_default") or "",
                    "comment": field.get("column_comment") or "",
                })
            for row in object_rows:
                row_count = int(row.get("live_rows") or row.get("estimated_rows") or 0) if row.get("object_type") != "view" else None
                row_fields = field_map.get((row["schema_name"], row["object_name"]), [])
                report_labels = database_report_labels_for_object(row["schema_name"], row["object_name"], row.get("object_comment") or "")
                objects.append({
                    "db_name": db_name,
                    "client_labels": db_summary["client_labels"],
                    "schema": row["schema_name"],
                    "name": row["object_name"],
                    "type": row["object_type"],
                    "comment": row.get("object_comment") or "",
                    "rows_estimate": row_count,
                    "dead_rows": int(row.get("dead_rows") or 0),
                    "table_bytes": int(row.get("table_bytes") or 0),
                    "total_bytes": int(row.get("total_bytes") or 0),
                    "field_count": len(row_fields),
                    "fields": row_fields,
                    "reports": report_labels,
                })
                db_summary["object_count"] += 1
                db_summary["field_count"] += len(row_fields)
                db_summary["rows_estimate"] += row_count or 0
                if row.get("object_type") == "materialized_view":
                    db_summary["materialized_view_count"] += 1
                elif row.get("object_type") == "view":
                    db_summary["view_count"] += 1
                else:
                    db_summary["table_count"] += 1
        except Exception as exc:
            db_summary["error"] = str(exc)
        databases.append(db_summary)

    mapped_db_names = set(clients_by_db)
    for row in db_catalog_rows:
        if row["db_name"] in mapped_db_names:
            continue
        databases.append({
            "db_name": row["db_name"],
            "clients": [],
            "client_labels": [],
            "reports": [],
            "size_bytes": int(row.get("size_bytes") or 0),
            "allow_connections": bool(row.get("allow_connections", True)),
            "object_count": 0,
            "table_count": 0,
            "materialized_view_count": 0,
            "view_count": 0,
            "field_count": 0,
            "rows_estimate": 0,
            "error": "Не привязана к клиенту BI",
        })

    for db in databases:
        totals["databases"] += 1
        totals["objects"] += int(db.get("object_count") or 0)
        totals["tables"] += int(db.get("table_count") or 0)
        totals["materialized_views"] += int(db.get("materialized_view_count") or 0)
        totals["fields"] += int(db.get("field_count") or 0)
        totals["rows_estimate"] += int(db.get("rows_estimate") or 0)
        totals["size_bytes"] += int(db.get("size_bytes") or 0)
    payload = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "catalog_error": catalog_error,
        "databases": databases,
        "objects": objects,
        "totals": totals,
        "row_count_source": "pg_stat_user_tables.n_live_tup / pg_class.reltuples",
    }


def save_admin_user(payload):
    from user_registry import save_user

    if not str(
        os.environ.get("DASHBOARD_USER_SESSION_SECRET")
        or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
        or ""
    ).strip():
        raise RuntimeError("Настройте DASHBOARD_USER_SESSION_SECRET или ADMIN_AUTH_SESSION_SECRET")
    with client_registry_connection() as conn:
        saved = save_user(
            conn,
            payload,
            set(ADMIN_CLIENTS),
            {item["id"] for item in ADMIN_REPORT_CATALOG},
            ADMIN_SECTION_IDS,
            allowed_data_sources=__import__("data_access").catalog(sys.modules[__name__]),
        )
    result = admin_users_payload()
    result["saved_user"] = saved
    return result


def registered_client_credential(client, credential_key):
    try:
        from client_registry import read_credential
    except ModuleNotFoundError:
        from ozon_category_dashboard.client_registry import read_credential

    master_key = client_credentials_master_key(create=False)
    if not master_key:
        return ""
    with client_registry_connection() as conn:
        return read_credential(conn, str(client or "").strip().lower(), credential_key, master_key)


def clear_wb_api_rate_limits(path=None):
    rate_limits_path = Path(path or WB_API_RATE_LIMITS_FILE)
    if rate_limits_path.exists():
        rate_limits_path.unlink()


def wb_api_token_env(client=None):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    if client_key == "km_trade":
        return "WB_API_TOKEN_KM_TRADE"
    return WB_API_TOKEN_ENV


def save_wb_api_token(token, client=None):
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("WB API token is empty")
    token_env = wb_api_token_env(client)
    save_env_value(token_env, clean_token)
    if token_env == WB_API_TOKEN_ENV:
        clear_wb_api_rate_limits()
    return {
        "ok": True,
        "client": normalize_client_key(client or DEFAULT_CLIENT),
        "token_saved": wb_api_token_saved(client),
        "token_env": token_env,
    }


def wb_api_token_saved(client=None):
    token_env = wb_api_token_env(client)
    return bool(
        os.environ.get(token_env)
        or read_app_env_file().get(token_env)
        or registered_client_credential(client or DEFAULT_CLIENT, "wb_api_token")
    )


def normalize_ozon_seller_client_id(value):
    client_id = str(value or "").strip()
    if not client_id:
        raise ValueError("Укажите Ozon Client ID")
    if "\r" in client_id or "\n" in client_id:
        raise ValueError("Ozon Client ID содержит недопустимый перенос строки")
    return client_id


def normalize_ozon_seller_api_key(value):
    api_key = str(value or "").strip()
    if not api_key:
        raise ValueError("Укажите Ozon API key")
    if "\r" in api_key or "\n" in api_key:
        raise ValueError("Ozon API key содержит недопустимый перенос строки")
    return api_key


def ozon_seo_env_names(client=None):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    suffix = re.sub(r"[^A-Z0-9]+", "_", client_key.upper()).strip("_")
    return (
        f"{OZON_SELLER_CLIENT_ID_ENV}_{suffix}",
        f"{OZON_SELLER_API_KEY_ENV}_{suffix}",
    )


def ozon_seo_credentials_value(client=None):
    client_id_env, api_key_env = ozon_seo_env_names(client)
    env_values = read_app_env_file()
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    return (
        os.environ.get(client_id_env) or env_values.get(client_id_env)
        or registered_client_credential(client_key, "ozon_client_id") or "",
        os.environ.get(api_key_env) or env_values.get(api_key_env)
        or registered_client_credential(client_key, "ozon_api_key") or "",
    )


def ozon_seo_credentials_saved(client=None):
    client_id, api_key = ozon_seo_credentials_value(client)
    return bool(client_id and api_key)


def ozon_seo_admin_payload(client=None):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    client_id_env, api_key_env = ozon_seo_env_names(client_key)
    client_id, api_key = ozon_seo_credentials_value(client_key)
    return {
        "client": client_key,
        "credentials_saved": bool(client_id and api_key),
        "client_id_saved": bool(client_id),
        "api_key_saved": bool(api_key),
        "client_id": client_id,
        "client_id_env": client_id_env,
        "api_key_env": api_key_env,
        "env_file": str(WB_API_ENV_FILE),
    }


def save_ozon_seo_credentials(client, client_id, api_key):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    client_id_env, api_key_env = ozon_seo_env_names(client_key)
    clean_client_id = normalize_ozon_seller_client_id(client_id)
    clean_api_key = normalize_ozon_seller_api_key(api_key)
    save_env_value(client_id_env, clean_client_id)
    save_env_value(api_key_env, clean_api_key)
    return {"ok": True, **ozon_seo_admin_payload(client_key)}


def ozon_performance_env_names(client=None):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    suffix = re.sub(r"[^A-Z0-9]+", "_", client_key.upper()).strip("_")
    return (
        f"{OZON_PERFORMANCE_CLIENT_ID_ENV}_{suffix}",
        f"{OZON_PERFORMANCE_CLIENT_SECRET_ENV}_{suffix}",
    )


def ozon_performance_credentials_value(client=None):
    client_id_env, client_secret_env = ozon_performance_env_names(client)
    env_values = read_app_env_file()
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    return (
        os.environ.get(client_id_env) or env_values.get(client_id_env)
        or registered_client_credential(client_key, "ozon_performance_client_id") or "",
        os.environ.get(client_secret_env) or env_values.get(client_secret_env)
        or registered_client_credential(client_key, "ozon_performance_client_secret") or "",
    )


def ozon_performance_admin_payload(client=None):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    client_id_env, client_secret_env = ozon_performance_env_names(client_key)
    client_id, client_secret = ozon_performance_credentials_value(client_key)
    return {
        "client": client_key,
        "credentials_saved": bool(client_id and client_secret),
        "client_id_saved": bool(client_id),
        "client_secret_saved": bool(client_secret),
        "client_id": client_id,
        "client_id_env": client_id_env,
        "client_secret_env": client_secret_env,
        "env_file": str(WB_API_ENV_FILE),
    }


def save_ozon_performance_credentials(client, client_id, client_secret):
    client_key = normalize_client_key(client or DEFAULT_CLIENT)
    if client_key != "km_trade":
        raise ValueError("Performance API pipeline разрешён только для KM Trade")
    client_id_env, client_secret_env = ozon_performance_env_names(client_key)
    clean_client_id = normalize_ozon_seller_client_id(client_id)
    clean_client_secret = normalize_ozon_seller_api_key(client_secret)
    save_env_value(client_id_env, clean_client_id)
    save_env_value(client_secret_env, clean_client_secret)
    return {"ok": True, **ozon_performance_admin_payload(client_key)}


def assert_wb_api_read_only_request(path, method="GET", data=None):
    method = str(method or "").strip().upper()
    raw_path = str(path or "").strip()
    endpoint = urlparse(raw_path).path
    if method == "POST" and endpoint in WB_API_READ_ONLY_POST_PATHS:
        validate_wb_api_read_only_post_body(endpoint, data)
        return
    if method not in WB_API_READ_ONLY_METHODS:
        raise ValueError("WB API токен разрешен только для GET запросов чтения")
    if data not in (None, b"", ""):
        raise ValueError("WB API токен разрешен только для запросов без тела")
    if endpoint not in WB_API_READ_ONLY_PATHS and not any(endpoint.startswith(prefix) for prefix in WB_API_READ_ONLY_PATH_PREFIXES):
        raise ValueError(f"WB API endpoint не разрешен read-only политикой: {endpoint}")


def validate_wb_api_read_only_post_body(endpoint, data):
    if data in (None, b"", ""):
        raise ValueError(f"WB API {endpoint} требует JSON-тело запроса")
    try:
        payload = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Некорректное JSON-тело для WB API {endpoint}") from exc
    if endpoint == "/adv/v1/stats":
        if not isinstance(payload, list) or not 1 <= len(payload) <= 100:
            raise ValueError("WB API /adv/v1/stats принимает массив от 1 до 100 кампаний")
        for item in payload:
            if not isinstance(item, dict) or set(item) != {"id", "dates"}:
                raise ValueError("WB API /adv/v1/stats разрешает только поля id и dates")
            if not isinstance(item.get("id"), int) or item["id"] <= 0:
                raise ValueError("WB API /adv/v1/stats требует положительный id кампании")
            dates = item.get("dates")
            if not isinstance(dates, list) or not dates:
                raise ValueError("WB API /adv/v1/stats требует непустой список dates")
            for value in dates:
                normalize_iso_date(value)
        return
    if endpoint == "/content/v2/get/cards/list":
        if not isinstance(payload, dict):
            raise ValueError("WB Content cards list принимает JSON-объект")
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("WB Content cards list требует settings")
        return
    raise ValueError(f"WB API POST endpoint не разрешен read-only политикой: {endpoint}")


class WbApiError(RuntimeError):
    def __init__(self, status_code, detail, retry_after=None):
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(detail)


def wb_api_error_message(status_code, detail, retry_after=None):
    if status_code != 429:
        return f"WB API вернул {status_code}: {detail}"
    retry_hint = ""
    if retry_after:
        retry_hint = f" WB просит повторить не раньше чем через {retry_after} сек."
    return (
        "WB API вернул 429: превышен лимит запросов."
        " Для Personal/Service токена у Media API лимит 10 запросов/сек,"
        " для Base токена у /adv/v1/adverts лимит 1 запрос в час."
        f"{retry_hint} Подождите перед повторным запуском или используйте Personal/Service токен."
        f" Ответ WB: {detail}"
    )


def is_wb_api_deadline_error(exc):
    detail = str(getattr(exc, "detail", "") or "").lower()
    return getattr(exc, "status_code", None) in {500, 502, 503, 504} and (
        "deadline" in detail
        or "context deadline exceeded" in detail
        or "timeout" in detail
        or "timed out" in detail
    )


def is_wb_api_retryable_server_error(exc):
    return getattr(exc, "status_code", None) in {500, 502, 503, 504} and not is_wb_api_deadline_error(exc)


def read_wb_api_rate_limits(path=None):
    rate_limits_path = Path(path or WB_API_RATE_LIMITS_FILE)
    if not rate_limits_path.exists():
        return {}
    try:
        data = json.loads(rate_limits_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_wb_api_rate_limits(data, path=None):
    rate_limits_path = Path(path or WB_API_RATE_LIMITS_FILE)
    rate_limits_path.parent.mkdir(parents=True, exist_ok=True)
    rate_limits_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def wb_api_cooldown_until(method_key):
    value = read_wb_api_rate_limits().get(method_key)
    if not value:
        return None
    try:
        until = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return until if until > datetime.now() else None


def remember_wb_api_cooldown(method_key, seconds=3600):
    seconds = max(60, int(seconds or 3600))
    until = datetime.now() + timedelta(seconds=seconds)
    data = read_wb_api_rate_limits()
    data[method_key] = until.isoformat(timespec="seconds")
    write_wb_api_rate_limits(data)
    return until


def raise_if_wb_api_cooldown(method_key, method_label):
    until = wb_api_cooldown_until(method_key)
    if not until:
        return
    raise RuntimeError(
        f"{method_label}: WB API временно ограничил метод после 429. "
        f"Можно повторить после {until.strftime('%d.%m.%Y %H:%M:%S')}. "
        "До этого времени новый запуск не отправляется в WB, чтобы не продлевать ожидание."
    )


class WbApiStopRequested(RuntimeError):
    pass


def wb_api_log_path(method_key):
    safe_method = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(method_key or "wb_api"))
    log_dir = Path(WB_API_LOG_OUTPUT_DIR) / safe_method
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(chr(39) + "%Y%m%d_%H%M%S" + chr(39))
    return log_dir / f"{safe_method}_{timestamp}.log"


def write_wb_api_log_line(state, message):
    if not state:
        return
    log_file = state.get("log_file")
    if not log_file:
        return
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}"
    with WB_API_LOG_LOCK:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(log_file).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def wb_api_last_log_file(method_key):
    value = WB_API_LAST_LOG_FILES.get(str(method_key or ""))
    return str(value or "")


def acquire_wb_api_run(method_key):
    with WB_API_RUNNING_LOCK:
        current = WB_API_RUNNING.get(method_key)
        if current:
            return False, current
        log_file = wb_api_log_path(method_key)
        state = {
            "method_key": method_key,
            "label": WB_API_METHOD_LABELS.get(method_key, method_key),
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "log_file": str(log_file),
            "stop_requested": False,
            "stop_event": threading.Event(),
            "progress": {
                "phase": "starting",
                "message": "Скрипт запущен, готовлю план запросов",
                "current": 0,
                "total": 0,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            },
        }
        WB_API_LAST_LOG_FILES[method_key] = str(log_file)
        WB_API_RUNNING[method_key] = state
    write_wb_api_log_line(state, f"START | {state['label']} | method={method_key}")
    with WB_API_RUNNING_LOCK:
        return True, state


def release_wb_api_run(method_key):
    with WB_API_RUNNING_LOCK:
        WB_API_RUNNING.pop(method_key, None)


def update_wb_api_progress(method_key, **progress):
    with WB_API_RUNNING_LOCK:
        state = WB_API_RUNNING.get(method_key)
        if not state:
            return
        current = dict(state.get("progress") or {})
        current.update(progress)
        current["updated_at"] = datetime.now().strftime("%H:%M:%S")
        state["progress"] = current
    write_wb_api_log_line(
        state,
        f"PROGRESS | phase={current.get('phase', '')} | current={current.get('current', 0)} | total={current.get('total', 0)} | {current.get('message', '')}",
    )
    emit_wb_api_stream_event(
        {
            "event": "progress",
            "method": method_key,
            "running": True,
            "progress": current,
            "log_file": state.get("log_file", ""),
        }
    )


def emit_wb_api_stream_event(payload):
    sender = getattr(WB_API_STREAM_CONTEXT, "sender", None)
    if not sender:
        return True
    ok = sender(payload)
    if ok:
        return True
    state = getattr(WB_API_RUN_CONTEXT, "state", None)
    if state:
        state["stop_requested"] = True
        stop_event = state.get("stop_event")
        if stop_event:
            stop_event.set()
    raise WbApiStopRequested("Поток браузера закрыт, выгрузка остановлена")


def handle_wb_api_progress(parsed):
    params = parse_qs(parsed.query)
    method_key = str(params.get("method", [""])[0] or "")
    if method_key not in WB_API_METHOD_LABELS:
        return {
            "ok": False,
            "method": method_key,
            "running": False,
            "progress": {},
            "error": "Не выбран скрипт WB API",
        }
    with WB_API_RUNNING_LOCK:
        state = WB_API_RUNNING.get(method_key)
        if not state:
            return {
                "ok": True,
                "method": method_key,
                "label": WB_API_METHOD_LABELS.get(method_key, method_key),
                "running": False,
                "progress": {},
                "log_file": wb_api_last_log_file(method_key),
            }
        return {
            "ok": True,
            "method": method_key,
            "label": state.get("label", method_key),
            "running": True,
            "started_at": state.get("started_at", ""),
            "log_file": state.get("log_file", ""),
            "stop_requested": bool(state.get("stop_requested")),
            "progress": dict(state.get("progress") or {}),
        }


def handle_wb_api_stop(payload):
    payload = payload if isinstance(payload, dict) else {}
    method_key = str(payload.get("method") or payload.get("method_key") or "")
    if method_key not in WB_API_METHOD_LABELS:
        return {"ok": False, "method": method_key, "state": "idle", "error": "Не выбран скрипт WB API для остановки"}
    with WB_API_RUNNING_LOCK:
        state = WB_API_RUNNING.get(method_key)
        if not state:
            return {"ok": False, "method": method_key, "state": "idle", "error": "Этот скрипт сейчас не запущен"}
        state["stop_requested"] = True
        stop_event = state.get("stop_event")
        if stop_event:
            stop_event.set()
        return {
            "ok": True,
            "method": method_key,
            "label": state.get("label", method_key),
            "state": "stopping",
            "started_at": state.get("started_at", ""),
        }


def check_wb_api_stop_requested():
    state = getattr(WB_API_RUN_CONTEXT, "state", None)
    if state and state.get("stop_requested"):
        raise WbApiStopRequested("Скрипт остановлен пользователем")


def sleep_wb_api(seconds):
    check_wb_api_stop_requested()
    state = getattr(WB_API_RUN_CONTEXT, "state", None)
    stop_event = state.get("stop_event") if state else None
    if stop_event:
        if stop_event.wait(max(0, float(seconds or 0))):
            check_wb_api_stop_requested()
            raise WbApiStopRequested("Скрипт остановлен пользователем")
    else:
        time.sleep(seconds)
    check_wb_api_stop_requested()


def format_wb_api_duration(seconds):
    seconds = max(0, int(round(float(seconds or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м {secs:02d}с"
    if minutes:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def wb_api_eta_text(started_monotonic, completed, total):
    if not started_monotonic or completed <= 0 or total <= completed:
        return "расчет..."
    elapsed = max(0.1, time.monotonic() - started_monotonic)
    avg_seconds = elapsed / completed
    remaining = max(0, total - completed) * avg_seconds
    return format_wb_api_duration(remaining)


def update_wb_promotion_fullstats_progress(
    method_key,
    phase,
    current,
    total,
    message,
    *,
    started_monotonic=None,
    completed_for_eta=None,
):
    completed = current if completed_for_eta is None else completed_for_eta
    pct = (current / total * 100) if total else 0
    eta = wb_api_eta_text(started_monotonic, completed, total) if started_monotonic else ""
    progress_message = f"ПРОГРЕСС: {current}/{total} запросов ({pct:.1f}%) | {message}"
    if eta:
        progress_message = f"{progress_message} | ETA: {eta}"
    update_wb_api_progress(
        method_key,
        phase=phase,
        current=current,
        total=total,
        message=progress_message,
    )


def sleep_wb_promotion_fullstats_pause(
    method_key,
    seconds,
    request_number,
    total_requests,
    started_monotonic,
    previous_result_message="",
):
    remaining = int(round(max(0, float(seconds or 0))))
    while remaining > 0:
        wait_chunk = min(5, remaining)
        prefix = f"{previous_result_message} | " if previous_result_message else ""
        update_wb_promotion_fullstats_progress(
            method_key,
            "wait",
            request_number,
            total_requests,
            f"{prefix}лимитная пауза перед следующим WB-запросом, осталось {format_wb_api_duration(remaining)}",
            started_monotonic=started_monotonic,
            completed_for_eta=request_number,
        )
        sleep_wb_api(wait_chunk)
        remaining -= wait_chunk


def run_wb_api_export(method_key, handler, payload):
    acquired, state = acquire_wb_api_run(method_key)
    if not acquired:
        raise RuntimeError(f"{WB_API_METHOD_LABELS.get(method_key, method_key)} уже выполняется. Остановите текущий запуск или дождитесь завершения.")
    previous_state = getattr(WB_API_RUN_CONTEXT, "state", None)
    WB_API_RUN_CONTEXT.state = state
    try:
        result = handler(payload)
        if isinstance(result, dict):
            result.setdefault("stopped", bool(state.get("stop_requested")))
            result["log_file"] = state.get("log_file", "")
            files = result.get("files")
            if isinstance(files, dict):
                files.setdefault("log", state.get("log_file", ""))
        write_wb_api_log_line(state, f"DONE | stopped={bool(state.get('stop_requested'))} | result={json.dumps({k: v for k, v in (result or {}).items() if k not in {'response'}}, ensure_ascii=False, default=str)[:2000]}")
        return result
    except WbApiStopRequested:
        write_wb_api_log_line(state, "STOPPED | Скрипт остановлен")
        return {
            "ok": True,
            "method": method_key,
            "count": 0,
            "requests": 0,
            "files": {"log": state.get("log_file", "")},
            "output_dir": "",
            "stopped": True,
            "log_file": state.get("log_file", ""),
        }
    except Exception as exc:
        write_wb_api_log_line(state, f"ERROR | {type(exc).__name__}: {exc}")
        raise
    finally:
        if previous_state is None:
            try:
                del WB_API_RUN_CONTEXT.state
            except AttributeError:
                pass
        else:
            WB_API_RUN_CONTEXT.state = previous_state
        release_wb_api_run(method_key)


def wb_api_request_json(path, token):
    assert_wb_api_read_only_request(path, method="GET", data=None)
    check_wb_api_stop_requested()
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("Сначала сохраните WB API token")
    headers = {
        "Authorization": clean_token,
        "Accept": "application/json",
    }
    request = Request(
        f"{WB_MEDIA_API_BASE_URL}{path}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к WB API: {exc.reason}") from exc


def wb_promotion_api_request_json(path, token):
    assert_wb_api_read_only_request(path, method="GET", data=None)
    check_wb_api_stop_requested()
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("Сначала сохраните WB API token")
    request = Request(
        f"{WB_PROMOTION_API_BASE_URL}{path}",
        headers={
            "Authorization": clean_token,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к WB Promotion API: {exc.reason}") from exc


def wb_api_post_json_read_only(path, token, body):
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    assert_wb_api_read_only_request(path, method="POST", data=body_bytes)
    check_wb_api_stop_requested()
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("Сначала сохраните WB API token")
    request = Request(
        f"{WB_MEDIA_API_BASE_URL}{path}",
        data=body_bytes,
        headers={
            "Authorization": clean_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload) if payload.strip() else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        detail = body_text.strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к WB API: {exc.reason}") from exc


WB_ANALYTICS_DIRECT_PATHS = {
    "products": "/api/analytics/v3/sales-funnel/products",
    "products_history": "/api/analytics/v3/sales-funnel/products/history",
    "grouped_history": "/api/analytics/v3/sales-funnel/grouped/history",
    "search_report": "/api/v2/search-report/report",
    "stocks_groups": "/api/v2/stocks-report/products/groups",
    "stocks_products": "/api/v2/stocks-report/products/products",
    "stocks_sizes": "/api/v2/stocks-report/products/sizes",
    "stocks_offices": "/api/v2/stocks-report/products/offices",
}
WB_ANALYTICS_DIRECT_KEYS = {
    "products": {"selectedPeriod", "pastPeriod", "nmIds", "brandNames", "subjectIds", "tagIds", "skipDeletedNm", "orderBy", "limit", "offset"},
    "products_history": {"selectedPeriod", "nmIds", "skipDeletedNm", "aggregationLevel"},
    "grouped_history": {"selectedPeriod", "brandNames", "subjectIds", "tagIds", "skipDeletedNm", "aggregationLevel"},
    "search_report": {"currentPeriod", "pastPeriod", "nmIds", "subjectIds", "brandNames", "tagIds", "positionCluster", "orderBy", "includeSubstitutedSKUs", "includeSearchTexts", "limit", "offset"},
    "stocks_groups": {"currentPeriod", "subjectIDs", "brandNames", "tagIDs", "stockType", "skipDeletedNm", "orderBy", "availabilityFilters", "limit", "offset"},
    "stocks_products": {"currentPeriod", "nmIDs", "subjectID", "brandName", "tagID", "stockType", "skipDeletedNm", "orderBy", "availabilityFilters", "limit", "offset"},
    "stocks_sizes": {"currentPeriod", "nmID", "stockType", "skipDeletedNm", "orderBy", "availabilityFilters", "limit", "offset"},
    "stocks_offices": {"currentPeriod", "nmID", "sizeName", "stockType", "skipDeletedNm", "orderBy", "availabilityFilters", "limit", "offset"},
}
WB_ANALYTICS_CSV_TYPES = {
    "DETAIL_HISTORY_REPORT", "GROUPED_HISTORY_REPORT",
    "SEARCH_QUERIES_PREMIUM_REPORT_GROUP", "SEARCH_QUERIES_PREMIUM_REPORT_TEXT",
    "STOCK_HISTORY_REPORT_CSV", "STOCK_HISTORY_DAILY_CSV",
}


def wb_analytics_request(path, token, method="POST", body=None, binary=False):
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("WB Analytics token is not configured")
    data = None
    headers = {"Authorization": clean_token, "Accept": "application/zip" if binary else "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{WB_ANALYTICS_API_BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=180) as response:
            raw = response.read()
            if binary:
                return raw, response.headers.get("Content-Type") or "application/zip"
            text = raw.decode("utf-8", errors="replace")
            return json.loads(text) if text.strip() else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("X-Ratelimit-Retry") or exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(float(retry_after)) if retry_after else None
        except (TypeError, ValueError):
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"WB Analytics connection failed: {exc.reason}") from exc


def validate_wb_analytics_period(value, label):
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError(f"{label} must contain start and end")
    start, end = normalize_iso_date(value.get("start")), normalize_iso_date(value.get("end"))
    if start > end:
        raise ValueError(f"{label}: start is after end")
    return {"start": start, "end": end}


def validate_wb_analytics_direct_body(report, body):
    if not isinstance(body, dict):
        raise ValueError("Request body must be an object")
    unknown = set(body) - WB_ANALYTICS_DIRECT_KEYS[report]
    if unknown:
        raise ValueError(f"Unsupported parameters for {report}: {', '.join(sorted(unknown))}")
    period_key = "currentPeriod" if report.startswith("stocks_") or report == "search_report" else "selectedPeriod"
    body[period_key] = validate_wb_analytics_period(body.get(period_key), period_key)
    if body.get("pastPeriod") is not None:
        body["pastPeriod"] = validate_wb_analytics_period(body["pastPeriod"], "pastPeriod")
    if report == "products_history":
        count = len(body.get("nmIds") or [])
        if count < 1 or count > 20:
            raise ValueError("products_history requires 1-20 nmIds")
    if report == "grouped_history":
        combinations = 1
        for key in ("brandNames", "subjectIds", "tagIds"):
            combinations *= max(1, len(body.get(key) or []))
        if combinations > 16:
            raise ValueError("brandNames x subjectIds x tagIds must not exceed 16")
    if report == "search_report" and not body.get("includeSubstitutedSKUs") and not body.get("includeSearchTexts"):
        raise ValueError("includeSubstitutedSKUs and includeSearchTexts cannot both be false")
    if "limit" in body:
        limit = int(body["limit"])
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        body["limit"] = limit
    if "offset" in body:
        body["offset"] = max(0, int(body["offset"]))
    if len(json.dumps(body, ensure_ascii=False)) > 200000:
        raise ValueError("Request body is too large")
    return body


def wb_analytics_output_file(kind, path, request_body, response):
    output_dir = Path(WB_API_LOG_OUTPUT_DIR) / "analytics-results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    file_path = output_dir / f"wb_analytics_{kind}_{stamp}.json"
    file_path.write_text(json.dumps({"path": path, "request": request_body, "response": response}, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def wb_analytics_flatten_record(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict):
                result.update(wb_analytics_flatten_record(child, name))
            elif isinstance(child, list):
                result[name] = json.dumps(child, ensure_ascii=False)
            else:
                result[name] = child
    else:
        result[prefix or "value"] = value
    return result


def wb_analytics_excel_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value)[:32767]


def wb_analytics_excel_sheet_name(value, used_names):
    clean = re.sub(r"[\[\]:*?/\\]", "_", str(value or "Данные")).strip()[:31] or "Данные"
    candidate = clean
    suffix = 2
    while candidate in used_names:
        tail = f"_{suffix}"
        candidate = f"{clean[:31 - len(tail)]}{tail}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def wb_analytics_collect_excel_tables(value, path="Ответ", tables=None):
    tables = tables if tables is not None else []
    if isinstance(value, list):
        if value:
            rows = [
                wb_analytics_flatten_record(item) if isinstance(item, dict) else {"value": item}
                for item in value
            ]
            tables.append((path, rows))
        return tables
    if isinstance(value, dict):
        scalar_row = {}
        for key, child in value.items():
            child_path = f"{path}_{key}"
            if isinstance(child, list):
                wb_analytics_collect_excel_tables(child, child_path, tables)
            elif isinstance(child, dict):
                scalar_row.update(wb_analytics_flatten_record(child, str(key)))
                wb_analytics_collect_excel_tables(child, child_path, tables)
            else:
                scalar_row[str(key)] = child
        if scalar_row:
            tables.insert(0, (path, [scalar_row]))
    return tables


def wb_analytics_write_excel_table(ws, rows):
    headers = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    if not headers:
        headers = ["value"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(name=KOKOC_EXCEL_FONT, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=KOKOC_EXCEL_TIFFANY_DARK)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in rows:
        ws.append([wb_analytics_excel_cell(row.get(header)) for header in headers])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, header in enumerate(headers, start=1):
        max_length = len(str(header))
        for column_cells in ws.iter_cols(min_col=index, max_col=index, min_row=2, max_row=min(ws.max_row, 250)):
            max_length = max(max_length, *(len(str(item.value or "")) for item in column_cells))
        ws.column_dimensions[get_column_letter(index)].width = min(max(max_length + 2, 12), 48)


def wb_analytics_excel_file(report, request_body, response, json_path):
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names = set()
    params = workbook.create_sheet(wb_analytics_excel_sheet_name("Параметры", used_names))
    wb_analytics_write_excel_table(
        params,
        [{"Параметр": key, "Значение": value} for key, value in wb_analytics_flatten_record(request_body).items()],
    )
    tables = wb_analytics_collect_excel_tables(response)
    if not tables:
        tables = [("Ответ", [{"value": response}])]
    for path, rows in tables:
        sheet = workbook.create_sheet(wb_analytics_excel_sheet_name(path, used_names))
        wb_analytics_write_excel_table(sheet, rows)
    xlsx_path = json_path.with_suffix(".xlsx")
    workbook.save(xlsx_path)
    return xlsx_path

def handle_wb_analytics_export(payload):
    payload = payload if isinstance(payload, dict) else {}
    report = str(payload.get("report") or "")
    path = WB_ANALYTICS_DIRECT_PATHS.get(report)
    if not path:
        raise ValueError("Unknown WB Analytics method")
    body = validate_wb_analytics_direct_body(report, payload.get("body"))
    result = wb_analytics_request(path, wb_api_token_value(), body=body)
    file_path = wb_analytics_output_file(report, path, body, result)
    xlsx_path = wb_analytics_excel_file(report, body, result, file_path)
    return {
        "ok": True,
        "report": report,
        "request": body,
        "response": result,
        "file": str(file_path),
        "xlsx": str(xlsx_path),
    }


def handle_wb_analytics_excel_download(payload):
    analytics_dir = (Path(WB_API_LOG_OUTPUT_DIR) / "analytics-results").resolve()
    file_path = Path(str((payload or {}).get("file") or "")).resolve()
    if file_path.suffix.lower() != ".xlsx" or not is_path_inside(file_path, analytics_dir):
        raise ValueError("Недопустимый путь Excel-файла")
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError("Excel-файл отчёта не найден")
    return file_path.read_bytes(), file_path.name


def handle_wb_analytics_csv_create(payload):
    payload = payload if isinstance(payload, dict) else {}
    body = dict(payload.get("body") or {})
    report_type = str(body.get("reportType") or "")
    if report_type not in WB_ANALYTICS_CSV_TYPES:
        raise ValueError("Unknown WB Analytics CSV report type")
    request_id = str(body.get("id") or uuid.uuid4())
    try:
        request_id = str(uuid.UUID(request_id))
    except ValueError as exc:
        raise ValueError("CSV request id must be UUID") from exc
    body["id"] = request_id
    if not isinstance(body.get("params"), dict):
        raise ValueError("CSV params must be an object")
    if len(json.dumps(body, ensure_ascii=False)) > 200000:
        raise ValueError("CSV request body is too large")
    path = "/api/v2/nm-report/downloads"
    result = wb_analytics_request(path, wb_api_token_value(), body=body)
    file_path = wb_analytics_output_file("csv_create", path, body, result)
    return {"ok": True, "download_id": request_id, "request": body, "response": result, "file": str(file_path)}


def wb_analytics_download_ids(payload, key="download_ids"):
    raw = (payload or {}).get(key) or []
    values = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw))
    result = []
    for value in values:
        if not str(value).strip():
            continue
        try:
            result.append(str(uuid.UUID(str(value).strip())))
        except ValueError as exc:
            raise ValueError(f"Invalid report UUID: {value}") from exc
    return result


def handle_wb_analytics_csv_list(payload):
    ids = wb_analytics_download_ids(payload)
    query = urlencode([("filter[downloadIds][]", value) for value in ids])
    path = "/api/v2/nm-report/downloads" + (f"?{query}" if query else "")
    result = wb_analytics_request(path, wb_api_token_value(), method="GET")
    file_path = wb_analytics_output_file("csv_list", path, None, result)
    return {"ok": True, "download_ids": ids, "response": result, "file": str(file_path)}


def handle_wb_analytics_csv_retry(payload):
    ids = wb_analytics_download_ids(payload, "download_id")
    if len(ids) != 1:
        raise ValueError("Exactly one report UUID is required")
    path, body = "/api/v2/nm-report/downloads/retry", {"downloadId": ids[0]}
    result = wb_analytics_request(path, wb_api_token_value(), body=body)
    file_path = wb_analytics_output_file("csv_retry", path, body, result)
    return {"ok": True, "download_id": ids[0], "response": result, "file": str(file_path)}


def handle_wb_analytics_csv_download(payload):
    ids = wb_analytics_download_ids(payload, "download_id")
    if len(ids) != 1:
        raise ValueError("Exactly one report UUID is required")
    download_id = ids[0]
    path = f"/api/v2/nm-report/downloads/file/{download_id}"
    body, content_type = wb_analytics_request(path, wb_api_token_value(), method="GET", binary=True)
    return body, f"wb_analytics_{download_id}.zip", content_type


def wb_content_api_request_json(path, token):
    assert_wb_api_read_only_request(path, method="GET", data=None)
    check_wb_api_stop_requested()
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("Сначала сохраните WB API token")
    request = Request(
        f"{WB_CONTENT_API_BASE_URL}{path}",
        headers={
            "Authorization": clean_token,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к WB Content API: {exc.reason}") from exc


def wb_content_api_post_json_read_only(path, token, body):
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    assert_wb_api_read_only_request(path, method="POST", data=body_bytes)
    check_wb_api_stop_requested()
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("Сначала сохраните WB API token")
    request = Request(
        f"{WB_CONTENT_API_BASE_URL}{path}",
        data=body_bytes,
        headers={
            "Authorization": clean_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return json.loads(payload) if payload.strip() else {}
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        detail = body_text.strip() or exc.reason or f"HTTP {exc.code}"
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            retry_after = int(retry_after) if retry_after else None
        except ValueError:
            retry_after = None
        raise WbApiError(exc.code, wb_api_error_message(exc.code, detail, retry_after), retry_after) from exc
    except URLError as exc:
        raise RuntimeError(f"Не удалось подключиться к WB Content API: {exc.reason}") from exc


def normalize_iso_date(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Некорректная дата: {value}") from exc


def ozon_api_datetime(value, *, start):
    clean = normalize_iso_date(value)
    return f"{clean}{'T00:00:00Z' if start else 'T23:59:59Z'}"


def build_ozon_seo_details_payload(payload):
    raw_skus = payload.get("skus")
    if isinstance(raw_skus, (list, tuple, set)):
        skus = []
        for value in raw_skus:
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in skus:
                skus.append(cleaned)
    else:
        skus = [str(payload.get("sku") or "").strip()] if str(payload.get("sku") or "").strip() else []
    if not skus:
        raise ValueError("Укажите SKU")
    if len(skus) > 1000:
        raise ValueError("Ozon принимает не более 1000 SKU за запрос")
    date_from = str(payload.get("date_from") or "").strip()
    date_to = str(payload.get("date_to") or "").strip()
    if not date_from or not date_to:
        raise ValueError("Укажите дату с и дату по")
    try:
        limit_by_sku = int(payload.get("limit_by_sku", 15))
        page = int(payload.get("page", 0))
        page_size = int(payload.get("page_size", 100))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit_by_sku, page и page_size должны быть числами") from exc
    if limit_by_sku < 1 or limit_by_sku > 15:
        raise ValueError("limit_by_sku должен быть от 1 до 15")
    if page < 0:
        raise ValueError("page должен быть 0 или больше")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size должен быть от 1 до 100")
    sort_by = str(payload.get("sort_by") or "BY_SEARCHES").strip() or "BY_SEARCHES"
    sort_dir = str(payload.get("sort_dir") or "DESCENDING").strip() or "DESCENDING"
    return {
        "date_from": ozon_api_datetime(date_from, start=True),
        "date_to": ozon_api_datetime(date_to, start=False),
        "limit_by_sku": limit_by_sku,
        "page": page,
        "page_size": page_size,
        "skus": skus,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }


def handle_ozon_seo_product_queries_details(payload):
    client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
    saved_client_id, saved_api_key = ozon_seo_credentials_value(client)
    client_id = str(payload.get("client_id") or saved_client_id or "").strip()
    api_key = str(payload.get("api_key") or saved_api_key or "").strip()
    if not client_id or not api_key:
        raise ValueError("Укажите или сохраните Ozon Client ID и API key")
    client_id = normalize_ozon_seller_client_id(client_id)
    api_key = normalize_ozon_seller_api_key(api_key)
    log_file = wb_api_log_path("ozon_seo_details")
    WB_API_LAST_LOG_FILES["ozon_seo_details"] = str(log_file)
    request_payload = build_ozon_seo_details_payload(payload)
    log_state = {"log_file": str(log_file)}
    write_wb_api_log_line(
        log_state,
        (
            "START | POST /v1/analytics/product-queries/details | "
            f"skus={len(request_payload.get('skus', []))} | "
            f"date_from={request_payload.get('date_from')} | date_to={request_payload.get('date_to')} | "
            f"page={request_payload.get('page')} | page_size={request_payload.get('page_size')}"
        ),
    )
    body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{OZON_SELLER_API_BASE_URL}/v1/analytics/product-queries/details",
        data=body,
        headers={
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_payload = json.loads(raw) if raw.strip() else {}
            items = []
            if isinstance(response_payload, dict):
                for key in ("queries", "items"):
                    if isinstance(response_payload.get(key), list):
                        items = response_payload[key]
                        break
            write_wb_api_log_line(log_state, f"DONE | HTTP {response.status} | items={len(items) if isinstance(items, list) else 0}")
            return {
                "ok": True,
                "status": response.status,
                "method": "POST /v1/analytics/product-queries/details",
                "request_payload": request_payload,
                "response": response_payload,
                "log_file": str(log_file),
                "files": {"log": str(log_file)},
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            detail = raw.strip() or exc.reason or f"HTTP {exc.code}"
        write_wb_api_log_line(log_state, f"ERROR | Ozon API HTTP {exc.code}: {detail}")
        raise RuntimeError(f"Ozon API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        write_wb_api_log_line(log_state, f"ERROR | Ozon connection: {exc.reason}")
        raise RuntimeError(f"Не удалось подключиться к Ozon Seller API: {exc.reason}") from exc


def fetch_ozon_project_questions(client, skus, period_from, period_to):
    """Read exact-SKU customer questions without changing their cabinet status."""
    client_id, api_key = ozon_seo_credentials_value(client)
    if not client_id or not api_key:
        raise ValueError(f"Для клиента {client} не сохранены Ozon Seller credentials")
    numeric_skus = []
    for value in skus or []:
        try:
            numeric_skus.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    if not numeric_skus:
        return []
    headers = {
        "Client-Id": normalize_ozon_seller_client_id(client_id),
        "Api-Key": normalize_ozon_seller_api_key(api_key),
        "Content-Type": "application/json", "Accept": "application/json",
    }
    last_id = ""
    rows = {}
    for page_number in range(1, 501):
        body = {
            "filter": {
                "date_from": f"{period_from.isoformat()}T00:00:00Z",
                "date_to": f"{period_to.isoformat()}T23:59:59Z",
                "sku": numeric_skus,
            },
            "limit": 100,
        }
        if last_id:
            body["last_id"] = last_id
        request = Request(
            f"{OZON_SELLER_API_BASE_URL}/v1/question/list",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST",
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
                break
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    detail = {}
                message = str(detail.get("message") or detail.get("error") or exc.reason or "")[:300]
                if (exc.code == 429 or 500 <= exc.code <= 599) and attempt < 2:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        pause = min(20.0, max(1.0, float(retry_after)))
                    except (TypeError, ValueError):
                        pause = 2.0 * (attempt + 1)
                    time.sleep(pause)
                    continue
                raise RuntimeError(f"Ozon Questions API HTTP {exc.code}: {message}") from exc
            except URLError as exc:
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise RuntimeError(f"Ozon Questions API недоступен: {exc.reason}") from exc
        page_rows = payload.get("questions") or ((payload.get("result") or {}).get("questions") if isinstance(payload.get("result"), dict) else []) or []
        for row in page_rows:
            if isinstance(row, dict) and row.get("id"):
                rows[str(row["id"])] = row
        next_id = str(payload.get("last_id") or ((payload.get("result") or {}).get("last_id") if isinstance(payload.get("result"), dict) else "") or "")
        has_next = bool(payload.get("has_next") if "has_next" in payload else ((payload.get("result") or {}).get("has_next") if isinstance(payload.get("result"), dict) else False))
        if not page_rows or not has_next or not next_id or next_id == last_id:
            break
        last_id = next_id
    return list(rows.values())


def handle_seo_source_products(payload):
    """Read-only bridge for SEO Bot: DB is tried first by the caller, then this
    endpoint uses the selected client's stored seller credentials."""
    client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
    marketplace = str(payload.get("marketplace") or "").strip().lower()
    raw_skus = payload.get("skus")
    skus = list(dict.fromkeys(str(value or "").strip() for value in (raw_skus if isinstance(raw_skus, list) else [raw_skus]) if str(value or "").strip()))
    if marketplace not in {"ozon", "wb"}:
        raise ValueError("marketplace должен быть ozon или wb")
    if not skus or len(skus) > 1000:
        raise ValueError("Укажите от 1 до 1000 SKU")
    items = []
    if marketplace == "ozon":
        client_id, api_key = ozon_seo_credentials_value(client)
        if not client_id or not api_key:
            raise ValueError(f"Для клиента {client} не сохранены Ozon Seller credentials")
        request = Request(
            f"{OZON_SELLER_API_BASE_URL}/v4/product/info/attributes",
            data=json.dumps({"filter": {"offer_id": skus, "visibility": "ALL"}, "last_id": "", "limit": 1000}, ensure_ascii=False).encode("utf-8"),
            headers={"Client-Id": normalize_ozon_seller_client_id(client_id), "Api-Key": normalize_ozon_seller_api_key(api_key), "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        items = data.get("items", []) if isinstance(data, dict) else []
    else:
        token = registered_client_credential(client, "wb_api_token")
        if not token:
            raise ValueError(f"Для клиента {client} не сохранён WB API token")
        wanted = set(skus)
        cursor = {"limit": 100}
        while True:
            request = Request(
                f"{WB_CONTENT_API_BASE_URL}/content/v2/get/cards/list?locale=ru",
                data=json.dumps({"settings": {"sort": {"ascending": True}, "cursor": cursor, "filter": {"withPhoto": -1}}}, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": str(token), "Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            cards = data.get("cards", []) if isinstance(data, dict) else []
            for card in cards:
                candidates = {str(card.get("nmID") or "").strip(), str(card.get("vendorCode") or "").strip()}
                for size in card.get("sizes", []) or []:
                    if isinstance(size, dict): candidates.update(str(sku or "").strip() for sku in size.get("skus", []) or [])
                if candidates & wanted: items.append(card)
            api_cursor = data.get("cursor", {}) if isinstance(data, dict) else {}
            if len(cards) < 100 or int(api_cursor.get("total") or 0) < 100 or not api_cursor.get("updatedAt") or not api_cursor.get("nmID"):
                break
            cursor = {"limit": 100, "updatedAt": api_cursor["updatedAt"], "nmID": api_cursor["nmID"]}
            time.sleep(0.7)
    return {"ok": True, "client": client, "marketplace": marketplace, "items": items}


def wb_api_token_value():
    return os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)


def wb_content_locale(payload):
    locale = str((payload or {}).get("locale") or "ru").strip().lower() or "ru"
    if not re.fullmatch(r"[a-z]{2}", locale):
        raise ValueError("locale должен быть двухбуквенным кодом, например ru")
    return locale


def extract_wb_content_rows(api_payload, *keys):
    if isinstance(api_payload, list):
        return api_payload
    if isinstance(api_payload, dict):
        for key in keys or ("data", "result", "items"):
            rows = api_payload.get(key)
            if isinstance(rows, list):
                return rows
    return []


def json_cell(value):
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def int_payload(value, field_name, default, min_value, max_value):
    try:
        parsed_value = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} должен быть числом") from exc
    return max(min_value, min(max_value, parsed_value))


def bool_payload(value, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "да", "on"}


def parse_wb_int_list(value, field_name="ID"):
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[\s,;]+", str(value or "").strip())
    ids = []
    for raw in raw_values:
        if raw in (None, ""):
            continue
        try:
            parsed = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"Некорректный {field_name}: {raw}") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} должен быть положительным: {raw}")
        if parsed not in ids:
            ids.append(parsed)
    return ids


def wb_content_subject_id(row):
    if not isinstance(row, dict):
        return None
    value = row.get("subjectID", row.get("subjectId", row.get("objectID", row.get("id"))))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_wb_content_categories_files(parent_categories, subjects, request_params):
    output_dir = Path(WB_CONTENT_CATEGORIES_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_content_categories_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /content/v2/object/parent/all + GET /content/v2/object/all",
        "created_at": requested_at,
        "request": request_params,
        "parent_categories": parent_categories,
        "subjects": subjects,
    }
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Parent Categories"
    ws.append(["id", "name", "isVisible", "raw_json"])
    for row in parent_categories:
        if isinstance(row, dict):
            ws.append([row.get("id"), row.get("name"), row.get("isVisible"), json_cell(row)])
    ws.freeze_panes = "A2"
    subjects_ws = wb.create_sheet("Subjects")
    subjects_ws.append(["subjectID", "subjectName", "parentID", "parentName", "raw_json"])
    for row in subjects:
        if isinstance(row, dict):
            subjects_ws.append([
                wb_content_subject_id(row),
                row.get("subjectName", row.get("name")),
                row.get("parentID", row.get("parentId")),
                row.get("parentName"),
                json_cell(row),
            ])
    subjects_ws.freeze_panes = "A2"
    for sheet in wb.worksheets:
        for column in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 22 if column < sheet.max_column else 60
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def save_wb_content_cards_files(cards, request_params, subject_ids):
    output_dir = Path(WB_CONTENT_CARDS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_content_cards_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "POST /content/v2/get/cards/list",
        "created_at": requested_at,
        "request": request_params,
        "subject_ids": subject_ids,
        "cards": cards,
    }
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Cards"
    headers = [
        "nmID", "imtID", "nmUUID", "subjectID", "subjectName", "vendorCode", "brand", "title", "description",
        "createdAt", "updatedAt", "skus", "sizes_count", "characteristics_count", "photos_count",
        "dimensions_width", "dimensions_height", "dimensions_length", "dimensions_weightBrutto",
        "sizes_json", "characteristics_json", "tags_json", "raw_json",
    ]
    ws.append(headers)
    for card in cards:
        if not isinstance(card, dict):
            continue
        sizes = card.get("sizes") if isinstance(card.get("sizes"), list) else []
        characteristics = card.get("characteristics") if isinstance(card.get("characteristics"), list) else []
        photos = card.get("photos") if isinstance(card.get("photos"), list) else []
        dimensions = card.get("dimensions") if isinstance(card.get("dimensions"), dict) else {}
        skus = []
        for size in sizes:
            if isinstance(size, dict):
                for sku in size.get("skus", []) if isinstance(size.get("skus"), list) else []:
                    skus.append(str(sku))
        ws.append([
            card.get("nmID"), card.get("imtID"), card.get("nmUUID"), wb_content_subject_id(card),
            card.get("subjectName"), card.get("vendorCode"), card.get("brand"), card.get("title"), card.get("description"),
            card.get("createdAt"), card.get("updatedAt"), ",".join(skus), len(sizes), len(characteristics), len(photos),
            dimensions.get("width"), dimensions.get("height"), dimensions.get("length"), dimensions.get("weightBrutto"),
            json_cell(sizes), json_cell(characteristics), json_cell(card.get("tags")), json_cell(card),
        ])
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(column)].width = 18 if column not in {8, 9, 20, 21, 22, 23} else 48
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def save_wb_content_characteristics_files(rows, errors, request_params):
    output_dir = Path(WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_content_characteristics_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /content/v2/object/charcs/{subjectId}",
        "created_at": requested_at,
        "request": request_params,
        "characteristics": rows,
        "errors": errors,
    }
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Characteristics"
    headers = ["subjectID", "charcID", "name", "required", "unitName", "maxCount", "popular", "charcType", "dictionary", "raw_json"]
    ws.append(headers)
    for row in rows:
        if not isinstance(row, dict):
            continue
        ws.append([
            row.get("subjectID"), row.get("charcID", row.get("id")), row.get("name"), row.get("required"),
            row.get("unitName"), row.get("maxCount"), row.get("popular"), row.get("charcType"),
            row.get("dictionary"), json_cell(row),
        ])
    ws.freeze_panes = "A2"
    if errors:
        err_ws = wb.create_sheet("Errors")
        err_ws.append(["subjectID", "error"])
        for error in errors:
            err_ws.append([error.get("subjectID"), error.get("error")])
    for sheet in wb.worksheets:
        for column in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 22 if column < sheet.max_column else 60
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def handle_wb_content_categories_export(payload):
    method_key = "content_categories"
    payload = payload if isinstance(payload, dict) else {}
    token = wb_api_token_value()
    locale = wb_content_locale(payload)
    limit = int_payload(payload.get("limit"), "limit", 1000, 1, 1000)
    sleep_seconds = max(0.0, min(10.0, float(payload.get("sleep_seconds", 0.65) or 0)))
    started = time.monotonic()
    update_wb_api_progress(
        method_key,
        phase="plan",
        current=0,
        total=0,
        message=f"ПЛАН: категории WB Content API | locale={locale} | страницы по {limit} | пауза {sleep_seconds:g} сек между запросами",
    )
    parent_path = f"/content/v2/object/parent/all?{urlencode({'locale': locale})}"
    parent_categories = extract_wb_content_rows(wb_content_api_request_json(parent_path, token), "data")
    requests_count = 1
    subjects = []
    offset = 0
    while True:
        check_wb_api_stop_requested()
        path = f"/content/v2/object/all?{urlencode({'locale': locale, 'limit': limit, 'offset': offset})}"
        page_payload = wb_content_api_request_json(path, token)
        requests_count += 1
        rows = extract_wb_content_rows(page_payload, "data")
        subjects.extend(rows)
        pct_label = "неизвестно"
        update_wb_api_progress(
            method_key,
            phase="request",
            current=requests_count,
            total=0,
            message=f"ПРОГРЕСС: {requests_count} запросов | offset {offset} | предметов накоплено {len(subjects)} | ETA: {pct_label}",
        )
        if len(rows) < limit:
            break
        offset += limit
        sleep_wb_api(sleep_seconds)
    files = save_wb_content_categories_files(parent_categories, subjects, {"locale": locale, "limit": limit, "requests": requests_count})
    update_wb_api_progress(
        method_key,
        phase="done",
        current=requests_count,
        total=requests_count,
        message=f"ИТОГ: родительских категорий {len(parent_categories)}, предметов {len(subjects)}, запросов {requests_count}, прошло {format_wb_api_duration(time.monotonic() - started)}",
    )
    return {
        "ok": True,
        "method": method_key,
        "parent_count": len(parent_categories),
        "count": len(subjects),
        "requests": requests_count,
        "files": files,
        "output_dir": str(WB_CONTENT_CATEGORIES_OUTPUT_DIR),
    }


def normalize_wb_content_cards_payload(payload):
    payload = payload if isinstance(payload, dict) else {}
    return {
        "locale": wb_content_locale(payload),
        "limit": int_payload(payload.get("limit"), "limit", 100, 1, 100),
        "max_pages": int_payload(payload.get("max_pages"), "max_pages", 0, 0, 100000),
        "object_ids": parse_wb_int_list(payload.get("object_ids"), "subjectID"),
        "text_search": str(payload.get("text_search") or "").strip(),
        "with_photo": int_payload(payload.get("with_photo"), "with_photo", -1, -1, 1),
        "allowed_categories_only": bool_payload(payload.get("allowed_categories_only"), True),
        "ascending": bool_payload(payload.get("ascending"), True),
        "sleep_seconds": max(0.0, min(10.0, float(payload.get("sleep_seconds", 0.65) or 0))),
    }


def build_wb_content_cards_body(params, cursor=None):
    cursor_body = {"limit": params["limit"]}
    if cursor:
        if cursor.get("updatedAt"):
            cursor_body["updatedAt"] = cursor.get("updatedAt")
        if cursor.get("nmID"):
            cursor_body["nmID"] = cursor.get("nmID")
    filters = {
        "withPhoto": params["with_photo"],
        "allowedCategoriesOnly": params["allowed_categories_only"],
    }
    if params["object_ids"]:
        filters["objectIDs"] = params["object_ids"]
    if params["text_search"]:
        filters["textSearch"] = params["text_search"]
    return {
        "settings": {
            "sort": {"ascending": params["ascending"]},
            "filter": filters,
            "cursor": cursor_body,
        }
    }


def extract_wb_content_cards(api_payload):
    if isinstance(api_payload, dict):
        rows = api_payload.get("cards")
        if isinstance(rows, list):
            return rows
        data = api_payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("cards"), list):
            return data.get("cards")
    return []


def extract_wb_content_cursor(api_payload):
    if not isinstance(api_payload, dict):
        return {}
    cursor = api_payload.get("cursor")
    if isinstance(cursor, dict):
        return cursor
    data = api_payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("cursor"), dict):
        return data.get("cursor")
    return {}


def handle_wb_content_cards_export(payload):
    method_key = "content_cards"
    token = wb_api_token_value()
    params = normalize_wb_content_cards_payload(payload)
    started = time.monotonic()
    planned = params["max_pages"] or 0
    update_wb_api_progress(
        method_key,
        phase="plan",
        current=0,
        total=planned,
        message=f"ПЛАН: карточки WB Content API | limit {params['limit']} | max_pages {params['max_pages'] or 'до конца'} | пауза {params['sleep_seconds']:g} сек",
    )
    cards = []
    cursor = None
    requests_count = 0
    seen_cursor_signatures = set()
    while True:
        check_wb_api_stop_requested()
        body = build_wb_content_cards_body(params, cursor)
        path = f"/content/v2/get/cards/list?{urlencode({'locale': params['locale']})}"
        api_payload = wb_content_api_post_json_read_only(path, token, body)
        requests_count += 1
        page_cards = extract_wb_content_cards(api_payload)
        cards.extend(page_cards)
        response_cursor = extract_wb_content_cursor(api_payload)
        total_in_page = int(response_cursor.get("total") or len(page_cards) or 0)
        update_wb_api_progress(
            method_key,
            phase="request",
            current=requests_count,
            total=planned,
            message=f"ПРОГРЕСС: {requests_count}/{planned or '?'} страниц | batch {len(page_cards)} товаров | накоплено {len(cards)} | ETA: {wb_api_eta_text(started, requests_count, planned) if planned else 'расчет после курсора'}",
        )
        if params["max_pages"] and requests_count >= params["max_pages"]:
            break
        next_cursor = {
            "updatedAt": response_cursor.get("updatedAt"),
            "nmID": response_cursor.get("nmID"),
        }
        signature = (next_cursor.get("updatedAt"), next_cursor.get("nmID"))
        if total_in_page < params["limit"] or len(page_cards) < params["limit"] or not any(signature) or signature in seen_cursor_signatures:
            break
        seen_cursor_signatures.add(signature)
        cursor = next_cursor
        sleep_wb_api(params["sleep_seconds"])
    subject_ids = sorted({subject_id for subject_id in (wb_content_subject_id(card) for card in cards) if subject_id})
    files = save_wb_content_cards_files(cards, {**params, "requests": requests_count}, subject_ids)
    update_wb_api_progress(
        method_key,
        phase="done",
        current=requests_count,
        total=requests_count,
        message=f"ИТОГ: товаров {len(cards)}, предметов {len(subject_ids)}, запросов {requests_count}, прошло {format_wb_api_duration(time.monotonic() - started)}",
    )
    return {
        "ok": True,
        "method": method_key,
        "count": len(cards),
        "requests": requests_count,
        "subject_ids": subject_ids,
        "files": files,
        "output_dir": str(WB_CONTENT_CARDS_OUTPUT_DIR),
    }


def handle_wb_content_characteristics_export(payload):
    method_key = "content_characteristics"
    payload = payload if isinstance(payload, dict) else {}
    token = wb_api_token_value()
    locale = wb_content_locale(payload)
    subject_ids = parse_wb_int_list(payload.get("subject_ids"), "subjectID")
    if not subject_ids:
        raise ValueError("Укажите subjectID предметов для выгрузки характеристик")
    sleep_seconds = max(0.0, min(10.0, float(payload.get("sleep_seconds", 0.65) or 0)))
    started = time.monotonic()
    rows = []
    errors = []
    total = len(subject_ids)
    update_wb_api_progress(
        method_key,
        phase="plan",
        current=0,
        total=total,
        message=f"ПЛАН: характеристики WB Content API | предметов {total} | пауза {sleep_seconds:g} сек",
    )
    for index, subject_id in enumerate(subject_ids, 1):
        check_wb_api_stop_requested()
        path = f"/content/v2/object/charcs/{subject_id}?{urlencode({'locale': locale})}"
        try:
            api_payload = wb_content_api_request_json(path, token)
            charcs = extract_wb_content_rows(api_payload, "data")
            for row in charcs:
                if isinstance(row, dict):
                    rows.append({"subjectID": subject_id, **row})
        except Exception as exc:
            errors.append({"subjectID": subject_id, "error": str(exc)})
        update_wb_api_progress(
            method_key,
            phase="request",
            current=index,
            total=total,
            message=f"ПРОГРЕСС: {index}/{total} ({index / total * 100:.1f}%) | subjectID {subject_id} | характеристик накоплено {len(rows)} | ошибок {len(errors)} | ETA: {wb_api_eta_text(started, index, total)}",
        )
        if index < total:
            sleep_wb_api(sleep_seconds)
    files = save_wb_content_characteristics_files(rows, errors, {"locale": locale, "subject_ids": subject_ids, "requests": total})
    update_wb_api_progress(
        method_key,
        phase="done",
        current=total,
        total=total,
        message=f"ИТОГ: предметов {total}, характеристик {len(rows)}, ошибок {len(errors)}, прошло {format_wb_api_duration(time.monotonic() - started)}",
    )
    return {
        "ok": True,
        "method": method_key,
        "count": len(rows),
        "requests": total,
        "subject_ids": subject_ids,
        "error_count": len(errors),
        "errors": errors,
        "files": files,
        "output_dir": str(WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR),
    }


def save_wb_media_count_files(api_payload):
    output_dir = Path(WB_MEDIA_COUNT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_media_campaign_count_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /adv/v1/count",
        "created_at": requested_at,
        "request": {"requested_at": requested_at},
        "response": api_payload,
    }

    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "WB Media Count"
    ws.append(["Параметр", "Значение"])
    ws.append(["Метод", payload["method"]])
    ws.append(["Дата запроса", requested_at])
    ws.append(["Период", "Не применяется: метод возвращает текущий срез"])
    ws.append(["Всего кампаний", api_payload.get("all", 0) if isinstance(api_payload, dict) else 0])
    ws.append([])
    ws.append(["type", "status", "count"])
    adverts = api_payload.get("adverts", []) if isinstance(api_payload, dict) else []
    if isinstance(adverts, dict):
        adverts = [adverts]
    for row in adverts:
        ws.append([row.get("type"), row.get("status"), row.get("count")])
    ws.freeze_panes = "A2"
    for column in range(1, 4):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 24
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def handle_wb_media_count_export(payload):
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    api_payload = wb_api_request_json("/adv/v1/count", token)
    files = save_wb_media_count_files(api_payload)
    return {
        "ok": True,
        "method": "media_count",
        "count": api_payload.get("all", 0) if isinstance(api_payload, dict) else 0,
        "files": files,
        "output_dir": str(WB_MEDIA_COUNT_OUTPUT_DIR),
    }


def parse_optional_int(value, field_name):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректное значение {field_name}: {value}") from exc


def normalize_wb_media_adverts_payload(payload):
    payload = payload or {}
    limit = parse_optional_int(payload.get("limit"), "limit") or 1000
    limit = max(1, min(limit, 1000))
    status = parse_optional_int(payload.get("status"), "status")
    advert_type = parse_optional_int(payload.get("type"), "type")
    if status is not None and status not in range(1, 12):
        raise ValueError("Статус кампании должен быть от 1 до 11")
    if advert_type is not None and advert_type not in {1, 2}:
        raise ValueError("Тип кампании должен быть 1 или 2")
    order = str(payload.get("order") or "create").strip().lower()
    direction = str(payload.get("direction") or "desc").strip().lower()
    if order not in {"create", "id"}:
        raise ValueError("Сортировка должна быть create или id")
    if direction not in {"asc", "desc"}:
        raise ValueError("Направление сортировки должно быть asc или desc")
    return {
        "limit": limit,
        "status": status,
        "type": advert_type,
        "order": order,
        "direction": direction,
    }


def build_wb_media_adverts_path(params, offset):
    query = {
        "limit": params["limit"],
        "offset": offset,
        "order": params["order"],
        "direction": params["direction"],
    }
    if params.get("status") is not None:
        query["status"] = params["status"]
    if params.get("type") is not None:
        query["type"] = params["type"]
    return f"/adv/v1/adverts?{urlencode(query)}"


def extract_wb_media_adverts_rows(api_payload):
    if isinstance(api_payload, list):
        return api_payload
    if isinstance(api_payload, dict):
        for key in ("adverts", "campaigns", "data"):
            rows = api_payload.get(key)
            if isinstance(rows, list):
                return rows
    return []


def save_wb_media_adverts_files(rows, request_params, pages_count):
    output_dir = Path(WB_MEDIA_ADVERTS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_media_adverts_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /adv/v1/adverts",
        "created_at": requested_at,
        "request": request_params,
        "pages": pages_count,
        "response": rows,
    }

    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "WB Media Adverts"
    headers = [
        "advertId",
        "name",
        "brand",
        "type",
        "status",
        "createTime",
        "startTime",
        "endTime",
        "dailyBudget",
        "budget",
        "nms_count",
    ]
    ws.append(headers)
    for row in rows:
        if not isinstance(row, dict):
            continue
        nms = row.get("nms")
        ws.append(
            [
                row.get("advertId"),
                row.get("name"),
                row.get("brand"),
                row.get("type"),
                row.get("status"),
                row.get("createTime"),
                row.get("startTime"),
                row.get("endTime"),
                row.get("dailyBudget"),
                row.get("budget"),
                len(nms) if isinstance(nms, list) else "",
            ]
        )
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 20
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def parse_campaign_ids(value):
    raw_values = re.split(r"[\s,;]+", str(value or "").strip())
    ids = []
    for raw in raw_values:
        if not raw:
            continue
        try:
            campaign_id = int(raw)
        except ValueError as exc:
            raise ValueError(f"Некорректный id кампании: {raw}") from exc
        if campaign_id <= 0:
            raise ValueError(f"Некорректный id кампании: {raw}")
        if campaign_id not in ids:
            ids.append(campaign_id)
    if not ids:
        raise ValueError("Укажите хотя бы один id кампании")
    return ids


def parse_wb_datetime_to_date(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            return None


def iso_dates_between(date_from, date_to):
    start = date.fromisoformat(normalize_iso_date(date_from))
    end = date.fromisoformat(normalize_iso_date(date_to))
    if start > end:
        raise ValueError("Дата с не может быть позже даты по")
    dates = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current = current + timedelta(days=1)
    return dates


def latest_wb_media_adverts_json_file():
    output_dir = Path(WB_MEDIA_ADVERTS_OUTPUT_DIR)
    if not output_dir.exists():
        return None
    files = sorted(output_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_wb_media_adverts_rows_from_folder():
    json_path = latest_wb_media_adverts_json_file()
    if not json_path:
        raise FileNotFoundError(f"В папке Adverts нет JSON-файлов: {WB_MEDIA_ADVERTS_OUTPUT_DIR}")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows = payload.get("response", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"Файл Adverts не содержит список кампаний: {json_path}")
    return rows, json_path


def campaign_overlaps_period(row, period_start, period_end):
    created = parse_wb_datetime_to_date(row.get("createTime") or row.get("createdAt") or row.get("startTime"))
    ended = parse_wb_datetime_to_date(row.get("endTime") or row.get("endedAt"))
    if created and created > period_end:
        return False
    if ended and ended < period_start:
        return False
    return True


def parse_wb_media_campaign_status(row):
    value = row.get("status") if isinstance(row, dict) else None
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wb_media_stats_skip_reason(row, period_start, period_end):
    if not campaign_overlaps_period(row, period_start, period_end):
        return "outside_period"
    status = parse_wb_media_campaign_status(row)
    if status in WB_MEDIA_STATS_EXCLUDED_STATUSES:
        return f"status_{status}_{WB_MEDIA_STATS_EXCLUDED_STATUSES[status]}"
    ended = parse_wb_datetime_to_date(row.get("endTime") or row.get("endedAt"))
    if status == WB_MEDIA_STATS_FINISHED_STATUS and not ended:
        return "status_7_without_end_time"
    return ""


def select_wb_media_stats_campaigns_from_adverts(date_from, date_to):
    period_start = date.fromisoformat(normalize_iso_date(date_from))
    period_end = date.fromisoformat(normalize_iso_date(date_to))
    rows, json_path = load_wb_media_adverts_rows_from_folder()
    ids = []
    skipped = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        campaign_id = row.get("advertId") or row.get("id")
        if not campaign_id:
            continue
        try:
            campaign_id = int(campaign_id)
        except (TypeError, ValueError):
            continue
        reason = wb_media_stats_skip_reason(row, period_start, period_end)
        if reason:
            skipped.append(
                {
                    "advert_id": campaign_id,
                    "status": parse_wb_media_campaign_status(row),
                    "reason": reason,
                    "row_index": index,
                }
            )
            continue
        if campaign_id not in ids:
            ids.append(campaign_id)
    if not ids:
        raise ValueError("В последней выгрузке Adverts нет подходящих кампаний для выбранного периода и статусов")
    return {
        "ids": ids,
        "skipped": skipped,
        "source_adverts_file": str(json_path),
        "candidate_count": len(rows),
    }


def campaign_ids_from_adverts_folder_for_period(date_from, date_to):
    return select_wb_media_stats_campaigns_from_adverts(date_from, date_to)["ids"]


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def build_wb_media_stats_body(payload):
    return build_wb_media_stats_plan(payload)["request_body"]


def build_wb_media_stats_plan(payload):
    payload = payload or {}
    raw_ids = payload.get("campaign_ids") or payload.get("ids")
    if str(raw_ids or "").strip():
        ids = parse_campaign_ids(raw_ids)
        skipped = []
        source_adverts_file = ""
        candidate_count = len(ids)
    else:
        selection = select_wb_media_stats_campaigns_from_adverts(payload.get("date_from"), payload.get("date_to"))
        ids = selection["ids"]
        skipped = selection["skipped"]
        source_adverts_file = selection["source_adverts_file"]
        candidate_count = selection["candidate_count"]
    dates = iso_dates_between(payload.get("date_from"), payload.get("date_to"))
    request_body = [{"id": campaign_id, "dates": dates} for campaign_id in ids]
    return {
        "request_body": request_body,
        "campaign_count": len(ids),
        "candidate_count": candidate_count,
        "skipped_count": len(skipped),
        "skipped_campaigns": skipped,
        "source_adverts_file": source_adverts_file,
        "date_count": len(dates),
    }


def wb_media_stats_report_date(value):
    parsed = parse_wb_datetime_to_date(value)
    return parsed.isoformat() if parsed else ""


def wb_media_stats_expenses_sum(api_payload):
    total = 0.0
    for campaign in api_payload if isinstance(api_payload, list) else []:
        if not isinstance(campaign, dict) or campaign.get("error"):
            continue
        stats = campaign.get("stats") if isinstance(campaign.get("stats"), list) else []
        if not stats:
            total += to_float(campaign.get("expenses"))
            continue
        for row in stats:
            if not isinstance(row, dict):
                continue
            if row.get("expenses") not in (None, ""):
                total += to_float(row.get("expenses"))
                continue
            daily_rows = row.get("daily_stats") if isinstance(row.get("daily_stats"), list) else []
            total += sum(to_float(daily_row.get("expenses")) for daily_row in daily_rows if isinstance(daily_row, dict))
    return round(total, 2)


def save_wb_media_stats_files(api_payload, request_body):
    output_dir = Path(WB_MEDIA_STATS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_media_stats_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "POST /adv/v1/stats",
        "created_at": requested_at,
        "request": request_body,
        "response": api_payload,
    }

    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "WB Media Stats"
    errors_ws = wb.create_sheet("Errors")
    headers = [
        "campaign_request_id",
        "advert_id",
        "error",
        "campaign_index",
        "stat_index",
        "daily_index",
        "app_type",
        "app_stat_index",
        "report_date",
        "item_id",
        "item_name",
        "category_name",
        "advert_type",
        "place",
        "views",
        "clicks",
        "ctr",
        "cpc",
        "sum",
        "atbs",
        "orders",
        "cr",
        "shks",
        "sum_price",
        "price",
        "status",
        "expenses",
        "expense_rub",
        "cr1",
        "cr2",
        "date_from",
        "date_to",
    ]
    ws.append(headers)
    error_headers = ["campaign_request_id", "advert_id", "error", "campaign_index"]
    errors_ws.append(error_headers)
    rows = api_payload if isinstance(api_payload, list) else []
    for campaign_index, campaign in enumerate(rows):
        stats = campaign.get("stats", []) if isinstance(campaign, dict) else []
        request_id = request_body[campaign_index]["id"] if campaign_index < len(request_body) else ""
        if isinstance(campaign, dict) and campaign.get("error"):
            errors_ws.append([
                request_id,
                campaign.get("advert_id") or campaign.get("id"),
                campaign.get("error"),
                campaign_index,
            ])
            continue
        if not isinstance(stats, list):
            continue
        for stat_index, row in enumerate(stats):
            if not isinstance(row, dict):
                continue
            advert_id = campaign.get("advert_id") or campaign.get("id") if isinstance(campaign, dict) else ""
            daily_rows = row.get("daily_stats") if isinstance(row.get("daily_stats"), list) else []
            if not daily_rows:
                ws.append(
                    [
                        request_id,
                        advert_id,
                        "",
                        campaign_index,
                        stat_index,
                        "",
                        "",
                        "",
                        wb_media_stats_report_date(row.get("date_from")),
                        row.get("item_id"),
                        row.get("item_name") or row.get("name"),
                        row.get("category_name"),
                        row.get("advert_type"),
                        row.get("place"),
                        row.get("views"),
                        row.get("clicks"),
                        row.get("ctr"),
                        row.get("cpc"),
                        row.get("sum"),
                        row.get("atbs"),
                        row.get("orders"),
                        row.get("cr"),
                        row.get("shks"),
                        row.get("sum_price"),
                        row.get("price"),
                        row.get("status"),
                        row.get("expenses"),
                        row.get("expenses"),
                        row.get("cr1"),
                        row.get("cr2"),
                        row.get("date_from"),
                        row.get("date_to"),
                    ]
                )
                continue
            for daily_index, daily_row in enumerate(daily_rows):
                if not isinstance(daily_row, dict):
                    continue
                report_date = wb_media_stats_report_date(daily_row.get("date"))
                app_type_rows = daily_row.get("app_type_stats")
                if not isinstance(app_type_rows, list) or not app_type_rows:
                    app_type_rows = [{"stats": [daily_row]}]
                for app_type_row in app_type_rows:
                    if not isinstance(app_type_row, dict):
                        continue
                    app_type = app_type_row.get("app_type", app_type_row.get("appType", ""))
                    metric_rows = app_type_row.get("stats")
                    if not isinstance(metric_rows, list) or not metric_rows:
                        metric_rows = [app_type_row]
                    for app_stat_index, metric_row in enumerate(metric_rows):
                        if not isinstance(metric_row, dict):
                            continue
                        ws.append(
                            [
                                request_id,
                                advert_id,
                                "",
                                campaign_index,
                                stat_index,
                                daily_index,
                                app_type,
                                app_stat_index,
                                report_date,
                                row.get("item_id"),
                                row.get("item_name") or row.get("name"),
                                row.get("category_name"),
                                row.get("advert_type"),
                                row.get("place"),
                                metric_row.get("views"),
                                metric_row.get("clicks"),
                                metric_row.get("ctr"),
                                metric_row.get("cpc"),
                                metric_row.get("sum"),
                                metric_row.get("atbs"),
                                metric_row.get("orders"),
                                metric_row.get("cr"),
                                metric_row.get("shks"),
                                metric_row.get("sum_price"),
                                row.get("price"),
                                row.get("status"),
                                row.get("expenses"),
                                row.get("expenses"),
                                row.get("cr1"),
                                row.get("cr2"),
                                row.get("date_from"),
                                row.get("date_to"),
                            ]
                        )
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 18
    errors_ws.freeze_panes = "A2"
    for column in range(1, len(error_headers) + 1):
        errors_ws.column_dimensions[errors_ws.cell(row=1, column=column).column_letter].width = 22
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def handle_wb_media_stats_export(payload):
    method_key = "media_stats"
    raise_if_wb_api_cooldown(method_key, "POST /adv/v1/stats")
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    plan = build_wb_media_stats_plan(payload or {})
    request_body = plan["request_body"]
    chunks = list(chunked(request_body, WB_MEDIA_STATS_CHUNK_SIZE))
    api_payload = []
    stopped = False
    requests_sent = 0
    try:
        for index, chunk in enumerate(chunks):
            check_wb_api_stop_requested()
            chunk_payload = wb_api_post_json_read_only("/adv/v1/stats", token, chunk)
            requests_sent += 1
            if isinstance(chunk_payload, list):
                api_payload.extend(chunk_payload)
            else:
                api_payload.append(chunk_payload)
            if index < len(chunks) - 1:
                sleep_wb_api(WB_MEDIA_STATS_CHUNK_PAUSE_SECONDS)
    except WbApiStopRequested:
        stopped = True
    except WbApiError as exc:
        if exc.status_code == 429:
            remember_wb_api_cooldown(method_key, exc.retry_after or 3600)
            raise RuntimeError(wb_api_error_message(exc.status_code, exc.detail, exc.retry_after)) from exc
        raise RuntimeError(str(exc)) from exc
    files = save_wb_media_stats_files(api_payload, request_body)
    error_count = sum(1 for item in api_payload if isinstance(item, dict) and item.get("error"))
    expenses_sum = wb_media_stats_expenses_sum(api_payload)
    return {
        "ok": True,
        "method": "media_stats",
        "count": len(api_payload) if isinstance(api_payload, list) else 0,
        "error_count": error_count,
        "requests": requests_sent if stopped else len(chunks),
        "campaigns_requested": plan["campaign_count"],
        "candidate_count": plan["candidate_count"],
        "skipped_count": plan["skipped_count"],
        "skipped_campaigns": plan["skipped_campaigns"],
        "expenses_sum": expenses_sum,
        "date_count": plan["date_count"],
        "source_adverts_file": plan["source_adverts_file"],
        "files": files,
        "output_dir": str(WB_MEDIA_STATS_OUTPUT_DIR),
        "stopped": stopped,
    }


def handle_wb_media_adverts_export(payload):
    method_key = "media_adverts"
    raise_if_wb_api_cooldown(method_key, "GET /adv/v1/adverts")
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    params = normalize_wb_media_adverts_payload(payload)
    offset = 0
    pages_count = 0
    rows = []
    stopped = False
    try:
        while True:
            check_wb_api_stop_requested()
            path = build_wb_media_adverts_path(params, offset)
            try:
                api_payload = wb_api_request_json(path, token)
            except WbApiError as exc:
                message = str(exc)
                if exc.status_code == 429 and "Base" not in message:
                    message = wb_api_error_message(exc.status_code, exc.detail, exc.retry_after)
                if exc.status_code == 429:
                    until = remember_wb_api_cooldown(method_key, exc.retry_after or 3600)
                    message = (
                        f"{message} Следующая попытка будет доступна после "
                        f"{until.strftime('%d.%m.%Y %H:%M:%S')}."
                    )
                raise RuntimeError(message) from exc
            page_rows = extract_wb_media_adverts_rows(api_payload)
            pages_count += 1
            rows.extend(page_rows)
            if len(page_rows) < params["limit"]:
                break
            offset += params["limit"]
            sleep_wb_api(0.25)
    except WbApiStopRequested:
        stopped = True
    files = save_wb_media_adverts_files(rows, params, pages_count)
    return {
        "ok": True,
        "method": "media_adverts",
        "count": len(rows),
        "pages": pages_count,
        "files": files,
        "output_dir": str(WB_MEDIA_ADVERTS_OUTPUT_DIR),
        "stopped": stopped,
    }


def normalize_wb_promotion_statuses(value):
    raw_value = str(value if value is not None else "9,11,7").strip()
    if not raw_value:
        raw_value = "9,11,7"
    statuses = []
    for raw in re.split(r"[\s,;]+", raw_value):
        if not raw:
            continue
        try:
            status = int(raw)
        except ValueError as exc:
            raise ValueError(f"Некорректный статус кампании WB Promotion: {raw}") from exc
        if status not in {-1, 4, 7, 8, 9, 11}:
            raise ValueError("Статусы WB Promotion: -1, 4, 7, 8, 9, 11")
        if status not in statuses:
            statuses.append(status)
    if not statuses:
        raise ValueError("Укажите хотя бы один статус WB Promotion")
    return statuses


def wb_promotion_campaign_id(row):
    if not isinstance(row, dict):
        return None
    value = row.get("id", row.get("advertId"))
    try:
        campaign_id = int(value)
    except (TypeError, ValueError):
        return None
    return campaign_id if campaign_id > 0 else None


def extract_wb_promotion_count_ids(api_payload, statuses):
    status_set = {int(status) for status in statuses}
    rows = api_payload.get("adverts", []) if isinstance(api_payload, dict) else []
    ids = []
    for group in rows if isinstance(rows, list) else []:
        if not isinstance(group, dict):
            continue
        try:
            status = int(group.get("status"))
        except (TypeError, ValueError):
            continue
        if status not in status_set:
            continue
        advert_list = group.get("advert_list", [])
        for item in advert_list if isinstance(advert_list, list) else []:
            campaign_id = wb_promotion_campaign_id(item)
            if campaign_id and campaign_id not in ids:
                ids.append(campaign_id)
    return ids


def extract_wb_promotion_adverts_rows(api_payload):
    if isinstance(api_payload, list):
        return api_payload
    if isinstance(api_payload, dict):
        rows = api_payload.get("adverts", api_payload.get("campaigns", api_payload.get("data", [])))
        return rows if isinstance(rows, list) else []
    return []


def save_wb_promotion_count_files(api_payload):
    output_dir = Path(WB_PROMOTION_COUNT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_promotion_count_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /adv/v1/promotion/count",
        "created_at": requested_at,
        "request": {"requested_at": requested_at},
        "response": api_payload,
    }
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "WB Promotion Count"
    ws.append(["Параметр", "Значение"])
    ws.append(["Метод", payload["method"]])
    ws.append(["Дата запроса", requested_at])
    ws.append(["Всего кампаний", api_payload.get("all", 0) if isinstance(api_payload, dict) else 0])
    ws.append([])
    ws.append(["type", "status", "count", "advert_ids"])
    adverts = api_payload.get("adverts", []) if isinstance(api_payload, dict) else []
    for row in adverts if isinstance(adverts, list) else []:
        advert_list = row.get("advert_list", []) if isinstance(row, dict) else []
        ids = [str(wb_promotion_campaign_id(item)) for item in advert_list if wb_promotion_campaign_id(item)]
        ws.append([row.get("type"), row.get("status"), row.get("count"), ",".join(ids)])
    ws.freeze_panes = "A2"
    for column in range(1, 5):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 24
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def save_wb_promotion_adverts_files(rows, request_params, count_payload):
    output_dir = Path(WB_PROMOTION_ADVERTS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"wb_promotion_adverts_{timestamp}"
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /api/advert/v2/adverts",
        "created_at": requested_at,
        "request": request_params,
        "source_count_response": count_payload,
        "response": rows,
    }
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "WB Promotion Adverts"
    headers = [
        "id",
        "status",
        "bid_type",
        "payment_type",
        "name",
        "created",
        "started",
        "updated",
        "deleted",
        "nm_count",
    ]
    ws.append(headers)
    for row in rows:
        if not isinstance(row, dict):
            continue
        settings = row.get("settings") if isinstance(row.get("settings"), dict) else {}
        timestamps = row.get("timestamps") if isinstance(row.get("timestamps"), dict) else {}
        nm_settings = row.get("nm_settings") if isinstance(row.get("nm_settings"), list) else []
        ws.append([
            row.get("id", row.get("advertId")),
            row.get("status"),
            row.get("bid_type"),
            settings.get("payment_type"),
            settings.get("name"),
            timestamps.get("created"),
            timestamps.get("started"),
            timestamps.get("updated"),
            timestamps.get("deleted"),
            len(nm_settings),
        ])
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 20
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def handle_wb_promotion_count_export(payload):
    method_key = "promotion_count"
    raise_if_wb_api_cooldown(method_key, "GET /adv/v1/promotion/count")
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    try:
        api_payload = wb_promotion_api_request_json("/adv/v1/promotion/count", token)
    except WbApiError as exc:
        if exc.status_code == 429:
            remember_wb_api_cooldown(method_key, exc.retry_after or 3600)
        raise RuntimeError(str(exc)) from exc
    files = save_wb_promotion_count_files(api_payload)
    return {
        "ok": True,
        "method": "promotion_count",
        "count": api_payload.get("all", 0) if isinstance(api_payload, dict) else 0,
        "files": files,
        "output_dir": str(WB_PROMOTION_COUNT_OUTPUT_DIR),
    }


def handle_wb_promotion_adverts_export(payload):
    method_key = "promotion_adverts"
    raise_if_wb_api_cooldown(method_key, "GET /api/advert/v2/adverts")
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    statuses = normalize_wb_promotion_statuses((payload or {}).get("statuses"))
    count_payload = {}
    count_files = {}
    campaign_ids = []
    rows = []
    request_paths = []
    stopped = False
    try:
        count_payload = wb_promotion_api_request_json("/adv/v1/promotion/count", token)
        count_files = save_wb_promotion_count_files(count_payload)
        campaign_ids = extract_wb_promotion_count_ids(count_payload, statuses)
        for index, ids_chunk in enumerate(chunked(campaign_ids, WB_PROMOTION_ADVERTS_CHUNK_SIZE)):
            sleep_wb_api(0.25)
            path = f"/api/advert/v2/adverts?ids={','.join(str(value) for value in ids_chunk)}"
            request_paths.append(path)
            api_payload = wb_promotion_api_request_json(path, token)
            rows.extend(extract_wb_promotion_adverts_rows(api_payload))
    except WbApiStopRequested:
        stopped = True
    except WbApiError as exc:
        if exc.status_code == 429:
            remember_wb_api_cooldown(method_key, exc.retry_after or 3600)
        raise RuntimeError(str(exc)) from exc
    adverts_files = save_wb_promotion_adverts_files(
        rows,
        {"statuses": statuses, "campaign_ids": campaign_ids, "request_paths": request_paths},
        count_payload,
    )
    return {
        "ok": True,
        "method": "promotion_adverts",
        "count": len(rows),
        "campaign_ids_count": len(campaign_ids),
        "requests": len(request_paths) + 1,
        "statuses": statuses,
        "files": {
            **adverts_files,
            "count_json": count_files.get("json", ""),
            "count_xlsx": count_files.get("xlsx", ""),
        },
        "output_dir": str(WB_PROMOTION_ADVERTS_OUTPUT_DIR),
        "stopped": stopped,
    }


def latest_wb_promotion_adverts_json_file():
    output_dir = Path(WB_PROMOTION_ADVERTS_OUTPUT_DIR)
    if not output_dir.exists():
        return None
    files = sorted(output_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_wb_promotion_adverts_rows_from_folder():
    json_path = latest_wb_promotion_adverts_json_file()
    if not json_path:
        raise FileNotFoundError(f"В папке Promotion\\Adverts нет JSON-файлов: {WB_PROMOTION_ADVERTS_OUTPUT_DIR}")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows = payload.get("response", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"Файл Promotion Adverts не содержит список кампаний: {json_path}")
    return rows, json_path


def parse_wb_promotion_campaign_status(row):
    value = row.get("status") if isinstance(row, dict) else None
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wb_promotion_timestamp(row, name, *fallback_keys):
    timestamps = row.get("timestamps") if isinstance(row.get("timestamps"), dict) else {}
    value = timestamps.get(name)
    if not value:
        for key in fallback_keys:
            value = row.get(key)
            if value:
                break
    parsed = parse_wb_datetime_to_date(value)
    if name == "deleted" and parsed == date(2100, 1, 1):
        return None
    return parsed


def wb_promotion_stats_auto_mode(value):
    mode = str(value or WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD).strip()
    if mode in {WB_PROMOTION_FULLSTATS_AUTO_MODE_ALL_STATUS, WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD}:
        return mode
    return WB_PROMOTION_FULLSTATS_AUTO_MODE_STRICT_STARTED


def wb_promotion_fullstats_window_mode(value):
    mode = str(value or WB_PROMOTION_FULLSTATS_WINDOW_PERIOD).strip()
    if mode == WB_PROMOTION_FULLSTATS_WINDOW_CAMPAIGN_LIFETIME:
        return mode
    if mode == WB_PROMOTION_FULLSTATS_WINDOW_DAY:
        return mode
    return WB_PROMOTION_FULLSTATS_WINDOW_PERIOD


def wb_promotion_fullstats_result_mode(value):
    mode = str(value or WB_PROMOTION_FULLSTATS_RESULT_DETAILS).strip()
    if mode == WB_PROMOTION_FULLSTATS_RESULT_CAMPAIGNS:
        return mode
    return WB_PROMOTION_FULLSTATS_RESULT_DETAILS


def wb_promotion_fullstats_campaign_batch_size(value):
    try:
        batch_size = int(value)
    except (TypeError, ValueError):
        return 1
    return batch_size if batch_size in WB_PROMOTION_FULLSTATS_CAMPAIGN_BATCH_SIZES else 1


def wb_promotion_stats_skip_reason(row, period_start, period_end, auto_mode=WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD):
    status = parse_wb_promotion_campaign_status(row)
    if status not in WB_PROMOTION_STATS_STATUSES:
        return "status_not_for_fullstats"
    normalized_mode = wb_promotion_stats_auto_mode(auto_mode)
    if normalized_mode == WB_PROMOTION_FULLSTATS_AUTO_MODE_ALL_STATUS:
        return ""
    started = wb_promotion_timestamp(row, "started", "startTime")
    created = wb_promotion_timestamp(row, "created", "createdAt", "createTime")
    deleted = wb_promotion_timestamp(row, "deleted", "endTime", "endedAt")
    if normalized_mode == WB_PROMOTION_FULLSTATS_AUTO_MODE_STRICT_STARTED:
        if not started:
            return "missing_started_at"
        active_from = started
    else:
        active_from = started or created
        if not active_from:
            return "missing_start_date"
    if active_from and active_from > period_end:
        return "starts_after_period"
    if deleted and deleted < period_start:
        return "ended_before_period"
    return ""


def select_wb_promotion_stats_campaigns_from_rows(rows, json_path, date_from, date_to, auto_mode=WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD):
    period_start = date.fromisoformat(normalize_iso_date(date_from))
    period_end = date.fromisoformat(normalize_iso_date(date_to))
    ids = []
    skipped = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        campaign_id = wb_promotion_campaign_id(row)
        if not campaign_id:
            continue
        reason = wb_promotion_stats_skip_reason(row, period_start, period_end, auto_mode)
        if reason:
            skipped.append(
                {
                    "advert_id": campaign_id,
                    "status": parse_wb_promotion_campaign_status(row),
                    "reason": reason,
                    "row_index": index,
                }
            )
            continue
        if campaign_id not in ids:
            ids.append(campaign_id)
    if not ids:
        raise ValueError("В последней выгрузке Promotion Adverts нет кампаний, пересекающихся с выбранным периодом")
    return {"ids": ids, "skipped": skipped, "source_adverts_file": str(json_path), "candidate_count": len(rows)}


def select_wb_promotion_stats_campaigns_from_adverts(date_from, date_to, auto_mode=WB_PROMOTION_FULLSTATS_AUTO_MODE_ACTIVE_PERIOD):
    rows, json_path = load_wb_promotion_adverts_rows_from_folder()
    return select_wb_promotion_stats_campaigns_from_rows(rows, json_path, date_from, date_to, auto_mode)


def summarize_wb_promotion_skips(skipped):
    summary = {}
    for row in skipped:
        reason = row.get("reason") or "unknown"
        summary[reason] = summary.get(reason, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: item[0]))


def wb_promotion_raw_timestamp(row, name, *fallback_keys):
    timestamps = row.get("timestamps") if isinstance(row.get("timestamps"), dict) else {}
    value = timestamps.get(name)
    if not value:
        for key in fallback_keys:
            value = row.get(key)
            if value:
                break
    return parse_wb_datetime_to_date(value)


def wb_promotion_campaign_lifetime_end(row, today=None):
    today = today or marketplace_today()
    yesterday = today - timedelta(days=1)
    deleted = wb_promotion_raw_timestamp(row, "deleted", "endTime", "endedAt")
    if not deleted or deleted == date(2100, 1, 1):
        return yesterday
    return min(deleted, yesterday)


def wb_promotion_campaign_lifetime_output_base(campaign_id):
    return str(campaign_id)


def wb_promotion_campaign_lifetime_existing_files(campaign_id):
    output_dir = Path(WB_PROMOTION_STATS_OUTPUT_DIR)
    base_name = wb_promotion_campaign_lifetime_output_base(campaign_id)
    xlsx_path = output_dir / f"{base_name}.xlsx"
    json_path = output_dir / f"{base_name}.json"
    if xlsx_path.exists():
        return {"xlsx": str(xlsx_path), "json": str(json_path) if json_path.exists() else ""}
    return None


def select_wb_promotion_lifetime_campaigns_from_rows(rows, json_path, campaign_ids=None, today=None):
    today = today or marketplace_today()
    requested_ids = parse_campaign_ids(campaign_ids) if str(campaign_ids or "").strip() else []
    requested_id_set = set(requested_ids)
    campaigns = []
    skipped = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        campaign_id = wb_promotion_campaign_id(row)
        if not campaign_id or campaign_id in seen:
            continue
        if requested_id_set and campaign_id not in requested_id_set:
            continue
        seen.add(campaign_id)
        status = parse_wb_promotion_campaign_status(row)
        if status not in WB_PROMOTION_STATS_STATUSES:
            skipped.append({"advert_id": campaign_id, "status": status, "reason": "status_not_for_fullstats", "row_index": index})
            continue
        created = wb_promotion_raw_timestamp(row, "created", "createdAt", "createTime")
        if not created:
            skipped.append({"advert_id": campaign_id, "status": status, "reason": "missing_created_at", "row_index": index})
            continue
        end_date = wb_promotion_campaign_lifetime_end(row, today=today)
        if end_date < created:
            skipped.append({"advert_id": campaign_id, "status": status, "reason": "created_after_lifetime_end", "row_index": index})
            continue
        existing_files = wb_promotion_campaign_lifetime_existing_files(campaign_id)
        campaigns.append({
            "id": campaign_id,
            "status": status,
            "created": created.isoformat(),
            "deleted": end_date.isoformat(),
            "source_row_index": index,
            "existing_files": existing_files or {},
            "skip_existing": bool(existing_files),
        })
    campaigns.sort(key=lambda item: (item["created"], item["id"]), reverse=True)
    if requested_ids:
        order = {campaign_id: index for index, campaign_id in enumerate(requested_ids)}
        campaigns.sort(key=lambda item: order.get(item["id"], len(order)))
    return {
        "campaigns": campaigns,
        "skipped": skipped,
        "source_adverts_file": str(json_path),
        "candidate_count": len(rows) if not requested_ids else len(requested_ids),
    }


def date_ranges_between(date_from, date_to, max_days):
    start = date.fromisoformat(normalize_iso_date(date_from))
    end = date.fromisoformat(normalize_iso_date(date_to))
    if start > end:
        raise ValueError("Дата с не может быть позже даты по")
    ranges = []
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=max_days - 1))
        ranges.append({"beginDate": current.isoformat(), "endDate": chunk_end.isoformat()})
        current = chunk_end + timedelta(days=1)
    return ranges


def batch_wb_promotion_lifetime_requests(requests, batch_size):
    batch_size = wb_promotion_fullstats_campaign_batch_size(batch_size)
    if batch_size <= 1:
        return requests
    batched = []
    current_batch = None
    current_key = None

    def flush_batch():
        nonlocal current_batch, current_key
        if current_batch:
            batched.append(current_batch)
        current_batch = None
        current_key = None

    for request_params in requests:
        ids = list(request_params.get("ids") or [])
        if len(ids) != 1:
            flush_batch()
            batched.append(request_params)
            continue
        key = (request_params.get("beginDate"), request_params.get("endDate"))
        if current_batch and key == current_key and len(current_batch["ids"]) < batch_size:
            current_batch["ids"].extend(ids)
            current_batch["campaign_ids"].extend(ids)
            continue
        flush_batch()
        current_key = key
        current_batch = {
            **request_params,
            "ids": ids,
            "campaign_ids": list(ids),
            "campaign_id": ids[0],
        }
    flush_batch()
    return batched


def build_wb_promotion_fullstats_plan(payload):
    payload = payload or {}
    raw_ids = payload.get("campaign_ids") or payload.get("ids")
    auto_mode = wb_promotion_stats_auto_mode(payload.get("auto_mode") or payload.get("campaign_auto_mode"))
    window_mode = wb_promotion_fullstats_window_mode(payload.get("window_mode") or payload.get("date_window_mode"))
    if window_mode == WB_PROMOTION_FULLSTATS_WINDOW_CAMPAIGN_LIFETIME:
        campaign_batch_size = wb_promotion_fullstats_campaign_batch_size(
            payload.get("campaign_batch_size") or payload.get("batch_size")
        )
        rows, json_path = load_wb_promotion_adverts_rows_from_folder()
        today = date.fromisoformat(normalize_iso_date(payload.get("today"))) if payload.get("today") else None
        selection = select_wb_promotion_lifetime_campaigns_from_rows(rows, json_path, raw_ids, today=today)
        campaigns = selection["campaigns"]
        requests = []
        skipped_existing = []
        for campaign in campaigns:
            if campaign.get("skip_existing"):
                skipped_existing.append(campaign)
                continue
            for period in date_ranges_between(campaign["created"], campaign["deleted"], WB_PROMOTION_FULLSTATS_MAX_DAYS):
                requests.append({"ids": [campaign["id"]], **period, "campaign_id": campaign["id"]})
        campaign_windows_count = len(requests)
        requests = batch_wb_promotion_lifetime_requests(requests, campaign_batch_size)
        return {
            "requests": requests,
            "campaigns": campaigns,
            "campaign_count": len(campaigns),
            "campaigns_to_export": len([campaign for campaign in campaigns if not campaign.get("skip_existing")]),
            "skipped_existing_count": len(skipped_existing),
            "skipped_existing_campaigns": skipped_existing,
            "candidate_count": selection["candidate_count"],
            "skipped_count": len(selection["skipped"]),
            "skipped_campaigns": selection["skipped"],
            "skipped_reasons": summarize_wb_promotion_skips(selection["skipped"]),
            "source_adverts_file": selection["source_adverts_file"],
            "periods_count": len(requests),
            "campaign_windows_count": campaign_windows_count,
            "campaign_batch_size": campaign_batch_size,
            "auto_mode": auto_mode,
            "window_mode": window_mode,
        }
    max_days = 1 if window_mode == WB_PROMOTION_FULLSTATS_WINDOW_DAY else WB_PROMOTION_FULLSTATS_MAX_DAYS
    periods = date_ranges_between(payload.get("date_from"), payload.get("date_to"), max_days)
    if str(raw_ids or "").strip():
        ids = parse_campaign_ids(raw_ids)
        skipped = []
        source_adverts_file = ""
        candidate_count = len(ids)
    else:
        rows, json_path = load_wb_promotion_adverts_rows_from_folder()
        selection = select_wb_promotion_stats_campaigns_from_rows(rows, json_path, payload.get("date_from"), payload.get("date_to"), auto_mode)
        ids = selection["ids"]
        skipped = selection["skipped"]
        source_adverts_file = selection["source_adverts_file"]
        candidate_count = selection["candidate_count"]
    requests = []
    if window_mode != WB_PROMOTION_FULLSTATS_WINDOW_DAY:
        for ids_chunk in chunked(ids, WB_PROMOTION_FULLSTATS_CHUNK_SIZE):
            for period in periods:
                requests.append({"ids": ids_chunk, **period})
    else:
        for period in periods:
            period_ids = ids
            if not str(raw_ids or "").strip():
                period_selection = select_wb_promotion_stats_campaigns_from_rows(
                    rows,
                    json_path,
                    period["beginDate"],
                    period["endDate"],
                    auto_mode,
                )
                period_ids = period_selection["ids"]
            for ids_chunk in chunked(period_ids, WB_PROMOTION_FULLSTATS_CHUNK_SIZE):
                requests.append({"ids": ids_chunk, **period})
    return {
        "requests": requests,
        "campaign_count": len(ids),
        "candidate_count": candidate_count,
        "skipped_count": len(skipped),
        "skipped_campaigns": skipped,
        "skipped_reasons": summarize_wb_promotion_skips(skipped),
        "source_adverts_file": source_adverts_file,
        "periods_count": len(periods),
        "auto_mode": auto_mode,
        "window_mode": window_mode,
    }


def build_wb_promotion_fullstats_path(request_params):
    ids = ",".join(str(value) for value in request_params["ids"])
    begin_date = normalize_iso_date(request_params["beginDate"])
    end_date = normalize_iso_date(request_params["endDate"])
    return f"/adv/v3/fullstats?ids={ids}&beginDate={begin_date}&endDate={end_date}"


def wb_promotion_report_date(value):
    parsed = parse_wb_datetime_to_date(value)
    return parsed.isoformat() if parsed else ""


def wb_promotion_metric_present(row, key):
    if not isinstance(row, dict):
        return False
    value = row.get(key)
    return value not in (None, "")


def wb_promotion_fullstats_expense_sum(api_payload):
    total = 0.0
    for campaign in api_payload if isinstance(api_payload, list) else []:
        if not isinstance(campaign, dict):
            continue
        if wb_promotion_metric_present(campaign, "sum"):
            total += to_float(campaign.get("sum"))
            continue
        campaign_total = 0.0
        for day_row in campaign.get("days") if isinstance(campaign.get("days"), list) else []:
            if not isinstance(day_row, dict):
                continue
            if wb_promotion_metric_present(day_row, "sum"):
                campaign_total += to_float(day_row.get("sum"))
                continue
            apps = day_row.get("apps") if isinstance(day_row.get("apps"), list) else []
            if not apps:
                continue
            for app_row in apps:
                if not isinstance(app_row, dict):
                    continue
                if wb_promotion_metric_present(app_row, "sum"):
                    campaign_total += to_float(app_row.get("sum"))
                    continue
                nms = app_row.get("nms") if isinstance(app_row.get("nms"), list) else []
                campaign_total += sum(to_float(nm_row.get("sum")) for nm_row in nms if isinstance(nm_row, dict))
        total += campaign_total
    return round(total, 2)


WB_PROMOTION_CAMPAIGN_TOTAL_FIELDS = [
    "views",
    "clicks",
    "ctr",
    "cpc",
    "sum",
    "atbs",
    "orders",
    "cr",
    "shks",
    "sum_price",
    "canceled",
]


def wb_promotion_nested_metric_sum(campaign, key):
    total = 0.0
    seen = False
    for day_row in campaign.get("days") if isinstance(campaign.get("days"), list) else []:
        if not isinstance(day_row, dict):
            continue
        if wb_promotion_metric_present(day_row, key):
            total += to_float(day_row.get(key))
            seen = True
    return round(total, 2) if seen else None


def wb_promotion_campaign_total_row(campaign):
    if not isinstance(campaign, dict):
        return {}
    row = {
        "advertId": campaign.get("advertId", campaign.get("id")),
    }
    for key in WB_PROMOTION_CAMPAIGN_TOTAL_FIELDS:
        if wb_promotion_metric_present(campaign, key):
            row[key] = campaign.get(key)
        else:
            total = wb_promotion_nested_metric_sum(campaign, key)
            if total is not None:
                row[key] = total
    row["expense_rub"] = row.get("sum")
    return row


def wb_promotion_campaign_totals_payload(api_payload):
    return [
        wb_promotion_campaign_total_row(campaign)
        for campaign in api_payload if isinstance(api_payload, list) and isinstance(campaign, dict)
    ]


def save_wb_promotion_fullstats_files(api_payload, request_params, request_errors=None, result_mode=WB_PROMOTION_FULLSTATS_RESULT_DETAILS, base_name=None):
    output_dir = Path(WB_PROMOTION_STATS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = str(base_name or f"wb_promotion_fullstats_{timestamp}")
    requested_at = datetime.now().isoformat(timespec="seconds")
    result_mode = wb_promotion_fullstats_result_mode(result_mode)
    response_payload = wb_promotion_campaign_totals_payload(api_payload) if result_mode == WB_PROMOTION_FULLSTATS_RESULT_CAMPAIGNS else api_payload
    payload = {
        "method": "GET /adv/v3/fullstats",
        "result_mode": result_mode,
        "created_at": requested_at,
        "request": request_params,
        "response": response_payload,
    }
    if request_errors:
        payload["errors"] = request_errors
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    if result_mode == WB_PROMOTION_FULLSTATS_RESULT_CAMPAIGNS:
        ws.title = "Campaign Totals"
        headers = ["advertId", *WB_PROMOTION_CAMPAIGN_TOTAL_FIELDS, "expense_rub"]
        ws.append(headers)
        for campaign in response_payload:
            ws.append([campaign.get(header, "") for header in headers])
    else:
        ws.title = "WB Promotion Fullstats"
        headers = [
            "advertId",
            "report_date",
            "appType",
            "nmId",
            "nm_name",
            "views",
            "clicks",
            "ctr",
            "cpc",
            "sum",
            "expense_rub",
            "atbs",
            "orders",
            "cr",
            "shks",
            "sum_price",
            "canceled",
        ]
        ws.append(headers)
        for campaign in api_payload if isinstance(api_payload, list) else []:
            if not isinstance(campaign, dict):
                continue
            advert_id = campaign.get("advertId", campaign.get("id"))
            days = campaign.get("days") if isinstance(campaign.get("days"), list) else []
            if not days:
                expense = campaign.get("sum")
                ws.append([advert_id, "", "", "", "", campaign.get("views"), campaign.get("clicks"), campaign.get("ctr"), campaign.get("cpc"), campaign.get("sum"), expense, campaign.get("atbs"), campaign.get("orders"), campaign.get("cr"), campaign.get("shks"), campaign.get("sum_price"), campaign.get("canceled")])
                continue
            for day_row in days:
                if not isinstance(day_row, dict):
                    continue
                report_date = wb_promotion_report_date(day_row.get("date"))
                apps = day_row.get("apps") if isinstance(day_row.get("apps"), list) else []
                if not apps:
                    apps = [day_row]
                for app_row in apps:
                    if not isinstance(app_row, dict):
                        continue
                    app_type = app_row.get("appType")
                    nms = app_row.get("nms") if isinstance(app_row.get("nms"), list) else []
                    if not nms:
                        nms = [app_row]
                    for nm_row in nms:
                        if not isinstance(nm_row, dict):
                            continue
                        expense = nm_row.get("sum", app_row.get("sum", day_row.get("sum")))
                        ws.append([
                            advert_id,
                            report_date,
                            app_type,
                            nm_row.get("nmId", nm_row.get("nm")),
                            nm_row.get("name"),
                            nm_row.get("views", app_row.get("views", day_row.get("views"))),
                            nm_row.get("clicks", app_row.get("clicks", day_row.get("clicks"))),
                            nm_row.get("ctr", app_row.get("ctr", day_row.get("ctr"))),
                            nm_row.get("cpc", app_row.get("cpc", day_row.get("cpc"))),
                            expense,
                            expense,
                            nm_row.get("atbs", app_row.get("atbs", day_row.get("atbs"))),
                            nm_row.get("orders", app_row.get("orders", day_row.get("orders"))),
                            nm_row.get("cr", app_row.get("cr", day_row.get("cr"))),
                            nm_row.get("shks", app_row.get("shks", day_row.get("shks"))),
                            nm_row.get("sum_price", app_row.get("sum_price", day_row.get("sum_price"))),
                            nm_row.get("canceled", app_row.get("canceled", day_row.get("canceled"))),
                        ])
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 18
    if request_errors:
        error_ws = wb.create_sheet("Ошибки")
        error_ws.append(["beginDate", "endDate", "ids", "status_code", "error"])
        for row in request_errors:
            error_ws.append([
                row.get("beginDate", ""),
                row.get("endDate", ""),
                ",".join(str(value) for value in row.get("ids", [])),
                row.get("status_code", ""),
                row.get("error", ""),
            ])
        error_ws.freeze_panes = "A2"
        for column in range(1, 6):
            error_ws.column_dimensions[error_ws.cell(row=1, column=column).column_letter].width = 24
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def save_wb_promotion_fullstats_manifest(daily_files, plan, request_errors=None, result_mode=WB_PROMOTION_FULLSTATS_RESULT_DETAILS, stopped=False, base_name=None):
    output_dir = Path(WB_PROMOTION_STATS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = str(base_name or f"wb_promotion_fullstats_daily_manifest_{timestamp}")
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /adv/v3/fullstats",
        "result_mode": wb_promotion_fullstats_result_mode(result_mode),
        "created_at": requested_at,
        "mode": "daily_files",
        "stopped": bool(stopped),
        "plan": {
            "campaign_count": plan.get("campaign_count", 0),
            "candidate_count": plan.get("candidate_count", 0),
            "skipped_count": plan.get("skipped_count", 0),
            "periods_count": plan.get("periods_count", 0),
            "window_mode": plan.get("window_mode", ""),
            "auto_mode": plan.get("auto_mode", ""),
            "source_adverts_file": plan.get("source_adverts_file", ""),
        },
        "daily_files": daily_files,
    }
    if request_errors:
        payload["errors"] = request_errors
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Files"
    headers = ["date", "status", "blocks", "requests", "expense_rub", "json", "xlsx"]
    ws.append(headers)
    for row in daily_files:
        ws.append([
            row.get("date", ""),
            row.get("status", ""),
            row.get("count", 0),
            row.get("requests", 0),
            row.get("expense_sum", 0),
            row.get("json", ""),
            row.get("xlsx", ""),
        ])
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 24
    if request_errors:
        error_ws = wb.create_sheet("Ошибки")
        error_ws.append(["beginDate", "endDate", "ids", "status_code", "error"])
        for row in request_errors:
            error_ws.append([
                row.get("beginDate", ""),
                row.get("endDate", ""),
                ",".join(str(value) for value in row.get("ids", [])),
                row.get("status_code", ""),
                row.get("error", ""),
            ])
        error_ws.freeze_panes = "A2"
        for column in range(1, 6):
            error_ws.column_dimensions[error_ws.cell(row=1, column=column).column_letter].width = 24
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def save_wb_promotion_fullstats_campaign_manifest(campaign_files, plan, request_errors=None, stopped=False, base_name=None):
    output_dir = Path(WB_PROMOTION_STATS_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = str(base_name or f"wb_promotion_fullstats_campaign_manifest_{timestamp}")
    requested_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "method": "GET /adv/v3/fullstats",
        "result_mode": WB_PROMOTION_FULLSTATS_RESULT_DETAILS,
        "created_at": requested_at,
        "mode": "campaign_lifetime_files",
        "stopped": bool(stopped),
        "plan": {
            "campaign_count": plan.get("campaign_count", 0),
            "campaigns_to_export": plan.get("campaigns_to_export", 0),
            "skipped_existing_count": plan.get("skipped_existing_count", 0),
            "candidate_count": plan.get("candidate_count", 0),
            "skipped_count": plan.get("skipped_count", 0),
            "periods_count": plan.get("periods_count", 0),
            "window_mode": plan.get("window_mode", ""),
            "source_adverts_file": plan.get("source_adverts_file", ""),
        },
        "campaign_files": campaign_files,
    }
    if request_errors:
        payload["errors"] = request_errors
    json_path = output_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_path = output_dir / f"{base_name}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Campaign Files"
    headers = ["advertId", "status", "created", "deleted", "blocks", "requests", "expense_rub", "json", "xlsx"]
    ws.append(headers)
    for row in campaign_files:
        ws.append([
            row.get("advertId", ""),
            row.get("status", ""),
            row.get("created", ""),
            row.get("deleted", ""),
            row.get("count", 0),
            row.get("requests", 0),
            row.get("expense_sum", 0),
            row.get("json", ""),
            row.get("xlsx", ""),
        ])
    ws.freeze_panes = "A2"
    for column in range(1, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = 24
    if request_errors:
        error_ws = wb.create_sheet("Ошибки")
        error_ws.append(["beginDate", "endDate", "ids", "status_code", "error"])
        for row in request_errors:
            error_ws.append([
                row.get("beginDate", ""),
                row.get("endDate", ""),
                ",".join(str(value) for value in row.get("ids", [])),
                row.get("status_code", ""),
                row.get("error", ""),
            ])
        error_ws.freeze_panes = "A2"
        for column in range(1, 6):
            error_ws.column_dimensions[error_ws.cell(row=1, column=column).column_letter].width = 24
    wb.save(xlsx_path)
    return {"json": str(json_path), "xlsx": str(xlsx_path)}


def handle_wb_promotion_fullstats_export(payload):
    method_key = "promotion_fullstats"
    raise_if_wb_api_cooldown(method_key, "GET /adv/v3/fullstats")
    token = os.environ.get(WB_API_TOKEN_ENV) or read_app_env_file().get(WB_API_TOKEN_ENV)
    plan = build_wb_promotion_fullstats_plan(payload or {})
    result_mode = wb_promotion_fullstats_result_mode((payload or {}).get("result_mode"))
    campaign_lifetime_mode = plan.get("window_mode") == WB_PROMOTION_FULLSTATS_WINDOW_CAMPAIGN_LIFETIME
    if campaign_lifetime_mode:
        result_mode = WB_PROMOTION_FULLSTATS_RESULT_DETAILS
    total_requests = len(plan["requests"])
    daily_save_mode = plan.get("window_mode") == WB_PROMOTION_FULLSTATS_WINDOW_DAY and plan.get("periods_count", 0) > 1
    update_wb_api_progress(
        method_key,
        phase="plan",
        current=0,
        total=total_requests,
        message=(
            f"План: кампаний {plan['campaign_count']} из {plan['candidate_count']}, "
            f"периодов {plan['periods_count']}, запросов к WB {total_requests}"
            + (f"; по кампаниям: к выгрузке {plan.get('campaigns_to_export', 0)}, уже есть файлов {plan.get('skipped_existing_count', 0)}" if campaign_lifetime_mode else "")
            + ("; дневной режим: каждый день сохраняется отдельным XLSX/JSON сразу после выгрузки" if daily_save_mode else "")
        ),
    )
    api_payload = []
    current_day_payload = []
    daily_files = []
    current_campaign_payload = []
    campaign_files = []
    request_errors = []
    stopped = False
    requests_sent = 0
    total_response_blocks = 0
    total_expense_sum = 0.0
    current_day = ""
    current_day_requests = []
    current_day_error_start = 0
    current_campaign = None
    current_campaign_requests = []
    current_campaign_error_start = 0
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    started_monotonic = time.monotonic()

    def accumulated_response_count():
        if daily_save_mode:
            return total_response_blocks + len(current_day_payload)
        if campaign_lifetime_mode:
            return total_response_blocks + len(current_campaign_payload)
        return len(api_payload)

    def request_fullstats_with_split(request_params, *, split_depth=0, server_retry_attempt=0, skip_initial_pause=False):
        nonlocal requests_sent
        check_wb_api_stop_requested()
        date_window = f"{request_params['beginDate']}..{request_params['endDate']}"
        progress_context = str(request_params.get("progress_context") or "").strip()
        progress_prefix = f"{progress_context} | " if progress_context else ""
        campaign_count = len(request_params["ids"])
        if requests_sent and not skip_initial_pause:
            sleep_wb_promotion_fullstats_pause(
                method_key,
                WB_PROMOTION_API_PAUSE_SECONDS,
                requests_sent,
                total_requests,
                started_monotonic,
                f"{progress_prefix}готовлю следующий WB-запрос, получено блоков накоплено: {accumulated_response_count()}",
            )
        request_number = requests_sent + 1
        split_note = f", дробление уровень {split_depth}" if split_depth else ""
        update_wb_promotion_fullstats_progress(
            method_key,
            "request",
            request_number,
            total_requests,
            f"{progress_prefix}запрашиваю окно {date_window}, кампаний в пачке: {campaign_count}{split_note}, ответов WB накоплено: {accumulated_response_count()}",
            started_monotonic=started_monotonic,
            completed_for_eta=requests_sent,
        )
        path = build_wb_promotion_fullstats_path(request_params)
        try:
            response = wb_promotion_api_request_json(path, token)
        except WbApiError as exc:
            requests_sent += 1
            if exc.status_code == 429:
                raise
            if is_wb_api_deadline_error(exc) and campaign_count > 1:
                mid = max(1, campaign_count // 2)
                left_ids = request_params["ids"][:mid]
                right_ids = request_params["ids"][mid:]
                left = {**request_params, "ids": left_ids, "campaign_ids": list(left_ids)}
                right = {**request_params, "ids": right_ids, "campaign_ids": list(right_ids)}
                update_wb_promotion_fullstats_progress(
                    method_key,
                    "split",
                    requests_sent,
                    total_requests,
                    f"{progress_prefix}WB вернул deadline на {campaign_count} кампаний за {date_window}; дроблю пачку на {len(left['ids'])} и {len(right['ids'])}",
                    started_monotonic=started_monotonic,
                    completed_for_eta=requests_sent,
                )
                return request_fullstats_with_split(left, split_depth=split_depth + 1) + request_fullstats_with_split(right, split_depth=split_depth + 1)
            if is_wb_api_deadline_error(exc):
                request_errors.append({
                    "beginDate": request_params["beginDate"],
                    "endDate": request_params["endDate"],
                    "ids": request_params["ids"],
                    "status_code": exc.status_code,
                    "error": str(exc.detail),
                })
                update_wb_promotion_fullstats_progress(
                    method_key,
                    "request_error",
                    requests_sent,
                    total_requests,
                    f"{progress_prefix}WB deadline даже на одиночной кампании {request_params['ids'][0]} за {date_window}; пропускаю и продолжаю",
                    started_monotonic=started_monotonic,
                    completed_for_eta=requests_sent,
                )
                return []
            if is_wb_api_retryable_server_error(exc) and server_retry_attempt < WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES:
                next_retry_attempt = server_retry_attempt + 1
                update_wb_promotion_fullstats_progress(
                    method_key,
                    "retry_wait",
                    requests_sent,
                    total_requests,
                    f"{progress_prefix}WB вернул {exc.status_code}: {str(exc.detail)[:300]}; повтор {next_retry_attempt}/{WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES} через {format_wb_api_duration(WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_RETRY_SECONDS)}",
                    started_monotonic=started_monotonic,
                    completed_for_eta=requests_sent,
                )
                sleep_wb_promotion_fullstats_pause(
                    method_key,
                    WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_RETRY_SECONDS,
                    requests_sent,
                    total_requests,
                    started_monotonic,
                    f"{progress_prefix}WB вернул {exc.status_code}; повтор {next_retry_attempt}/{WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES}",
                )
                return request_fullstats_with_split(
                    request_params,
                    split_depth=split_depth,
                    server_retry_attempt=next_retry_attempt,
                    skip_initial_pause=True,
                )
            raise
        except TimeoutError as exc:
            requests_sent += 1
            timeout_message = str(exc) or "The read operation timed out"
            if server_retry_attempt < WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES:
                next_retry_attempt = server_retry_attempt + 1
                update_wb_promotion_fullstats_progress(
                    method_key,
                    "retry_wait",
                    requests_sent,
                    total_requests,
                    f"{progress_prefix}WB не отдал ответ за 120с: {timeout_message}; повтор {next_retry_attempt}/{WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES} через {format_wb_api_duration(WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_RETRY_SECONDS)}",
                    started_monotonic=started_monotonic,
                    completed_for_eta=requests_sent,
                )
                sleep_wb_promotion_fullstats_pause(
                    method_key,
                    WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_RETRY_SECONDS,
                    requests_sent,
                    total_requests,
                    started_monotonic,
                    f"{progress_prefix}WB timeout; повтор {next_retry_attempt}/{WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES}",
                )
                return request_fullstats_with_split(
                    request_params,
                    split_depth=split_depth,
                    server_retry_attempt=next_retry_attempt,
                    skip_initial_pause=True,
                )
            request_errors.append({
                "beginDate": request_params["beginDate"],
                "endDate": request_params["endDate"],
                "ids": request_params["ids"],
                "status_code": "timeout",
                "error": timeout_message,
            })
            update_wb_promotion_fullstats_progress(
                method_key,
                "request_error",
                requests_sent,
                total_requests,
                f"{progress_prefix}WB timeout за {date_window} после {WB_PROMOTION_FULLSTATS_TRANSIENT_5XX_MAX_RETRIES} повторов; пропускаю окно и продолжаю",
                started_monotonic=started_monotonic,
                completed_for_eta=requests_sent,
            )
            return []
        requests_sent += 1
        return response if isinstance(response, list) else [response]

    def grouped_daily_requests():
        groups = []
        current_key = None
        for request_params in plan["requests"]:
            key = (request_params["beginDate"], request_params["endDate"])
            if key != current_key:
                groups.append({"beginDate": key[0], "endDate": key[1], "requests": []})
                current_key = key
            groups[-1]["requests"].append(request_params)
        return groups

    def grouped_campaign_requests():
        by_id = {}
        for request_params in plan["requests"]:
            campaign_ids = request_params.get("campaign_ids") or [request_params.get("campaign_id") or (request_params.get("ids") or [""])[0]]
            for campaign_id in campaign_ids:
                by_id.setdefault(campaign_id, []).append({**request_params, "ids": [campaign_id], "campaign_id": campaign_id})
        return [
            {**campaign, "requests": by_id.get(campaign["id"], [])}
            for campaign in plan.get("campaigns", [])
            if not campaign.get("skip_existing")
        ]

    def fullstats_items_by_campaign(response_items):
        grouped = {}
        for item in response_items if isinstance(response_items, list) else []:
            if not isinstance(item, dict):
                continue
            campaign_id = wb_promotion_campaign_id(item)
            if not campaign_id:
                continue
            grouped.setdefault(campaign_id, []).append(item)
        return grouped

    def save_current_day_files(status="ok"):
        nonlocal current_day_payload, total_response_blocks, total_expense_sum
        if not current_day or not current_day_payload:
            return None
        day_errors = request_errors[current_day_error_start:]
        safe_date = current_day.replace("-", "")
        base_name = f"wb_promotion_fullstats_{current_day}_{run_timestamp}"
        update_wb_api_progress(
            method_key,
            phase="saving_day",
            current=requests_sent,
            total=total_requests,
            message=(
                f"Сохраняю день {current_day}: блоков {len(current_day_payload)}, "
                f"запросов за день {len(current_day_requests)}, прошло {format_wb_api_duration(time.monotonic() - started_monotonic)}"
            ),
        )
        files = save_wb_promotion_fullstats_files(
            current_day_payload,
            current_day_requests,
            day_errors,
            result_mode,
            base_name=base_name,
        )
        expense_sum = wb_promotion_fullstats_expense_sum(current_day_payload)
        row = {
            "date": current_day,
            "status": status,
            "count": len(current_day_payload),
            "requests": len(current_day_requests),
            "expense_sum": expense_sum,
            "json": files["json"],
            "xlsx": files["xlsx"],
        }
        daily_files.append(row)
        total_response_blocks += len(current_day_payload)
        total_expense_sum = round(total_expense_sum + expense_sum, 2)
        update_wb_api_progress(
            method_key,
            phase="day_saved",
            current=requests_sent,
            total=total_requests,
            message=f"День {current_day} сохранен: {files['xlsx']} | блоков {len(current_day_payload)}, расход {expense_sum} руб.",
        )
        current_day_payload = []
        return row

    def save_current_campaign_files(status="ok"):
        nonlocal current_campaign_payload, current_campaign, total_response_blocks, total_expense_sum
        if not current_campaign:
            return None
        campaign_id = current_campaign["id"]
        campaign_errors = request_errors[current_campaign_error_start:]
        base_name = wb_promotion_campaign_lifetime_output_base(campaign_id)
        if status != "ok":
            base_name = f"{base_name}_partial_{run_timestamp}"
        update_wb_api_progress(
            method_key,
            phase="saving_campaign",
            current=requests_sent,
            total=total_requests,
            message=(
                f"Сохраняю кампанию {campaign_id}: блоков {len(current_campaign_payload)}, "
                f"окон {len(current_campaign_requests)}, файл {base_name}.xlsx"
                + ("; это частичная остановка, точный ID.xlsx не создается и кампания будет перекачана при следующем запуске" if status != "ok" else "")
            ),
        )
        files = save_wb_promotion_fullstats_files(
            current_campaign_payload,
            current_campaign_requests,
            campaign_errors,
            WB_PROMOTION_FULLSTATS_RESULT_DETAILS,
            base_name=base_name,
        )
        expense_sum = wb_promotion_fullstats_expense_sum(current_campaign_payload)
        row = {
            "advertId": campaign_id,
            "status": status,
            "created": current_campaign.get("created", ""),
            "deleted": current_campaign.get("deleted", ""),
            "count": len(current_campaign_payload),
            "requests": len(current_campaign_requests),
            "expense_sum": expense_sum,
            "json": files["json"],
            "xlsx": files["xlsx"],
        }
        campaign_files.append(row)
        total_response_blocks += len(current_campaign_payload)
        total_expense_sum = round(total_expense_sum + expense_sum, 2)
        update_wb_api_progress(
            method_key,
            phase="campaign_saved",
            current=requests_sent,
            total=total_requests,
            message=f"Кампания {campaign_id} сохранена: {files['xlsx']} | блоков {len(current_campaign_payload)}, расход {expense_sum} руб.",
        )
        current_campaign_payload = []
        current_campaign = None
        return row

    try:
        if campaign_lifetime_mode:
            skipped_existing = plan.get("skipped_existing_campaigns", [])
            for campaign in skipped_existing:
                files = campaign.get("existing_files") or {}
                campaign_files.append({
                    "advertId": campaign.get("id"),
                    "status": "skipped_existing",
                    "created": campaign.get("created", ""),
                    "deleted": campaign.get("deleted", ""),
                    "count": 0,
                    "requests": 0,
                    "expense_sum": 0,
                    "json": files.get("json", ""),
                    "xlsx": files.get("xlsx", ""),
                })
            campaign_groups = grouped_campaign_requests()
            update_wb_api_progress(
                method_key,
                phase="campaign_plan",
                current=0,
                total=total_requests,
                message=(
                    f"Режим по кампаниям: {len(campaign_groups)} к выгрузке, "
                    f"батч до {plan.get('campaign_batch_size', 1)} кампаний с одинаковым окном дат, "
                    f"{len(skipped_existing)} уже имеют файл ID.xlsx и будут пропущены; "
                    "каждая кампания сохраняется сразу после завершения всех 31-дневных окон."
                ),
            )
            campaign_index = 1
            while campaign_index <= len(campaign_groups):
                campaign = campaign_groups[campaign_index - 1]
                check_wb_api_stop_requested()
                campaign_batch_size = int(plan.get("campaign_batch_size") or 1)
                can_batch_single_window = campaign_batch_size > 1 and len(campaign.get("requests", [])) == 1
                if can_batch_single_window:
                    first_request = campaign["requests"][0]
                    batch_campaigns = [campaign]
                    batch_key = (first_request["beginDate"], first_request["endDate"])
                    lookahead_index = campaign_index
                    while lookahead_index < len(campaign_groups) and len(batch_campaigns) < campaign_batch_size:
                        next_campaign = campaign_groups[lookahead_index]
                        next_requests = next_campaign.get("requests", [])
                        if len(next_requests) != 1:
                            break
                        next_key = (next_requests[0]["beginDate"], next_requests[0]["endDate"])
                        if next_key != batch_key:
                            break
                        batch_campaigns.append(next_campaign)
                        lookahead_index += 1
                    if len(batch_campaigns) > 1:
                        batch_ids = [item["id"] for item in batch_campaigns]
                        date_window = f"{first_request['beginDate']}..{first_request['endDate']}"
                        batch_context = (
                            f"СЕЙЧАС: батч кампаний {campaign_index}-{campaign_index + len(batch_campaigns) - 1}/{len(campaign_groups)} | "
                            f"ID {','.join(str(value) for value in batch_ids)} | окно 1/1 | {date_window} | "
                            f"файлы {','.join(str(value) + '.xlsx' for value in batch_ids)}"
                        )
                        update_wb_api_progress(
                            method_key,
                            phase="campaign_batch_start",
                            current=requests_sent,
                            total=total_requests,
                            message=(
                                f"Батч {len(batch_campaigns)} кампаний с одинаковым окном {date_window}: "
                                f"{', '.join(str(value) for value in batch_ids)}"
                            ),
                        )
                        batch_error_start = len(request_errors)
                        response_items = request_fullstats_with_split(
                            {
                                **first_request,
                                "ids": batch_ids,
                                "campaign_ids": batch_ids,
                                "progress_context": batch_context,
                            }
                        )
                        response_by_campaign = fullstats_items_by_campaign(response_items)
                        for batch_campaign in batch_campaigns:
                            current_campaign = batch_campaign
                            current_campaign_requests = [{**first_request, "ids": [batch_campaign["id"]], "campaign_id": batch_campaign["id"]}]
                            current_campaign_error_start = batch_error_start
                            current_campaign_payload = response_by_campaign.get(batch_campaign["id"], [])
                            save_current_campaign_files()
                        update_wb_promotion_fullstats_progress(
                            method_key,
                            "request_done",
                            requests_sent,
                            total_requests,
                            f"{batch_context} | готово: получено блоков {len(response_items)}, кампаний в батче: {len(batch_campaigns)}, ранее сохранено: {total_response_blocks}",
                            started_monotonic=started_monotonic,
                            completed_for_eta=requests_sent,
                        )
                        campaign_index += len(batch_campaigns)
                        continue
                current_campaign = campaign
                current_campaign_requests = campaign.get("requests", [])
                current_campaign_error_start = len(request_errors)
                current_campaign_payload = []
                update_wb_api_progress(
                    method_key,
                    phase="campaign_start",
                    current=requests_sent,
                    total=total_requests,
                    message=(
                        f"Кампания {campaign_index}/{len(campaign_groups)}: {campaign['id']} | "
                        f"{campaign.get('created')}..{campaign.get('deleted')} | "
                        f"окон по 31 день: {len(current_campaign_requests)}"
                    ),
                )
                total_campaign_windows = len(current_campaign_requests)
                for window_index, request_params in enumerate(current_campaign_requests, start=1):
                    check_wb_api_stop_requested()
                    date_window = f"{request_params['beginDate']}..{request_params['endDate']}"
                    campaign_context = (
                        f"СЕЙЧАС: кампания {campaign_index}/{len(campaign_groups)} | "
                        f"ID {campaign['id']} | окно {window_index}/{total_campaign_windows} | "
                        f"{date_window} | файл {campaign['id']}.xlsx"
                    )
                    response_items = request_fullstats_with_split({**request_params, "progress_context": campaign_context})
                    response_blocks = len(response_items)
                    current_campaign_payload.extend(response_items)
                    update_wb_promotion_fullstats_progress(
                        method_key,
                        "request_done",
                        requests_sent,
                        total_requests,
                        f"{campaign_context} | готово: получено блоков {response_blocks}, всего по кампании: {len(current_campaign_payload)}, ранее сохранено: {total_response_blocks}",
                        started_monotonic=started_monotonic,
                        completed_for_eta=requests_sent,
                    )
                save_current_campaign_files()
                campaign_index += 1
        elif daily_save_mode:
            day_groups = grouped_daily_requests()
            update_wb_promotion_fullstats_progress(
                method_key,
                "day_plan",
                0,
                total_requests,
                f"Дневной режим: {len(day_groups)} дней; каждый день будет сохранен отдельным XLSX/JSON до перехода к следующему",
                started_monotonic=started_monotonic,
                completed_for_eta=requests_sent,
            )
            for day_index, day_group in enumerate(day_groups, start=1):
                check_wb_api_stop_requested()
                current_day = day_group["beginDate"]
                current_day_requests = day_group["requests"]
                current_day_error_start = len(request_errors)
                current_day_payload = []
                day_campaigns = sum(len(row.get("ids", [])) for row in current_day_requests)
                update_wb_api_progress(
                    method_key,
                    phase="day_start",
                    current=requests_sent,
                    total=total_requests,
                    message=(
                        f"День {day_index}/{len(day_groups)}: {current_day}; "
                        f"пачек WB {len(current_day_requests)}, campaign-day ID {day_campaigns}. "
                        "После дня сразу сохраняю файл, без фоновых backend-процессов."
                    ),
                )
                for request_params in current_day_requests:
                    check_wb_api_stop_requested()
                    date_window = f"{request_params['beginDate']}..{request_params['endDate']}"
                    campaign_count = len(request_params["ids"])
                    response_items = request_fullstats_with_split(request_params)
                    response_blocks = len(response_items)
                    current_day_payload.extend(response_items)
                    update_wb_promotion_fullstats_progress(
                        method_key,
                        "request_done",
                        requests_sent,
                        total_requests,
                        f"[{requests_sent}/{total_requests}] {date_window}: получено блоков {response_blocks}, кампаний в исходной пачке {campaign_count}, ответов за день: {len(current_day_payload)}, всего ранее сохранено: {total_response_blocks}",
                        started_monotonic=started_monotonic,
                        completed_for_eta=requests_sent,
                    )
                save_current_day_files()
        else:
            for request_params in plan["requests"]:
                check_wb_api_stop_requested()
                date_window = f"{request_params['beginDate']}..{request_params['endDate']}"
                campaign_count = len(request_params["ids"])
                response_items = request_fullstats_with_split(request_params)
                response_blocks = len(response_items)
                api_payload.extend(response_items)
                update_wb_promotion_fullstats_progress(
                    method_key,
                    "request_done",
                    requests_sent,
                    total_requests,
                    f"[{requests_sent}/{total_requests}] {date_window}: получено блоков {response_blocks}, кампаний в исходной пачке {campaign_count}, всего ответов WB: {len(api_payload)}",
                    started_monotonic=started_monotonic,
                    completed_for_eta=requests_sent,
                )
    except WbApiStopRequested:
        stopped = True
        if campaign_lifetime_mode and current_campaign:
            save_current_campaign_files(status="partial_stopped")
        if daily_save_mode and current_day_payload:
            save_current_day_files(status="partial_stopped")
    except WbApiError as exc:
        if exc.status_code == 429:
            remember_wb_api_cooldown(method_key, exc.retry_after or 1200)
        raise RuntimeError(str(exc)) from exc
    update_wb_api_progress(
        method_key,
        phase="saving",
        current=requests_sent,
        total=total_requests,
        message=f"Сохраняю итог: ответов WB {accumulated_response_count()}, запросов отправлено {requests_sent}, прошло {format_wb_api_duration(time.monotonic() - started_monotonic)}",
    )
    if campaign_lifetime_mode:
        files = save_wb_promotion_fullstats_campaign_manifest(
            campaign_files,
            plan,
            request_errors,
            stopped=stopped,
            base_name=f"wb_promotion_fullstats_campaign_manifest_{run_timestamp}",
        )
        expense_sum = total_expense_sum
        result_count = total_response_blocks
    elif daily_save_mode:
        files = save_wb_promotion_fullstats_manifest(
            daily_files,
            plan,
            request_errors,
            result_mode,
            stopped=stopped,
            base_name=f"wb_promotion_fullstats_daily_manifest_{run_timestamp}",
        )
        expense_sum = total_expense_sum
        result_count = total_response_blocks
    else:
        files = save_wb_promotion_fullstats_files(api_payload, plan["requests"], request_errors, result_mode)
        expense_sum = wb_promotion_fullstats_expense_sum(api_payload)
        result_count = len(api_payload)
    return {
        "ok": True,
        "method": "promotion_fullstats",
        "count": result_count,
        "requests": requests_sent,
        "planned_requests": len(plan["requests"]),
        "campaigns_requested": plan["campaign_count"],
        "candidate_count": plan["candidate_count"],
        "skipped_count": plan["skipped_count"],
        "skipped_campaigns": plan["skipped_campaigns"],
        "skipped_reasons": plan["skipped_reasons"],
        "expense_sum": expense_sum,
        "periods_count": plan["periods_count"],
        "result_mode": result_mode,
        "error_count": len(request_errors),
        "request_errors": request_errors,
        "source_adverts_file": plan["source_adverts_file"],
        "files": files,
        "daily_files": daily_files,
        "daily_save_mode": daily_save_mode,
        "campaign_files": campaign_files,
        "campaign_lifetime_mode": campaign_lifetime_mode,
        "campaign_batch_size": plan.get("campaign_batch_size", 1),
        "campaign_windows_count": plan.get("campaign_windows_count", plan.get("periods_count", 0)),
        "skipped_existing_count": plan.get("skipped_existing_count", 0),
        "output_dir": str(WB_PROMOTION_STATS_OUTPUT_DIR),
        "stopped": stopped,
    }


def open_wb_api_result_file(file_path, opener=None):
    target = Path(str(file_path or "")).resolve()
    output_dirs = [
        Path(WB_MEDIA_COUNT_OUTPUT_DIR).resolve(),
        Path(WB_MEDIA_ADVERTS_OUTPUT_DIR).resolve(),
        Path(WB_MEDIA_STATS_OUTPUT_DIR).resolve(),
        Path(WB_PROMOTION_COUNT_OUTPUT_DIR).resolve(),
        Path(WB_PROMOTION_ADVERTS_OUTPUT_DIR).resolve(),
        Path(WB_PROMOTION_STATS_OUTPUT_DIR).resolve(),
        Path(WB_CONTENT_CATEGORIES_OUTPUT_DIR).resolve(),
        Path(WB_CONTENT_CARDS_OUTPUT_DIR).resolve(),
        Path(WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR).resolve(),
        Path(WB_API_LOG_OUTPUT_DIR).resolve(),
    ]
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Файл результата не найден: {target}")
    if not any(output_dir in target.parents for output_dir in output_dirs):
        raise ValueError("Можно открыть только файл из папки WB API")
    open_file = opener or os.startfile
    open_file(str(target))
    return {"ok": True, "file": str(target)}


load_app_env()

ADMIN_AUTH_COOKIE_NAME = "kokoc_admin_session"
ADMIN_AUTH_PASSWORD_ITERATIONS = 310_000
ADMIN_AUTH_SESSION_TTL_SECONDS = 3 * 60 * 60
ADMIN_AUTH_FAILURE_WINDOW_SECONDS = 10 * 60
ADMIN_AUTH_MAX_FAILURES = 5
ADMIN_AUTH_PUBLIC_PATHS = {
    "/api/admin/auth/login",
    "/api/admin/auth/logout",
    "/api/admin/auth/status",
}


def _admin_b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _admin_b64decode(value):
    clean_value = str(value or "")
    return base64.urlsafe_b64decode(clean_value + "=" * (-len(clean_value) % 4))


def admin_auth_configured():
    return all(
        str(os.environ.get(key) or "").strip()
        for key in (
            "ADMIN_AUTH_USERNAME",
            "ADMIN_AUTH_PASSWORD_SALT",
            "ADMIN_AUTH_PASSWORD_HASH",
            "ADMIN_AUTH_SESSION_SECRET",
        )
    )


def admin_password_digest(password, salt=None):
    salt_value = str(salt or os.environ.get("ADMIN_AUTH_PASSWORD_SALT") or "").strip()
    if not salt_value:
        return ""
    try:
        salt_bytes = _admin_b64decode(salt_value)
    except (ValueError, TypeError):
        return ""
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt_bytes,
        ADMIN_AUTH_PASSWORD_ITERATIONS,
    )
    return _admin_b64encode(digest)


def verify_admin_credentials(username, password):
    if not admin_auth_configured():
        return False
    expected_username = str(os.environ.get("ADMIN_AUTH_USERNAME") or "")
    expected_digest = str(os.environ.get("ADMIN_AUTH_PASSWORD_HASH") or "")
    supplied_digest = admin_password_digest(password)
    return hmac.compare_digest(str(username or ""), expected_username) and hmac.compare_digest(
        supplied_digest,
        expected_digest,
    )


def create_admin_session_token(now=None):
    issued_at = int(time.time() if now is None else now)
    payload = {
        "username": str(os.environ.get("ADMIN_AUTH_USERNAME") or ""),
        "issued_at": issued_at,
        "expires_at": issued_at + ADMIN_AUTH_SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(12),
    }
    payload_encoded = _admin_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    secret = str(os.environ.get("ADMIN_AUTH_SESSION_SECRET") or "").encode("utf-8")
    signature = hmac.new(secret, payload_encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_encoded}.{_admin_b64encode(signature)}"


def verify_admin_session_token(token, now=None):
    if not admin_auth_configured() or not token:
        return False
    try:
        payload_encoded, signature_encoded = str(token).split(".", 1)
        secret = str(os.environ.get("ADMIN_AUTH_SESSION_SECRET") or "").encode("utf-8")
        expected_signature = hmac.new(secret, payload_encoded.encode("ascii"), hashlib.sha256).digest()
        supplied_signature = _admin_b64decode(signature_encoded)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        payload = json.loads(_admin_b64decode(payload_encoded).decode("utf-8"))
        current_time = int(time.time() if now is None else now)
        return (
            hmac.compare_digest(str(payload.get("username") or ""), str(os.environ.get("ADMIN_AUTH_USERNAME") or ""))
            and int(payload.get("issued_at") or 0) <= current_time + 60
            and int(payload.get("expires_at") or 0) > current_time
            and int(payload.get("issued_at") or 0) + 3 * 60 * 60 > current_time
        )
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def admin_session_from_cookie(cookie_header):
    try:
        cookie = SimpleCookie()
        cookie.load(str(cookie_header or ""))
        morsel = cookie.get(ADMIN_AUTH_COOKIE_NAME)
        return morsel.value if morsel else ""
    except (CookieError, KeyError):
        return ""


def admin_login_retry_after(client_key, now=None):
    current_time = time.time() if now is None else float(now)
    with ADMIN_AUTH_FAILURES_LOCK:
        attempts = [
            timestamp
            for timestamp in ADMIN_AUTH_FAILURES.get(client_key, [])
            if current_time - timestamp < ADMIN_AUTH_FAILURE_WINDOW_SECONDS
        ]
        if attempts:
            ADMIN_AUTH_FAILURES[client_key] = attempts
        else:
            ADMIN_AUTH_FAILURES.pop(client_key, None)
        if len(attempts) < ADMIN_AUTH_MAX_FAILURES:
            return 0
        return max(1, int(ADMIN_AUTH_FAILURE_WINDOW_SECONDS - (current_time - attempts[0])))


def record_admin_login_failure(client_key, now=None):
    current_time = time.time() if now is None else float(now)
    with ADMIN_AUTH_FAILURES_LOCK:
        attempts = [
            timestamp
            for timestamp in ADMIN_AUTH_FAILURES.get(client_key, [])
            if current_time - timestamp < ADMIN_AUTH_FAILURE_WINDOW_SECONDS
        ]
        attempts.append(current_time)
        ADMIN_AUTH_FAILURES[client_key] = attempts


def clear_admin_login_failures(client_key):
    with ADMIN_AUTH_FAILURES_LOCK:
        ADMIN_AUTH_FAILURES.pop(client_key, None)


DASHBOARD_ACCESS_USERNAME_ENV = "DASHBOARD_ACCESS_USERNAME"
DASHBOARD_ACCESS_PASSWORD_ENV = "DASHBOARD_ACCESS_PASSWORD"
DASHBOARD_ACCESS_COOKIE_NAME = "kokoc_bi_access"
DASHBOARD_ACCESS_SESSION_TTL_SECONDS = 3 * 60 * 60


def dashboard_locked_client():
    client = str(os.environ.get("DASHBOARD_LOCKED_CLIENT") or "").strip().lower()
    return client if client in ADMIN_CLIENTS else ""


def dashboard_client_locked():
    return bool(dashboard_locked_client())


def dashboard_excluded_clients():
    values = str(os.environ.get("DASHBOARD_EXCLUDED_CLIENTS") or "").split(",")
    return {value.strip().lower() for value in values if value.strip().lower() in ADMIN_CLIENTS}


def normalize_client_key(value):
    locked_client = dashboard_locked_client()
    if locked_client:
        return locked_client
    access_user = CURRENT_ACCESS_USER.get()
    allowed_clients = None
    if access_user and not access_user.get("is_admin"):
        allowed_clients = [key for key in access_user.get("clients", []) if key in ADMIN_CLIENTS]
    excluded = dashboard_excluded_clients()
    client = (value or DEFAULT_CLIENT).strip().lower()
    if client in ADMIN_CLIENTS and client not in excluded and (allowed_clients is None or client in allowed_clients):
        return client
    if allowed_clients:
        return next((key for key in allowed_clients if key not in excluded), allowed_clients[0])
    if DEFAULT_CLIENT not in excluded:
        return DEFAULT_CLIENT
    return next((key for key in ADMIN_CLIENTS if key not in excluded), DEFAULT_CLIENT)


def client_from_query(query):
    params = parse_qs(query)
    return normalize_client_key(params.get("client", [DEFAULT_CLIENT])[0])


def current_client_key():
    return normalize_client_key(CURRENT_CLIENT.get() or DEFAULT_CLIENT)


def dashboard_access_configured():
    return bool(
        str(os.environ.get(DASHBOARD_ACCESS_USERNAME_ENV) or "")
        and str(os.environ.get(DASHBOARD_ACCESS_PASSWORD_ENV) or "")
    )


def verify_dashboard_access_credentials(username, password):
    if not dashboard_access_configured():
        return False
    expected_username = str(os.environ.get(DASHBOARD_ACCESS_USERNAME_ENV) or "")
    expected_password = str(os.environ.get(DASHBOARD_ACCESS_PASSWORD_ENV) or "")
    supplied_username = str(username or "").strip()
    supplied_password = str(password or "")
    trimmed_password = supplied_password.strip()
    password_candidates = [supplied_password, trimmed_password]
    if trimmed_password.endswith("`"):
        password_candidates.append(trimmed_password[:-1])
    return hmac.compare_digest(supplied_username, expected_username) and any(
        hmac.compare_digest(candidate, expected_password) for candidate in password_candidates
    )


def verify_dashboard_access_header(header):
    if not dashboard_access_configured():
        return True
    value = str(header or "").strip()
    if not value.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(value.split(None, 1)[1], validate=True).decode("utf-8")
        username, password = raw.split(":", 1)
    except (ValueError, TypeError, UnicodeDecodeError):
        return False
    return verify_dashboard_access_credentials(username, password)


def _dashboard_access_signing_key():
    password = str(os.environ.get(DASHBOARD_ACCESS_PASSWORD_ENV) or "").encode("utf-8")
    return hmac.new(password, b"kokoc-bi-dashboard-access-session-v1", hashlib.sha256).digest()


def create_dashboard_access_session_token(now=None):
    if not dashboard_access_configured():
        return ""
    issued_at = int(time.time() if now is None else now)
    payload = {
        "username": str(os.environ.get(DASHBOARD_ACCESS_USERNAME_ENV) or ""),
        "issued_at": issued_at,
        "expires_at": issued_at + DASHBOARD_ACCESS_SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(12),
    }
    payload_encoded = _admin_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(_dashboard_access_signing_key(), payload_encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_encoded}.{_admin_b64encode(signature)}"


def verify_dashboard_access_session_token(token, now=None):
    if not dashboard_access_configured() or not token:
        return False
    try:
        payload_encoded, signature_encoded = str(token).split(".", 1)
        expected_signature = hmac.new(
            _dashboard_access_signing_key(), payload_encoded.encode("ascii"), hashlib.sha256
        ).digest()
        supplied_signature = _admin_b64decode(signature_encoded)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        payload = json.loads(_admin_b64decode(payload_encoded).decode("utf-8"))
        current_time = int(time.time() if now is None else now)
        return (
            hmac.compare_digest(
                str(payload.get("username") or ""), str(os.environ.get(DASHBOARD_ACCESS_USERNAME_ENV) or "")
            )
            and int(payload.get("issued_at") or 0) <= current_time + 60
            and int(payload.get("expires_at") or 0) > current_time
            and int(payload.get("issued_at") or 0) + 3 * 60 * 60 > current_time
        )
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def dashboard_access_session_from_cookie(cookie_header):
    try:
        cookie = SimpleCookie()
        cookie.load(str(cookie_header or ""))
        morsel = cookie.get(DASHBOARD_ACCESS_COOKIE_NAME)
        return morsel.value if morsel else ""
    except (CookieError, KeyError):
        return ""

def managed_user_access_enabled():
    if not str(
        os.environ.get("DASHBOARD_USER_SESSION_SECRET")
        or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
        or ""
    ).strip():
        return False
    try:
        from user_registry import active_user_count
        with client_registry_connection() as conn:
            return active_user_count(conn) > 0
    except Exception:
        return False


def _managed_access_signing_key():
    secret = str(
        os.environ.get("DASHBOARD_USER_SESSION_SECRET")
        or os.environ.get("ADMIN_AUTH_SESSION_SECRET")
        or ""
    ).encode("utf-8")
    return hmac.new(secret, b"kokoc-bi-managed-user-session-v1", hashlib.sha256).digest()


def create_managed_access_token(user, now=None):
    from galactica_entitlement import session_revision

    issued_at = int(time.time() if now is None else now)
    payload = {
        "user_id": int(user.get("user_id") or 0),
        "username": str(user.get("username") or ""),
        "is_admin": bool(user.get("is_admin")),
        "issued_at": issued_at,
        "expires_at": issued_at + DASHBOARD_ACCESS_SESSION_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(32),
    }
    if not payload["is_admin"]:
        payload["user_revision"] = session_revision(user.get("updated_at"))
    encoded = _admin_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_managed_access_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_admin_b64encode(signature)}"


def verify_managed_access_token(token, now=None):
    if not token:
        return None
    try:
        encoded, supplied = str(token).split(".", 1)
        expected = hmac.new(_managed_access_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_admin_b64decode(supplied), expected):
            return None
        payload = json.loads(_admin_b64decode(encoded).decode("utf-8"))
        current_time = int(time.time() if now is None else now)
        if int(payload.get("issued_at") or 0) > current_time + 60 or int(payload.get("expires_at") or 0) <= current_time:
            return None
        if payload.get("is_admin") and int(payload.get("issued_at") or 0) + DASHBOARD_ACCESS_SESSION_TTL_SECONDS <= current_time:
            return None
        if payload.get("is_admin"):
            return {"username": payload.get("username"), "is_admin": True, "clients": [], "reports": [], "admin_sections": list(ADMIN_SECTION_IDS)}
        from galactica_entitlement import configured_subject, require_current_session, transaction_limits, session_revision
        subject = configured_subject(sys.modules[__name__], token)
        from user_registry import list_users
        with client_registry_connection() as conn:
            transaction_limits(conn)
            require_current_session(conn, subject)
            # The schema is bootstrapped before serving. Re-running ALTER TABLE
            # for every concurrent session check can deadlock valid requests.
            users = list_users(conn, prepare_schema=False)
        if subject[2] <= time.time():
            return None
        return next(
            (user for user in users if user.get("is_active") and int(user.get("user_id") or 0) == int(payload.get("user_id") or 0)
             and session_revision(user.get("updated_at")) == subject[3]),
            None,
        )
    except Exception:
        return None


def verify_managed_credentials(username, password):
    try:
        from user_registry import verify_user
        with client_registry_connection() as conn:
            return verify_user(conn, username, password)
    except Exception:
        return None


def report_id_for_request(parsed):
    if parsed.path == "/api/assortment":
        from assortment_api import report_id
        return report_id(parsed)
    # Reuse existing permissions; query parameters cannot override these endpoints.
    if str(parsed.path or '').startswith('/api/autobidder'):
        return 'adv'
    if str(parsed.path or '').startswith('/api/cluster-supply/'):
        return 'inventoryHistory'
    if parsed.path == '/api/order-feed':
        return 'funnel'
    if parsed.path == '/api/weekly-sku-inventory':
        return 'inventoryHistory'
    if parsed.path == '/api/weekly-sku-advertising':
        return 'adv'
    if parsed.path == '/api/sales-order-days':
        return 'funnel'
    if str(parsed.path or '').startswith('/api/reviews'):
        return 'reviews'
    requested = (parse_qs(parsed.query).get("dashboard") or [""])[0]
    # Home is the authenticated shell landing page, not a separately granted
    # report.  Treating it as a report makes a direct /react/?dashboard=home
    # reload fail with 403 for every managed non-admin user.
    if requested in {"home", "admin"}:
        return ""
    if requested:
        return requested
    path = str(parsed.path or "")
    prefixes = (
        ("/api/sku-", "sku"), ("/api/product-", "product"), ("/api/adv-", "adv"),
        ("/api/media-adv-", "mediaAdv"), ("/api/funnel-", "funnel"),
        ("/api/inventory-history", "inventoryHistory"), ("/api/weekly-dynamics", "weeklyDynamics"),
        ("/api/seo-monitoring", "seoMonitoring"), ("/api/seo-project", "seoMonitoring"),
        ("/api/wb-search-", "wbSearchQueries"),
        ("/api/wb-entrance", "wbEntrance"), ("/api/planfact", "planfact"),
        ("/api/km-trade/sales-", "salesPlanning"), ("/api/km-trade/media-plan", "mediaPlan"),
        ("/api/km-trade/unit-", "unitEconomics"), ("/api/km-trade/pl", "profitLoss"),
        ("/api/reviews", "reviews"),
        ("/api/avito-ads-dashboard", "avitoOverview"),
        ("/api/yandex-market/analytics", "yandexOverview"),
        ("/api/lamoda/dashboard", "lamodaSales"),
    )
    if path in {"/api/summary", "/api/stats"}:
        return "abc"
    return next((report for prefix, report in prefixes if path.startswith(prefix)), "")


def locked_dashboard_feature_disabled(path):
    if not dashboard_client_locked():
        return False
    normalized = str(path or "")
    return (
        normalized.startswith("/api/admin")
        or normalized.startswith("/api/review")
        or normalized == "/review"
        or normalized.startswith("/review/")
    )


def current_client_is_boiron():
    return current_client_key() == "boiron"


def adv_marketplace_from_query(query):
    return "ozon" if current_client_is_boiron() else marketplace_from_query(query)


def dashboard_clients_payload():
    locked_client = dashboard_locked_client()
    access_user = CURRENT_ACCESS_USER.get()
    restricted = access_user and not access_user.get("is_admin")
    allowed_clients = set(access_user.get("clients") or []) if restricted else None
    allowed_reports = set(access_user.get("reports") or []) if restricted else None
    return [
        {
            "key": key,
            "label": value["label"],
            "status": value["status"],
            "reports": [report for report in effective_client_reports(key) if allowed_reports is None or report in allowed_reports],
            "marketplaces": list(value.get("marketplaces") or []),
        }
        for key, value in ADMIN_CLIENTS.items()
        if value.get("show_in_dashboard")
        and (allowed_clients is None or key in allowed_clients)
        and (not locked_client or key == locked_client)
        and (locked_client or key not in dashboard_excluded_clients())
    ]


def preferred_client_dashboard(reports):
    reports = list(reports or [])
    if "planfact" in reports:
        return "planfact"
    if "avitoOverview" in reports:
        return "avitoOverview"
    if "adv" in reports:
        return "adv"
    return reports[0] if reports else "abc"


def effective_client_reports(client, config=None):
    client_key = normalize_client_key(client)
    config = ADMIN_CLIENTS[client_key] if config is None else config
    configured = list(config.get("reports") or [])
    marketplaces = set(config.get("marketplaces") or [])
    if (
        "wb" in marketplaces
        and "adv" in configured
        and client_key != "sportmaster"
        and "wbAdSearchQueries" not in configured
    ):
        configured.append("wbAdSearchQueries")
    if "avito" in marketplaces:
        configured.extend(report for report in AVITO_REPORT_IDS if report not in configured)
    if "yandex_market" in marketplaces:
        configured.extend(report for report in YANDEX_REPORT_IDS if report not in configured)
    if "lamoda" in marketplaces:
        configured.extend(report for report in LAMODA_REPORT_IDS if report not in configured)
    if marketplaces == {"avito"}:
        allowed = set(AVITO_REPORT_IDS)
        configured = [report for report in configured if report in allowed]
    if "wb" not in marketplaces:
        configured = [report for report in configured if report not in WB_ONLY_REPORT_IDS]
    if client_key not in WB_SEARCH_QUERY_CLIENT_IDS:
        configured = [report for report in configured if report != "wbSearchQueries"]
    if client_key not in MEDIA_ADV_CLIENT_IDS:
        configured = [report for report in configured if report != "mediaAdv"]
    if client_key == "sportmaster":
        configured = [report for report in configured if report != "wbAdSearchQueries"]
    return configured


SHARED_PORTFOLIO_PATHS = {"/", "/index.html", "/glory", "/glory/"}


def is_shared_portfolio_request(parsed):
    if parsed.path not in SHARED_PORTFOLIO_PATHS or dashboard_client_locked():
        return False
    params = parse_qs(parsed.query, keep_blank_values=True)
    return not any(
        str(value or "").strip()
        for key in ("client", "dashboard")
        for value in params.get(key, [])
    )


def render_portfolio_page():
    from pulse_portfolio import render_page

    return render_page(STATIC_DIR)


def handle_portfolio_overview(parsed=None):
    from pulse_portfolio import build_payload

    params = parse_qs(parsed.query) if parsed is not None else {}
    force = str((params.get("refresh") or [""])[0]).strip().lower() in {"1", "true", "yes"}
    return build_payload(sys.modules[__name__], force=force)


def handle_portfolio_config(payload):
    from pulse_portfolio import save_monitoring_config

    return save_monitoring_config(sys.modules[__name__], payload)

def client_supports_report(client, report):
    access_user = CURRENT_ACCESS_USER.get()
    if access_user and not access_user.get("is_admin") and report not in set(access_user.get("reports") or []):
        return False
    return report in effective_client_reports(client)


def ordered_admin_sections(section_ids):
    available = set(section_ids or [])
    return [item["id"] for item in ADMIN_SECTION_CATALOG if item["id"] in available]


def admin_payload_sections(full_admin=False):
    if full_admin:
        return ordered_admin_sections(ADMIN_SECTION_IDS)
    return ordered_admin_sections(current_admin_section_ids())


def current_admin_section_ids():
    access_user = CURRENT_ACCESS_USER.get() or {}
    if access_user.get("is_admin"):
        return set(ADMIN_SECTION_IDS)
    sections = {str(value) for value in access_user.get("admin_sections") or [] if str(value) in ADMIN_SECTION_IDS}
    if "client" in sections:
        sections.add("clientOnboarding")
    return sections


def admin_sections_for_request(path, method="GET"):
    normalized = str(path or "")
    request_method = str(method or "GET").upper()
    if normalized == "/api/admin/users":
        return {"users"}
    if normalized == "/api/admin/database-overview":
        return {"database"}
    if normalized == "/api/admin/integrations":
        return {"integrations"}
    if normalized == "/api/admin/connections":
        return {"integrations", "clientOnboarding"}
    if normalized == "/api/admin/clients":
        return {"client", "clientOnboarding"} if request_method == "GET" else {"client"}
    if normalized == "/api/admin/yandex-market/discover":
        return {"client", "clientOnboarding"}
    if normalized == "/api/admin/yandex-market/analytics":
        return {"api", "apiDaily", "database"}
    if normalized.startswith("/api/admin/client-onboarding") or normalized == "/api/admin/client-paths/validate":
        return {"clientOnboarding"}
    if normalized.startswith("/api/admin/client-assortment"):
        return {"client"}
    if normalized.startswith("/api/admin/wb-api") or normalized.startswith("/api/admin/ozon-seo") or normalized.startswith("/api/admin/ozon-performance"):
        return {"api", "apiDaily"}
    if normalized == "/api/admin/imports" and request_method == "GET":
        return set(ADMIN_SECTION_IDS)
    if normalized.startswith("/api/admin/all-clients-daily") or normalized.startswith("/api/admin/all-clients-assortment"):
        return {"allDaily"}
    if normalized in {"/api/admin/imports", "/api/admin/run-import", "/api/admin/run-import-sync", "/api/admin/run-import-stream", "/api/admin/stop-import"}:
        return {"manual", "daily", "apiDaily"}
    return set(ADMIN_SECTION_IDS)


def admin_request_permitted(path, method="GET"):
    granted = current_admin_section_ids()
    required = admin_sections_for_request(path, method)
    return bool(granted and (not required or granted.intersection(required)))


def admin_access_status_payload(admin_cookie_authenticated=False):
    access_user = CURRENT_ACCESS_USER.get() or {}
    full_admin = bool(admin_cookie_authenticated or access_user.get("is_admin"))
    sections = admin_payload_sections(full_admin)
    return {
        "ok": True,
        "configured": admin_auth_configured() or managed_user_access_enabled(),
        "authenticated": bool(full_admin or sections),
        "full_admin": full_admin,
        "admin_sections": ADMIN_SECTION_CATALOG,
        "allowed_admin_sections": sections,
    }


def client_marketplace_ids(client=None):
    client_key = normalize_client_key(client or current_client_key())
    configured = ADMIN_CLIENTS[client_key].get("marketplaces") or list(MARKETPLACES)
    supported = [key for key in configured if key in MARKETPLACES]
    return supported or [DEFAULT_MARKETPLACE]


def client_marketplaces_payload(client=None):
    return [{"id": key, "label": MARKETPLACES[key]["label"]} for key in client_marketplace_ids(client)]


def read_db_config(client=None):
    source = Path(os.environ.get("DB_CONFIG_SOURCE", DEFAULT_CONFIG_SOURCE))
    text = source.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"DB_CONFIG\s*=\s*(\{.*?\})", text, re.S)
    if not match:
        raise RuntimeError(f"DB_CONFIG not found in {source}")
    config = ast.literal_eval(match.group(1))
    if os.environ.get("DASHBOARD_DB_NAME"):
        config["database"] = os.environ["DASHBOARD_DB_NAME"]
    if os.environ.get("DASHBOARD_DB_HOST"):
        config["host"] = os.environ["DASHBOARD_DB_HOST"]
    if os.environ.get("DASHBOARD_DB_PORT"):
        config["port"] = int(os.environ["DASHBOARD_DB_PORT"])
    if os.environ.get("DASHBOARD_DB_USER"):
        config["user"] = os.environ["DASHBOARD_DB_USER"]
    if os.environ.get("DASHBOARD_DB_PASSWORD"):
        config["password"] = os.environ["DASHBOARD_DB_PASSWORD"]
    if client is not None:
        config["database"] = ADMIN_CLIENTS[normalize_client_key(client)]["db_name"]
    else:
        active_client = CURRENT_CLIENT.get()
        if active_client:
            config["database"] = ADMIN_CLIENTS[normalize_client_key(active_client)]["db_name"]
    config["connect_timeout"] = 5
    statement_timeout = os.environ.get("DASHBOARD_STATEMENT_TIMEOUT_MS")
    if statement_timeout:
        timeout_ms = int(statement_timeout)
        options = config.get("options", "")
        timeout_option = f"-c statement_timeout={timeout_ms}"
        config["options"] = f"{options} {timeout_option}".strip()
    return config


def get_conn():
    report_connection = CURRENT_GALACTICA_REPORT_CONNECTION.get()
    if report_connection is not None:
        return report_connection()
    return psycopg2.connect(**read_db_config(), cursor_factory=RealDictCursor)


def to_float(value):
    if value is None:
        return 0.0
    if isinstance(value, str):
        value = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def marketplace_from_query(query):
    params = parse_qs(query)
    marketplace = params.get("marketplace", [DEFAULT_MARKETPLACE])[0].strip().lower()
    supported = client_marketplace_ids()
    if marketplace not in supported:
        marketplace = supported[0]
    return marketplace


def planfact_marketplace_from_query(query):
    params = parse_qs(query)
    marketplace = params.get("marketplace", [DEFAULT_MARKETPLACE])[0].strip().lower()
    if marketplace == "total":
        return "total"
    if marketplace not in MARKETPLACES:
        marketplace = DEFAULT_MARKETPLACE
    return marketplace


def view_for_query(query):
    return MARKETPLACES[marketplace_from_query(query)]["view"]


def sku_view_for_query(query):
    return MARKETPLACES[marketplace_from_query(query)]["sku_view"]


def product_view_for_query(query):
    return MARKETPLACES[marketplace_from_query(query)]["product_view"]


def funnel_view_for_marketplace(marketplace, kind="daily"):
    return FUNNEL_VIEWS.get(marketplace, FUNNEL_VIEWS[DEFAULT_MARKETPLACE])[kind]


def funnel_view_for_query(query, kind="daily"):
    return funnel_view_for_marketplace(marketplace_from_query(query), kind)


def funnel_rollup_view_for_query(query):
    marketplace = marketplace_from_query(query)
    return FUNNEL_ROLLUP_VIEWS.get(marketplace, FUNNEL_ROLLUP_VIEWS[DEFAULT_MARKETPLACE])


def funnel_daily_rollup_view_for_query(query):
    marketplace = marketplace_from_query(query)
    return FUNNEL_DAILY_ROLLUP_VIEWS.get(marketplace, FUNNEL_DAILY_ROLLUP_VIEWS[DEFAULT_MARKETPLACE])


def can_use_funnel_rollup(query):
    params = parse_qs(query)
    if params.get("categories") or params.get("category", [""])[0].strip() or params.get("category_exact", [""])[0].strip():
        return False
    if params.get("article", [""])[0].strip():
        return False
    if params.get("product", [""])[0].strip():
        return False
    if repeated_filter_values(params, "inventory_skus"):
        return False
    if params.get(SEO_STATUS_PARAM, [""])[0].strip():
        return False
    if params.get(COLLECTION_STATUS_PARAM, [""])[0].strip():
        return False
    if any(params.get(param, [""])[0].strip() for param, _ in MAPPING_FILTERS):
        return False
    if any(repeated_filter_values(params, param) for param in OZON_PRODUCT_ATTRIBUTE_FILTERS):
        return False
    return True


def relation_exists(cur, view_name):
    cur.execute("SELECT to_regclass(%s) AS object_name", (f"public.{view_name}",))
    row = cur.fetchone()
    return bool(row and row.get("object_name"))


def funnel_summary_source_for_query(cur, query):
    if can_use_funnel_rollup(query):
        rollup_view = funnel_daily_rollup_view_for_query(query)
        if relation_exists(cur, rollup_view):
            return True, rollup_view
    return False, funnel_view_for_query(query)


def abc_base_views_for_marketplace(marketplace):
    return ABC_BASE_VIEWS.get(marketplace, ABC_BASE_VIEWS[DEFAULT_MARKETPLACE])


def normalize_value(value):
    if isinstance(value, Decimal):
        as_float = float(value)
        return int(as_float) if as_float.is_integer() else as_float
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def get_view_columns(cur, view_name):
    cur.execute(
        """
        SELECT a.attname AS column_name
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (view_name,),
    )
    return [row["column_name"] for row in cur.fetchall()]


OZON_EXTENDED_FUNNEL_FIELDS = (
    "impressions_card",
    "cart_adds_search_catalog",
    "cart_adds_card",
    "sessions_total",
    "sessions_search_catalog",
    "sessions_card",
    "returned_units",
    "cancelled_units",
    "delivered_units",
)


def optional_funnel_sum_select(available_columns, field_names=OZON_EXTENDED_FUNNEL_FIELDS):
    columns = set(available_columns or [])
    clauses = []
    for field_name in field_names:
        value = (
            sql.SQL("coalesce(sum(v.{}), 0)").format(sql.Identifier(field_name))
            if field_name in columns
            else sql.SQL("0::numeric")
        )
        clauses.append(
            sql.SQL("\n                , {} AS {}").format(value, sql.Identifier(field_name))
        )
    return sql.SQL("").join(clauses)


def adv_view_for_marketplace(marketplace="ozon"):
    return ADV_VIEWS.get(marketplace, ADV_VIEW)


def adv_promoted_sku_expr(marketplace="ozon"):
    if marketplace == "wb":
        return sql.SQL(
            "coalesce(nullif(v.sku, ''), nullif(v.wb_marketplace_article, ''), "
            "nullif(v.product_artikul, ''), nullif(v.seller_article, ''), "
            "nullif(v.ozon_marketplace_article, ''))"
        )
    return sql.SQL(
        "coalesce(nullif(v.sku, ''), nullif(v.ozon_marketplace_article, ''), "
        "nullif(v.product_artikul, ''), nullif(v.seller_article, ''))"
    )


def media_adv_level_from_query(query):
    params = parse_qs(query)
    level = params.get("media_level", ["campaign"])[0].strip().lower()
    return level if level in {"campaign", "group", "creative"} else "campaign"


def media_adv_view_for_query(query):
    marketplace = marketplace_from_query(query)
    level = media_adv_level_from_query(query)
    return MEDIA_ADV_VIEWS.get(marketplace, MEDIA_ADV_VIEWS[DEFAULT_MARKETPLACE]).get(level, MEDIA_ADV_VIEW)


def media_adv_option_select_fields(marketplace):
    fields = [
        "array_remove(array_agg(DISTINCT campaign_status ORDER BY campaign_status), NULL) AS media_statuses",
        "array_remove(array_agg(DISTINCT campaign_segment ORDER BY campaign_segment), NULL) AS media_segments",
        "array_remove(array_agg(DISTINCT campaign_id ORDER BY campaign_id), NULL) AS media_campaign_ids",
    ]
    if marketplace == "wb":
        fields.extend(
            [
                "array_remove(array_agg(DISTINCT group_id ORDER BY group_id), NULL) AS media_group_ids",
                "array_remove(array_agg(DISTINCT creative_id ORDER BY creative_id), NULL) AS media_creative_ids",
            ]
        )
    return fields


def mapping_join_for_report(report, marketplace=None):
    if current_client_is_boiron() or current_client_key() in MAPPINGLESS_CLIENTS:
        return sql.SQL("")
    if report == "adv":
        if marketplace == "wb":
            return sql.SQL(
                " LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m "
                "ON m.wb_nmid = v.sku"
            )
        return sql.SQL(
            " LEFT JOIN public.mv_sku_mapping_gj_ozon_sku m "
            "ON m.ozon_sku = v.ozon_marketplace_article"
        )
    if report == "funnel":
        if marketplace == "wb":
            return sql.SQL(
                " LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m "
                "ON m.wb_nmid = v.sku"
            )
        return sql.SQL(
            " LEFT JOIN public.mv_sku_mapping_gj_barcode m "
            "ON m.barcode = v.barcode"
        )
    if marketplace == "wb":
        return sql.SQL(
            " LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m "
            "ON m.wb_nmid = v.artikul_wb"
        )
    return sql.SQL(
        " LEFT JOIN public.mv_sku_mapping_gj_ozon_sku m "
        "ON m.ozon_sku = v.artikul_wb"
    )


def seo_status_values_from_params(params):
    values = []
    for raw_value in params.get(SEO_STATUS_PARAM, []):
        values.extend(value.strip() for value in raw_value.split(","))
    return [value for value in values if value]


def collection_status_values_from_params(params):
    values = []
    for raw_value in params.get(COLLECTION_STATUS_PARAM, []):
        values.extend(value.strip() for value in raw_value.split(","))
    return [value for value in values if value]


def sql_text_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def collection_tag_label(tag):
    return COLLECTION_TAG_LABELS.get(tag, tag)


def normalize_collection_status_value(value):
    if value == COLLECTION_PRIORITY:
        return PRIORITY_COLLECTION_SOURCE_TAG
    for source_tag, label in COLLECTION_TAG_LABELS.items():
        if value == label:
            return source_tag
    return value


def is_collection_tag_value(value):
    normalized = normalize_collection_status_value(value)
    if normalized in COLLECTION_TAG_LABELS:
        return True
    return any(str(normalized).startswith(prefix) for prefix in COLLECTION_TAG_PREFIXES)


def collection_tag_predicate_sql(alias="st"):
    tag_expr = f"{alias}.tag" if alias else "tag"
    clauses = [f"{tag_expr} = {sql_text_literal(tag)}" for tag in sorted(COLLECTION_TAG_LABELS)]
    clauses.extend(f"{tag_expr} ILIKE {sql_text_literal(prefix + '%%')}" for prefix in COLLECTION_TAG_PREFIXES)
    return "(" + " OR ".join(dict.fromkeys(clauses)) + ")"


def collection_tag_label_expr(alias="st"):
    tag_expr = f"{alias}.tag" if alias else "tag"
    cases = [
        f"WHEN {tag_expr} = {sql_text_literal(tag)} THEN {sql_text_literal(label)}"
        for tag, label in COLLECTION_TAG_LABELS.items()
    ]
    return "CASE " + " ".join(cases) + f" ELSE {tag_expr} END"


def append_collection_status_filter(filters, values, query, sku_expr, marketplace=None):
    if current_client_is_boiron():
        return
    params = parse_qs(query)
    collection_status_values = collection_status_values_from_params(params)
    if not collection_status_values:
        return
    selected_collections = [
        normalize_collection_status_value(value)
        for value in collection_status_values
        if value != COLLECTION_STATUS_NO_TAG
    ]
    include_no_tag = COLLECTION_STATUS_NO_TAG in collection_status_values
    marketplace = marketplace or marketplace_from_query(query)
    collection_filters = []
    if selected_collections:
        collection_filters.append(
            "EXISTS ("
            f"SELECT 1 FROM public.{SEO_TAGS_TABLE} st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr} AND st.tag = ANY(%s)"
            ")"
        )
        values.extend([marketplace, selected_collections])
    if include_no_tag:
        collection_filters.append(
            "NOT EXISTS ("
            f"SELECT 1 FROM public.{SEO_TAGS_TABLE} st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr} AND {collection_tag_predicate_sql('st')}"
            ")"
        )
        values.append(marketplace)
    if collection_filters:
        filters.append("(" + " OR ".join(collection_filters) + ")")


def append_seo_status_filter(filters, values, query, sku_expr, marketplace=None):
    if current_client_is_boiron():
        return
    params = parse_qs(query)
    seo_status_values = seo_status_values_from_params(params)
    if not seo_status_values:
        return
    seo_status_values = [value for value in seo_status_values if not is_collection_tag_value(value)]
    if not seo_status_values:
        return
    selected_tags = [value for value in seo_status_values if value != SEO_STATUS_NO_TAG]
    include_no_tag = SEO_STATUS_NO_TAG in seo_status_values
    marketplace = marketplace or marketplace_from_query(query)
    seo_filters = []
    if selected_tags:
        seo_filters.append(
            "EXISTS ("
            f"SELECT 1 FROM public.{SEO_TAGS_TABLE} st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr} AND st.tag = ANY(%s)"
            ")"
        )
        values.extend([marketplace, selected_tags])
    if include_no_tag:
        seo_filters.append(
            "NOT EXISTS ("
            f"SELECT 1 FROM public.{SEO_TAGS_TABLE} st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr} AND NOT {collection_tag_predicate_sql('st')}"
            ")"
        )
        values.append(marketplace)
    if seo_filters:
        filters.append("(" + " OR ".join(seo_filters) + ")")


def seo_sku_expr_for_mapping(marketplace, value_alias="v", mapping_alias="m"):
    if marketplace == "wb":
        return f"coalesce({mapping_alias}.wb_nmid::text, {value_alias}.artikul_wb::text)"
    return f"coalesce({mapping_alias}.ozon_sku::text, {value_alias}.artikul_wb::text)"


def seo_sku_expr_for_abc_alias(marketplace, alias):
    if marketplace == "wb":
        return f"coalesce({alias}.artikul_wb::text, {alias}.gj_wb_article::text)"
    return f"coalesce({alias}.gj_ozon_sku::text, {alias}.artikul_wb::text)"


def seo_status_join_sql(marketplace, sku_expr="v.sku"):
    return sql.SQL(
        """
        LEFT JOIN (
            SELECT sku, string_agg(DISTINCT tag, ', ' ORDER BY tag) AS seo_status
            FROM public.{table} st
            WHERE marketplace = {marketplace}
              AND NOT {collection_tag_predicate}
            GROUP BY sku
        ) seo ON seo.sku = {sku_expr}
        """
    ).format(
        table=sql.Identifier(SEO_TAGS_TABLE),
        marketplace=sql.Literal(marketplace),
        collection_tag_predicate=sql.SQL(collection_tag_predicate_sql("st")),
        sku_expr=sql.SQL(sku_expr),
    )


def collection_status_join_sql(marketplace, sku_expr="v.sku"):
    return sql.SQL(
        """
        LEFT JOIN (
            SELECT sku, string_agg(DISTINCT {label_expr}, ', ' ORDER BY {label_expr}) AS priority_collection
            FROM public.{table} st
            WHERE marketplace = {marketplace}
              AND {collection_tag_predicate}
            GROUP BY sku
        ) collection ON collection.sku = {sku_expr}
        """
    ).format(
        table=sql.Identifier(SEO_TAGS_TABLE),
        marketplace=sql.Literal(marketplace),
        collection_tag_predicate=sql.SQL(collection_tag_predicate_sql("st")),
        label_expr=sql.SQL(collection_tag_label_expr("st")),
        sku_expr=sql.SQL(sku_expr),
    )


def funnel_join_sql(marketplace, include_seo=False, include_collection=False):
    joins = [mapping_join_for_report("funnel", marketplace)]
    if include_collection:
        joins.append(collection_status_join_sql(marketplace))
    if include_seo:
        joins.append(seo_status_join_sql(marketplace))
    return sql.SQL("").join(joins)


def mapping_select_sql():
    if current_client_key() in MAPPINGLESS_CLIENTS:
        return sql.SQL(
            """
            , NULL::text AS gj_wb_article
            , NULL::text AS gj_ozon_sku
            , NULL::text AS gj_model
            , NULL::text AS assortment_bia
            , NULL::text AS tg
            , NULL::text AS tg_plus
            , NULL::text AS cg
            , NULL::text AS season
            """
        )
    return sql.SQL(
        """
        , m.wb_article AS gj_wb_article
        , m.ozon_sku AS gj_ozon_sku
        , m.gj_model
        , m.assortment_bia
        , m.tg
        , m.tg_plus
        , m.cg
        , m.season
        """
    )


def seo_status_select_sql():
    return sql.SQL(", coalesce(seo.seo_status, {no_tag}) AS seo_status").format(no_tag=sql.Literal(NO_TAG_LABEL))


def collection_status_select_sql():
    return sql.SQL(", coalesce(collection.priority_collection, {no_tag}) AS priority_collection").format(no_tag=sql.Literal(NO_TAG_LABEL))


def repeated_filter_values(params, param):
    values = []
    for raw_value in params.get(param, []):
        value = str(raw_value).strip()
        if value and value not in values:
            values.append(value)
    return values


def mapping_filter_sql(params, values, alias="m"):
    if current_client_is_boiron() or current_client_key() in MAPPINGLESS_CLIENTS:
        return []
    filters = []
    for param, column in MAPPING_FILTERS:
        selected = repeated_filter_values(params, param)
        if not selected:
            continue
        if param == "gj_model":
            if len(selected) == 1:
                filters.append(f"coalesce({alias}.{column}, '') ILIKE %s")
                values.append(f"%{selected[0]}%")
            else:
                filters.append(f"coalesce({alias}.{column}, '') ILIKE ANY(%s)")
                values.append([f"%{value}%" for value in selected])
        else:
            if len(selected) == 1:
                filters.append(f"{alias}.{column} = %s")
                values.append(selected[0])
            else:
                filters.append(f"{alias}.{column} = ANY(%s)")
                values.append(selected)
    return filters


def sportmaster_outer_sku_expr(alias=None):
    return f"{alias}.artikul_wb" if alias else "artikul_wb"


def sportmaster_attr_exists_sql(marketplace, value_operator="= %s", source_alias="sp"):
    if marketplace == "ozon":
        return f"""
            EXISTS (
                SELECT 1
                FROM public.ozon_cat_product_attributes spa
                LEFT JOIN public.ozon_cat_category_attributes sca
                    ON sca.category_id = {source_alias}.category_id
                   AND sca.attribute_id = spa.attribute_id
                LEFT JOIN public.ozon_cat_common_attributes scm
                    ON scm.attribute_id = spa.attribute_id
                WHERE spa.product_id = {source_alias}.product_id
                  AND coalesce(sca.attribute_name, scm.attribute_name) = ANY(%s)
                  AND nullif(trim(spa.value_text), '') IS NOT NULL
                  AND spa.value_text {value_operator}
            )
        """
    return f"""
        EXISTS (
            SELECT 1
            FROM public.product_attributes spa
            LEFT JOIN public.category_attributes sca
                ON sca.category_id = {source_alias}.category_id
               AND sca.attribute_id = spa.attribute_id
            LEFT JOIN public.common_attributes scm
                ON scm.attribute_id = spa.attribute_id
            WHERE spa.product_id = {source_alias}.product_id
              AND coalesce(sca.attribute_name, scm.attribute_name) = ANY(%s)
              AND nullif(trim(spa.value_text), '') IS NOT NULL
              AND spa.value_text {value_operator}
        )
    """


def sportmaster_param_values(params, param):
    values = []
    for raw in params.get(param, []):
        value = str(raw).strip()
        if value and value not in values:
            values.append(value)
    return values


def sportmaster_category_param_values(params, param):
    return sportmaster_param_values(params, param)


def sportmaster_source_filter_conditions(params, values, marketplace, exclude_param=None, source_alias="sp", include_categories=True):
    source_conditions = []
    category_expr = (
        f"{source_alias}.category_name"
        if marketplace == "ozon"
        else f"coalesce(nullif({source_alias}.seller_category_name, ''), {source_alias}.kategoriya_prodavtsa)"
    )
    base_views = abc_base_views_for_marketplace(marketplace)
    sku_expr = f"{source_alias}.sku::text" if marketplace == "ozon" else f"{source_alias}.artikul_prodavtsa::text"

    def base_sku_scope(predicate_column, predicate):
        return """
            {sku_expr} IN (
                SELECT artikul_wb::text FROM public.{stock_view} WHERE {predicate_column} {predicate}
                UNION
                SELECT artikul_wb::text FROM public.{order_view} WHERE {predicate_column} {predicate}
            )
        """.format(
            sku_expr=sku_expr,
            stock_view=base_views["stock"],
            order_view=base_views["orders"],
            predicate_column=predicate_column,
            predicate=predicate,
        )

    if include_categories:
        categories = sportmaster_category_param_values(params, "categories")
        exact_category = params.get("category_exact", [""])[0].strip()
        category = params.get("category", [""])[0].strip()
        if categories:
            source_conditions.append(base_sku_scope("category_name", "= ANY(%s)"))
            values.append(categories)
            values.append(categories)
        elif exact_category:
            source_conditions.append(base_sku_scope("category_name", "= %s"))
            values.append(exact_category)
            values.append(exact_category)
        elif category:
            source_conditions.append(base_sku_scope("category_name", "ILIKE %s"))
            values.append(f"%{category}%")
            values.append(f"%{category}%")

    for param, config in SPORTMASTER_FILTERS.items():
        if param == exclude_param:
            continue
        selected_values = sportmaster_param_values(params, param)
        if not selected_values:
            continue
        if config["kind"] == "subcategory":
            source_conditions.append(base_sku_scope("coalesce(nullif(subcategory_name, ''), category_name)", "= ANY(%s)"))
            values.append(selected_values)
            values.append(selected_values)
        elif config["kind"] == "brand" and marketplace == "wb":
            source_conditions.append(f"{source_alias}.brend = ANY(%s)")
            values.append(selected_values)
        else:
            if config["kind"] == "attribute_search":
                operator = "ILIKE ANY(%s)"
                selected_payload = [f"%{value}%" for value in selected_values]
            else:
                operator = "= ANY(%s)"
                selected_payload = selected_values
            source_conditions.append(sportmaster_attr_exists_sql(marketplace, value_operator=operator, source_alias=source_alias))
            values.append(list(config["attribute_names"]))
            values.append(selected_payload)
    return source_conditions


def sportmaster_filter_sql(params, values, marketplace, alias=None, sku_expr=None, include_categories=True, exclude_param=None):
    if current_client_key() != "sportmaster":
        return []
    sku_expr = sku_expr or sportmaster_outer_sku_expr(alias)
    filters = []
    source_conditions = []
    if marketplace == "ozon":
        source_table = "public.ozon_cat_products sp"
        source_conditions.append(f"sp.sku::text = {sku_expr}::text")
    else:
        source_table = "public.products sp"
        source_conditions.append(f"sp.artikul_prodavtsa::text = {sku_expr}::text")

    source_conditions.extend(
        sportmaster_source_filter_conditions(
            params,
            values,
            marketplace,
            exclude_param=exclude_param,
            include_categories=include_categories,
        )
    )

    if len(source_conditions) <= 1:
        return filters
    filters.append(
        "EXISTS (SELECT 1 FROM {source_table} WHERE {conditions})".format(
            source_table=source_table,
            conditions=" AND ".join(source_conditions),
        )
    )
    return filters


def ozon_product_attribute_filter_sql(params, values, marketplace, sku_expr):
    if marketplace != "ozon":
        return []
    is_gloria = current_client_key() == "gloria_jeans"
    product_mapping_join = """
                JOIN public.sku_mapping_gj product_mapping
                  ON product_mapping.barcode::text = product_attr_product.artikul::text
    """ if is_gloria else ""
    sku_condition = (
        f"product_mapping.ozon_sku::text = {sku_expr}::text"
        if is_gloria
        else f"product_attr_product.sku::text = {sku_expr}::text"
    )
    filters = []
    for param, config in OZON_PRODUCT_ATTRIBUTE_FILTERS.items():
        selected = repeated_filter_values(params, param)
        if not selected:
            continue
        filters.append(
            f"""
            EXISTS (
                SELECT 1
                FROM public.ozon_cat_products product_attr_product
                {product_mapping_join}
                JOIN public.ozon_cat_product_attributes product_attr
                  ON product_attr.product_id = product_attr_product.product_id
                LEFT JOIN public.ozon_cat_category_attributes category_attr
                  ON category_attr.category_id = product_attr_product.category_id
                 AND category_attr.attribute_id = product_attr.attribute_id
                LEFT JOIN public.ozon_cat_common_attributes common_attr
                  ON common_attr.attribute_id = product_attr.attribute_id
                CROSS JOIN LATERAL regexp_split_to_table(
                    coalesce(product_attr.value_text, ''), ';'
                ) AS split_value(value)
                WHERE {sku_condition}
                  AND coalesce(category_attr.attribute_name, common_attr.attribute_name) = ANY(%s)
                  AND nullif(trim(split_value.value), '') IS NOT NULL
                  AND trim(split_value.value) = ANY(%s)
            )
            """
        )
        values.append(list(config["attribute_names"]))
        values.append(selected)
    return filters


def add_ozon_product_attribute_options(cur, payload, marketplace):
    cache_key = (current_client_key(), marketplace)
    for param in OZON_PRODUCT_ATTRIBUTE_FILTERS:
        payload[f"{param}_values"] = []
    if marketplace != "ozon" or not relation_exists(cur, "ozon_cat_products") or not relation_exists(cur, "ozon_cat_product_attributes"):
        return payload
    now = time.monotonic()
    with OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE_LOCK:
        cached = OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE.get(cache_key)
    if cached and cached["expires_at"] > now:
        for param, options in cached["options"].items():
            payload[f"{param}_values"] = list(options)
        return payload
    attribute_to_params = {}
    for param, config in OZON_PRODUCT_ATTRIBUTE_FILTERS.items():
        for attribute_name in config["attribute_names"]:
            attribute_to_params.setdefault(attribute_name, []).append(param)
    is_gloria = current_client_key() == "gloria_jeans"
    product_mapping_join = """
        JOIN public.sku_mapping_gj product_mapping
          ON product_mapping.barcode::text = product_attr_product.artikul::text
         AND product_mapping.ozon_sku IS NOT NULL
    """ if is_gloria else ""
    cur.execute(
        f"""
        SELECT DISTINCT
            coalesce(category_attr.attribute_name, common_attr.attribute_name) AS attribute_name,
            trim(split_value.value) AS value
        FROM public.ozon_cat_product_attributes product_attr
        JOIN public.ozon_cat_products product_attr_product
          ON product_attr_product.product_id = product_attr.product_id
        {product_mapping_join}
        LEFT JOIN public.ozon_cat_category_attributes category_attr
          ON category_attr.category_id = product_attr_product.category_id
         AND category_attr.attribute_id = product_attr.attribute_id
        LEFT JOIN public.ozon_cat_common_attributes common_attr
          ON common_attr.attribute_id = product_attr.attribute_id
        CROSS JOIN LATERAL regexp_split_to_table(coalesce(product_attr.value_text, ''), ';') AS split_value(value)
        WHERE coalesce(category_attr.attribute_name, common_attr.attribute_name) = ANY(%s)
          AND nullif(trim(split_value.value), '') IS NOT NULL
        ORDER BY attribute_name, value
        """,
        [list(attribute_to_params)],
    )
    option_sets = {param: set() for param in OZON_PRODUCT_ATTRIBUTE_FILTERS}
    for row in cur.fetchall():
        for param in attribute_to_params.get(row["attribute_name"], []):
            option_sets[param].add(row["value"])
    for param, options in option_sets.items():
        payload[f"{param}_values"] = sorted(options, key=lambda value: value.casefold())
    with OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE_LOCK:
        OZON_PRODUCT_ATTRIBUTE_OPTIONS_CACHE[cache_key] = {
            "expires_at": now + OZON_PRODUCT_ATTRIBUTE_OPTIONS_TTL_SECONDS,
            "options": {param: tuple(payload[f"{param}_values"]) for param in OZON_PRODUCT_ATTRIBUTE_FILTERS},
        }
    return payload


def sql_text_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def sql_text_array_literal(values):
    return "ARRAY[" + ", ".join(sql_text_literal(value) for value in values) + "]::text[]"


def has_mapping_filters(query):
    params = parse_qs(query)
    return any(repeated_filter_values(params, param) for param, _ in MAPPING_FILTERS)


def quoted_column(column, alias=None):
    safe_column = column.replace('"', '""')
    if alias:
        safe_alias = alias.replace('"', '""')
        return f'"{safe_alias}"."{safe_column}"'
    return f'"{safe_column}"'


def parsed_column_filters(query):
    params = parse_qs(query)
    raw = params.get("column_filters", [""])[0].strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [
            {"column": column, **config}
            for column, config in payload.items()
            if isinstance(config, dict)
        ]
    if not isinstance(payload, list):
        return []
    filters = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        op = str(item.get("op") or "").strip()
        value = str(item.get("value") or "").strip()
        if column and op and value:
            filters.append({"column": column, "op": op, "value": value})
    return filters


def column_filters_sql(query, columns, alias=None):
    allowed = set(columns)
    filters = []
    values = []
    for item in parsed_column_filters(query):
        column = item["column"]
        if column not in allowed:
            continue
        op = item["op"]
        value = item["value"]
        expr = quoted_column(column, alias=alias)
        is_numeric = column in NUMERIC_FIELDS
        if op == "contains":
            filters.append(f"coalesce({expr}::text, '') ILIKE %s")
            values.append(f"%{value}%")
        elif op == "not_contains":
            filters.append(f"coalesce({expr}::text, '') NOT ILIKE %s")
            values.append(f"%{value}%")
        elif op == "eq":
            filters.append(f"{expr} = %s" if is_numeric else f"coalesce({expr}::text, '') = %s")
            values.append(to_float(value) if is_numeric else value)
        elif op == "neq":
            filters.append(f"{expr} <> %s" if is_numeric else f"coalesce({expr}::text, '') <> %s")
            values.append(to_float(value) if is_numeric else value)
        elif op in {"gt", "gte", "lt", "lte"}:
            operator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            if is_numeric:
                filters.append(f"coalesce({expr}, 0)::numeric {operator} %s")
                values.append(to_float(value))
            else:
                filters.append(f"{expr} {operator} %s")
                values.append(value)
    return filters, values


def append_column_filters(where, values, query, columns, alias=None):
    filters, filter_values = column_filters_sql(query, columns, alias=alias)
    if not filters:
        return where, values
    connector = " AND " if where else " WHERE "
    return where + connector + " AND ".join(filters), values + filter_values


def outer_column_filter_clause(query, columns, alias="x"):
    filters, values = column_filters_sql(query, columns, alias=alias)
    return (" WHERE " + " AND ".join(filters) if filters else ""), values


def filters_from_query(query, include_abc=True, include_mapping=False, seo_marketplace=None, seo_sku_expr=None, relation_alias=None):
    params = parse_qs(query)
    marketplace = marketplace_from_query(query)
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    exact_category = params.get("category_exact", [""])[0].strip()
    category = params.get("category", [""])[0].strip()
    if categories:
        filters.append("category_name = ANY(%s)")
        values.append(categories)
    elif exact_category:
        filters.append("category_name = %s")
        values.append(exact_category)
    elif category:
        filters.append("category_name ILIKE %s")
        values.append(f"%{category}%")

    if include_abc:
        for name in ("abc_orders", "abc_sales", "abc_stock", "abc_combined"):
            selected = params.get(name, [""])[0].strip()
            if selected:
                filters.append(f"{name} = %s")
                values.append(selected)

    if include_mapping:
        filters.extend(mapping_filter_sql(params, values))
        if relation_alias:
            product = params.get("product", [""])[0].strip()
            if product:
                filters.append(f"coalesce({relation_alias}.naimenovanie, '') = %s")
                values.append(product)

            article = params.get("article", [""])[0].strip()
            if article:
                filters.append(
                    "("
                    f"coalesce({relation_alias}.artikul_wb::text, '') ILIKE %s OR "
                    "coalesce(m.wb_article::text, '') ILIKE %s OR "
                    "coalesce(m.ozon_sku::text, '') ILIKE %s OR "
                    f"coalesce({relation_alias}.naimenovanie, '') ILIKE %s"
                    ")"
                )
                values.extend([f"%{article}%"] * 4)
    filters.extend(
        sportmaster_filter_sql(
            params,
            values,
            marketplace,
            alias=relation_alias,
            sku_expr=sportmaster_outer_sku_expr(relation_alias) if relation_alias else None,
        )
    )
    if seo_marketplace and seo_sku_expr:
        filters.extend(ozon_product_attribute_filter_sql(params, values, seo_marketplace, seo_sku_expr))
        append_collection_status_filter(filters, values, query, seo_sku_expr, marketplace=seo_marketplace)
        append_seo_status_filter(filters, values, query, seo_sku_expr, marketplace=seo_marketplace)

    where = " WHERE " + " AND ".join(filters) if filters else ""
    return where, values


def boiron_brand_values_from_params(params):
    values = []
    for raw_value in params.get(BOIRON_BRAND_PARAM, []):
        values.extend(value.strip() for value in str(raw_value).split(","))
    return [value for value in dict.fromkeys(values) if value]


def adv_filters_from_query(query):
    params = parse_qs(query)
    marketplace = adv_marketplace_from_query(query)
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    if categories:
        filters.append("category_name = ANY(%s)")
        values.append(categories)

    inventory_skus = repeated_filter_values(params, "inventory_skus")
    if inventory_skus:
        filters.append("coalesce(v.sku, '') = ANY(%s)")
        values.append(inventory_skus)

    brand_names = boiron_brand_values_from_params(params)
    if current_client_is_boiron() and brand_names:
        filters.append("brand_name = ANY(%s)")
        values.append(brand_names)

    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        filters.append("report_date >= %s")
        values.append(date_from)
    if date_to:
        filters.append("report_date <= %s")
        values.append(date_to)

    article = params.get("article", [""])[0].strip()
    if article:
        filters.append(
            "(coalesce(ozon_marketplace_article, '') ILIKE %s OR "
            "coalesce(seller_article, '') ILIKE %s OR "
            "coalesce(product_artikul, '') ILIKE %s OR "
            "coalesce(sku, '') ILIKE %s)"
        )
        values.extend([f"%{article}%"] * 4)

    product = params.get("product", [""])[0].strip()
    if product:
        filters.append("coalesce(product_name, '') ILIKE %s")
        values.append(f"%{product}%")

    campaign_ids = repeated_filter_values(params, "adv_campaign_id")
    if marketplace == "ozon" and campaign_ids:
        filters.append("coalesce(to_jsonb(v)->>'campaign_id', '') = ANY(%s)")
        values.append(campaign_ids)

    if not current_client_is_boiron():
        filters.extend(mapping_filter_sql(params, values))
        filters.extend(
            ozon_product_attribute_filter_sql(
                params,
                values,
                marketplace,
                "v.ozon_marketplace_article",
            )
        )
        append_collection_status_filter(filters, values, query, "v.sku", marketplace=marketplace)
        append_seo_status_filter(filters, values, query, "v.sku", marketplace=marketplace)

    where = " WHERE " + " AND ".join(filters) if filters else ""
    return where, values


def funnel_sku_counts_from_adv_query(cur, query, marketplace, by_date=False):
    if current_client_is_boiron():
        return {} if by_date else 0
    funnel_view = funnel_view_for_marketplace(marketplace, "daily")
    where, values = funnel_filters_from_query(query)
    sku_expr = sql.SQL(
        "coalesce(nullif(v.sku, ''), nullif(v.barcode, ''), "
        "nullif(v.seller_article, ''), nullif(v.product_artikul, ''))"
    )
    if by_date:
        query_sql = sql.SQL(
            """
            SELECT
                v.report_date,
                count(DISTINCT {sku_expr}) AS total_sku_count
            FROM public.{view} v
            {join}
            {where}
            GROUP BY v.report_date
            ORDER BY v.report_date
            """
        ).format(
            view=sql.Identifier(funnel_view),
            join=mapping_join_for_report("funnel", marketplace),
            where=sql.SQL(where),
            sku_expr=sku_expr,
        )
        cur.execute(query_sql, values)
        return {
            str(normalize_value(row.get("report_date")) or ""): to_float(row.get("total_sku_count"))
            for row in cur.fetchall()
        }
    query_sql = sql.SQL(
        """
        SELECT count(DISTINCT {sku_expr}) AS total_sku_count
        FROM public.{view} v
        {join}
        {where}
        """
    ).format(
        view=sql.Identifier(funnel_view),
        join=mapping_join_for_report("funnel", marketplace),
        where=sql.SQL(where),
        sku_expr=sku_expr,
    )
    cur.execute(query_sql, values)
    row = cur.fetchone()
    return int(to_float(row.get("total_sku_count") if row else 0))


def media_adv_filters_from_query(query):
    params = parse_qs(query)
    marketplace = marketplace_from_query(query)
    is_wb_media = marketplace == "wb"
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    if categories:
        filters.append("category_name = ANY(%s)")
        values.append(categories)

    inventory_skus = repeated_filter_values(params, "inventory_skus")
    if inventory_skus:
        filters.append("coalesce(v.sku, '') = ANY(%s)")
        values.append(inventory_skus)

    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        filters.append("report_date >= %s")
        values.append(date_from)
    if date_to:
        filters.append("report_date <= %s")
        values.append(date_to)

    media_statuses = [value.strip() for value in params.get("media_status", []) if value.strip()]
    if media_statuses:
        filters.append("campaign_status = ANY(%s)")
        values.append(media_statuses)

    media_segments = [value.strip() for value in params.get("media_segment", []) if value.strip()]
    if media_segments:
        filters.append("campaign_segment = ANY(%s)")
        values.append(media_segments)

    media_campaign_ids = [value.strip() for value in params.get("media_campaign_id", []) if value.strip()]
    if media_campaign_ids:
        filters.append("campaign_id = ANY(%s)")
        values.append(media_campaign_ids)

    if is_wb_media:
        media_group_ids = [value.strip() for value in params.get("media_group_id", []) if value.strip()]
        if media_group_ids:
            filters.append("group_id = ANY(%s)")
            values.append(media_group_ids)

        media_creative_ids = [value.strip() for value in params.get("media_creative_id", []) if value.strip()]
        if media_creative_ids:
            filters.append("creative_id = ANY(%s)")
            values.append(media_creative_ids)

    article = params.get("article", [""])[0].strip()
    if article:
        search_columns = ["campaign_id", "campaign_name", "campaign_segment", "promoted_category"]
        if is_wb_media:
            search_columns[1:1] = ["group_id", "creative_id"]
            search_columns.insert(4, "entity_name")
        filters.append(
            "(" + " OR ".join(f"coalesce({column}, '') ILIKE %s" for column in search_columns) + ")"
        )
        values.extend([f"%{article}%"] * len(search_columns))

    product = params.get("product", [""])[0].strip()
    if product:
        filters.append("coalesce(campaign_name, '') ILIKE %s")
        values.append(f"%{product}%")

    where = " WHERE " + " AND ".join(filters) if filters else ""
    return where, values


def funnel_filters_from_query(query):
    params = parse_qs(query)
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    if categories:
        filters.append("category_name = ANY(%s)")
        values.append(categories)

    inventory_skus = repeated_filter_values(params, "inventory_skus")
    if inventory_skus:
        filters.append("coalesce(v.sku, '') = ANY(%s)")
        values.append(inventory_skus)

    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        filters.append("report_date >= %s")
        values.append(date_from)
    if date_to:
        filters.append("report_date <= %s")
        values.append(date_to)

    article = params.get("article", [""])[0].strip()
    if article:
        filters.append(
            "(coalesce(seller_article, '') ILIKE %s OR "
            "coalesce(product_artikul, '') ILIKE %s OR "
            "coalesce(sku, '') ILIKE %s OR "
            "coalesce(barcode, '') ILIKE %s)"
        )
        values.extend([f"%{article}%"] * 4)

    product = params.get("product", [""])[0].strip()
    if product:
        filters.append("coalesce(product_name, '') ILIKE %s")
        values.append(f"%{product}%")

    append_collection_status_filter(filters, values, query, "v.sku")
    append_seo_status_filter(filters, values, query, "v.sku")

    filters.extend(mapping_filter_sql(params, values))
    filters.extend(ozon_product_attribute_filter_sql(params, values, marketplace_from_query(query), "v.sku"))

    where = " WHERE " + " AND ".join(filters) if filters else ""
    return where, values


def ozon_abc_date_and_mapping_filters(query, alias):
    params = parse_qs(query)
    filters = []
    values = []

    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if alias == "v":
        if date_from:
            filters.append("v.report_date >= %s")
            values.append(date_from)
        if date_to:
            filters.append("v.report_date <= %s")
            values.append(date_to)

    filters.extend(mapping_filter_sql(params, values, alias="mf" if alias == "v" else "ms"))
    return " AND ".join(filters) if filters else "TRUE", values


def ozon_abc_mv_filters(query, alias, include_date=False):
    params = parse_qs(query)
    marketplace = marketplace_from_query(query)
    filters = []
    values = []

    if include_date:
        date_from = params.get("date_from", [""])[0].strip()
        date_to = params.get("date_to", [""])[0].strip()
        if date_from:
            filters.append(f"{alias}.report_date >= %s")
            values.append(date_from)
        if date_to:
            filters.append(f"{alias}.report_date <= %s")
            values.append(date_to)

    sportmaster_source_category_applied = current_client_key() == "sportmaster" and abc_category_level_from_query(query) != "category"
    if sportmaster_source_category_applied:
        source_category_filters, source_category_values = abc_source_category_filters_from_query(query, alias)
        filters.extend(source_category_filters)
        values.extend(source_category_values)
    sportmaster_subcategories = sportmaster_param_values(params, "sm_subcategory") if current_client_key() == "sportmaster" else []
    if sportmaster_subcategories:
        filters.append(f"coalesce(nullif({alias}.subcategory_name, ''), {alias}.category_name) = ANY(%s)")
        values.append(sportmaster_subcategories)

    product = params.get("product", [""])[0].strip()
    if product:
        filters.append(f"coalesce({alias}.naimenovanie, '') = %s")
        values.append(product)

    article = params.get("article", [""])[0].strip()
    if article:
        filters.append(
            "("
            f"coalesce({alias}.artikul_wb::text, '') ILIKE %s OR "
            f"coalesce({alias}.gj_wb_article::text, '') ILIKE %s OR "
            f"coalesce({alias}.gj_ozon_sku::text, '') ILIKE %s OR "
            f"coalesce({alias}.naimenovanie, '') ILIKE %s"
            ")"
        )
        values.extend([f"%{article}%"] * 4)

    filters.extend(mapping_filter_sql(params, values, alias=alias))
    filters.extend(ozon_product_attribute_filter_sql(params, values, marketplace, f"{alias}.artikul_wb"))
    filters.extend(
        sportmaster_filter_sql(
            params,
            values,
            marketplace,
            alias=alias,
            include_categories=not sportmaster_source_category_applied,
            exclude_param="sm_subcategory" if sportmaster_subcategories else None,
        )
    )
    append_collection_status_filter(
        filters,
        values,
        query,
        seo_sku_expr_for_abc_alias(marketplace, alias),
        marketplace=marketplace,
    )
    append_seo_status_filter(
        filters,
        values,
        query,
        seo_sku_expr_for_abc_alias(marketplace, alias),
        marketplace=marketplace,
    )
    return " AND ".join(filters) if filters else "TRUE", values


def query_without_category_selection(query):
    params = parse_qs(query)
    for key in ("categories", "category", "category_exact"):
        params.pop(key, None)
    return urlencode(params, doseq=True)


def abc_source_category_filters_from_query(query, alias):
    params = parse_qs(query)
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    exact_category = params.get("category_exact", [""])[0].strip()
    category = params.get("category", [""])[0].strip()
    if categories:
        filters.append(f"{alias}.category_name = ANY(%s)")
        values.append(categories)
    elif exact_category:
        filters.append(f"{alias}.category_name = %s")
        values.append(exact_category)
    elif category:
        filters.append(f"{alias}.category_name ILIKE %s")
        values.append(f"%{category}%")

    return filters, values


def ozon_abc_outer_filters_from_query(query, include_product=True):
    params = parse_qs(query)
    filters = []
    values = []

    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    exact_category = params.get("category_exact", [""])[0].strip()
    category = params.get("category", [""])[0].strip()
    apply_category_filter_to_group = not (
        client_from_query(query) == "sportmaster" and abc_category_level_from_query(query) != "category"
    )
    if apply_category_filter_to_group:
        if categories:
            filters.append("category_name = ANY(%s)")
            values.append(categories)
        elif exact_category:
            filters.append("category_name = %s")
            values.append(exact_category)
        elif category:
            filters.append("category_name ILIKE %s")
            values.append(f"%{category}%")

    for name in ("abc_orders", "abc_sales", "abc_stock", "abc_combined"):
        selected = params.get(name, [""])[0].strip()
        if selected:
            filters.append(f"{name} = %s")
            values.append(selected)

    if include_product:
        product = params.get("product", [""])[0].strip()
        if product:
            filters.append("coalesce(naimenovanie, '') = %s")
            values.append(product)

        article = params.get("article", [""])[0].strip()
        if article:
            filters.append(
                "(coalesce(artikul_wb::text, '') ILIKE %s OR "
                "coalesce(gj_wb_article::text, '') ILIKE %s OR "
                "coalesce(gj_ozon_sku::text, '') ILIKE %s OR "
                "coalesce(naimenovanie, '') ILIKE %s)"
            )
            values.extend([f"%{article}%"] * 4)

    return " WHERE " + " AND ".join(filters) if filters else "", values


def abc_category_level_from_query(query):
    params = parse_qs(query)
    level = params.get("category_level", ["category"])[0].strip().lower()
    if client_from_query(query) == "sportmaster" and level in ABC_SPORTMASTER_GROUP_DIMENSIONS:
        return level
    if level == "subcategory" and client_from_query(query) == "sportmaster":
        return "subcategory"
    return "category"


def abc_category_group_expr(query, alias):
    if abc_category_level_from_query(query) == "subcategory":
        return f"coalesce(nullif({alias}.subcategory_name, ''), {alias}.category_name)"
    return f"{alias}.category_name"


def abc_category_title(level):
    return ABC_SPORTMASTER_GROUP_DIMENSIONS.get(level, ABC_SPORTMASTER_GROUP_DIMENSIONS["category"])["label"]


def abc_category_noun(level):
    return ABC_SPORTMASTER_GROUP_DIMENSIONS.get(level, ABC_SPORTMASTER_GROUP_DIMENSIONS["category"])["noun"]


def sportmaster_group_dimension_cte(query, marketplace):
    dimension = abc_category_level_from_query(query)
    if client_from_query(query) != "sportmaster" or dimension in {"category", "subcategory"}:
        return ""
    config = ABC_SPORTMASTER_GROUP_DIMENSIONS.get(dimension)
    if not config:
        return ""
    fallback = sql_text_literal(config["fallback"])
    if marketplace == "wb" and dimension == "brand":
        return f"""
        sportmaster_group_dimension AS (
            SELECT
                sp.artikul_prodavtsa::text AS sku,
                coalesce(nullif(max(sp.brend), ''), {fallback}) AS group_value
            FROM public.products sp
            GROUP BY sp.artikul_prodavtsa
        ),
        """
    filter_key = config.get("filter")
    attr_names = SPORTMASTER_FILTERS.get(filter_key, {}).get("attribute_names", ())
    attr_array = sql_text_array_literal(attr_names)
    if marketplace == "ozon":
        return f"""
        sportmaster_group_dimension AS (
            SELECT
                sp.sku::text AS sku,
                coalesce(
                    nullif(min(spa.value_text) FILTER (
                        WHERE coalesce(sca.attribute_name, scm.attribute_name) = ANY({attr_array})
                          AND nullif(trim(spa.value_text), '') IS NOT NULL
                    ), ''),
                    {fallback}
                ) AS group_value
            FROM public.ozon_cat_products sp
            LEFT JOIN public.ozon_cat_product_attributes spa
                ON spa.product_id = sp.product_id
            LEFT JOIN public.ozon_cat_category_attributes sca
                ON sca.category_id = sp.category_id
               AND sca.attribute_id = spa.attribute_id
            LEFT JOIN public.ozon_cat_common_attributes scm
                ON scm.attribute_id = spa.attribute_id
            GROUP BY sp.sku
        ),
        """
    return f"""
        sportmaster_group_dimension AS (
            SELECT
                sp.artikul_prodavtsa::text AS sku,
                coalesce(
                    nullif(min(spa.value_text) FILTER (
                        WHERE coalesce(sca.attribute_name, scm.attribute_name) = ANY({attr_array})
                          AND nullif(trim(spa.value_text), '') IS NOT NULL
                    ), ''),
                    {fallback}
                ) AS group_value
            FROM public.products sp
            LEFT JOIN public.product_attributes spa
                ON spa.product_id = sp.product_id
            LEFT JOIN public.category_attributes sca
                ON sca.category_id = sp.category_id
               AND sca.attribute_id = spa.attribute_id
            LEFT JOIN public.common_attributes scm
                ON scm.attribute_id = spa.attribute_id
            GROUP BY sp.artikul_prodavtsa
        ),
        """


def abc_category_group_parts(query, alias, dimension_alias):
    dimension = abc_category_level_from_query(query)
    if dimension in {"category", "subcategory"}:
        return "", abc_category_group_expr(query, alias)
    fallback = sql_text_literal(
        ABC_SPORTMASTER_GROUP_DIMENSIONS.get(dimension, ABC_SPORTMASTER_GROUP_DIMENSIONS["category"]).get("fallback", "Без значения")
    )
    return (
        f"LEFT JOIN sportmaster_group_dimension {dimension_alias} ON {dimension_alias}.sku = {alias}.artikul_wb::text",
        f"coalesce({dimension_alias}.group_value, {fallback})",
    )


def ozon_abc_category_sql(query, outer_where="", marketplace="ozon"):
    base_views = abc_base_views_for_marketplace(marketplace)
    order_view = base_views["orders"]
    stock_view = base_views["stock"]
    order_filter, order_values = ozon_abc_mv_filters(query, "o", include_date=True)
    stock_filter, stock_values = ozon_abc_mv_filters(query, "s")
    group_dimension_cte = sportmaster_group_dimension_cte(query, marketplace)
    stock_group_join, stock_category_expr = abc_category_group_parts(query, "s", "sd_s")
    order_group_join, order_category_expr = abc_category_group_parts(query, "o", "sd_o")
    attribute_product_ctes = ""
    attribute_values = []
    attrs_sql = """
        SELECT
            trim(both from category) AS category_name,
            count(DISTINCT attribute_name) AS category_attribute_count
        FROM public.ozon_attribute_definitions
        GROUP BY trim(both from category)
    """
    if current_client_key() == "sportmaster" and marketplace == "ozon":
        attribute_product_ctes = f"""
        stock_products AS (
            SELECT
                {stock_category_expr} AS category_name,
                s.artikul_wb::text AS artikul_wb
            FROM public.{stock_view} s
            {stock_group_join}
            WHERE {stock_filter}
            GROUP BY {stock_category_expr}, s.artikul_wb
        ),
        order_products AS (
            SELECT
                {order_category_expr} AS category_name,
                o.artikul_wb::text AS artikul_wb
            FROM public.{order_view} o
            {order_group_join}
            WHERE {order_filter}
            GROUP BY {order_category_expr}, o.artikul_wb
        ),
        group_products AS (
            SELECT category_name, artikul_wb FROM stock_products
            UNION
            SELECT category_name, artikul_wb FROM order_products
        ),
        """
        attribute_values = stock_values + order_values
        attrs_sql = """
        SELECT
            gp.category_name,
            count(DISTINCT coalesce(nullif(sca.attribute_name, ''), nullif(scm.attribute_name, ''), spa.attribute_id::text)) AS category_attribute_count
        FROM group_products gp
        JOIN public.ozon_cat_products sp
            ON sp.sku = gp.artikul_wb
        JOIN public.ozon_cat_product_attributes spa
            ON spa.product_id = sp.product_id
        LEFT JOIN public.ozon_cat_category_attributes sca
            ON sca.category_id = sp.category_id
           AND sca.attribute_id = spa.attribute_id
        LEFT JOIN public.ozon_cat_common_attributes scm
            ON scm.attribute_id = spa.attribute_id
        WHERE nullif(trim(spa.value_text), '') IS NOT NULL
        GROUP BY gp.category_name
        """
    if marketplace == "wb":
        attrs_sql = """
            SELECT
                category_name,
                0::bigint AS category_attribute_count
            FROM public.mv_wb_abc_product_stock_base
            GROUP BY category_name
        """
    query_text = f"""
        WITH {group_dimension_cte}{attribute_product_ctes}stock_products_for_count AS (
            SELECT {stock_category_expr} AS category_name, s.artikul_wb::text AS sku
            FROM public.{stock_view} s
            {stock_group_join}
            WHERE {stock_filter}
        ),
        order_products_for_count AS (
            SELECT {order_category_expr} AS category_name, o.artikul_wb::text AS sku
            FROM public.{order_view} o
            {order_group_join}
            WHERE {order_filter}
        ),
        category_sku_counts AS (
            SELECT category_name, count(DISTINCT sku) AS sku_count
            FROM (
                SELECT * FROM stock_products_for_count
                UNION ALL
                SELECT * FROM order_products_for_count
            ) products
            GROUP BY category_name
        ),
        stock AS (
            SELECT
                {stock_category_expr} AS category_name,
                count(DISTINCT s.artikul_wb) AS sku_count,
                coalesce(sum(s.total_stock_qty), 0)::numeric AS total_stock_qty
            FROM public.{stock_view} s
            {stock_group_join}
            WHERE {stock_filter}
            GROUP BY {stock_category_expr}
        ),
        orders AS (
            SELECT
                {order_category_expr} AS category_name,
                count(DISTINCT o.artikul_wb) AS ordered_sku_count,
                coalesce(sum(o.zakazano_sht), 0)::numeric AS zakazano_sht,
                coalesce(sum(o.zakazano_rub), 0)::numeric AS zakazano_rub
            FROM public.{order_view} o
            {order_group_join}
            WHERE {order_filter}
            GROUP BY {order_category_expr}
        ),
        attrs AS (
            {attrs_sql}
        ),
        base AS (
            SELECT
                coalesce(o.category_name, s.category_name) AS category_name,
                coalesce(s.total_stock_qty, 0::numeric) AS total_stock_qty,
                coalesce(c.sku_count, 0) AS sku_count,
                coalesce(a.category_attribute_count, 0) AS category_attribute_count,
                coalesce(o.zakazano_sht, 0::numeric) AS zakazano_sht,
                coalesce(o.zakazano_rub, 0::numeric) AS zakazano_rub
            FROM orders o
            FULL JOIN stock s ON s.category_name = o.category_name
            LEFT JOIN category_sku_counts c ON c.category_name = coalesce(o.category_name, s.category_name)
            LEFT JOIN attrs a ON a.category_name = coalesce(o.category_name, s.category_name)
        ),
        totals AS (
            SELECT
                coalesce(sum(zakazano_sht), 0::numeric) AS total_orders_qty,
                coalesce(sum(zakazano_rub), 0::numeric) AS total_orders_rub,
                coalesce(sum(total_stock_qty), 0::numeric) AS total_stock
            FROM base
        ),
        ranked AS (
            SELECT
                b.category_name,
                b.total_stock_qty,
                b.sku_count,
                b.category_attribute_count,
                b.zakazano_sht,
                b.zakazano_rub,
                CASE WHEN t.total_orders_qty > 0 THEN round(b.zakazano_sht / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_share_pct,
                CASE WHEN t.total_orders_qty > 0 THEN round(sum(b.zakazano_sht) OVER (ORDER BY b.zakazano_sht DESC NULLS LAST, b.category_name) / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_cumulative_pct,
                CASE WHEN t.total_orders_rub > 0 THEN round(b.zakazano_rub / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_share_pct,
                CASE WHEN t.total_orders_rub > 0 THEN round(sum(b.zakazano_rub) OVER (ORDER BY b.zakazano_rub DESC NULLS LAST, b.category_name) / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_cumulative_pct,
                CASE WHEN t.total_stock > 0 THEN round(b.total_stock_qty / t.total_stock * 100, 4) ELSE 0 END AS stock_share_pct,
                CASE WHEN t.total_stock > 0 THEN round(sum(b.total_stock_qty) OVER (ORDER BY b.total_stock_qty DESC NULLS LAST, b.category_name) / t.total_stock * 100, 4) ELSE 0 END AS stock_cumulative_pct
            FROM base b
            CROSS JOIN totals t
        ),
        classified AS (
            SELECT
                *,
                CASE
                    WHEN zakazano_sht = 0 THEN 'Без ABC'
                    WHEN orders_cumulative_pct <= 80 THEN 'A'
                    WHEN orders_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_orders,
                CASE
                    WHEN zakazano_rub = 0 THEN 'Без ABC'
                    WHEN sales_cumulative_pct <= 80 THEN 'A'
                    WHEN sales_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_sales,
                CASE
                    WHEN total_stock_qty = 0 THEN 'Без ABC'
                    WHEN stock_cumulative_pct <= 80 THEN 'A'
                    WHEN stock_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_stock
            FROM ranked
        ),
        final AS (
            SELECT
                category_name,
                total_stock_qty,
                sku_count,
                category_attribute_count,
                zakazano_sht,
                zakazano_rub,
                CASE WHEN zakazano_sht > 0 THEN round(zakazano_rub / zakazano_sht, 2) ELSE 0 END AS avg_price_rub,
                orders_share_pct,
                orders_cumulative_pct,
                abc_orders,
                sales_share_pct,
                sales_cumulative_pct,
                abc_sales,
                stock_share_pct,
                stock_cumulative_pct,
                abc_stock,
                CASE
                    WHEN abc_orders = 'Без ABC' OR abc_sales = 'Без ABC' OR abc_stock = 'Без ABC' THEN 'Без ABC'
                    ELSE concat(abc_orders, abc_sales, abc_stock)
                END AS abc_combined
            FROM classified
        )
        SELECT *
        FROM final
        {outer_where}
    """
    return query_text, attribute_values + stock_values + order_values + stock_values + order_values


def ozon_abc_product_sql(query, outer_where="", marketplace="ozon"):
    base_views = abc_base_views_for_marketplace(marketplace)
    order_view = base_views["orders"]
    stock_view = base_views["stock"]
    order_filter, order_values = ozon_abc_mv_filters(query, "o", include_date=True)
    stock_filter, stock_values = ozon_abc_mv_filters(query, "s")
    group_dimension_cte = sportmaster_group_dimension_cte(query, marketplace)
    stock_group_join, stock_category_expr = abc_category_group_parts(query, "s", "sd_s")
    order_group_join, order_category_expr = abc_category_group_parts(query, "o", "sd_o")
    wb_sku_map_cte = ""
    stock_sku_expr = "s.artikul_wb"
    stock_key_join = ""
    stock_group_by = "s.artikul_wb"
    if marketplace == "wb":
        wb_sku_map_cte = """
        wb_sku_map AS (
            SELECT stock_key, max(wb_nmid) AS wb_nmid
            FROM (
                SELECT nullif(artikul_prodavtsa, '') AS stock_key, nullif(artikul_wb, '') AS wb_nmid
                FROM public.products
                UNION ALL
                SELECT nullif(artikul_wb, '') AS stock_key, nullif(artikul_wb, '') AS wb_nmid
                FROM public.products
            ) raw
            WHERE stock_key IS NOT NULL
              AND wb_nmid IS NOT NULL
            GROUP BY stock_key
        ),
        """
        stock_sku_expr = "coalesce(wm.wb_nmid, s.artikul_wb)"
        stock_key_join = "LEFT JOIN wb_sku_map wm ON wm.stock_key = s.artikul_wb"
        stock_group_by = "coalesce(wm.wb_nmid, s.artikul_wb)"
    params = parse_qs(query)
    query_client = normalize_client_key(
        (params.get("client") or [current_client_key()])[0]
    )
    adv_filters = []
    adv_values = []
    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        adv_filters.append("v.report_date >= %s")
        adv_values.append(date_from)
    if date_to:
        adv_filters.append("v.report_date <= %s")
        adv_values.append(date_to)
    adv_filter = " AND ".join(adv_filters) if adv_filters else "TRUE"
    adv_cte = f"""
        adv AS (
            SELECT
                v.ozon_marketplace_article AS artikul_wb,
                coalesce(sum(v.impressions), 0)::numeric AS adv_impressions,
                coalesce(sum(v.clicks), 0)::numeric AS adv_clicks,
                coalesce(sum(v.orders_amount_rub), 0)::numeric AS adv_sales_rub,
                coalesce(sum(v.expense_rub), 0)::numeric AS adv_expense_rub
            FROM public.mv_ozon_adv_daily_by_article_category v
            WHERE {adv_filter}
            GROUP BY v.ozon_marketplace_article
        ),
    """
    adv_join = "LEFT JOIN adv a ON a.artikul_wb = coalesce(o.artikul_wb, s.artikul_wb)"
    adv_select = """
                coalesce(a.adv_impressions, 0::numeric) AS adv_impressions,
                coalesce(a.adv_clicks, 0::numeric) AS adv_clicks,
                coalesce(a.adv_sales_rub, 0::numeric) AS adv_sales_rub,
                CASE WHEN coalesce(a.adv_sales_rub, 0::numeric) <> 0
                    THEN round(coalesce(a.adv_expense_rub, 0::numeric) / coalesce(a.adv_sales_rub, 0::numeric) * 100, 2)
                    ELSE 0 END AS adv_acos_pct,
                CASE WHEN coalesce(o.zakazano_rub, 0::numeric) <> 0
                    THEN round(coalesce(a.adv_expense_rub, 0::numeric) / coalesce(o.zakazano_rub, 0::numeric) * 100, 2)
                    ELSE 0 END AS adv_tacos_pct,
    """
    if marketplace != "ozon":
        adv_cte = ""
        adv_join = ""
        adv_select = """
                0::numeric AS adv_impressions,
                0::numeric AS adv_clicks,
                0::numeric AS adv_sales_rub,
                0::numeric AS adv_acos_pct,
                0::numeric AS adv_tacos_pct,
        """
        adv_values = []
    marketplace_sku_expr = "coalesce(o.artikul_wb, s.artikul_wb)"
    if query_client in MAPPINGLESS_CLIENTS:
        dual_sku_mapping_join = ""
        sku_wb_expr = marketplace_sku_expr if marketplace == "wb" else "coalesce(o.gj_wb_article, s.gj_wb_article)"
        sku_ozon_expr = marketplace_sku_expr if marketplace == "ozon" else "coalesce(o.gj_ozon_sku, s.gj_ozon_sku)"
    elif marketplace == "ozon":
        dual_sku_mapping_join = f"""
            LEFT JOIN public.mv_sku_mapping_gj_ozon_sku current_sku_map
              ON current_sku_map.ozon_sku = {marketplace_sku_expr}
            LEFT JOIN public.mv_sku_mapping_gj_wb_article article_sku_map
              ON article_sku_map.wb_article = coalesce(o.gj_wb_article, s.gj_wb_article)
        """
        # Sportmaster's Ozon mapping view intentionally has no wb_nmid column;
        # its WB SKU is resolved through the article mapping view instead.
        sku_wb_expr = (
            "article_sku_map.wb_nmid"
            if query_client == "sportmaster"
            else "coalesce(current_sku_map.wb_nmid, article_sku_map.wb_nmid)"
        )
        sku_ozon_expr = marketplace_sku_expr
    else:
        dual_sku_mapping_join = f"""
            LEFT JOIN public.mv_sku_mapping_gj_wb_nmid current_sku_map
              ON current_sku_map.wb_nmid = {marketplace_sku_expr}
            LEFT JOIN public.mv_sku_mapping_gj_wb_article article_sku_map
              ON article_sku_map.wb_article = coalesce(o.gj_wb_article, s.gj_wb_article)
        """
        sku_wb_expr = marketplace_sku_expr
        sku_ozon_expr = (
            "coalesce(current_sku_map.ozon_sku, "
            "o.gj_ozon_sku, s.gj_ozon_sku, article_sku_map.ozon_sku)"
        )
    seo_sku_expr = "coalesce(gj_ozon_sku::text, artikul_wb::text)" if marketplace == "ozon" else "coalesce(artikul_wb::text, gj_wb_article::text)"
    collection_tag_predicate = collection_tag_predicate_sql("st")
    collection_label_expr = collection_tag_label_expr("st")
    if query_client in MAPPINGLESS_CLIENTS:
        tag_status_select = f"""
                '{NO_TAG_LABEL}'::text AS priority_collection,
                '{NO_TAG_LABEL}'::text AS seo_status,
        """
    else:
        tag_status_select = f"""
                coalesce((
                    SELECT string_agg(DISTINCT {collection_label_expr}, ', ' ORDER BY {collection_label_expr})
                    FROM public.{SEO_TAGS_TABLE} st
                    WHERE st.marketplace = '{marketplace}'
                      AND st.sku = {seo_sku_expr}
                      AND {collection_tag_predicate}
                ), '{NO_TAG_LABEL}') AS priority_collection,
                coalesce((
                    SELECT string_agg(DISTINCT st.tag, ', ' ORDER BY st.tag)
                    FROM public.{SEO_TAGS_TABLE} st
                    WHERE st.marketplace = '{marketplace}'
                      AND st.sku = {seo_sku_expr}
                      AND NOT {collection_tag_predicate}
                ), '{NO_TAG_LABEL}') AS seo_status,
        """
    query_text = f"""
        WITH {group_dimension_cte}{wb_sku_map_cte}stock AS (
            SELECT
                {stock_sku_expr} AS artikul_wb,
                max(s.naimenovanie) AS naimenovanie,
                max({stock_category_expr}) AS category_name,
                coalesce(sum(s.total_stock_qty), 0)::numeric AS total_stock_qty,
                max(s.reyting_kartochki) AS reyting_kartochki,
                max(s.reyting_po_otzyvam) AS reyting_po_otzyvam,
                max(s.gj_wb_article) AS gj_wb_article,
                max(s.gj_ozon_sku) AS gj_ozon_sku,
                max(s.gj_model) AS gj_model,
                max(s.assortment_bia) AS assortment_bia,
                max(s.tg) AS tg,
                max(s.tg_plus) AS tg_plus,
                max(s.cg) AS cg,
                max(s.season) AS season
            FROM public.{stock_view} s
            {stock_group_join}
            {stock_key_join}
            WHERE {stock_filter}
            GROUP BY {stock_group_by}
        ),
        orders AS (
            SELECT
                o.artikul_wb,
                max(o.naimenovanie) AS naimenovanie,
                max({order_category_expr}) AS category_name,
                coalesce(sum(o.zakazano_sht), 0)::numeric AS zakazano_sht,
                coalesce(sum(o.zakazano_rub), 0)::numeric AS zakazano_rub,
                max(o.reyting_kartochki) AS reyting_kartochki,
                max(o.reyting_po_otzyvam) AS reyting_po_otzyvam,
                max(o.gj_wb_article) AS gj_wb_article,
                max(o.gj_ozon_sku) AS gj_ozon_sku,
                max(o.gj_model) AS gj_model,
                max(o.assortment_bia) AS assortment_bia,
                max(o.tg) AS tg,
                max(o.tg_plus) AS tg_plus,
                max(o.cg) AS cg,
                max(o.season) AS season
            FROM public.{order_view} o
            {order_group_join}
            WHERE {order_filter}
            GROUP BY o.artikul_wb
        ),
        {adv_cte}
        base AS (
            SELECT
                coalesce(o.category_name, s.category_name) AS category_name,
                coalesce(o.artikul_wb, s.artikul_wb) AS artikul_wb,
                {sku_wb_expr} AS sku_wb,
                {sku_ozon_expr} AS sku_ozon,
                coalesce(o.naimenovanie, s.naimenovanie) AS naimenovanie,
                coalesce(s.total_stock_qty, 0::numeric) AS total_stock_qty,
                coalesce(o.zakazano_sht, 0::numeric) AS zakazano_sht,
                coalesce(o.zakazano_rub, 0::numeric) AS zakazano_rub,
                coalesce(o.reyting_kartochki, s.reyting_kartochki) AS reyting_kartochki,
                coalesce(o.reyting_po_otzyvam, s.reyting_po_otzyvam) AS reyting_po_otzyvam,
                {adv_select}
                coalesce(o.gj_wb_article, s.gj_wb_article) AS gj_wb_article,
                coalesce(o.gj_ozon_sku, s.gj_ozon_sku) AS gj_ozon_sku,
                coalesce(o.gj_model, s.gj_model) AS gj_model,
                coalesce(o.assortment_bia, s.assortment_bia) AS assortment_bia,
                coalesce(o.tg, s.tg) AS tg,
                coalesce(o.tg_plus, s.tg_plus) AS tg_plus,
                coalesce(o.cg, s.cg) AS cg,
                coalesce(o.season, s.season) AS season
            FROM orders o
            FULL JOIN stock s ON s.artikul_wb = o.artikul_wb
            {dual_sku_mapping_join}
            {adv_join}
        ),
        totals AS (
            SELECT
                coalesce(sum(zakazano_sht), 0::numeric) AS total_orders_qty,
                coalesce(sum(zakazano_rub), 0::numeric) AS total_orders_rub,
                coalesce(sum(total_stock_qty), 0::numeric) AS total_stock
            FROM base
        ),
        ranked AS (
            SELECT
                b.*,
                CASE WHEN t.total_orders_qty > 0 THEN round(b.zakazano_sht / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_share_pct,
                CASE WHEN t.total_orders_qty > 0 THEN round(sum(b.zakazano_sht) OVER (ORDER BY b.zakazano_sht DESC NULLS LAST, b.artikul_wb) / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_cumulative_pct,
                CASE WHEN t.total_orders_rub > 0 THEN round(b.zakazano_rub / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_share_pct,
                CASE WHEN t.total_orders_rub > 0 THEN round(sum(b.zakazano_rub) OVER (ORDER BY b.zakazano_rub DESC NULLS LAST, b.artikul_wb) / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_cumulative_pct,
                CASE WHEN t.total_stock > 0 THEN round(b.total_stock_qty / t.total_stock * 100, 4) ELSE 0 END AS stock_share_pct,
                CASE WHEN t.total_stock > 0 THEN round(sum(b.total_stock_qty) OVER (ORDER BY b.total_stock_qty DESC NULLS LAST, b.artikul_wb) / t.total_stock * 100, 4) ELSE 0 END AS stock_cumulative_pct
            FROM base b
            CROSS JOIN totals t
        ),
        classified AS (
            SELECT
                *,
                CASE
                    WHEN zakazano_sht = 0 THEN 'Без ABC'
                    WHEN orders_cumulative_pct <= 80 THEN 'A'
                    WHEN orders_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_orders,
                CASE
                    WHEN zakazano_rub = 0 THEN 'Без ABC'
                    WHEN sales_cumulative_pct <= 80 THEN 'A'
                    WHEN sales_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_sales,
                CASE
                    WHEN total_stock_qty = 0 THEN 'Без ABC'
                    WHEN stock_cumulative_pct <= 80 THEN 'A'
                    WHEN stock_cumulative_pct <= 95 THEN 'B'
                    ELSE 'C'
                END AS abc_stock
            FROM ranked
        ),
        final AS (
            SELECT
                category_name,
                artikul_wb,
                sku_wb,
                sku_ozon,
                naimenovanie,
                total_stock_qty,
                zakazano_sht,
                zakazano_rub,
                CASE WHEN zakazano_sht > 0 THEN round(zakazano_rub / zakazano_sht, 2) ELSE 0 END AS avg_price_rub,
                orders_share_pct,
                orders_cumulative_pct,
                abc_orders,
                sales_share_pct,
                sales_cumulative_pct,
                abc_sales,
                stock_share_pct,
                stock_cumulative_pct,
                abc_stock,
                CASE
                    WHEN abc_orders = 'Без ABC' OR abc_sales = 'Без ABC' OR abc_stock = 'Без ABC' THEN 'Без ABC'
                    ELSE concat(abc_orders, abc_sales, abc_stock)
                END AS abc_combined,
                {tag_status_select}
                reyting_kartochki,
                reyting_po_otzyvam,
                adv_impressions,
                adv_clicks,
                adv_sales_rub,
                adv_acos_pct,
                adv_tacos_pct,
                gj_wb_article,
                gj_ozon_sku,
                gj_model,
                assortment_bia,
                tg,
                tg_plus,
                cg,
                season
            FROM classified
        )
        SELECT *
        FROM final
        {outer_where}
    """
    return query_text, stock_values + order_values + adv_values


def planfact_filters_from_query(query, date_column="report_date"):
    params = parse_qs(query)
    filters = []
    values = []

    marketplace = params.get("marketplace", [DEFAULT_MARKETPLACE])[0].strip().lower()
    if marketplace in MARKETPLACES:
        filters.append("marketplace = %s")
        values.append(marketplace)

    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        filters.append(f"{date_column} >= %s")
        values.append(date_from)
    if date_to:
        filters.append(f"{date_column} <= %s")
        values.append(date_to)

    where = " WHERE " + " AND ".join(filters) if filters else ""
    return where, values


def adv_columns(cur, marketplace="ozon"):
    columns = get_view_columns(cur, adv_view_for_marketplace(marketplace))
    columns = columns + ["adv_sales_to_total_sales_pct"]
    if current_client_is_boiron():
        return columns
    return columns + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS


OZON_ADV_PRODUCT_TABLE_COLUMNS = (
    "report_date",
    "ozon_marketplace_article",
    "seller_article",
    "product_name",
    "category_name",
    "campaign_id",
    "impressions",
    "clicks",
    "ctr_pct",
    "expense_rub",
    "cpc_rub",
    "cpm_rub",
    "added_to_cart",
    "orders_qty",
    "direct_orders_qty",
    "indirect_orders_qty",
    "cr_pct",
    "orders_amount_rub",
    "direct_orders_amount_rub",
    "indirect_orders_amount_rub",
    "cpa_rub",
    "drr_pct",
    "total_orders_qty",
    "total_orders_amount_rub",
    "total_drr_pct",
    "adv_sales_to_total_sales_pct",
)


def adv_product_columns(base_columns, marketplace="ozon"):
    """Return user-facing product-ad columns without source/mapping internals."""
    if current_client_is_boiron():
        return list(base_columns) + ["adv_sales_to_total_sales_pct"]
    if marketplace != "ozon":
        columns = list(base_columns) + ["adv_sales_to_total_sales_pct"]
        return columns + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS
    available = set(base_columns) | {"adv_sales_to_total_sales_pct"}
    return [column for column in OZON_ADV_PRODUCT_TABLE_COLUMNS if column in available]


def append_adv_product_grain_filter(where, marketplace="ozon"):
    if marketplace != "ozon":
        return where
    condition = (
        "coalesce(nullif(v.ozon_marketplace_article::text, ''), '') <> '' "
        "AND coalesce(v.ozon_marketplace_article::text, '') NOT LIKE 'campaign:%%'"
    )
    return f"{where} AND {condition}" if where else f" WHERE {condition}"


def media_adv_columns(cur, view_name=MEDIA_ADV_VIEW):
    return get_view_columns(cur, view_name)


def funnel_columns(cur, marketplace="ozon"):
    return get_view_columns(cur, funnel_view_for_marketplace(marketplace)) + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS


def adv_ratio_sql(numerator="orders_amount_rub", denominator="total_orders_amount_rub"):
    return sql.SQL(
        "CASE WHEN coalesce({den}, 0) <> 0 "
        "THEN round(coalesce({num}, 0)::numeric / coalesce({den}, 0)::numeric * 100, 2) "
        "ELSE 0 END"
    ).format(num=sql.Identifier(numerator), den=sql.Identifier(denominator))


def mapping_options_view(report=None, marketplace=None):
    if current_client_key() in MAPPINGLESS_CLIENTS:
        return None
    if report == "adv":
        if marketplace == "wb":
            return "mv_sku_mapping_gj_wb_nmid"
        return "mv_sku_mapping_gj_ozon_sku"
    if report == "funnel":
        if marketplace == "wb":
            return "mv_sku_mapping_gj_wb_nmid"
        return "mv_sku_mapping_gj_barcode"
    if marketplace == "wb":
        return "mv_sku_mapping_gj_wb_nmid"
    return "mv_sku_mapping_gj_ozon_sku"


def add_mapping_options(cur, payload, report=None, marketplace=None):
    view_name = mapping_options_view(report, marketplace)
    if not view_name:
        for param, _ in MAPPING_FILTERS:
            payload[f"{param}_values"] = []
        return payload
    select_parts = [
        sql.SQL("array_remove(array_agg(DISTINCT {col} ORDER BY {col}), NULL) AS {alias}").format(
            col=sql.Identifier(column),
            alias=sql.Identifier(f"{param}_values"),
        )
        for param, column in MAPPING_FILTERS
        if param != "gj_model"
    ]
    query = sql.SQL("SELECT {selects} FROM public.{view}").format(
        selects=sql.SQL(", ").join(select_parts),
        view=sql.Identifier(view_name),
    )
    cur.execute(query)
    row = dict(cur.fetchone())
    for param, _ in MAPPING_FILTERS:
        payload[f"{param}_values"] = row.get(f"{param}_values") or []
    return payload


def sportmaster_filter_option_scope_sql(params, values, marketplace, exclude_param=None):
    conditions = sportmaster_source_filter_conditions(params, values, marketplace, exclude_param=exclude_param, source_alias="sp")
    return " AND ".join(conditions) if conditions else "TRUE"


def sportmaster_filter_options_query(query, include_categories=True):
    params = parse_qs(query)
    if not include_categories:
        for key in ("categories", "category", "category_exact"):
            params.pop(key, None)
    return urlencode(params, doseq=True)


def sportmaster_subcategory_options(cur, marketplace, params=None):
    params = params or {}
    scoped_params = {key: value for key, value in params.items() if key != "sm_subcategory"}
    scoped_query = urlencode(scoped_params, doseq=True)
    base_views = abc_base_views_for_marketplace(marketplace)
    order_filter, order_values = ozon_abc_mv_filters(scoped_query, "o", include_date=True)
    stock_filter, stock_values = ozon_abc_mv_filters(scoped_query, "s")
    cur.execute(
        f"""
        WITH subcategories AS (
            SELECT coalesce(nullif(s.subcategory_name, ''), s.category_name) AS value
            FROM public.{base_views["stock"]} s
            WHERE {stock_filter}
            UNION
            SELECT coalesce(nullif(o.subcategory_name, ''), o.category_name) AS value
            FROM public.{base_views["orders"]} o
            WHERE {order_filter}
        )
        SELECT DISTINCT value
        FROM subcategories
        WHERE nullif(trim(value), '') IS NOT NULL
        ORDER BY value
        LIMIT 600
        """,
        stock_values + order_values,
    )
    return [row["value"] for row in cur.fetchall()]


def sportmaster_attribute_options(cur, marketplace, attribute_names, limit=400, params=None, exclude_param=None):
    params = params or {}
    scope_values = []
    scope_where = sportmaster_filter_option_scope_sql(params, scope_values, marketplace, exclude_param=exclude_param)
    if marketplace == "ozon":
        cur.execute(
            f"""
            WITH filtered_products AS (
                SELECT sp.product_id, sp.category_id
                FROM public.ozon_cat_products sp
                WHERE {scope_where}
            )
            SELECT DISTINCT spa.value_text AS value
            FROM public.ozon_cat_product_attributes spa
            JOIN filtered_products sp ON sp.product_id = spa.product_id
            LEFT JOIN public.ozon_cat_category_attributes sca
                ON sca.category_id = sp.category_id
               AND sca.attribute_id = spa.attribute_id
            LEFT JOIN public.ozon_cat_common_attributes scm
                ON scm.attribute_id = spa.attribute_id
            WHERE coalesce(sca.attribute_name, scm.attribute_name) = ANY(%s)
              AND nullif(trim(spa.value_text), '') IS NOT NULL
            ORDER BY spa.value_text
            LIMIT %s
            """,
            [*scope_values, list(attribute_names), limit],
        )
    else:
        cur.execute(
            f"""
            WITH filtered_products AS (
                SELECT sp.product_id, sp.category_id
                FROM public.products sp
                WHERE {scope_where}
            )
            SELECT DISTINCT spa.value_text AS value
            FROM public.product_attributes spa
            JOIN filtered_products sp ON sp.product_id = spa.product_id
            LEFT JOIN public.category_attributes sca
                ON sca.category_id = sp.category_id
               AND sca.attribute_id = spa.attribute_id
            LEFT JOIN public.common_attributes scm
                ON scm.attribute_id = spa.attribute_id
            WHERE coalesce(sca.attribute_name, scm.attribute_name) = ANY(%s)
              AND nullif(trim(spa.value_text), '') IS NOT NULL
            ORDER BY spa.value_text
            LIMIT %s
            """,
            [*scope_values, list(attribute_names), limit],
        )
    return [row["value"] for row in cur.fetchall()]


def add_sportmaster_filter_options(cur, payload, marketplace, query=""):
    if current_client_key() != "sportmaster":
        return payload
    params = parse_qs(query)
    payload["sm_subcategory_values"] = sportmaster_subcategory_options(cur, marketplace, params)
    if marketplace == "wb":
        scope_values = []
        scope_where = sportmaster_filter_option_scope_sql(params, scope_values, marketplace, exclude_param="sm_brand")
        cur.execute(
            f"""
            SELECT DISTINCT sp.brend AS value
            FROM public.products sp
            WHERE nullif(trim(sp.brend), '') IS NOT NULL
              AND {scope_where}
            ORDER BY sp.brend
            LIMIT 400
            """,
            scope_values,
        )
        payload["sm_brand_values"] = [row["value"] for row in cur.fetchall()]
    else:
        payload["sm_brand_values"] = sportmaster_attribute_options(
            cur,
            marketplace,
            SPORTMASTER_FILTERS["sm_brand"]["attribute_names"],
            params=params,
            exclude_param="sm_brand",
        )
    for param in ("sm_gender", "sm_age", "sm_collection", "sm_season", "sm_sport"):
        payload[f"{param}_values"] = sportmaster_attribute_options(
            cur,
            marketplace,
            SPORTMASTER_FILTERS[param]["attribute_names"],
            params=params,
            exclude_param=param,
        )
    payload["sm_model_values"] = sportmaster_attribute_options(
        cur,
        marketplace,
        SPORTMASTER_FILTERS["sm_model"]["attribute_names"],
        limit=600,
        params=params,
        exclude_param="sm_model",
    )
    return payload


def add_seo_status_options(cur, payload, marketplace):
    cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{SEO_TAGS_TABLE}",))
    if not cur.fetchone()["table_name"]:
        payload["seo_status_values"] = []
        return payload
    query = sql.SQL(
        """
        SELECT array_remove(array_agg(DISTINCT tag ORDER BY tag), NULL) AS seo_status_values
        FROM public.{table} st
        WHERE marketplace = %s
          AND NOT {collection_tag_predicate}
        """
    ).format(
        table=sql.Identifier(SEO_TAGS_TABLE),
        collection_tag_predicate=sql.SQL(collection_tag_predicate_sql("st")),
    )
    cur.execute(query, (marketplace,))
    payload["seo_status_values"] = (cur.fetchone() or {}).get("seo_status_values") or []
    return payload


def add_collection_status_options(cur, payload, marketplace):
    cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{SEO_TAGS_TABLE}",))
    if not cur.fetchone()["table_name"]:
        payload["collection_status_values"] = []
        return payload
    query = sql.SQL(
        """
        SELECT array_remove(array_agg(DISTINCT tag ORDER BY tag), NULL) AS collection_status_values
        FROM public.{table} st
        WHERE marketplace = %s
          AND {collection_tag_predicate}
        """
    ).format(
        table=sql.Identifier(SEO_TAGS_TABLE),
        collection_tag_predicate=sql.SQL(collection_tag_predicate_sql("st")),
    )
    cur.execute(query, (marketplace,))
    payload["collection_status_values"] = (cur.fetchone() or {}).get("collection_status_values") or []
    payload["collection_status_labels"] = {
        tag: collection_tag_label(tag)
        for tag in payload["collection_status_values"]
    }
    return payload


def compact_filters_requested(query):
    value = parse_qs(query).get("compact", ["0"])[0].strip().lower()
    return value in {"1", "true", "yes", "y"}


def normalize_rows(rows):
    out = []
    for row in rows:
        item = {key: normalize_value(value) for key, value in dict(row).items()}
        out.append(item)
    return out


def is_ozon_abc_query(query):
    return marketplace_from_query(query) in ABC_BASE_VIEWS


def ozon_abc_column_payload(columns=None, category_label=None):
    columns = columns or OZON_ABC_COLUMNS
    return [
        {
            "key": column,
            "label": category_label if column == "category_name" and category_label else COLUMN_LABELS.get(column, column),
            "type": "number" if column in NUMERIC_FIELDS else "text",
        }
        for column in columns
    ]


def abc_product_columns_for_marketplace(marketplace):
    columns = list(OZON_ABC_PRODUCT_COLUMNS)
    if marketplace != "ozon":
        adv_columns = {"adv_impressions", "adv_clicks", "adv_sales_rub", "adv_acos_pct", "adv_tacos_pct"}
        columns = [column for column in columns if column not in adv_columns]
    if COLLECTION_STATUS_COLUMN not in columns:
        insert_at = columns.index("abc_combined") + 1 if "abc_combined" in columns else len(columns)
        columns.insert(insert_at, COLLECTION_STATUS_COLUMN)
    if SEO_STATUS_COLUMN not in columns:
        insert_at = columns.index(COLLECTION_STATUS_COLUMN) + 1 if COLLECTION_STATUS_COLUMN in columns else (columns.index("abc_combined") + 1 if "abc_combined" in columns else len(columns))
        columns.insert(insert_at, SEO_STATUS_COLUMN)
    return columns


def sku_scoring_period_sql(query, marketplace, base_columns):
    base_views = abc_base_views_for_marketplace(marketplace)
    order_view = base_views["orders"]
    sku_view = MARKETPLACES[marketplace]["sku_view"]
    order_filter, order_values = ozon_abc_mv_filters(query, "o", include_date=True)
    static_columns = [column for column in base_columns if column not in {"zakazano_sht", "zakazano_rub"}]
    select_static = ",\n                ".join(quoted_column(column, alias="v") for column in static_columns)
    query_text = f"""
        WITH orders AS (
            SELECT
                o.artikul_wb,
                coalesce(sum(o.zakazano_sht), 0)::numeric AS zakazano_sht,
                coalesce(sum(o.zakazano_rub), 0)::numeric AS zakazano_rub
            FROM public.{order_view} o
            WHERE {order_filter}
            GROUP BY o.artikul_wb
        )
        SELECT
            {select_static},
            coalesce(o.zakazano_sht, 0::numeric) AS zakazano_sht,
            coalesce(o.zakazano_rub, 0::numeric) AS zakazano_rub
        FROM public.{sku_view} v
        LEFT JOIN orders o ON o.artikul_wb = v.artikul_wb
    """
    return query_text, order_values


def sku_scoring_source_sql(query, marketplace, base_columns):
    if marketplace in ABC_BASE_VIEWS:
        return sku_scoring_period_sql(query, marketplace, base_columns)
    view_name = MARKETPLACES[marketplace]["sku_view"]
    return f"SELECT * FROM public.{view_name}", []


def handle_ozon_abc_stats(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    category_level = abc_category_level_from_query(parsed.query)
    outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query, include_product=False)
    base_sql, base_values = ozon_abc_category_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)
    column_where, column_values = outer_column_filter_clause(parsed.query, OZON_ABC_COLUMNS)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"
    sort_column = params.get("sort_col", [DEFAULT_SORT_COLUMN])[0]
    if sort_column not in OZON_ABC_COLUMNS:
        sort_column = DEFAULT_SORT_COLUMN
    direction = "ASC" if sort_dir == "asc" else "DESC"
    nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"

    with get_conn() as conn, conn.cursor() as cur:
        count_query = f"SELECT count(*) AS total FROM ({base_sql}) x{column_where}"
        cur.execute(count_query, base_values + outer_values + column_values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        data_query = (
            f"SELECT * FROM ({base_sql}) x "
            f"{column_where} "
            f"ORDER BY {sort_column} {direction} {nulls}, category_name "
            "LIMIT %s OFFSET %s"
        )
        cur.execute(data_query, base_values + outer_values + column_values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": ozon_abc_column_payload(category_label=abc_category_title(category_level)),
        "category_level": category_level,
        "category_label": abc_category_title(category_level),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_ozon_abc_summary(parsed):
    marketplace = marketplace_from_query(parsed.query)
    category_level = abc_category_level_from_query(parsed.query)
    outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query, include_product=False)
    base_sql, base_values = ozon_abc_category_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)
    query = f"""
        SELECT
            count(*) AS categories,
            coalesce(sum(sku_count), 0) AS sku_count,
            coalesce(sum(total_stock_qty), 0) AS total_stock_qty,
            coalesce(sum(category_attribute_count), 0) AS category_attribute_count,
            coalesce(sum(zakazano_sht), 0) AS zakazano_sht,
            coalesce(sum(zakazano_rub), 0) AS zakazano_rub
        FROM ({base_sql}) x
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, base_values + outer_values)
        row = dict(cur.fetchone())
        payload = {key: to_float(value) for key, value in row.items()}
        payload["category_level"] = category_level
        payload["category_label"] = abc_category_title(category_level)
        return payload


def handle_ozon_abc_filters(parsed):
    marketplace = marketplace_from_query(parsed.query)
    category_level = abc_category_level_from_query(parsed.query)
    base_views = abc_base_views_for_marketplace(marketplace)
    order_view = base_views["orders"]
    stock_view = base_views["stock"]
    category_options_query = query_without_category_selection(parsed.query)
    order_filter, order_values = ozon_abc_mv_filters(category_options_query, "o", include_date=True)
    stock_filter, stock_values = ozon_abc_mv_filters(category_options_query, "s")
    query = f"""
        WITH categories AS (
            SELECT s.category_name AS category_name
            FROM public.{stock_view} s
            WHERE {stock_filter}
            UNION
            SELECT o.category_name AS category_name
            FROM public.{order_view} o
            WHERE {order_filter}
        )
        SELECT
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(*) AS categories
        FROM categories
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, stock_values + order_values)
        payload = dict(cur.fetchone())
        cur.execute(f"SELECT min(report_date) AS date_from, max(report_date) AS date_to FROM public.{order_view}")
        dates = dict(cur.fetchone())
        payload["abc_orders"] = ABC_CLASS_VALUES
        payload["abc_sales"] = ABC_CLASS_VALUES
        payload["abc_stock"] = ABC_CLASS_VALUES
        payload["abc_combined"] = ABC_COMBINED_VALUES
        payload["category_level"] = category_level
        payload["category_label"] = abc_category_title(category_level)
        payload["date_from"] = normalize_value(dates.get("date_from"))
        payload["date_to"] = normalize_value(dates.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{marketplace}_abc_from_funnel_and_current_stock"
        add_collection_status_options(cur, payload, marketplace)
        add_seo_status_options(cur, payload, marketplace)
        add_ozon_product_attribute_options(cur, payload, marketplace)
        add_sportmaster_filter_options(cur, payload, marketplace, parsed.query)
        if current_client_key() != "sportmaster" and not compact_filters_requested(parsed.query):
            add_mapping_options(cur, payload, marketplace=marketplace)
        return payload


def handle_ozon_product_stats(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    category_level = abc_category_level_from_query(parsed.query)
    outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query)
    base_sql, base_values = ozon_abc_product_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"
    sort_column = params.get("sort_col", ["zakazano_rub"])[0]
    columns = abc_product_columns_for_marketplace(marketplace) + ABC_PRODUCT_MAPPING_COLUMNS
    column_where, column_values = outer_column_filter_clause(parsed.query, columns)
    if sort_column not in columns:
        sort_column = "zakazano_rub"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"

    with get_conn() as conn, conn.cursor() as cur:
        count_query = f"SELECT count(*) AS total FROM ({base_sql}) x{column_where}"
        cur.execute(count_query, base_values + outer_values + column_values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        data_query = (
            f"SELECT * FROM ({base_sql}) x "
            f"{column_where} "
            f"ORDER BY {sort_column} {direction} {nulls}, category_name, artikul_wb "
            "LIMIT %s OFFSET %s"
        )
        cur.execute(data_query, base_values + outer_values + column_values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": ozon_abc_column_payload(columns, category_label=abc_category_title(category_level)),
        "category_level": category_level,
        "category_label": abc_category_title(category_level),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_ozon_product_summary(parsed):
    marketplace = marketplace_from_query(parsed.query)
    outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query)
    base_sql, base_values = ozon_abc_product_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)
    query = f"""
        SELECT
            count(DISTINCT category_name) AS categories,
            count(DISTINCT artikul_wb) AS sku_count,
            coalesce(sum(total_stock_qty), 0) AS total_stock_qty,
            coalesce(sum(zakazano_sht), 0) AS zakazano_sht,
            coalesce(sum(zakazano_rub), 0) AS zakazano_rub
        FROM ({base_sql}) x
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, base_values + outer_values)
        row = dict(cur.fetchone())
        return {key: to_float(value) for key, value in row.items()}


def handle_ozon_product_filters(parsed):
    marketplace = marketplace_from_query(parsed.query)
    category_level = abc_category_level_from_query(parsed.query)
    base_views = abc_base_views_for_marketplace(marketplace)
    order_view = base_views["orders"]
    stock_view = base_views["stock"]
    category_options_query = query_without_category_selection(parsed.query)
    order_filter, order_values = ozon_abc_mv_filters(category_options_query, "o", include_date=True)
    stock_filter, stock_values = ozon_abc_mv_filters(category_options_query, "s")
    query = f"""
        WITH categories AS (
            SELECT s.category_name AS category_name
            FROM public.{stock_view} s
            WHERE {stock_filter}
            UNION
            SELECT o.category_name AS category_name
            FROM public.{order_view} o
            WHERE {order_filter}
        )
        SELECT
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(DISTINCT category_name) AS categories
        FROM categories
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, stock_values + order_values)
        payload = dict(cur.fetchone())
        cur.execute(f"SELECT min(report_date) AS date_from, max(report_date) AS date_to FROM public.{order_view}")
        dates = dict(cur.fetchone())
        payload["abc_orders"] = ABC_CLASS_VALUES
        payload["abc_sales"] = ABC_CLASS_VALUES
        payload["abc_stock"] = ABC_CLASS_VALUES
        payload["abc_combined"] = ABC_COMBINED_VALUES
        payload["category_level"] = category_level
        payload["category_label"] = abc_category_title(category_level)
        payload["date_from"] = normalize_value(dates.get("date_from"))
        payload["date_to"] = normalize_value(dates.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{marketplace}_product_abc_from_funnel_and_current_stock"
        add_collection_status_options(cur, payload, marketplace)
        add_seo_status_options(cur, payload, marketplace)
        add_ozon_product_attribute_options(cur, payload, marketplace)
        add_sportmaster_filter_options(cur, payload, marketplace, parsed.query)
        if current_client_key() != "sportmaster" and not compact_filters_requested(parsed.query):
            add_mapping_options(cur, payload, marketplace=marketplace)
        return payload


def handle_stats(parsed):
    params = parse_qs(parsed.query)
    home_keys = {
        "client", "marketplace", "date_from", "date_to",
        "sort_col", "sort_dir", "limit", "page",
    }
    is_home_category = (
        set(params).issubset(home_keys)
        and params.get("sort_col", [""])[0] == "zakazano_rub"
        and params.get("sort_dir", ["desc"])[0].lower() == "desc"
        and params.get("page", ["1"])[0] == "1"
        and bool(params.get("date_from", [""])[0])
        and bool(params.get("date_to", [""])[0])
    )
    if is_home_category:
        try:
            from home_marts import category_payload
            home_limit = max(5, min(int(params.get("limit", ["5"])[0]), MAX_PAGE_SIZE))
            with get_conn() as conn:
                fast_payload = category_payload(
                    conn,
                    marketplace_from_query(parsed.query),
                    params["date_from"][0],
                    params["date_to"][0],
                    home_limit,
                )
            if fast_payload is not None:
                fast_payload["rows"] = normalize_rows(fast_payload["rows"])
                return fast_payload
        except Exception as exc:
            print(f"home category mart fallback: {exc}", flush=True)
    if is_ozon_abc_query(parsed.query):
        return handle_ozon_abc_stats(parsed)
    if has_mapping_filters(parsed.query):
        return handle_mapped_category_stats(parsed)
    where, values = filters_from_query(parsed.query)
    view_name = view_for_query(parsed.query)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        columns = get_view_columns(cur, view_name)
        where, values = append_column_filters(where, values, parsed.query, columns)
        sort_column = params.get("sort_col", [DEFAULT_SORT_COLUMN])[0]
        if sort_column not in columns:
            sort_column = DEFAULT_SORT_COLUMN if DEFAULT_SORT_COLUMN in columns else columns[0]

        count_query = sql.SQL("SELECT count(*) AS total FROM public.{view} {where}").format(
            view=sql.Identifier(view_name),
            where=sql.SQL(where),
        )
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT *
            FROM public.{view}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, category_name
            LIMIT %s OFFSET %s
            """
        ).format(
            view=sql.Identifier(view_name),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_mapped_category_stats(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=False,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )
    view_name = sku_view_for_query(parsed.query)
    columns = [
        "category_name",
        "total_stock_qty",
        "sku_count",
        "category_attribute_count",
        "zakazano_sht",
        "zakazano_rub",
    ]
    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"
    sort_column = params.get("sort_col", [DEFAULT_SORT_COLUMN])[0]
    if sort_column not in columns:
        sort_column = DEFAULT_SORT_COLUMN

    with get_conn() as conn, conn.cursor() as cur:
        count_query = sql.SQL(
            """
            SELECT count(*) AS total
            FROM (
                SELECT v.category_name
                FROM public.{view} v
                {join}
                {where}
                GROUP BY v.category_name
            ) x
            """
        ).format(view=sql.Identifier(view_name), join=mapping_join_for_report("sku", marketplace), where=sql.SQL(where))
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.category_name,
                coalesce(sum(v.total_stock_qty), 0) AS total_stock_qty,
                count(DISTINCT v.artikul_wb) AS sku_count,
                coalesce(sum(v.category_attrs_total), 0) AS category_attribute_count,
                coalesce(sum(v.zakazano_sht), 0) AS zakazano_sht,
                coalesce(sum(v.zakazano_rub), 0) AS zakazano_rub
            FROM public.{view} v
            {join}
            {where}
            GROUP BY v.category_name
            ORDER BY {sort_col} {direction} {nulls}, v.category_name
            LIMIT %s OFFSET %s
            """
        ).format(
            view=sql.Identifier(view_name),
            join=mapping_join_for_report("sku", marketplace),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_sku_stats(parsed):
    params = parse_qs(parsed.query)
    view_name = sku_view_for_query(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=False,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        base_columns = get_view_columns(cur, view_name)
        columns = base_columns + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS
        source_sql, source_values = sku_scoring_source_sql(parsed.query, marketplace, base_columns)
        where, values = append_column_filters(where, values, parsed.query, base_columns, alias="v")
        sort_column = params.get("sort_col", ["total_stock_qty"])[0]
        if sort_column not in columns:
            sort_column = "total_stock_qty" if "total_stock_qty" in columns else columns[0]
        sku_join = sql.SQL("").join(
            [
                mapping_join_for_report("sku", marketplace),
                collection_status_join_sql(marketplace, seo_sku_expr_for_mapping(marketplace)),
                seo_status_join_sql(marketplace, seo_sku_expr_for_mapping(marketplace)),
            ]
        )

        count_query = sql.SQL("SELECT count(*) AS total FROM ({source}) v {join} {where}").format(
            source=sql.SQL(source_sql),
            join=sku_join,
            where=sql.SQL(where),
        )
        cur.execute(count_query, source_values + values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.*
                {collection_status_col}
                {seo_status_col}
                {mapping_cols}
            FROM ({source}) v
            {join}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, artikul_wb
            LIMIT %s OFFSET %s
            """
        ).format(
            source=sql.SQL(source_sql),
            collection_status_col=collection_status_select_sql(),
            seo_status_col=seo_status_select_sql(),
            mapping_cols=mapping_select_sql(),
            join=sku_join,
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, source_values + values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def sku_card_value_is_filled(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def sku_card_weight(attribute_name, attribute_kind):
    if attribute_name in OZON_SKU_CARD_PRIMARY_NAMES:
        return 10
    if attribute_name.endswith("*"):
        return 8
    return 6 if attribute_kind == "Общая" else 4


def sku_card_importance_score(attribute_name, value, weight):
    if not sku_card_value_is_filled(value):
        return 0
    text = str(value).strip()
    if attribute_name in {"Наименование", "Название товара"}:
        length = len(text)
        if length >= 30:
            return weight
        if length >= 15:
            return round(weight * 0.6, 1)
        return round(weight * 0.3, 1)
    if attribute_name == "Описание":
        length = len(text)
        if length >= 300:
            return weight
        if length >= 120:
            return round(weight * 0.7, 1)
        if length:
            return round(weight * 0.35, 1)
        return 0
    return weight


def sku_card_attribute_row(attribute_name, attribute_kind, value):
    normalized = normalize_value(value)
    if isinstance(normalized, str) and len(normalized) > 4000:
        normalized = f"{normalized[:4000]}..."
    filled = sku_card_value_is_filled(normalized)
    weight = sku_card_weight(attribute_name, attribute_kind)
    if attribute_name in {"Наименование", "Описание", "Название товара", "Аннотация"}:
        state = f"{len(str(normalized or '').strip())} знаков" if filled else "нет"
    else:
        state = "заполнено" if filled else "нет"
    return {
        "attribute_name": attribute_name,
        "attribute_kind": attribute_kind,
        "value": normalized,
        "weight": weight,
        "state": state,
        "importance_score": sku_card_importance_score(attribute_name, normalized, weight),
        "filled": filled,
    }


def handle_sku_card(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    sku = params.get("sku", [""])[0].strip()
    if not sku:
        return {"ok": False, "error": "SKU не передан"}
    if marketplace == "ozon":
        return handle_ozon_sku_card(parsed, sku)
    if marketplace == "wb":
        return handle_wb_sku_card(parsed, sku)
    return {"ok": False, "error": f"Неизвестный маркетплейс: {marketplace}"}


def handle_sku_content_scoring_providers():
    try:
        from ozon_category_dashboard.content_scoring import provider_catalog
    except ModuleNotFoundError:
        from content_scoring import provider_catalog

    return provider_catalog(service_credential)


def handle_saved_sku_content_scoring(parsed):
    try:
        from ozon_category_dashboard.content_scoring_store import load_content_scoring
    except ModuleNotFoundError:
        from content_scoring_store import load_content_scoring

    params = parse_qs(parsed.query)
    client = normalize_client_key(params.get("client", [current_client_key()])[0])
    marketplace = str(params.get("marketplace", ["wb"])[0] or "wb").strip().lower()
    sku = str(params.get("sku", [""])[0] or "").strip()
    if marketplace not in client_marketplace_ids(client):
        raise ValueError("Площадка недоступна для выбранного аккаунта")
    if not sku:
        raise ValueError("SKU не передан")
    return load_content_scoring(read_db_config(client), client, marketplace, sku)


def handle_sku_content_scoring(payload):
    try:
        from ozon_category_dashboard.content_scoring import run_scoring
        from ozon_category_dashboard.content_scoring_store import save_content_scoring
    except ModuleNotFoundError:
        from content_scoring import run_scoring
        from content_scoring_store import save_content_scoring

    client = normalize_client_key(payload.get("client") or current_client_key())
    marketplace = str(payload.get("marketplace") or "wb").strip().lower()
    sku = str(payload.get("sku") or "").strip()
    provider = str(payload.get("provider") or "codex").strip().lower()
    model = str(payload.get("model") or "").strip()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
    if marketplace not in client_marketplace_ids(client):
        raise ValueError("Площадка недоступна для выбранного аккаунта")
    if not sku:
        raise ValueError("SKU не передан")
    query = urlencode({key: value for key, value in {
        "client": client,
        "marketplace": marketplace,
        "sku": sku,
        "date_from": str(payload.get("date_from") or ""),
        "date_to": str(payload.get("date_to") or ""),
    }.items() if value})
    client_token = CURRENT_CLIENT.set(client)
    try:
        card = handle_sku_card(urlparse(f"/api/sku-card?{query}"))
    finally:
        CURRENT_CLIENT.reset(client_token)
    if not card.get("ok"):
        raise ValueError(str(card.get("error") or "Карточка SKU не найдена"))
    response = run_scoring(card, provider, model, service_credential, reasoning_effort=reasoning_effort)
    response["saved"] = save_content_scoring(
        read_db_config(client), client, marketplace, sku, response["result"]
    )
    return response


def build_sku_card_payload(marketplace, sku, detail, attribute_rows, *, media=None, source=None):
    summary_keys = [
        "category_name",
        "artikul_wb",
        "naimenovanie",
        "card_name",
        "naimenovanie_len",
        "opisanie_len",
        "common_attrs_total",
        "common_attrs_filled",
        "category_attrs_total",
        "category_attrs_filled",
        "foto_count",
        "total_stock_qty",
        "zakazano_sht",
        "zakazano_rub",
        "reyting_kartochki",
        "reyting_po_otzyvam",
        "artikul_prodavtsa",
        "product_id",
        "category_id",
        "card_category_name",
    ]
    payload = {
        "ok": True,
        "marketplace": marketplace,
        "sku": sku,
        "summary": {key: detail.get(key) for key in summary_keys},
        "cards": [
            {"label": "SKU", "value": detail.get("artikul_wb")},
            {"label": "Категория", "value": detail.get("category_name") or detail.get("card_category_name")},
            {"label": "Остаток, шт", "value": detail.get("total_stock_qty")},
            {"label": "Заказано, руб", "value": detail.get("zakazano_rub")},
            {"label": "Заказано, шт", "value": detail.get("zakazano_sht")},
            {
                "label": "Общие атрибуты",
                "value": f"{detail.get('common_attrs_filled') or 0} / {detail.get('common_attrs_total') or 0}",
            },
            {
                "label": "Категорийные",
                "value": f"{detail.get('category_attrs_filled') or 0} / {detail.get('category_attrs_total') or 0}",
            },
            {"label": "Фото, шт", "value": detail.get("foto_count")},
            {"label": "Рейтинг карточки", "value": detail.get("reyting_kartochki")},
        ],
        "attributes": attribute_rows,
    }
    if media:
        payload["media"] = media
    if source:
        payload["content_source"] = source
    return payload


def wb_content_card_scalar(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def wb_content_card_media(card):
    media = []
    for index, photo in enumerate(card.get("photos") or [], start=1):
        if not isinstance(photo, dict):
            continue
        url = photo.get("hq") or photo.get("big") or photo.get("c516x688") or photo.get("c246x328")
        thumbnail_url = photo.get("c246x328") or photo.get("tm") or photo.get("square") or url
        if url:
            media.append(
                {
                    "kind": "photo",
                    "label": f"Фото {index}",
                    "url": url,
                    "thumbnail_url": thumbnail_url,
                }
            )
    video = card.get("video")
    if isinstance(video, str) and video.strip():
        media.append({"kind": "video", "label": "Видео", "url": video.strip()})
    return media


def ozon_content_card_media(detail):
    """Return every distinct Ozon card image, with the primary image first."""
    urls = []

    def append_url(value):
        value = str(value or "").strip()
        if value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)

    def append_many(value):
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    append_url(item.get("url") or item.get("file_name") or item.get("image_url"))
                else:
                    append_url(item)
            return
        if isinstance(value, dict):
            append_url(value.get("url") or value.get("file_name") or value.get("image_url"))
            return
        text = str(value or "").strip()
        if not text:
            return
        if text.startswith("[") or text.startswith("{"):
            try:
                append_many(json.loads(text))
                return
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        for url in re.findall(r"https?://[^\s;,]+", text):
            append_url(url)

    append_url(detail.get("ssylka_na_glavnoe_foto"))
    append_many(detail.get("images_json"))
    append_many(detail.get("ssylki_na_dopolnitelnye_foto"))
    return [
        {
            "kind": "photo",
            "label": "Главное фото" if index == 1 else f"Фото {index}",
            "url": url,
            "thumbnail_url": url,
        }
        for index, url in enumerate(urls, start=1)
    ]


def wb_content_characteristic_key(characteristic):
    attribute_id = characteristic.get("id")
    if attribute_id not in (None, ""):
        return str(attribute_id)
    return f"name:{str(characteristic.get('name') or '').strip().casefold()}"


def wb_content_card_attribute_rows(card, category_schema=None):
    dimensions = card.get("dimensions") if isinstance(card.get("dimensions"), dict) else {}
    sizes = card.get("sizes") if isinstance(card.get("sizes"), list) else []
    size_values = []
    barcodes = []
    for size in sizes:
        if not isinstance(size, dict):
            continue
        size_value = size.get("wbSize") or size.get("techSize")
        if size_value not in (None, ""):
            size_values.append(str(size_value))
        barcodes.extend(str(item) for item in (size.get("skus") or []) if item not in (None, ""))

    common_values = [
        ("Наименование", card.get("title")),
        ("Описание", card.get("description")),
        ("Артикул продавца", card.get("vendorCode")),
        ("Бренд", card.get("brand")),
        ("Категория WB", card.get("subjectName")),
        ("Вес с упаковкой, кг*", dimensions.get("weightBrutto")),
        ("Длина упаковки, см", dimensions.get("length")),
        ("Ширина упаковки, см", dimensions.get("width")),
        ("Высота упаковки, см", dimensions.get("height")),
        ("Размеры WB", size_values),
        ("Штрихкоды", barcodes),
        ("Фото", card.get("photos") or []),
        ("Видео", card.get("video")),
    ]
    rows = [
        sku_card_attribute_row(name, "Общая", wb_content_card_scalar(value))
        for name, value in common_values
    ]
    characteristics = {
        wb_content_characteristic_key(item): item
        for item in (card.get("characteristics") or [])
        if isinstance(item, dict)
    }
    schema = category_schema or [
        {"attribute_key": key, "attribute_name": item.get("name")}
        for key, item in characteristics.items()
    ]
    for schema_item in schema:
        characteristic = characteristics.get(str(schema_item.get("attribute_key") or ""), {})
        attribute_id = characteristic.get("id") or schema_item.get("attribute_id")
        name = characteristic.get("name") or schema_item.get("attribute_name") or (
            f"Атрибут {attribute_id}" if attribute_id else "Атрибут WB"
        )
        rows.append(
            sku_card_attribute_row(name, "Категорийная", wb_content_card_scalar(characteristic.get("value")))
        )
    return rows


def merge_sku_card_attribute_rows(primary_rows, fallback_rows):
    merged = []
    positions = {}
    for row in [*primary_rows, *fallback_rows]:
        key = str(row.get("attribute_name") or "").strip().casefold()
        if not key:
            continue
        if key not in positions:
            positions[key] = len(merged)
            merged.append(row)
            continue
        existing = merged[positions[key]]
        if not existing.get("filled") and row.get("filled"):
            merged[positions[key]] = row
    return merged


def handle_ozon_sku_card(parsed, sku):
    marketplace = "ozon"

    detail_query = """
        WITH sku_row AS (
            SELECT *
            FROM public.mv_sku_card_scoring_ozon
            WHERE artikul_wb::text = %s
            LIMIT 1
        ),
        sales AS (
            SELECT *
            FROM public.vw_ozon_sku_sales_90d
            WHERE ozon_sku::text = %s
            ORDER BY ostatok_na_konets DESC NULLS LAST, zakazano_rub DESC NULLS LAST
            LIMIT 1
        ),
        op_by_sku AS (
            SELECT
                sku,
                max(artikul) FILTER (WHERE nullif(trim(artikul), '') IS NOT NULL) AS artikul,
                max(kontent_reyting) FILTER (WHERE nullif(trim(kontent_reyting), '') IS NOT NULL) AS kontent_reyting,
                max(reyting) FILTER (WHERE nullif(trim(reyting), '') IS NOT NULL) AS reyting
            FROM public.ozon_products
            WHERE nullif(trim(sku), '') IS NOT NULL
            GROUP BY sku
        ),
        product_by_article AS (
            SELECT DISTINCT ON (artikul)
                artikul,
                product_id,
                category_id,
                category_name,
                nazvanie_tovara,
                annotatsiya,
                heshtegi,
                rich_kontent_json,
                sku,
                ves_v_upakovke_g,
                vysota_upakovki_mm,
                dlina_upakovki_mm,
                kolichestvo_zavodskih_upakovok,
                kolichestvo_tovara_v_uei,
                minimalnoe_kolichestvo_optom,
                nds_pct,
                obedinit_v_pohozhie_tovary,
                rassrochka,
                ssylka_na_glavnoe_foto,
                ssylki_na_dopolnitelnye_foto,
                images_json,
                strana_izgotovitel,
                tip,
                uskorennyy_sbor_otzyvov,
                tsena_do_skidki_rub,
                tsena_rub,
                shirina_upakovki_mm,
                shtrihkod_seriynyy_nomer_ean
            FROM public.ozon_cat_products
            WHERE nullif(trim(artikul), '') IS NOT NULL
            ORDER BY artikul, updated_at DESC NULLS LAST, imported_at DESC NULLS LAST, product_id DESC
        ),
        product_by_sku AS (
            SELECT DISTINCT ON (sku)
                sku,
                artikul,
                product_id,
                category_id,
                category_name,
                nazvanie_tovara,
                annotatsiya,
                heshtegi,
                rich_kontent_json,
                ves_v_upakovke_g,
                vysota_upakovki_mm,
                dlina_upakovki_mm,
                kolichestvo_zavodskih_upakovok,
                kolichestvo_tovara_v_uei,
                minimalnoe_kolichestvo_optom,
                nds_pct,
                obedinit_v_pohozhie_tovary,
                rassrochka,
                ssylka_na_glavnoe_foto,
                ssylki_na_dopolnitelnye_foto,
                images_json,
                strana_izgotovitel,
                tip,
                uskorennyy_sbor_otzyvov,
                tsena_do_skidki_rub,
                tsena_rub,
                shirina_upakovki_mm,
                shtrihkod_seriynyy_nomer_ean
            FROM public.ozon_cat_products
            WHERE nullif(trim(sku), '') IS NOT NULL
            ORDER BY sku, updated_at DESC NULLS LAST, imported_at DESC NULLS LAST, product_id DESC
        )
        SELECT
            sr.*,
            COALESCE(s.artikul_prodavtsa, pa.artikul, po.artikul, ps.artikul) AS artikul_prodavtsa,
            s.ozon_category,
            COALESCE(pa.product_id, po.product_id, ps.product_id) AS product_id,
            COALESCE(pa.category_id, po.category_id, ps.category_id) AS category_id,
            COALESCE(pa.category_name, po.category_name, ps.category_name) AS card_category_name,
            COALESCE(pa.nazvanie_tovara, po.nazvanie_tovara, ps.nazvanie_tovara) AS card_name,
            COALESCE(pa.annotatsiya, po.annotatsiya, ps.annotatsiya) AS annotatsiya,
            COALESCE(pa.heshtegi, po.heshtegi, ps.heshtegi) AS heshtegi,
            COALESCE(pa.rich_kontent_json, po.rich_kontent_json, ps.rich_kontent_json) AS rich_kontent_json,
            COALESCE(pa.sku, po.sku, ps.sku) AS card_sku,
            COALESCE(pa.ves_v_upakovke_g, po.ves_v_upakovke_g, ps.ves_v_upakovke_g) AS ves_v_upakovke_g,
            COALESCE(pa.vysota_upakovki_mm, po.vysota_upakovki_mm, ps.vysota_upakovki_mm) AS vysota_upakovki_mm,
            COALESCE(pa.dlina_upakovki_mm, po.dlina_upakovki_mm, ps.dlina_upakovki_mm) AS dlina_upakovki_mm,
            COALESCE(pa.kolichestvo_zavodskih_upakovok, po.kolichestvo_zavodskih_upakovok, ps.kolichestvo_zavodskih_upakovok) AS kolichestvo_zavodskih_upakovok,
            COALESCE(pa.kolichestvo_tovara_v_uei, po.kolichestvo_tovara_v_uei, ps.kolichestvo_tovara_v_uei) AS kolichestvo_tovara_v_uei,
            COALESCE(pa.minimalnoe_kolichestvo_optom, po.minimalnoe_kolichestvo_optom, ps.minimalnoe_kolichestvo_optom) AS minimalnoe_kolichestvo_optom,
            COALESCE(pa.nds_pct, po.nds_pct, ps.nds_pct) AS nds_pct,
            COALESCE(pa.obedinit_v_pohozhie_tovary, po.obedinit_v_pohozhie_tovary, ps.obedinit_v_pohozhie_tovary) AS obedinit_v_pohozhie_tovary,
            COALESCE(pa.rassrochka, po.rassrochka, ps.rassrochka) AS rassrochka,
            COALESCE(pa.ssylka_na_glavnoe_foto, po.ssylka_na_glavnoe_foto, ps.ssylka_na_glavnoe_foto) AS ssylka_na_glavnoe_foto,
            COALESCE(pa.ssylki_na_dopolnitelnye_foto, po.ssylki_na_dopolnitelnye_foto, ps.ssylki_na_dopolnitelnye_foto) AS ssylki_na_dopolnitelnye_foto,
            COALESCE(pa.images_json, po.images_json, ps.images_json) AS images_json,
            COALESCE(pa.strana_izgotovitel, po.strana_izgotovitel, ps.strana_izgotovitel) AS strana_izgotovitel,
            COALESCE(pa.tip, po.tip, ps.tip) AS card_tip,
            COALESCE(pa.uskorennyy_sbor_otzyvov, po.uskorennyy_sbor_otzyvov, ps.uskorennyy_sbor_otzyvov) AS uskorennyy_sbor_otzyvov,
            COALESCE(pa.tsena_do_skidki_rub, po.tsena_do_skidki_rub, ps.tsena_do_skidki_rub) AS tsena_do_skidki_rub,
            COALESCE(pa.tsena_rub, po.tsena_rub, ps.tsena_rub) AS tsena_rub,
            COALESCE(pa.shirina_upakovki_mm, po.shirina_upakovki_mm, ps.shirina_upakovki_mm) AS shirina_upakovki_mm,
            COALESCE(pa.shtrihkod_seriynyy_nomer_ean, po.shtrihkod_seriynyy_nomer_ean, ps.shtrihkod_seriynyy_nomer_ean) AS shtrihkod_seriynyy_nomer_ean
        FROM sku_row sr
        LEFT JOIN sales s ON s.ozon_sku::text = sr.artikul_wb::text
        LEFT JOIN op_by_sku op ON op.sku::text = sr.artikul_wb::text
        LEFT JOIN product_by_article pa ON pa.artikul = s.artikul_prodavtsa
        LEFT JOIN product_by_article po ON po.artikul = op.artikul
        LEFT JOIN product_by_sku ps ON ps.sku = sr.artikul_wb::text
    """

    category_query = """
        SELECT
            ca.attribute_name,
            ca.attribute_id,
            pa.value_text
        FROM public.ozon_cat_category_attributes ca
        LEFT JOIN public.ozon_cat_product_attributes pa
            ON pa.product_id = %s
           AND pa.attribute_id = ca.attribute_id
        WHERE ca.category_id = %s
        ORDER BY lower(ca.attribute_name), ca.attribute_id
    """

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(detail_query, [sku, sku])
        detail = cur.fetchone()
        if not detail:
            return {"ok": False, "error": f"SKU {sku} не найден в SKU-скоринге Ozon"}
        detail = {key: normalize_value(value) for key, value in dict(detail).items()}
        if marketplace in ABC_BASE_VIEWS:
            order_filter, order_values = ozon_abc_mv_filters(parsed.query, "o", include_date=True)
            cur.execute(
                f"""
                SELECT
                    coalesce(sum(o.zakazano_sht), 0)::numeric AS zakazano_sht,
                    coalesce(sum(o.zakazano_rub), 0)::numeric AS zakazano_rub
                FROM public.{abc_base_views_for_marketplace(marketplace)["orders"]} o
                WHERE {order_filter}
                  AND o.artikul_wb::text = %s
                """,
                order_values + [sku],
            )
            period_orders = dict(cur.fetchone())
            detail["zakazano_sht"] = normalize_value(period_orders.get("zakazano_sht"))
            detail["zakazano_rub"] = normalize_value(period_orders.get("zakazano_rub"))

        attribute_rows = [
            sku_card_attribute_row("Наименование", "Общая", detail.get("naimenovanie") or detail.get("card_name")),
            sku_card_attribute_row("Описание", "Общая", detail.get("annotatsiya")),
        ]
        used_common_columns = {"annotatsiya"}
        for attribute_name, column_name in OZON_SKU_CARD_COMMON_ATTRIBUTES:
            if column_name in used_common_columns:
                continue
            attribute_rows.append(sku_card_attribute_row(attribute_name, "Общая", detail.get(column_name)))

        product_id = detail.get("product_id")
        category_id = detail.get("category_id")
        if product_id and category_id:
            cur.execute(category_query, [product_id, category_id])
            for row in cur.fetchall():
                row = dict(row)
                attribute_rows.append(
                    sku_card_attribute_row(
                        row.get("attribute_name") or f"Атрибут {row.get('attribute_id')}",
                        "Категорийная",
                        row.get("value_text"),
                    )
                )

    media = ozon_content_card_media(detail)
    return build_sku_card_payload(
        marketplace,
        sku,
        detail,
        attribute_rows,
        media=media,
        source="Ozon Product API · карточка товара",
    )


def handle_wb_sku_card(parsed, sku):
    marketplace = "wb"
    detail_query = """
        WITH sku_row AS (
            SELECT *
            FROM public.mv_sku_card_scoring_wb
            WHERE artikul_wb::text = %s
            LIMIT 1
        ),
        product_row AS (
            SELECT DISTINCT ON (artikul_wb)
                product_id,
                artikul_wb,
                artikul_ozon,
                artikul_prodavtsa,
                barkod,
                brend,
                ves_s_upakovkoy_kg,
                video,
                vysota_upakovki,
                gruppa,
                data_okonchaniya_deystviya_sertifikata_deklaratsii,
                data_registratsii_sertifikata_deklaratsii,
                dlina_upakovki,
                ikpu,
                kategoriya_prodavtsa,
                kod_upakovki,
                komplektatsiya,
                naimenovanie AS card_name,
                nomer_deklaratsii_sootvetstviya,
                nomer_sertifikata_sootvetstviya,
                opisanie,
                stavka_nds,
                strana_proizvodstva,
                foto,
                shirina_upakovki,
                category_id,
                seller_category_name
            FROM public.products
            WHERE artikul_wb::text = %s
            ORDER BY artikul_wb, product_id DESC
        )
        SELECT
            sr.*,
            pr.*,
            COALESCE(c.category_name, sr.category_name, pr.seller_category_name) AS card_category_name
        FROM sku_row sr
        LEFT JOIN product_row pr ON pr.artikul_wb::text = sr.artikul_wb::text
        LEFT JOIN public.categories c ON c.category_id = pr.category_id
    """
    common_query = """
        SELECT
            ca.attribute_name,
            ca.attribute_id,
            pa.value_text
        FROM public.common_attributes ca
        LEFT JOIN public.product_attributes pa
            ON pa.product_id = %s
           AND pa.attribute_id = ca.attribute_id
        ORDER BY lower(ca.attribute_name), ca.attribute_id
    """
    category_query = """
        SELECT
            ca.attribute_name,
            ca.attribute_id,
            pa.value_text
        FROM public.category_attributes ca
        LEFT JOIN public.product_attributes pa
            ON pa.product_id = %s
           AND pa.attribute_id = ca.attribute_id
        WHERE ca.category_id = %s
        ORDER BY lower(ca.attribute_name), ca.attribute_id
    """

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(detail_query, [sku, sku])
        detail = cur.fetchone()
        if not detail:
            return {"ok": False, "error": f"SKU {sku} не найден в SKU-скоринге WB"}
        detail = {key: normalize_value(value) for key, value in dict(detail).items()}
        legacy_category_id = detail.get("category_id")
        content_card = None
        content_category_schema = []
        if relation_exists(cur, "wb_api_entities"):
            cur.execute(
                """
                SELECT payload
                FROM public.wb_api_entities
                WHERE source_key = 'content.cards'
                  AND coalesce(payload->>'nmID', payload->>'nmId') = %s
                ORDER BY captured_at DESC, entity_key DESC
                LIMIT 1
                """,
                [sku],
            )
            content_row = cur.fetchone()
            if content_row and isinstance(content_row.get("payload"), dict):
                content_card = content_row["payload"]
                category_key = str(content_card.get("subjectID") or "").strip()
                category_name = str(content_card.get("subjectName") or "").strip()
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON ((payload->>'nmID')::bigint) payload
                        FROM public.wb_api_entities
                        WHERE source_key = 'content.cards'
                          AND nullif(payload->>'nmID', '') IS NOT NULL
                          AND (
                              (%s <> '' AND payload->>'subjectID' = %s)
                              OR (%s = '' AND lower(btrim(payload->>'subjectName')) = lower(%s))
                          )
                        ORDER BY (payload->>'nmID')::bigint, captured_at DESC, entity_key DESC
                    )
                    SELECT
                        coalesce(
                            nullif(item->>'id', ''),
                            'name:' || lower(btrim(coalesce(item->>'name', '')))
                        ) AS attribute_key,
                        max(nullif(item->>'id', '')) AS attribute_id,
                        max(nullif(item->>'name', '')) AS attribute_name
                    FROM latest
                    CROSS JOIN LATERAL jsonb_array_elements(CASE
                        WHEN jsonb_typeof(payload->'characteristics') = 'array' THEN payload->'characteristics'
                        ELSE '[]'::jsonb
                    END) item
                    WHERE nullif(item->>'id', '') IS NOT NULL
                       OR nullif(btrim(item->>'name'), '') IS NOT NULL
                    GROUP BY 1
                    ORDER BY lower(max(coalesce(item->>'name', ''))), 1
                    """,
                    [category_key, category_key, category_key, category_name],
                )
                content_category_schema = [dict(row) for row in cur.fetchall()]

        media = []
        content_attribute_rows = []
        if content_card:
            dimensions = content_card.get("dimensions") if isinstance(content_card.get("dimensions"), dict) else {}
            media = wb_content_card_media(content_card)
            content_attribute_rows = wb_content_card_attribute_rows(content_card, content_category_schema)
            detail.update(
                {
                    "naimenovanie": content_card.get("title") or detail.get("naimenovanie"),
                    "card_name": content_card.get("title") or detail.get("card_name"),
                    "opisanie": content_card.get("description") or detail.get("opisanie"),
                    "artikul_prodavtsa": content_card.get("vendorCode") or detail.get("artikul_prodavtsa"),
                    "brend": content_card.get("brand") or detail.get("brend"),
                    "category_name": content_card.get("subjectName") or detail.get("category_name"),
                    "card_category_name": content_card.get("subjectName") or detail.get("card_category_name"),
                    "product_id": content_card.get("nmID") or detail.get("product_id"),
                    "category_id": content_card.get("subjectID") or detail.get("category_id"),
                    "ves_s_upakovkoy_kg": dimensions.get("weightBrutto") or detail.get("ves_s_upakovkoy_kg"),
                    "dlina_upakovki": dimensions.get("length") or detail.get("dlina_upakovki"),
                    "shirina_upakovki": dimensions.get("width") or detail.get("shirina_upakovki"),
                    "vysota_upakovki": dimensions.get("height") or detail.get("vysota_upakovki"),
                    "foto_count": len([item for item in media if item.get("kind") == "photo"]),
                }
            )
            detail["naimenovanie_len"] = len(str(detail.get("naimenovanie") or "").strip())
            detail["opisanie_len"] = len(str(detail.get("opisanie") or "").strip())
        if marketplace in ABC_BASE_VIEWS:
            order_filter, order_values = ozon_abc_mv_filters(parsed.query, "o", include_date=True)
            cur.execute(
                f"""
                SELECT
                    coalesce(sum(o.zakazano_sht), 0)::numeric AS zakazano_sht,
                    coalesce(sum(o.zakazano_rub), 0)::numeric AS zakazano_rub
                FROM public.{abc_base_views_for_marketplace(marketplace)["orders"]} o
                WHERE {order_filter}
                  AND o.artikul_wb::text = %s
                """,
                order_values + [sku],
            )
            period_orders = dict(cur.fetchone())
            detail["zakazano_sht"] = normalize_value(period_orders.get("zakazano_sht"))
            detail["zakazano_rub"] = normalize_value(period_orders.get("zakazano_rub"))

        fallback_attribute_rows = [
            sku_card_attribute_row("Наименование", "Общая", detail.get("naimenovanie") or detail.get("card_name")),
            sku_card_attribute_row("Описание", "Общая", detail.get("opisanie")),
        ]
        used_names = {"Наименование", "Описание"}
        for attribute_name, column_name in WB_SKU_CARD_COMMON_ATTRIBUTES:
            if attribute_name in used_names:
                continue
            fallback_attribute_rows.append(sku_card_attribute_row(attribute_name, "Общая", detail.get(column_name)))
            used_names.add(attribute_name)

        product_id = detail.get("product_id")
        category_id = detail.get("category_id")
        if product_id:
            cur.execute(common_query, [product_id])
            for row in cur.fetchall():
                row = dict(row)
                attribute_name = row.get("attribute_name") or f"Атрибут {row.get('attribute_id')}"
                if attribute_name in used_names:
                    continue
                fallback_attribute_rows.append(sku_card_attribute_row(attribute_name, "Общая", row.get("value_text")))
                used_names.add(attribute_name)
        if product_id and legacy_category_id:
            cur.execute(category_query, [product_id, legacy_category_id])
            for row in cur.fetchall():
                row = dict(row)
                fallback_attribute_rows.append(
                    sku_card_attribute_row(
                        row.get("attribute_name") or f"Атрибут {row.get('attribute_id')}",
                        "Категорийная",
                        row.get("value_text"),
                    )
                )

        attribute_rows = (
            content_attribute_rows
            if content_card
            else merge_sku_card_attribute_rows(content_attribute_rows, fallback_attribute_rows)
        )
        common_rows = [row for row in attribute_rows if row.get("attribute_kind") == "Общая"]
        category_rows = [row for row in attribute_rows if row.get("attribute_kind") == "Категорийная"]
        detail["common_attrs_total"] = len(common_rows)
        detail["common_attrs_filled"] = sum(1 for row in common_rows if row.get("filled"))
        detail["category_attrs_total"] = len(category_rows)
        detail["category_attrs_filled"] = sum(1 for row in category_rows if row.get("filled"))

    return build_sku_card_payload(
        marketplace,
        sku,
        detail,
        attribute_rows,
        media=media,
        source="WB Content API · content.cards" if content_card else "Нормализованные таблицы WB",
    )


def handle_product_stats(parsed):
    params = parse_qs(parsed.query)
    if is_ozon_abc_query(parsed.query):
        return handle_ozon_product_stats(parsed)
    view_name = product_view_for_query(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=True,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        base_columns = get_view_columns(cur, view_name)
        columns = base_columns + MAPPING_COLUMNS
        where, values = append_column_filters(where, values, parsed.query, base_columns, alias="v")
        sort_column = params.get("sort_col", ["zakazano_sht"])[0]
        if sort_column not in columns:
            sort_column = "zakazano_sht" if "zakazano_sht" in columns else columns[0]

        count_query = sql.SQL("SELECT count(*) AS total FROM public.{view} v {join} {where}").format(
            view=sql.Identifier(view_name),
            join=mapping_join_for_report("product", marketplace),
            where=sql.SQL(where),
        )
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.*
                {mapping_cols}
            FROM public.{view} v
            {join}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, category_name, artikul_wb
            LIMIT %s OFFSET %s
            """
        ).format(
            view=sql.Identifier(view_name),
            mapping_cols=mapping_select_sql(),
            join=mapping_join_for_report("product", marketplace),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_sku_summary(parsed):
    view_name = sku_view_for_query(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=False,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )
    with get_conn() as conn, conn.cursor() as cur:
        base_columns = get_view_columns(cur, view_name)
        source_sql, source_values = sku_scoring_source_sql(parsed.query, marketplace, base_columns)
        query = sql.SQL(
            """
            SELECT
                count(DISTINCT v.category_name) AS categories,
                count(DISTINCT v.artikul_wb) AS sku_count,
                coalesce(sum(v.total_stock_qty), 0) AS total_stock_qty,
                coalesce(sum(v.zakazano_sht), 0) AS zakazano_sht,
                coalesce(sum(v.zakazano_rub), 0) AS zakazano_rub,
                0 AS vykup_pct_sht
            FROM ({source}) v
            {join}
            {where}
            """
        ).format(source=sql.SQL(source_sql), join=mapping_join_for_report("sku", marketplace), where=sql.SQL(where))
        cur.execute(query, source_values + values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        return row

def handle_product_summary(parsed):
    if is_ozon_abc_query(parsed.query):
        return handle_ozon_product_summary(parsed)
    view_name = product_view_for_query(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=True,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )
    query = sql.SQL(
        """
        SELECT
            count(DISTINCT category_name) AS categories,
            count(DISTINCT artikul_wb) AS sku_count,
            coalesce(sum(total_stock_qty), 0) AS total_stock_qty,
            coalesce(sum(zakazano_sht), 0) AS zakazano_sht,
            coalesce(sum(zakazano_rub), 0) AS zakazano_rub,
            0 AS vykup_pct_sht
        FROM public.{view} v
        {join}
        {where}
        """
    ).format(view=sql.Identifier(view_name), join=mapping_join_for_report("product", marketplace), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        return row


def handle_adv_stats(parsed):
    params = parse_qs(parsed.query)
    marketplace = adv_marketplace_from_query(parsed.query)
    view_name = adv_view_for_marketplace(marketplace)
    where, values = adv_filters_from_query(parsed.query)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        base_columns = get_view_columns(cur, view_name)
        include_legacy_columns = not current_client_is_boiron()
        columns = adv_product_columns(base_columns, marketplace)
        where, values = append_column_filters(where, values, parsed.query, base_columns, alias="v")
        sort_column = params.get("sort_col", ["report_date"])[0]
        if sort_column not in columns:
            sort_column = "report_date"
        adv_join = (
            sql.SQL("").join(
                [
                    mapping_join_for_report("adv", marketplace),
                    collection_status_join_sql(marketplace, "v.sku"),
                    seo_status_join_sql(marketplace, "v.sku"),
                ]
            )
            if include_legacy_columns
            else sql.SQL("")
        )
        collection_status_col = collection_status_select_sql() if include_legacy_columns else sql.SQL("")
        seo_status_col = seo_status_select_sql() if include_legacy_columns else sql.SQL("")
        mapping_cols = mapping_select_sql() if include_legacy_columns else sql.SQL("")

        excluded_campaign_rows = 0
        if marketplace == "ozon":
            fallback_query = sql.SQL(
                """
                SELECT count(*) AS total
                FROM public.{view} v
                {join}
                {where}
                {tail}
                """
            ).format(
                view=sql.Identifier(view_name),
                join=adv_join,
                where=sql.SQL(where),
                tail=sql.SQL(
                    " AND coalesce(v.ozon_marketplace_article::text, '') LIKE 'campaign:%%'"
                    if where
                    else " WHERE coalesce(v.ozon_marketplace_article::text, '') LIKE 'campaign:%%'"
                ),
            )
            cur.execute(fallback_query, values)
            excluded_campaign_rows = int(cur.fetchone()["total"])
            where = append_adv_product_grain_filter(where, marketplace)

        count_query = sql.SQL("SELECT count(*) AS total FROM public.{view} v {join} {where}").format(
            view=sql.Identifier(view_name),
            join=adv_join,
            where=sql.SQL(where),
        )
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.*,
                {ratio} AS adv_sales_to_total_sales_pct
                {collection_status_col}
                {seo_status_col}
                {mapping_cols}
            FROM public.{view} v
            {join}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, report_date, category_name, product_artikul
            LIMIT %s OFFSET %s
            """
        ).format(
            ratio=adv_ratio_sql(),
            collection_status_col=collection_status_col,
            seo_status_col=seo_status_col,
            mapping_cols=mapping_cols,
            view=sql.Identifier(view_name),
            join=adv_join,
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())
        for row in rows:
            if row.get("source_sheet") == "api-all-sku-orders":
                for field in ("impressions", "clicks", "ctr_pct", "cpc_rub", "cpm_rub", "added_to_cart"):
                    row[field] = None

    if total:
        data_status = "partial" if excluded_campaign_rows else "ok"
        data_message = (
            f"Показатели по реальным SKU: {total}. "
            "Итоги уровня кампаний показаны отдельным блоком выше и не смешиваются с товарами."
        )
    elif excluded_campaign_rows:
        data_status = "partial"
        data_message = (
            "В источнике за выбранный период есть только показатели уровня кампаний. "
            "SKU-детализация недоступна; кампанийные строки показаны выше и не подмешиваются в товары."
        )
    else:
        data_status = "empty"
        data_message = "Нет товарных рекламных данных под выбранные фильтры."

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
        "grain": "date_product_campaign" if marketplace == "ozon" else "product",
        "data_status": data_status,
        "data_message": data_message,
        "excluded_campaign_rows": excluded_campaign_rows,
    }


def handle_adv_campaigns(parsed):
    """Return one compact row per Ozon campaign under the active dashboard filters."""
    columns = [
        "campaign_id",
        "campaign_title",
        "instrument",
        "placement",
        "date_from",
        "date_to",
        "promoted_sku_count",
        "impressions",
        "clicks",
        "expense_rub",
        "direct_orders_qty",
        "indirect_orders_qty",
        "attributed_orders_qty",
        "direct_orders_amount_rub",
        "indirect_orders_amount_rub",
        "attributed_orders_amount_rub",
        "attributed_drr_pct",
    ]
    numeric_columns = {
        "promoted_sku_count",
        "impressions",
        "clicks",
        "expense_rub",
        "direct_orders_qty",
        "indirect_orders_qty",
        "attributed_orders_qty",
        "direct_orders_amount_rub",
        "indirect_orders_amount_rub",
        "attributed_orders_amount_rub",
        "attributed_drr_pct",
    }
    labels = {
        "campaign_id": "ID кампании",
        "campaign_title": "Название кампании",
        "instrument": "Инструмент",
        "placement": "Место",
        "date_from": "Период от",
        "date_to": "Период до",
        "promoted_sku_count": "SKU в продвижении",
        "impressions": "Показы",
        "clicks": "Клики",
        "expense_rub": "Расход, руб",
        "direct_orders_qty": "Заказы — прямая, шт",
        "indirect_orders_qty": "Заказы — косвенная, шт",
        "attributed_orders_qty": "Заказы — всего, шт",
        "direct_orders_amount_rub": "Продажи — прямая, руб",
        "indirect_orders_amount_rub": "Продажи — косвенная, руб",
        "attributed_orders_amount_rub": "Продажи — всего, руб",
        "attributed_drr_pct": "ДРР атрибуции, %",
    }
    empty_payload = {
        "rows": [],
        "columns": [
            {"key": column, "label": labels[column], "type": "number" if column in numeric_columns else "text"}
            for column in columns
        ],
        "page": 1,
        "page_size": 0,
        "total": 0,
        "total_pages": 1,
        "sort_col": "expense_rub",
        "sort_dir": "desc",
    }

    marketplace = adv_marketplace_from_query(parsed.query)
    if marketplace != "ozon":
        return empty_payload

    view_name = adv_view_for_marketplace(marketplace)
    where, values = adv_filters_from_query(parsed.query)
    campaign_condition = "coalesce(nullif(v.campaign_id::text, ''), '') <> ''"
    where = f"{where} AND {campaign_condition}" if where else f" WHERE {campaign_condition}"
    direct_qty = "coalesce(v.direct_orders_qty, 0)"
    indirect_qty = "coalesce(v.indirect_orders_qty, 0)"
    direct_sales = "coalesce(v.direct_orders_amount_rub, 0)"
    indirect_sales = "coalesce(v.indirect_orders_amount_rub, 0)"
    query = sql.SQL(
        f"""
        SELECT
            coalesce(nullif(v.campaign_id::text, ''), '') AS campaign_id,
            coalesce(string_agg(DISTINCT nullif(v.instrument, ''), ', ' ORDER BY nullif(v.instrument, '')), '') AS instrument,
            coalesce(string_agg(DISTINCT nullif(v.placement, ''), ', ' ORDER BY nullif(v.placement, '')), '') AS placement,
            min(v.report_date) AS date_from,
            max(v.report_date) AS date_to,
            count(DISTINCT {{promoted_sku}}) AS promoted_sku_count,
            coalesce(sum(v.impressions), 0) AS impressions,
            coalesce(sum(v.clicks), 0) AS clicks,
            coalesce(sum(v.expense_rub), 0) AS expense_rub,
            coalesce(sum({direct_qty}), 0) AS direct_orders_qty,
            coalesce(sum({indirect_qty}), 0) AS indirect_orders_qty,
            coalesce(sum({direct_qty} + {indirect_qty}), 0) AS attributed_orders_qty,
            coalesce(sum({direct_sales}), 0) AS direct_orders_amount_rub,
            coalesce(sum({indirect_sales}), 0) AS indirect_orders_amount_rub,
            coalesce(sum({direct_sales} + {indirect_sales}), 0) AS attributed_orders_amount_rub,
            CASE WHEN coalesce(sum({direct_sales} + {indirect_sales}), 0) <> 0
                THEN round(coalesce(sum(v.expense_rub), 0)::numeric / sum({direct_sales} + {indirect_sales}) * 100, 2)
                ELSE 0 END AS attributed_drr_pct
        FROM public.{{view}} v
        {{join}}
        {{where}}
        GROUP BY coalesce(nullif(v.campaign_id::text, ''), '')
        ORDER BY expense_rub DESC, campaign_id
        LIMIT 5000
        """
    ).format(
        promoted_sku=adv_promoted_sku_expr(marketplace),
        view=sql.Identifier(view_name),
        join=mapping_join_for_report("adv", marketplace) if has_mapping_filters(parsed.query) else sql.SQL(""),
        where=sql.SQL(where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())

    from ozon_adv_campaign_metrics import fetch_campaign_metrics

    campaign_rows = fetch_campaign_metrics(get_conn, parsed.query, "campaign")
    if campaign_rows is not None:
        attribution_by_campaign = {str(row.get("campaign_id") or ""): row for row in rows}
        merged_rows = []
        for campaign in campaign_rows:
            campaign_id = str(campaign.get("campaign_id") or "")
            attribution = attribution_by_campaign.get(campaign_id, {})
            direct_qty = to_float(attribution.get("direct_orders_qty"))
            indirect_qty = to_float(attribution.get("indirect_orders_qty"))
            direct_sales = to_float(attribution.get("direct_orders_amount_rub"))
            indirect_sales = to_float(attribution.get("indirect_orders_amount_rub"))
            attributed_sales = direct_sales + indirect_sales
            expense = to_float(attribution.get("expense_rub")) or to_float(campaign.get("campaign_expense_rub"))
            attributed_qty = direct_qty + indirect_qty
            attribution_available = bool(attribution)
            if not attribution_available or not attributed_qty:
                attributed_qty = to_float(campaign.get("campaign_orders_qty"))
            if not attribution_available or not attributed_sales:
                attributed_sales = to_float(campaign.get("campaign_orders_amount_rub"))
            merged_rows.append({
                "campaign_id": campaign_id,
                "campaign_title": campaign.get("campaign_title") or "",
                "instrument": attribution.get("instrument") or "",
                "placement": attribution.get("placement") or "",
                "date_from": campaign.get("date_from"),
                "date_to": campaign.get("date_to"),
                "promoted_sku_count": to_float(attribution.get("promoted_sku_count")),
                "impressions": to_float(campaign.get("impressions")),
                "clicks": to_float(campaign.get("clicks")),
                "expense_rub": expense,
                "direct_orders_qty": direct_qty,
                "indirect_orders_qty": indirect_qty,
                "attributed_orders_qty": attributed_qty,
                "direct_orders_amount_rub": direct_sales,
                "indirect_orders_amount_rub": indirect_sales,
                "attributed_orders_amount_rub": attributed_sales,
                "attributed_drr_pct": round(expense / attributed_sales * 100, 2) if attributed_sales else 0,
                "attribution_breakdown_available": attribution_available,
            })
        rows = merged_rows

    payload = dict(empty_payload)
    payload.update({"rows": rows, "page_size": len(rows), "total": len(rows)})
    if rows and all(row.get("attribution_breakdown_available") is False for row in rows):
        unavailable_columns = {
            "direct_orders_qty", "indirect_orders_qty",
            "direct_orders_amount_rub", "indirect_orders_amount_rub",
        }
        payload["columns"] = [
            column for column in payload["columns"] if column["key"] not in unavailable_columns
        ]
    return payload


def adv_attribution_aggregate_sql(marketplace):
    if marketplace != "ozon":
        return sql.SQL(
            """
            0::numeric AS direct_orders_qty,
            0::numeric AS indirect_orders_qty,
            0::numeric AS direct_orders_amount_rub,
            0::numeric AS indirect_orders_amount_rub,
            0::numeric AS direct_drr_pct,
            0::numeric AS indirect_drr_pct
            """
        )
    return sql.SQL(
        """
        coalesce(sum(nullif(to_jsonb(v)->>'direct_orders_qty', '')::numeric), 0) AS direct_orders_qty,
        coalesce(sum(nullif(to_jsonb(v)->>'indirect_orders_qty', '')::numeric), 0) AS indirect_orders_qty,
        coalesce(sum(nullif(to_jsonb(v)->>'direct_orders_amount_rub', '')::numeric), 0) AS direct_orders_amount_rub,
        coalesce(sum(nullif(to_jsonb(v)->>'indirect_orders_amount_rub', '')::numeric), 0) AS indirect_orders_amount_rub,
        CASE WHEN coalesce(sum(nullif(to_jsonb(v)->>'direct_orders_amount_rub', '')::numeric), 0) <> 0
            THEN round(coalesce(sum(v.expense_rub), 0)::numeric / sum(nullif(to_jsonb(v)->>'direct_orders_amount_rub', '')::numeric) * 100, 2)
            ELSE 0 END AS direct_drr_pct,
        CASE WHEN coalesce(sum(nullif(to_jsonb(v)->>'indirect_orders_amount_rub', '')::numeric), 0) <> 0
            THEN round(coalesce(sum(v.expense_rub), 0)::numeric / sum(nullif(to_jsonb(v)->>'indirect_orders_amount_rub', '')::numeric) * 100, 2)
            ELSE 0 END AS indirect_drr_pct
        """
    )

def handle_adv_summary(parsed):
    marketplace = adv_marketplace_from_query(parsed.query)
    view_name = adv_view_for_marketplace(marketplace)
    where, values = adv_filters_from_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            count(DISTINCT report_date) AS days_count,
            count(DISTINCT category_name) AS categories,
            count(DISTINCT coalesce(product_artikul, seller_article, ozon_marketplace_article)) AS sku_count,
            count(DISTINCT {promoted_sku}) AS promoted_sku_count,
            count(DISTINCT CASE WHEN coalesce(v.total_orders_qty, 0) > 0 THEN {promoted_sku} END) AS ordered_sku_count,
            coalesce(sum(impressions), 0) AS impressions,
            coalesce(sum(clicks), 0) AS clicks,
            bool_or(coalesce(to_jsonb(v)->>'source_sheet', '') <> 'api-all-sku-orders') AS added_to_cart_available,
            coalesce(sum(expense_rub), 0) AS expense_rub,
            coalesce(sum(orders_qty), 0) AS orders_qty,
            coalesce(sum(orders_amount_rub), 0) AS orders_amount_rub,
            {attribution_metrics},
            coalesce(sum(total_orders_qty), 0) AS total_orders_qty,
            coalesce(sum(total_orders_amount_rub), 0) AS total_orders_amount_rub,
            CASE WHEN coalesce(sum(total_orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(orders_amount_rub), 0)::numeric / coalesce(sum(total_orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS adv_sales_to_total_sales_pct,
            CASE WHEN coalesce(sum(total_orders_qty), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(total_orders_qty), 0)::numeric * 100, 2)
                ELSE 0 END AS adv_orders_to_total_orders_pct,
            CASE WHEN coalesce(sum(orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_pct,
            CASE WHEN coalesce(sum(total_orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(total_orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS total_drr_pct
        FROM public.{view} v
        {join}
        {where}
        """
    ).format(
        view=sql.Identifier(view_name),
        promoted_sku=adv_promoted_sku_expr(marketplace),
        attribution_metrics=adv_attribution_aggregate_sql(marketplace),
        join=mapping_join_for_report("adv", marketplace),
        where=sql.SQL(where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = dict(cur.fetchone())
        row["total_sku_count"] = funnel_sku_counts_from_adv_query(cur, parsed.query, marketplace)
        cart_metrics_available = bool(row.pop("added_to_cart_available", True))
        for key in row:
            row[key] = to_float(row[key])
        from ozon_adv_campaign_metrics import fetch_campaign_metrics

        campaign_metrics = fetch_campaign_metrics(get_conn, parsed.query)
        if campaign_metrics is not None:
            attribution_available = bool(row.get("sku_count"))
            row["campaign_count"] = to_float(campaign_metrics.get("campaign_count"))
            row["impressions"] = to_float(campaign_metrics.get("impressions"))
            row["clicks"] = to_float(campaign_metrics.get("clicks"))
            row["expense_rub"] = to_float(campaign_metrics.get("expense_rub"))
            row["orders_qty"] = to_float(campaign_metrics.get("orders_qty"))
            row["orders_amount_rub"] = to_float(campaign_metrics.get("orders_amount_rub"))
            row["ctr_calc_pct"] = round(row["clicks"] / row["impressions"] * 100, 2) if row["impressions"] else 0
            row["cpc_calc_rub"] = round(row["expense_rub"] / row["clicks"], 2) if row["clicks"] else 0
            row["cpm_calc_rub"] = round(row["expense_rub"] / row["impressions"] * 1000, 2) if row["impressions"] else 0
            row["drr_pct"] = round(row["expense_rub"] / row["orders_amount_rub"] * 100, 2) if row["orders_amount_rub"] else 0
            row["adv_sales_to_total_sales_pct"] = round(row["orders_amount_rub"] / row["total_orders_amount_rub"] * 100, 2) if row["total_orders_amount_rub"] else 0
            row["adv_orders_to_total_orders_pct"] = round(row["orders_qty"] / row["total_orders_qty"] * 100, 2) if row["total_orders_qty"] else 0
            row["total_drr_pct"] = round(row["expense_rub"] / row["total_orders_amount_rub"] * 100, 2) if row["total_orders_amount_rub"] else 0
            row["campaign_metrics_scope"] = "campaign"
            row["attribution_breakdown_available"] = attribution_available
        row["added_to_cart_available"] = cart_metrics_available
        row["total_stock_qty"] = row["expense_rub"]
        row["zakazano_rub"] = row["orders_amount_rub"]
        row["vykup_pct_sht"] = row["adv_sales_to_total_sales_pct"]
        return row


def handle_adv_daily(parsed):
    marketplace = adv_marketplace_from_query(parsed.query)
    view_name = adv_view_for_marketplace(marketplace)
    where, values = adv_filters_from_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            report_date,
            coalesce(sum(expense_rub), 0) AS expense_rub,
            coalesce(sum(orders_amount_rub), 0) AS orders_amount_rub,
            coalesce(sum(total_orders_amount_rub), 0) AS total_orders_amount_rub,
            coalesce(sum(impressions), 0) AS impressions,
            count(DISTINCT {promoted_sku}) AS promoted_sku_count,
            count(DISTINCT CASE WHEN coalesce(v.total_orders_qty, 0) > 0 THEN {promoted_sku} END) AS ordered_sku_count,
            coalesce(sum(clicks), 0) AS clicks,
            bool_or(coalesce(to_jsonb(v)->>'source_sheet', '') <> 'api-all-sku-orders') AS added_to_cart_available,
            coalesce(sum(added_to_cart), 0) AS added_to_cart,
            coalesce(sum(orders_qty), 0) AS orders_qty,
            {attribution_metrics},
            coalesce(sum(total_orders_qty), 0) AS total_orders_qty,
            CASE WHEN coalesce(sum(total_orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(orders_amount_rub), 0)::numeric / coalesce(sum(total_orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS adv_sales_to_total_sales_pct,
            CASE WHEN coalesce(sum(total_orders_qty), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(total_orders_qty), 0)::numeric * 100, 2)
                ELSE 0 END AS adv_orders_to_total_orders_pct,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(clicks), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 100, 2)
                ELSE 0 END AS ctr_calc_pct,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(added_to_cart), 0)::numeric / coalesce(sum(clicks), 0)::numeric * 100, 2)
                ELSE 0 END AS click_to_cart_pct,
            CASE WHEN coalesce(sum(added_to_cart), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(added_to_cart), 0)::numeric * 100, 2)
                ELSE 0 END AS cart_to_order_pct,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 100, 4)
                ELSE 0 END AS impression_to_order_pct,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(clicks), 0)::numeric * 100, 2)
                ELSE 0 END AS click_to_order_pct,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(clicks), 0)::numeric, 2)
                ELSE 0 END AS cpc_calc_rub,
            CASE WHEN coalesce(sum(orders_qty), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(orders_qty), 0)::numeric, 2)
                ELSE 0 END AS cpa_calc_rub,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 1000, 2)
                ELSE 0 END AS cpm_calc_rub,
            CASE WHEN coalesce(sum(orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_pct,
            CASE WHEN coalesce(sum(total_orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(total_orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS total_drr_pct
        FROM public.{view} v
        {join}
        {where}
        GROUP BY report_date
        ORDER BY report_date
        """
    ).format(
        view=sql.Identifier(view_name),
        promoted_sku=adv_promoted_sku_expr(marketplace),
        attribution_metrics=adv_attribution_aggregate_sql(marketplace),
        join=mapping_join_for_report("adv", marketplace),
        where=sql.SQL(where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())
        attribution_dates = {str(row.get("report_date") or "") for row in rows}
        rows_by_date = {str(row.get("report_date") or ""): row for row in rows}
        funnel_counts = funnel_sku_counts_from_adv_query(cur, parsed.query, marketplace, by_date=True)
        for report_date, total_sku_count in funnel_counts.items():
            row = rows_by_date.setdefault(report_date, {"report_date": report_date})
            row["total_sku_count"] = total_sku_count

        from ozon_adv_campaign_metrics import fetch_campaign_metrics

        campaign_daily = fetch_campaign_metrics(get_conn, parsed.query, "date")
        if campaign_daily is not None:
            for campaign_row in campaign_daily:
                report_date = str(campaign_row.get("report_date") or "")
                row = rows_by_date.setdefault(report_date, {"report_date": report_date})
                impressions = to_float(campaign_row.get("impressions"))
                clicks = to_float(campaign_row.get("clicks"))
                expense = to_float(campaign_row.get("expense_rub"))
                orders = to_float(campaign_row.get("orders_qty"))
                revenue = to_float(campaign_row.get("orders_amount_rub"))
                row["impressions"] = impressions
                row["clicks"] = clicks
                row["expense_rub"] = expense
                row["orders_qty"] = orders
                row["orders_amount_rub"] = revenue
                row["campaign_count"] = to_float(campaign_row.get("campaign_count"))
                row["ctr_calc_pct"] = round(clicks / impressions * 100, 2) if impressions else 0
                row["impression_to_order_pct"] = round(orders / impressions * 100, 4) if impressions else 0
                row["click_to_order_pct"] = round(orders / clicks * 100, 2) if clicks else 0
                row["cpc_calc_rub"] = round(expense / clicks, 2) if clicks else 0
                row["cpa_calc_rub"] = round(expense / orders, 2) if orders else 0
                row["cpm_calc_rub"] = round(expense / impressions * 1000, 2) if impressions else 0
                row["drr_pct"] = round(expense / revenue * 100, 2) if revenue else 0
                row["campaign_metrics_scope"] = "campaign"
                row["attribution_breakdown_available"] = report_date in attribution_dates

        for row in rows_by_date.values():
            row["total_sku_count"] = to_float(row.get("total_sku_count", 0))
            row["added_to_cart_available"] = bool(row.get("added_to_cart_available", False))
        return {"rows": sorted(rows_by_date.values(), key=lambda item: str(item.get("report_date") or ""))}


def handle_adv_waterfalls(parsed):
    marketplace = adv_marketplace_from_query(parsed.query)
    view_name = adv_view_for_marketplace(marketplace)
    where, values = adv_filters_from_query(parsed.query)

    def top_query(label_column, value_column):
        return sql.SQL(
            """
            SELECT
                coalesce({label}, 'Без названия') AS label,
                coalesce(sum({value}), 0) AS value
            FROM public.{view} v
            {join}
            {where}
            GROUP BY coalesce({label}, 'Без названия')
            ORDER BY value DESC NULLS LAST, label
            LIMIT 10
            """
        ).format(
            label=sql.Identifier(label_column),
            value=sql.Identifier(value_column),
            view=sql.Identifier(view_name),
            join=mapping_join_for_report("adv", marketplace),
            where=sql.SQL(where),
        )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(top_query("category_name", "orders_amount_rub"), values)
        category_sales = normalize_rows(cur.fetchall())
        cur.execute(top_query("category_name", "expense_rub"), values)
        category_expense = normalize_rows(cur.fetchall())
        cur.execute(top_query("product_name", "orders_amount_rub"), values)
        product_sales = normalize_rows(cur.fetchall())
        cur.execute(top_query("product_name", "expense_rub"), values)
        product_expense = normalize_rows(cur.fetchall())

    return {
        "category_sales": category_sales,
        "category_expense": category_expense,
        "product_sales": product_sales,
        "product_expense": product_expense,
    }


def handle_media_adv_stats(parsed):
    params = parse_qs(parsed.query)
    where, values = media_adv_filters_from_query(parsed.query)
    view_name = media_adv_view_for_query(parsed.query)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, view_name):
            return {
                "rows": [],
                "columns": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 1,
                "sort_col": "report_date",
                "sort_dir": sort_dir,
                "available": False,
                "unavailable_reason": "source_not_loaded",
            }
        columns = media_adv_columns(cur, view_name)
        where, values = append_column_filters(where, values, parsed.query, columns, alias="v")
        sort_column = params.get("sort_col", ["report_date"])[0]
        if sort_column not in columns:
            sort_column = "report_date"

        count_query = sql.SQL("SELECT count(*) AS total FROM public.{view} v {where}").format(
            view=sql.Identifier(view_name),
            where=sql.SQL(where),
        )
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT v.*
            FROM public.{view} v
            {where}
            ORDER BY {sort_col} {direction} {nulls}, campaign_format, campaign_name
            LIMIT %s OFFSET %s
            """
        ).format(
            view=sql.Identifier(view_name),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_media_adv_summary(parsed):
    where, values = media_adv_filters_from_query(parsed.query)
    view_name = media_adv_view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            count(DISTINCT report_date) AS days_count,
            count(DISTINCT category_name) AS categories,
            count(DISTINCT campaign_id) AS campaign_count,
            coalesce(sum(expense_rub), 0) AS expense_rub,
            coalesce(sum(impressions), 0) AS impressions,
            coalesce(sum(clicks), 0) AS clicks,
            coalesce(sum(orders_qty), 0) AS orders_qty,
            coalesce(sum(orders_amount_rub), 0) AS orders_amount_rub,
            coalesce(sum(post_view_orders_qty), 0) AS post_view_orders_qty,
            coalesce(sum(post_view_revenue_rub), 0) AS post_view_revenue_rub,
            coalesce(sum(attributed_orders_qty), 0) AS attributed_orders_qty,
            coalesce(sum(attributed_revenue_rub), 0) AS attributed_revenue_rub,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(clicks), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 100, 2)
                ELSE 0 END AS ctr_calc_pct,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(clicks), 0)::numeric, 2)
                ELSE 0 END AS cpc_calc_rub,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 1000, 2)
                ELSE 0 END AS cpm_calc_rub,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(clicks), 0)::numeric * 100, 2)
                ELSE 0 END AS click_to_order_pct,
            CASE WHEN coalesce(sum(orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_direct_pct,
            CASE WHEN coalesce(sum(attributed_revenue_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(attributed_revenue_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_attributed_pct,
            CASE WHEN coalesce(sum(expense_rub), 0) <> 0
                THEN round(coalesce(sum(attributed_revenue_rub), 0)::numeric / coalesce(sum(expense_rub), 0)::numeric, 2)
                ELSE 0 END AS attributed_roas,
            CASE WHEN coalesce(sum(attributed_revenue_rub), 0) <> 0
                THEN round(coalesce(sum(post_view_revenue_rub), 0)::numeric / coalesce(sum(attributed_revenue_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS post_view_revenue_share_pct
        FROM public.{view} v
        {where}
        """
    ).format(view=sql.Identifier(view_name), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, view_name):
            return {
                "days_count": 0,
                "categories": 0,
                "campaign_count": 0,
                "sku_count": 0,
                "expense_rub": 0,
                "impressions": 0,
                "clicks": 0,
                "orders_qty": 0,
                "orders_amount_rub": 0,
                "post_view_orders_qty": 0,
                "post_view_revenue_rub": 0,
                "attributed_orders_qty": 0,
                "attributed_revenue_rub": 0,
                "ctr_calc_pct": 0,
                "cpc_calc_rub": 0,
                "cpm_calc_rub": 0,
                "click_to_order_pct": 0,
                "drr_direct_pct": 0,
                "drr_attributed_pct": 0,
                "attributed_roas": 0,
                "post_view_revenue_share_pct": 0,
                "total_stock_qty": 0,
                "zakazano_rub": 0,
                "vykup_pct_sht": 0,
                "available": False,
                "unavailable_reason": "source_not_loaded",
            }
        cur.execute(query, values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        row["sku_count"] = row["campaign_count"]
        row["total_stock_qty"] = row["expense_rub"]
        row["zakazano_rub"] = row["attributed_revenue_rub"]
        row["vykup_pct_sht"] = row["drr_attributed_pct"]
        return row


def handle_media_adv_daily(parsed):
    where, values = media_adv_filters_from_query(parsed.query)
    view_name = media_adv_view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            report_date,
            coalesce(sum(expense_rub), 0) AS expense_rub,
            coalesce(sum(impressions), 0) AS impressions,
            coalesce(sum(clicks), 0) AS clicks,
            coalesce(sum(orders_qty), 0) AS orders_qty,
            coalesce(sum(orders_amount_rub), 0) AS orders_amount_rub,
            coalesce(sum(post_view_orders_qty), 0) AS post_view_orders_qty,
            coalesce(sum(post_view_revenue_rub), 0) AS post_view_revenue_rub,
            coalesce(sum(attributed_orders_qty), 0) AS attributed_orders_qty,
            coalesce(sum(attributed_revenue_rub), 0) AS attributed_revenue_rub,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(clicks), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 100, 2)
                ELSE 0 END AS ctr_calc_pct,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(clicks), 0)::numeric, 2)
                ELSE 0 END AS cpc_calc_rub,
            CASE WHEN coalesce(sum(impressions), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(impressions), 0)::numeric * 1000, 2)
                ELSE 0 END AS cpm_calc_rub,
            CASE WHEN coalesce(sum(clicks), 0) <> 0
                THEN round(coalesce(sum(orders_qty), 0)::numeric / coalesce(sum(clicks), 0)::numeric * 100, 2)
                ELSE 0 END AS click_to_order_pct,
            CASE WHEN coalesce(sum(orders_amount_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(orders_amount_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_direct_pct,
            CASE WHEN coalesce(sum(attributed_revenue_rub), 0) <> 0
                THEN round(coalesce(sum(expense_rub), 0)::numeric / coalesce(sum(attributed_revenue_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS drr_attributed_pct,
            CASE WHEN coalesce(sum(expense_rub), 0) <> 0
                THEN round(coalesce(sum(attributed_revenue_rub), 0)::numeric / coalesce(sum(expense_rub), 0)::numeric, 2)
                ELSE 0 END AS attributed_roas,
            CASE WHEN coalesce(sum(attributed_revenue_rub), 0) <> 0
                THEN round(coalesce(sum(post_view_revenue_rub), 0)::numeric / coalesce(sum(attributed_revenue_rub), 0)::numeric * 100, 2)
                ELSE 0 END AS post_view_revenue_share_pct
        FROM public.{view} v
        {where}
        GROUP BY report_date
        ORDER BY report_date
        """
    ).format(view=sql.Identifier(view_name), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, view_name):
            return {"rows": [], "available": False, "unavailable_reason": "source_not_loaded"}
        cur.execute(query, values)
        return {"rows": normalize_rows(cur.fetchall())}


def handle_media_adv_waterfalls(parsed):
    where, values = media_adv_filters_from_query(parsed.query)
    view_name = media_adv_view_for_query(parsed.query)

    def top_query(label_column, value_column):
        return sql.SQL(
            """
            SELECT
                coalesce({label}, 'Без названия') AS label,
                coalesce(sum({value}), 0) AS value
            FROM public.{view} v
            {where}
            GROUP BY coalesce({label}, 'Без названия')
            ORDER BY value DESC NULLS LAST, label
            LIMIT 10
            """
        ).format(
            label=sql.Identifier(label_column),
            value=sql.Identifier(value_column),
            view=sql.Identifier(view_name),
            where=sql.SQL(where),
        )

    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, view_name):
            return {
                "format_revenue": [],
                "format_expense": [],
                "campaign_revenue": [],
                "campaign_expense": [],
                "available": False,
                "unavailable_reason": "source_not_loaded",
            }
        cur.execute(top_query("campaign_format", "attributed_revenue_rub"), values)
        format_revenue = normalize_rows(cur.fetchall())
        cur.execute(top_query("campaign_format", "expense_rub"), values)
        format_expense = normalize_rows(cur.fetchall())
        cur.execute(top_query("campaign_name", "attributed_revenue_rub"), values)
        campaign_revenue = normalize_rows(cur.fetchall())
        cur.execute(top_query("campaign_name", "expense_rub"), values)
        campaign_expense = normalize_rows(cur.fetchall())

    return {
        "format_revenue": format_revenue,
        "format_expense": format_expense,
        "campaign_revenue": campaign_revenue,
        "campaign_expense": campaign_expense,
    }


def handle_funnel_stats(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    view_name = funnel_view_for_query(parsed.query)
    where, values = funnel_filters_from_query(parsed.query)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        base_columns = get_view_columns(cur, view_name)
        columns = base_columns + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS
        where, values = append_column_filters(where, values, parsed.query, base_columns, alias="v")
        sort_column = params.get("sort_col", ["report_date"])[0]
        if sort_column not in columns:
            sort_column = "report_date"

        count_query = sql.SQL("SELECT count(*) AS total FROM public.{view} v {join} {where}").format(
            view=sql.Identifier(view_name),
            join=funnel_join_sql(marketplace, include_seo=True, include_collection=True),
            where=sql.SQL(where),
        )
        cur.execute(count_query, values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.*
                {collection_status_col}
                {seo_status_col}
                {mapping_cols}
            FROM public.{view} v
            {join}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, report_date, category_name, product_artikul
            LIMIT %s OFFSET %s
            """
        ).format(
            view=sql.Identifier(view_name),
            collection_status_col=collection_status_select_sql(),
            seo_status_col=seo_status_select_sql(),
            mapping_cols=mapping_select_sql(),
            join=funnel_join_sql(marketplace, include_seo=True, include_collection=True),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values + [page_size, offset])
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": [
            {
                "key": column,
                "label": COLUMN_LABELS.get(column, column),
                "type": "number" if column in NUMERIC_FIELDS else "text",
            }
            for column in columns
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
    }


def handle_funnel_summary(parsed):
    marketplace = marketplace_from_query(parsed.query)
    adv_view = adv_view_for_marketplace(marketplace)
    with get_conn() as conn, conn.cursor() as cur:
        use_rollup, funnel_view = funnel_summary_source_for_query(cur, parsed.query)
        funnel_columns = get_view_columns(cur, funnel_view)
    funnel_where, funnel_values = funnel_filters_from_query(parsed.query)
    adv_where, adv_values = adv_filters_from_query(parsed.query)
    funnel_join = sql.SQL("") if use_rollup else mapping_join_for_report("funnel", marketplace)
    adv_join = sql.SQL("") if use_rollup else mapping_join_for_report("adv", marketplace)
    sku_count_expr = (
        sql.SQL("coalesce(max(v.sku_count), 0)")
        if use_rollup
        else sql.SQL("count(DISTINCT coalesce(v.product_artikul, v.seller_article, v.sku, v.barcode))")
    )
    category_count_expr = (
        sql.SQL("coalesce(max(v.categories), 0)")
        if use_rollup
        else sql.SQL("count(DISTINCT v.category_name)")
    )
    bought_units_expr = sql.SQL("coalesce(sum(v.bought_units), 0)") if marketplace == "wb" else sql.SQL("0::numeric")
    bought_amount_expr = sql.SQL("coalesce(sum(v.bought_amount_rub), 0)") if marketplace == "wb" else sql.SQL("0::numeric")
    wb_extra_select = sql.SQL(
        """
                , 0::numeric AS impressions_card
                , 0::numeric AS cart_adds_search_catalog
                , 0::numeric AS cart_adds_card
                , 0::numeric AS sessions_total
                , 0::numeric AS sessions_search_catalog
                , 0::numeric AS sessions_card
                , coalesce(sum(v.returned_units),0) AS returned_units
                , 0::numeric AS delivered_units
                , coalesce(sum(v.favorites_adds), 0) AS favorites_adds
                , coalesce(sum(v.cancelled_units), 0) AS cancelled_units
                , coalesce(sum(v.cancelled_amount_rub), 0) AS cancelled_amount_rub
                , coalesce(sum(v.wb_club_ordered_units), 0) AS wb_club_ordered_units
                , coalesce(sum(v.wb_club_bought_units), 0) AS wb_club_bought_units
                , coalesce(sum(v.wb_club_cancelled_units), 0) AS wb_club_cancelled_units
                , coalesce(sum(v.wb_club_ordered_amount_rub), 0) AS wb_club_ordered_amount_rub
                , coalesce(sum(v.wb_club_bought_amount_rub), 0) AS wb_club_bought_amount_rub
                , coalesce(sum(v.wb_club_cancelled_amount_rub), 0) AS wb_club_cancelled_amount_rub
        """
    ) if marketplace == "wb" else sql.SQL(
        """
                {ozon_extended_select}
                , 0::numeric AS favorites_adds
                , 0::numeric AS cancelled_amount_rub
                , 0::numeric AS wb_club_ordered_units
                , 0::numeric AS wb_club_bought_units
                , 0::numeric AS wb_club_cancelled_units
                , 0::numeric AS wb_club_ordered_amount_rub
                , 0::numeric AS wb_club_bought_amount_rub
                , 0::numeric AS wb_club_cancelled_amount_rub
        """
    ).format(ozon_extended_select=optional_funnel_sum_select(funnel_columns))
    query = sql.SQL(
        """
        WITH funnel AS (
            SELECT
                count(DISTINCT v.report_date) AS days_count,
                {category_count_expr} AS categories,
                {sku_count_expr} AS sku_count,
                coalesce(sum(v.impressions_total), 0) AS impressions_total,
                coalesce(sum(v.impressions_search_catalog), 0) AS impressions_search_catalog,
                coalesce(sum(v.card_visits), 0) AS card_visits,
                coalesce(sum(v.cart_adds), 0) AS cart_adds,
                coalesce(sum(v.ordered_units), 0) AS ordered_units,
                coalesce(sum(v.ordered_amount_rub), 0) AS ordered_amount_rub,
                {bought_units_expr} AS bought_units,
                {bought_amount_expr} AS bought_amount_rub,
                {cohort_units_expr} AS cohort_bought_units,
                {cohort_amount_expr} AS cohort_bought_amount_rub
                {wb_extra_select}
            FROM public.{funnel_view} v
            {funnel_join}
            {funnel_where}
        ),
        adv AS (
            SELECT
                coalesce(sum(v.impressions), 0) AS adv_impressions,
                coalesce(sum(v.clicks), 0) AS adv_clicks,
                coalesce(sum(v.added_to_cart), 0) AS adv_cart_adds,
                coalesce(sum(v.orders_qty), 0) AS adv_orders,
                coalesce(sum(v.orders_amount_rub), 0) AS adv_orders_amount_rub,
                coalesce(sum(v.expense_rub), 0) AS adv_expense_rub
            FROM public.{adv_view} v
            {adv_join}
            {adv_where}
        )
        SELECT
            f.days_count,
            f.categories,
            f.sku_count,
            f.impressions_total,
            f.impressions_search_catalog,
            f.impressions_card,
            f.sessions_total,
            f.sessions_search_catalog,
            f.sessions_card,
            f.card_visits,
            f.cart_adds,
            f.cart_adds_search_catalog,
            f.cart_adds_card,
            f.returned_units,
            f.delivered_units,
            f.ordered_units,
            f.ordered_amount_rub,
            f.bought_units,
            f.bought_amount_rub,
            f.cohort_bought_units,
            f.cohort_bought_amount_rub,
            f.favorites_adds,
            f.cancelled_units,
            f.cancelled_amount_rub,
            f.wb_club_ordered_units,
            f.wb_club_bought_units,
            f.wb_club_cancelled_units,
            f.wb_club_ordered_amount_rub,
            f.wb_club_bought_amount_rub,
            f.wb_club_cancelled_amount_rub,
            a.adv_impressions,
            a.adv_clicks,
            a.adv_cart_adds,
            a.adv_orders,
            a.adv_orders_amount_rub,
            a.adv_expense_rub,
            greatest(f.impressions_total - a.adv_impressions, 0) AS organic_impressions,
            greatest(f.card_visits - a.adv_clicks, 0) AS organic_card_visits,
            greatest(f.cart_adds - a.adv_cart_adds, 0) AS organic_cart_adds,
            greatest(f.ordered_units - a.adv_orders, 0) AS organic_orders,
            CASE WHEN coalesce(f.impressions_search_catalog, 0) <> 0
                THEN round(coalesce(f.card_visits, 0)::numeric / coalesce(f.impressions_search_catalog, 0)::numeric * 100, 2)
                ELSE 0 END AS search_to_card_visit_pct,
            CASE WHEN coalesce(f.impressions_total, 0) <> 0
                THEN round(coalesce(f.card_visits, 0)::numeric / coalesce(f.impressions_total, 0)::numeric * 100, 2)
                ELSE 0 END AS total_impression_to_card_visit_pct,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.cart_adds, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_cart_pct,
            CASE WHEN coalesce(f.cart_adds, 0) <> 0
                THEN round(coalesce(f.ordered_units, 0)::numeric / coalesce(f.cart_adds, 0)::numeric * 100, 2)
                ELSE 0 END AS cart_to_order_pct,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.ordered_units, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_order_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.ordered_amount_rub, 0)::numeric / coalesce(f.ordered_units, 0)::numeric, 2)
                ELSE 0 END AS ordered_amount_per_unit_rub,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.favorites_adds, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS favorite_to_card_visit_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.cohort_bought_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS buyout_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.cancelled_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS cancellation_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.wb_club_ordered_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS wb_club_order_share_pct,
            CASE WHEN coalesce(a.adv_impressions, 0) <> 0
                THEN round(coalesce(a.adv_clicks, 0)::numeric / coalesce(a.adv_impressions, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_ctr_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_cart_adds, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_click_to_cart_pct,
            CASE WHEN coalesce(a.adv_cart_adds, 0) <> 0
                THEN round(coalesce(a.adv_orders, 0)::numeric / coalesce(a.adv_cart_adds, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_cart_to_order_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_orders, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_click_to_order_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric, 2)
                ELSE 0 END AS adv_cpc_rub,
            CASE WHEN coalesce(a.adv_orders, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_orders, 0)::numeric, 2)
                ELSE 0 END AS adv_cpa_rub,
            CASE WHEN coalesce(a.adv_impressions, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_impressions, 0)::numeric * 1000, 2)
                ELSE 0 END AS adv_cpm_rub,
            CASE WHEN coalesce(a.adv_orders_amount_rub, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_orders_amount_rub, 0)::numeric * 100, 2)
                ELSE 0 END AS acos_pct,
            CASE WHEN coalesce(f.ordered_amount_rub, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(f.ordered_amount_rub, 0)::numeric * 100, 2)
                ELSE 0 END AS tacos_pct
        FROM funnel f CROSS JOIN adv a
        """
    ).format(
        funnel_view=sql.Identifier(funnel_view),
        adv_view=sql.Identifier(adv_view),
        funnel_join=funnel_join,
        adv_join=adv_join,
        sku_count_expr=sku_count_expr,
        category_count_expr=category_count_expr,
        bought_units_expr=bought_units_expr,
        bought_amount_expr=bought_amount_expr,
        cohort_units_expr=sql.SQL("coalesce(sum(v.cohort_bought_units),0)" if marketplace == "wb" else "0::numeric"),
        cohort_amount_expr=sql.SQL("coalesce(sum(v.cohort_bought_amount_rub),0)" if marketplace == "wb" else "0::numeric"),
        wb_extra_select=wb_extra_select,
        funnel_where=sql.SQL(funnel_where),
        adv_where=sql.SQL(adv_where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, funnel_values + adv_values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        row["total_stock_qty"] = row["impressions_total"]
        row["zakazano_rub"] = row["ordered_amount_rub"]
        row["vykup_pct_sht"] = row["cart_to_order_pct"]
        return row


def handle_funnel_daily(parsed):
    marketplace = marketplace_from_query(parsed.query)
    with get_conn() as conn, conn.cursor() as cur:
        use_rollup, funnel_view = funnel_summary_source_for_query(cur, parsed.query)
        funnel_columns = get_view_columns(cur, funnel_view)
    funnel_where, funnel_values = funnel_filters_from_query(parsed.query)
    adv_where, adv_values = adv_filters_from_query(parsed.query)
    funnel_join = sql.SQL("") if use_rollup else mapping_join_for_report("funnel", marketplace)
    adv_join = sql.SQL("") if use_rollup else mapping_join_for_report("adv", marketplace)
    bought_units_expr = sql.SQL("coalesce(sum(v.bought_units), 0)") if marketplace == "wb" else sql.SQL("0::numeric")
    bought_amount_expr = sql.SQL("coalesce(sum(v.bought_amount_rub), 0)") if marketplace == "wb" else sql.SQL("0::numeric")
    wb_extra_select = sql.SQL(
        """
                , 0::numeric AS impressions_card
                , 0::numeric AS cart_adds_search_catalog
                , 0::numeric AS cart_adds_card
                , 0::numeric AS sessions_total
                , 0::numeric AS sessions_search_catalog
                , 0::numeric AS sessions_card
                , coalesce(sum(v.returned_units),0) AS returned_units
                , 0::numeric AS delivered_units
                , coalesce(sum(v.favorites_adds), 0) AS favorites_adds
                , coalesce(sum(v.cancelled_units), 0) AS cancelled_units
                , coalesce(sum(v.cancelled_amount_rub), 0) AS cancelled_amount_rub
                , coalesce(sum(v.wb_club_ordered_units), 0) AS wb_club_ordered_units
                , coalesce(sum(v.wb_club_bought_units), 0) AS wb_club_bought_units
                , coalesce(sum(v.wb_club_cancelled_units), 0) AS wb_club_cancelled_units
                , coalesce(sum(v.wb_club_ordered_amount_rub), 0) AS wb_club_ordered_amount_rub
                , coalesce(sum(v.wb_club_bought_amount_rub), 0) AS wb_club_bought_amount_rub
                , coalesce(sum(v.wb_club_cancelled_amount_rub), 0) AS wb_club_cancelled_amount_rub
        """
    ) if marketplace == "wb" else sql.SQL(
        """
                {ozon_extended_select}
                , 0::numeric AS favorites_adds
                , 0::numeric AS cancelled_amount_rub
                , 0::numeric AS wb_club_ordered_units
                , 0::numeric AS wb_club_bought_units
                , 0::numeric AS wb_club_cancelled_units
                , 0::numeric AS wb_club_ordered_amount_rub
                , 0::numeric AS wb_club_bought_amount_rub
                , 0::numeric AS wb_club_cancelled_amount_rub
        """
    ).format(ozon_extended_select=optional_funnel_sum_select(funnel_columns))
    query = sql.SQL(
        """
        WITH funnel AS (
            SELECT
                v.report_date,
                coalesce(sum(v.impressions_total), 0) AS impressions_total,
                coalesce(sum(v.impressions_search_catalog), 0) AS impressions_search_catalog,
                coalesce(sum(v.card_visits), 0) AS card_visits,
                coalesce(sum(v.cart_adds), 0) AS cart_adds,
                coalesce(sum(v.ordered_units), 0) AS ordered_units,
                coalesce(sum(v.ordered_amount_rub), 0) AS ordered_amount_rub,
                {bought_units_expr} AS bought_units,
                {bought_amount_expr} AS bought_amount_rub,
                {cohort_units_expr} AS cohort_bought_units,
                {cohort_amount_expr} AS cohort_bought_amount_rub
                {wb_extra_select}
            FROM public.{funnel_view} v
            {funnel_join}
            {funnel_where}
            GROUP BY v.report_date
        ),
        adv AS (
            SELECT
                v.report_date,
                coalesce(sum(v.impressions), 0) AS adv_impressions,
                coalesce(sum(v.clicks), 0) AS adv_clicks,
                coalesce(sum(v.added_to_cart), 0) AS adv_cart_adds,
                coalesce(sum(v.orders_qty), 0) AS adv_orders,
                coalesce(sum(v.orders_amount_rub), 0) AS adv_orders_amount_rub,
                coalesce(sum(v.expense_rub), 0) AS adv_expense_rub
            FROM public.{adv_view} v
            {adv_join}
            {adv_where}
            GROUP BY v.report_date
        )
        SELECT
            coalesce(f.report_date, a.report_date) AS report_date,
            coalesce(f.impressions_total, 0) AS impressions_total,
            coalesce(f.impressions_search_catalog, 0) AS impressions_search_catalog,
            coalesce(f.impressions_card, 0) AS impressions_card,
            coalesce(f.sessions_total, 0) AS sessions_total,
            coalesce(f.sessions_search_catalog, 0) AS sessions_search_catalog,
            coalesce(f.sessions_card, 0) AS sessions_card,
            coalesce(f.card_visits, 0) AS card_visits,
            coalesce(f.cart_adds, 0) AS cart_adds,
            coalesce(f.cart_adds_search_catalog, 0) AS cart_adds_search_catalog,
            coalesce(f.cart_adds_card, 0) AS cart_adds_card,
            coalesce(f.returned_units, 0) AS returned_units,
            coalesce(f.delivered_units, 0) AS delivered_units,
            coalesce(f.ordered_units, 0) AS ordered_units,
            coalesce(f.ordered_amount_rub, 0) AS ordered_amount_rub,
            coalesce(f.bought_units, 0) AS bought_units,
            coalesce(f.bought_amount_rub, 0) AS bought_amount_rub,
            coalesce(f.cohort_bought_units,0) AS cohort_bought_units,
            coalesce(f.cohort_bought_amount_rub,0) AS cohort_bought_amount_rub,
            coalesce(f.favorites_adds, 0) AS favorites_adds,
            coalesce(f.cancelled_units, 0) AS cancelled_units,
            coalesce(f.cancelled_amount_rub, 0) AS cancelled_amount_rub,
            coalesce(f.wb_club_ordered_units, 0) AS wb_club_ordered_units,
            coalesce(f.wb_club_bought_units, 0) AS wb_club_bought_units,
            coalesce(f.wb_club_cancelled_units, 0) AS wb_club_cancelled_units,
            coalesce(f.wb_club_ordered_amount_rub, 0) AS wb_club_ordered_amount_rub,
            coalesce(f.wb_club_bought_amount_rub, 0) AS wb_club_bought_amount_rub,
            coalesce(f.wb_club_cancelled_amount_rub, 0) AS wb_club_cancelled_amount_rub,
            coalesce(a.adv_impressions, 0) AS adv_impressions,
            coalesce(a.adv_clicks, 0) AS adv_clicks,
            coalesce(a.adv_cart_adds, 0) AS adv_cart_adds,
            coalesce(a.adv_orders, 0) AS adv_orders,
            coalesce(a.adv_orders_amount_rub, 0) AS adv_orders_amount_rub,
            coalesce(a.adv_expense_rub, 0) AS adv_expense_rub,
            greatest(coalesce(f.impressions_total, 0) - coalesce(a.adv_impressions, 0), 0) AS organic_impressions,
            greatest(coalesce(f.card_visits, 0) - coalesce(a.adv_clicks, 0), 0) AS organic_card_visits,
            greatest(coalesce(f.cart_adds, 0) - coalesce(a.adv_cart_adds, 0), 0) AS organic_cart_adds,
            greatest(coalesce(f.ordered_units, 0) - coalesce(a.adv_orders, 0), 0) AS organic_orders,
            CASE WHEN coalesce(f.impressions_search_catalog, 0) <> 0
                THEN round(coalesce(f.card_visits, 0)::numeric / coalesce(f.impressions_search_catalog, 0)::numeric * 100, 2)
                ELSE 0 END AS search_to_card_visit_pct,
            CASE WHEN coalesce(f.impressions_total, 0) <> 0
                THEN round(coalesce(f.card_visits, 0)::numeric / coalesce(f.impressions_total, 0)::numeric * 100, 2)
                ELSE 0 END AS total_impression_to_card_visit_pct,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.cart_adds, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_cart_pct,
            CASE WHEN coalesce(f.cart_adds, 0) <> 0
                THEN round(coalesce(f.ordered_units, 0)::numeric / coalesce(f.cart_adds, 0)::numeric * 100, 2)
                ELSE 0 END AS cart_to_order_pct,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.ordered_units, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_order_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.ordered_amount_rub, 0)::numeric / coalesce(f.ordered_units, 0)::numeric, 2)
                ELSE 0 END AS ordered_amount_per_unit_rub,
            CASE WHEN coalesce(f.card_visits, 0) <> 0
                THEN round(coalesce(f.favorites_adds, 0)::numeric / coalesce(f.card_visits, 0)::numeric * 100, 2)
                ELSE 0 END AS favorite_to_card_visit_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.cohort_bought_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS buyout_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.cancelled_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS cancellation_pct,
            CASE WHEN coalesce(f.ordered_units, 0) <> 0
                THEN round(coalesce(f.wb_club_ordered_units, 0)::numeric / coalesce(f.ordered_units, 0)::numeric * 100, 2)
                ELSE 0 END AS wb_club_order_share_pct,
            CASE WHEN coalesce(a.adv_impressions, 0) <> 0
                THEN round(coalesce(a.adv_clicks, 0)::numeric / coalesce(a.adv_impressions, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_ctr_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_cart_adds, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_click_to_cart_pct,
            CASE WHEN coalesce(a.adv_cart_adds, 0) <> 0
                THEN round(coalesce(a.adv_orders, 0)::numeric / coalesce(a.adv_cart_adds, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_cart_to_order_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_orders, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric * 100, 2)
                ELSE 0 END AS adv_click_to_order_pct,
            CASE WHEN coalesce(a.adv_clicks, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_clicks, 0)::numeric, 2)
                ELSE 0 END AS adv_cpc_rub,
            CASE WHEN coalesce(a.adv_orders, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_orders, 0)::numeric, 2)
                ELSE 0 END AS adv_cpa_rub,
            CASE WHEN coalesce(a.adv_impressions, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_impressions, 0)::numeric * 1000, 2)
                ELSE 0 END AS adv_cpm_rub,
            CASE WHEN coalesce(a.adv_orders_amount_rub, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(a.adv_orders_amount_rub, 0)::numeric * 100, 2)
                ELSE 0 END AS acos_pct,
            CASE WHEN coalesce(f.ordered_amount_rub, 0) <> 0
                THEN round(coalesce(a.adv_expense_rub, 0)::numeric / coalesce(f.ordered_amount_rub, 0)::numeric * 100, 2)
                ELSE 0 END AS tacos_pct
        FROM funnel f
        FULL OUTER JOIN adv a ON a.report_date = f.report_date
        ORDER BY coalesce(f.report_date, a.report_date)
        """
    ).format(
        funnel_view=sql.Identifier(funnel_view),
        adv_view=sql.Identifier(adv_view_for_marketplace(marketplace)),
        funnel_join=funnel_join,
        adv_join=adv_join,
        bought_units_expr=bought_units_expr,
        bought_amount_expr=bought_amount_expr,
        cohort_units_expr=sql.SQL("coalesce(sum(v.cohort_bought_units),0)" if marketplace == "wb" else "0::numeric"),
        cohort_amount_expr=sql.SQL("coalesce(sum(v.cohort_bought_amount_rub),0)" if marketplace == "wb" else "0::numeric"),
        wb_extra_select=wb_extra_select,
        funnel_where=sql.SQL(funnel_where),
        adv_where=sql.SQL(adv_where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, funnel_values + adv_values)
        return {"rows": normalize_rows(cur.fetchall())}


def handle_inventory_history(parsed):
    from inventory_history import handle_dashboard

    payload = handle_dashboard(parsed, get_conn, marketplace_from_query, handle_funnel_daily, funnel_view_for_marketplace)
    return {**payload, "rows": normalize_rows(payload.get("rows") or [])}


def handle_inventory_history_summary(parsed):
    from inventory_history import handle_summary

    payload = handle_summary(parsed, get_conn, marketplace_from_query, handle_funnel_daily, funnel_view_for_marketplace)
    return normalize_rows([payload])[0]


def handle_inventory_history_products(parsed):
    from inventory_history import handle_products
    from pulse_supply import enrich

    payload = handle_products(
        parsed,
        get_conn,
        marketplace_from_query,
        funnel_view_for_marketplace,
    )
    payload = enrich(payload, parsed, get_conn)
    from inventory_management import enrich as enrich_management
    return enrich_management(payload, parsed, get_conn, funnel_view_for_marketplace(payload['marketplace']))


def cached_read_only_report(namespace, parsed, loader, *, ignore_params=()):
    """Cache expensive analytical GET payloads only in the isolated VPS reader."""
    from pulse_report_cache import cached_payload

    params = parse_qs(parsed.query, keep_blank_values=True)
    client = normalize_client_key((params.get("client") or [current_client_key()])[0])
    for name in ignore_params:
        params.pop(name, None)
    canonical_query = urlencode(
        [(name, value) for name in sorted(params) for value in params[name]]
    )
    generation_file = Path(
        os.environ.get("PULSE_REPORT_CACHE_DIR", "/var/lib/pulse/report_cache")
    ) / "home-marts-generation"
    try:
        generation = generation_file.read_text(encoding="utf-8").strip()
    except OSError:
        generation = "legacy"
    canonical_query = f"{canonical_query}|home_marts={generation}"
    return cached_payload(namespace, f"{client}|{canonical_query}", loader)


WEEKLY_DYNAMICS_RANKING_KEYS = (
    "category_growth_qty", "category_growth_pct", "category_growth_amount",
    "category_decline_qty", "category_decline_pct", "category_decline_amount",
    "sku_growth_qty", "sku_growth_pct", "sku_growth_amount",
    "sku_decline_qty", "sku_decline_pct", "sku_decline_amount",
)


def weekly_dynamics_rankings_payload(rows):
    payload = {key: [] for key in WEEKLY_DYNAMICS_RANKING_KEYS}
    payload.update(previous_week="", current_week="")
    for source in normalize_rows(rows):
        row = {
            "entity_type": source.get("entity_type") or "",
            "entity_key": source.get("entity_key") or "",
            "entity_label": source.get("entity_label") or source.get("entity_key") or "Без названия",
            "previous_week": source.get("previous_week") or "",
            "current_week": source.get("current_week") or "",
            "previous_units": to_float(source.get("previous_units")),
            "current_units": to_float(source.get("current_units")),
            "change_units": to_float(source.get("change_units")),
            "previous_amount_rub": to_float(source.get("previous_amount_rub")),
            "current_amount_rub": to_float(source.get("current_amount_rub")),
            "change_amount_rub": to_float(source.get("change_amount_rub")),
            "change_pct": to_float(source.get("change_pct")),
        }
        payload["previous_week"] = payload["previous_week"] or row["previous_week"]
        payload["current_week"] = payload["current_week"] or row["current_week"]
        prefix = "category" if row["entity_type"] == "category" else "sku"
        checks = (
            ("growth_qty", "change_units", "growth_qty_rank", lambda value: value > 0),
            ("growth_pct", "change_pct", "growth_pct_rank", lambda value: value > 0 and row["previous_amount_rub"] > 0),
            ("growth_amount", "change_amount_rub", "growth_amount_rank", lambda value: value > 0),
            ("decline_qty", "change_units", "decline_qty_rank", lambda value: value < 0),
            ("decline_pct", "change_pct", "decline_pct_rank", lambda value: value < 0 and row["previous_amount_rub"] > 0),
            ("decline_amount", "change_amount_rub", "decline_amount_rank", lambda value: value < 0),
        )
        for suffix, metric, rank, include in checks:
            if include(row[metric]) and to_float(source.get(rank)) <= 10:
                payload[f"{prefix}_{suffix}"].append(row)
    for key in WEEKLY_DYNAMICS_RANKING_KEYS:
        metric = "change_units" if key.endswith("_qty") else ("change_pct" if key.endswith("_pct") else "change_amount_rub")
        payload[key].sort(key=lambda item: item[metric], reverse="growth" in key)
    return payload


def _handle_weekly_dynamics_ranked_sql(parsed):
    marketplace = marketplace_from_query(parsed.query)
    view_name = funnel_view_for_query(parsed.query)
    where, values = funnel_filters_from_query(parsed.query)
    params = parse_qs(parsed.query)
    date_to_raw = params.get("date_to", [""])[0].strip()
    try:
        selected_end = date.fromisoformat(date_to_raw) if date_to_raw else None
    except ValueError:
        selected_end = None
    if selected_end:
        current_week_start = selected_end - timedelta(days=selected_end.weekday())
        ranking_start = current_week_start - timedelta(days=7)
        where += " AND v.report_date >= %s" if where else " WHERE v.report_date >= %s"
        values.append(ranking_start.isoformat())
    where += " AND coalesce(v.ordered_amount_rub, 0) <> 0" if where else " WHERE coalesce(v.ordered_amount_rub, 0) <> 0"
    funnel_join = mapping_join_for_report("funnel", marketplace) if has_mapping_filters(parsed.query) else sql.SQL("")
    sku_expr = sql.SQL(
        "coalesce(nullif(trim(v.sku::text), ''), nullif(trim(v.product_artikul::text), ''), "
        "nullif(trim(v.seller_article::text), ''), nullif(trim(v.barcode::text), ''), "
        "nullif(trim(v.product_name::text), ''), 'Без SKU')"
    )
    query = sql.SQL(
        """
        WITH raw_weekly AS (
            SELECT
                date_trunc('week', v.report_date)::date AS week_start,
                coalesce(nullif(trim(v.category_name::text), ''), 'Без категории') AS category_name,
                {sku_expr} AS sku_key,
                max(coalesce(nullif(trim(v.product_name::text), ''), {sku_expr})) AS sku_label,
                coalesce(sum(v.ordered_amount_rub), 0)::numeric AS ordered_amount_rub
            FROM public.{view} v
            {join}
            {where}
            GROUP BY
                date_trunc('week', v.report_date)::date,
                coalesce(nullif(trim(v.category_name::text), ''), 'Без категории'),
                {sku_expr}
        ),
        entity_weekly AS (
            SELECT
                'category'::text AS entity_type,
                category_name::text AS entity_key,
                category_name::text AS entity_label,
                week_start,
                sum(ordered_amount_rub)::numeric AS ordered_amount_rub
            FROM raw_weekly
            GROUP BY category_name, week_start
            UNION ALL
            SELECT
                'sku'::text AS entity_type,
                sku_key::text AS entity_key,
                max(sku_label)::text AS entity_label,
                week_start,
                sum(ordered_amount_rub)::numeric AS ordered_amount_rub
            FROM raw_weekly
            GROUP BY sku_key, week_start
        ),
        latest AS (
            SELECT max(week_start) AS current_week FROM entity_weekly
        ),
        bounds AS (
            SELECT
                latest.current_week,
                (SELECT max(week_start) FROM entity_weekly WHERE week_start < latest.current_week) AS previous_week
            FROM latest
        ),
        paired AS (
            SELECT
                e.entity_type,
                e.entity_key,
                max(e.entity_label) AS entity_label,
                b.previous_week,
                b.current_week,
                coalesce(sum(e.ordered_amount_rub) FILTER (WHERE e.week_start = b.previous_week), 0)::numeric AS previous_amount_rub,
                coalesce(sum(e.ordered_amount_rub) FILTER (WHERE e.week_start = b.current_week), 0)::numeric AS current_amount_rub
            FROM entity_weekly e
            CROSS JOIN bounds b
            WHERE e.week_start = b.previous_week OR e.week_start = b.current_week
            GROUP BY e.entity_type, e.entity_key, b.previous_week, b.current_week
        ),
        scored AS (
            SELECT
                *,
                current_amount_rub - previous_amount_rub AS change_amount_rub,
                CASE WHEN previous_amount_rub <> 0
                    THEN round((current_amount_rub - previous_amount_rub) / abs(previous_amount_rub) * 100, 2)
                    ELSE NULL END AS change_pct
            FROM paired
        ),
        ranked AS (
            SELECT
                *,
                row_number() OVER (PARTITION BY entity_type ORDER BY change_pct DESC NULLS LAST, change_amount_rub DESC, entity_label) AS growth_pct_rank,
                row_number() OVER (PARTITION BY entity_type ORDER BY change_amount_rub DESC, entity_label) AS growth_amount_rank,
                row_number() OVER (PARTITION BY entity_type ORDER BY change_pct ASC NULLS LAST, change_amount_rub ASC, entity_label) AS decline_pct_rank,
                row_number() OVER (PARTITION BY entity_type ORDER BY change_amount_rub ASC, entity_label) AS decline_amount_rank
            FROM scored
        )
        SELECT *
        FROM ranked
        WHERE
            (change_pct > 0 AND previous_amount_rub > 0 AND growth_pct_rank <= 10)
            OR (change_amount_rub > 0 AND growth_amount_rank <= 10)
            OR (change_pct < 0 AND previous_amount_rub > 0 AND decline_pct_rank <= 10)
            OR (change_amount_rub < 0 AND decline_amount_rank <= 10)
        ORDER BY entity_type, entity_label
        """
    ).format(
        view=sql.Identifier(view_name),
        join=funnel_join,
        where=sql.SQL(where),
        sku_expr=sku_expr,
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        return weekly_dynamics_rankings_payload(cur.fetchall())


def weekly_dynamics_from_weekly_rows(rows, limit=10):
    normalized = normalize_rows(rows)
    weeks = sorted({str(row.get("week_start") or "") for row in normalized if row.get("week_start")})
    payload = {key: [] for key in WEEKLY_DYNAMICS_RANKING_KEYS}
    payload["previous_week"] = weeks[-2] if len(weeks) >= 2 else ""
    payload["current_week"] = weeks[-1] if weeks else ""
    if len(weeks) < 2:
        return payload
    previous_week, current_week = weeks[-2], weeks[-1]
    entities = {"category": {}, "sku": {}}

    def add(entity_type, key, label, week_start, row):
        bucket = entities[entity_type].setdefault(key, {"entity_label": label or key or "Без названия", "weeks": {}})
        week = bucket["weeks"].setdefault(week_start, {"amount": 0.0, "units": 0.0})
        week["amount"] += to_float(row.get("ordered_amount_rub"))
        week["units"] += to_float(row.get("ordered_units"))

    for row in normalized:
        week_start = str(row.get("week_start") or "")
        if week_start not in (previous_week, current_week):
            continue
        category = str(row.get("category_name") or "Без категории")
        sku_key = str(row.get("sku_key") or "Без SKU")
        add("category", category, category, week_start, row)
        add("sku", sku_key, str(row.get("sku_label") or sku_key), week_start, row)

    for entity_type, entity_rows in entities.items():
        prefix = "category" if entity_type == "category" else "sku"
        for entity_key, entity in entity_rows.items():
            previous = entity["weeks"].get(previous_week, {})
            current = entity["weeks"].get(current_week, {})
            previous_units, current_units = to_float(previous.get("units")), to_float(current.get("units"))
            previous_amount, current_amount = to_float(previous.get("amount")), to_float(current.get("amount"))
            change_units, change_amount = current_units - previous_units, current_amount - previous_amount
            change_pct = round(change_amount / abs(previous_amount) * 100, 2) if previous_amount else None
            result = {
                "entity_type": entity_type, "entity_key": entity_key, "entity_label": entity["entity_label"],
                "previous_week": previous_week, "current_week": current_week,
                "previous_units": previous_units, "current_units": current_units, "change_units": change_units,
                "previous_amount_rub": previous_amount, "current_amount_rub": current_amount,
                "change_amount_rub": change_amount, "change_pct": change_pct,
            }
            if change_units > 0: payload[f"{prefix}_growth_qty"].append(result)
            if change_pct is not None and change_pct > 0: payload[f"{prefix}_growth_pct"].append(result)
            if change_amount > 0: payload[f"{prefix}_growth_amount"].append(result)
            if change_units < 0: payload[f"{prefix}_decline_qty"].append(result)
            if change_pct is not None and change_pct < 0: payload[f"{prefix}_decline_pct"].append(result)
            if change_amount < 0: payload[f"{prefix}_decline_amount"].append(result)
    for key in WEEKLY_DYNAMICS_RANKING_KEYS:
        metric = "change_units" if key.endswith("_qty") else ("change_pct" if key.endswith("_pct") else "change_amount_rub")
        payload[key] = sorted(payload[key], key=lambda item: item[metric], reverse="growth" in key)[:limit]
    return payload


def weekly_dynamics_oos_payload(weekly_rows, stock_rows, limit=10):
    rows = normalize_rows(weekly_rows)
    weeks = sorted({str(row.get("week_start") or "") for row in rows if row.get("week_start")})
    empty = {"stock_date": "", "sku_count": 0, "previous_units": 0.0, "current_units": 0.0,
             "previous_amount_rub": 0.0, "current_amount_rub": 0.0, "rows_by_units": [], "rows_by_amount": []}
    if len(weeks) < 2: return empty
    previous_week, current_week = weeks[-2], weeks[-1]
    stocks = {str(row.get("sku_key") or ""): row for row in normalize_rows(stock_rows) if str(row.get("sku_key") or "")}
    sales = {}
    for source in rows:
        week = str(source.get("week_start") or "")
        sku = str(source.get("sku_key") or "")
        if week not in (previous_week, current_week) or not sku: continue
        row = sales.setdefault(sku, {
            "entity_type": "sku", "entity_key": sku, "entity_label": str(source.get("sku_label") or sku),
            "category_name": str(source.get("category_name") or "Без категории"),
            "previous_week": previous_week, "current_week": current_week,
            "previous_units": 0.0, "current_units": 0.0, "previous_amount_rub": 0.0, "current_amount_rub": 0.0,
        })
        side = "previous" if week == previous_week else "current"
        row[f"{side}_units"] += to_float(source.get("ordered_units"))
        row[f"{side}_amount_rub"] += to_float(source.get("ordered_amount_rub"))
    oos = []
    for sku, row in sales.items():
        stock = stocks.get(sku)
        if not stock or to_float(stock.get("total_stock_qty")) > 0 or row["previous_units"] <= 0: continue
        row["total_stock_qty"] = to_float(stock.get("total_stock_qty"))
        row["stock_date"] = str(stock.get("stock_date") or "")
        row["change_units"] = row["current_units"] - row["previous_units"]
        row["change_amount_rub"] = row["current_amount_rub"] - row["previous_amount_rub"]
        row["change_pct"] = round(row["change_amount_rub"] / abs(row["previous_amount_rub"]) * 100, 2) if row["previous_amount_rub"] else None
        oos.append(row)
    result = dict(empty)
    result["stock_date"] = max((row["stock_date"] for row in oos), default="")
    result["sku_count"] = len(oos)
    for field in ("previous_units", "current_units", "previous_amount_rub", "current_amount_rub"):
        result[field] = sum(to_float(row.get(field)) for row in oos)
    result["rows_by_units"] = sorted(oos, key=lambda row: (row["previous_units"], row["previous_amount_rub"]), reverse=True)[:limit]
    result["rows_by_amount"] = sorted(oos, key=lambda row: (row["previous_amount_rub"], row["previous_units"]), reverse=True)[:limit]
    return result


def weekly_dynamics_stock_rows(cur, marketplace):
    if marketplace == "wb":
        query = """
            WITH latest AS (
                SELECT max(snapshot_date) AS stock_date
                FROM public.inventory_history_daily
                WHERE marketplace = 'wb'
            ), stock_keys AS (
                SELECT nullif(trim(stock.sku::text), '') sku_key,
                       stock.stock_total_qty total_stock_qty,
                       stock.snapshot_date stock_date
                FROM public.inventory_history_daily stock
                JOIN latest ON latest.stock_date = stock.snapshot_date
                WHERE stock.marketplace = 'wb' AND nullif(trim(stock.sku::text), '') IS NOT NULL
                UNION
                SELECT nullif(trim(stock.seller_article::text), '') sku_key,
                       stock.stock_total_qty total_stock_qty,
                       stock.snapshot_date stock_date
                FROM public.inventory_history_daily stock
                JOIN latest ON latest.stock_date = stock.snapshot_date
                WHERE stock.marketplace = 'wb'
                  AND nullif(trim(stock.seller_article::text), '') IS NOT NULL
            )
            SELECT sku_key, coalesce(sum(total_stock_qty), 0)::numeric total_stock_qty, max(stock_date)::date stock_date
            FROM stock_keys WHERE sku_key IS NOT NULL GROUP BY sku_key
        """
    else:
        query = """
            WITH stock_keys AS (
                SELECT nullif(trim(sku::text), '') sku_key, available_to_sell total_stock_qty, imported_at::date stock_date
                FROM public.vw_ozon_current_stock_by_sku WHERE nullif(trim(sku::text), '') IS NOT NULL
                UNION
                SELECT nullif(trim(article::text), '') sku_key, available_to_sell total_stock_qty, imported_at::date stock_date
                FROM public.vw_ozon_current_stock_by_sku WHERE nullif(trim(article::text), '') IS NOT NULL
            )
            SELECT sku_key, coalesce(sum(total_stock_qty), 0)::numeric total_stock_qty, max(stock_date)::date stock_date
            FROM stock_keys WHERE sku_key IS NOT NULL GROUP BY sku_key
        """
    cur.execute(query)
    return normalize_rows(cur.fetchall())


def handle_weekly_dynamics(parsed):
    if parse_qs(parsed.query).get("weekly_view", [""])[0] == "decisions":
        from weekly_decisions import load_report
        return load_report(parsed, globals())
    marketplace = marketplace_from_query(parsed.query)
    view_name = funnel_view_for_query(parsed.query)
    where, values = funnel_filters_from_query(parsed.query)
    params = parse_qs(parsed.query)
    try:
        selected_end = date.fromisoformat(params.get("date_to", [""])[0].strip()) if params.get("date_to", [""])[0].strip() else None
    except ValueError:
        selected_end = None
    if selected_end:
        ranking_start = selected_end - timedelta(days=selected_end.weekday() + 7)
        where += " AND v.report_date >= %s" if where else " WHERE v.report_date >= %s"
        values.append(ranking_start.isoformat())
    quantity_predicate = "(coalesce(v.ordered_units, 0) <> 0 OR coalesce(v.ordered_amount_rub, 0) <> 0)"
    where += f" AND {quantity_predicate}" if where else f" WHERE {quantity_predicate}"
    funnel_join = mapping_join_for_report("funnel", marketplace) if has_mapping_filters(parsed.query) else sql.SQL("")
    sku_expr = sql.SQL(
        "coalesce(nullif(trim(v.sku::text), ''), nullif(trim(v.product_artikul::text), ''), "
        "nullif(trim(v.seller_article::text), ''), nullif(trim(v.barcode::text), ''), "
        "nullif(trim(v.product_name::text), ''), 'Без SKU')"
    )
    query = sql.SQL("""
        SELECT date_trunc('week', v.report_date)::date week_start,
            coalesce(nullif(trim(v.category_name::text), ''), 'Без категории') category_name,
            {sku_expr} sku_key, max(coalesce(nullif(trim(v.product_name::text), ''), {sku_expr})) sku_label,
            coalesce(sum(v.ordered_units), 0)::numeric ordered_units,
            coalesce(sum(v.ordered_amount_rub), 0)::numeric ordered_amount_rub
        FROM public.{view} v {join} {where}
        GROUP BY date_trunc('week', v.report_date)::date,
            coalesce(nullif(trim(v.category_name::text), ''), 'Без категории'), {sku_expr}
        ORDER BY week_start, category_name, sku_key
    """).format(view=sql.Identifier(view_name), join=funnel_join, where=sql.SQL(where), sku_expr=sku_expr)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        weekly_rows = normalize_rows(cur.fetchall())
        stock_rows = weekly_dynamics_stock_rows(cur, marketplace)
    payload = weekly_dynamics_from_weekly_rows(weekly_rows)
    payload["oos_impact"] = weekly_dynamics_oos_payload(weekly_rows, stock_rows)
    return payload


def handle_funnel_waterfalls(parsed):
    marketplace = marketplace_from_query(parsed.query)
    view_name = funnel_view_for_query(parsed.query)
    where, values = funnel_filters_from_query(parsed.query)

    def top_query(label_column, value_column):
        return sql.SQL(
            """
            SELECT
                coalesce({label}, 'Без названия') AS label,
                coalesce(sum({value}), 0) AS value
            FROM public.{view} v
            {join}
            {where}
            GROUP BY coalesce({label}, 'Без названия')
            ORDER BY value DESC NULLS LAST, label
            LIMIT 10
            """
        ).format(
            label=sql.Identifier(label_column),
            value=sql.Identifier(value_column),
            view=sql.Identifier(view_name),
            join=mapping_join_for_report("funnel", marketplace),
            where=sql.SQL(where),
        )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(top_query("category_name", "ordered_units"), values)
        category_orders = normalize_rows(cur.fetchall())
        cur.execute(top_query("category_name", "card_visits"), values)
        category_visits = normalize_rows(cur.fetchall())
        cur.execute(top_query("product_name", "ordered_units"), values)
        product_orders = normalize_rows(cur.fetchall())
        cur.execute(top_query("product_name", "cart_adds"), values)
        product_carts = normalize_rows(cur.fetchall())

    return {
        "category_orders": category_orders,
        "category_visits": category_visits,
        "product_orders": product_orders,
        "product_carts": product_carts,
    }


def handle_funnel_filters(parsed):
    marketplace = marketplace_from_query(parsed.query)
    filters_view = funnel_view_for_query(parsed.query, "filters")
    daily_view = funnel_view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            date_from,
            date_to,
            category_names,
            categories
        FROM public.{view}
        """
    ).format(view=sql.Identifier(filters_view))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        payload = dict(cur.fetchone())
        payload["product_names"] = []
        payload["date_from"] = normalize_value(payload.get("date_from"))
        payload["date_to"] = normalize_value(payload.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{daily_view}"
        add_collection_status_options(cur, payload, marketplace)
        add_seo_status_options(cur, payload, marketplace)
        add_ozon_product_attribute_options(cur, payload, marketplace)
        if not compact_filters_requested(parsed.query):
            add_mapping_options(cur, payload, report="funnel", marketplace=marketplace)
        return payload


def handle_seo_monitoring_filters(parsed):
    payload = handle_funnel_filters(parsed)
    marketplace = marketplace_from_query(parsed.query)
    if current_client_key() == "gloria_jeans":
        return payload
    if current_client_key() == "sportmaster":
        sportmaster_options_query = sportmaster_filter_options_query(parsed.query, include_categories=False)
        with get_conn() as conn, conn.cursor() as cur:
            add_sportmaster_filter_options(cur, payload, marketplace, sportmaster_options_query)
    return payload


SEO_MONITORING_COLUMNS = [
    {"key": "selected", "label": "Выбрать", "type": "checkbox"},
    {"key": "naimenovanie", "label": "Название товара", "type": "text"},
    {"key": "zakazano_rub", "label": "Заказы за период, руб", "type": "number"},
    {"key": "zakazano_sht", "label": "Заказы за период, шт", "type": "number"},
    {"key": "total_stock_qty", "label": "Остатки, шт", "type": "number"},
    {"key": "stock_value_rub", "label": "Остатки, руб по средней цене", "type": "number"},
]


def handle_seo_monitoring_products(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query)
    base_sql, base_values = ozon_abc_product_sql(
        parsed.query,
        outer_where=outer_where,
        marketplace=marketplace,
    )
    order_view = abc_base_views_for_marketplace(marketplace)["orders"]
    price_filter, price_values = ozon_abc_mv_filters(parsed.query, "p", include_date=True)

    try:
        page_size = max(5, min(int(params.get("limit", ["50"])[0]), MAX_PAGE_SIZE))
    except ValueError:
        page_size = 50
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1

    allowed_sort_columns = {
        "naimenovanie",
        "zakazano_rub",
        "zakazano_sht",
        "total_stock_qty",
        "stock_value_rub",
    }
    sort_column = params.get("sort_col", ["zakazano_rub"])[0]
    if sort_column not in allowed_sort_columns:
        sort_column = "zakazano_rub"
    sort_dir = params.get("sort_dir", ["desc"])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"

    source_sql = f"""
        WITH products AS (
            {base_sql}
        ),
        price_rows AS (
            SELECT
                p.report_date,
                p.artikul_wb,
                coalesce(sum(p.zakazano_sht), 0)::numeric AS zakazano_sht,
                coalesce(sum(p.zakazano_rub), 0)::numeric AS zakazano_rub
            FROM public.{order_view} p
            WHERE {price_filter}
              AND coalesce(p.zakazano_sht, 0) > 0
            GROUP BY p.report_date, p.artikul_wb
        ),
        latest_prices AS (
            SELECT DISTINCT ON (artikul_wb)
                artikul_wb,
                report_date AS price_date,
                round(zakazano_rub / nullif(zakazano_sht, 0), 2) AS current_avg_price_rub
            FROM price_rows
            ORDER BY artikul_wb, report_date DESC
        )
        SELECT
            p.artikul_wb,
            p.naimenovanie,
            p.zakazano_rub,
            p.zakazano_sht,
            p.total_stock_qty,
            coalesce(lp.current_avg_price_rub, p.avg_price_rub, 0) AS current_avg_price_rub,
            lp.price_date,
            round(
                coalesce(p.total_stock_qty, 0)
                * coalesce(lp.current_avg_price_rub, p.avg_price_rub, 0),
                2
            ) AS stock_value_rub
        FROM products p
        LEFT JOIN latest_prices lp ON lp.artikul_wb = p.artikul_wb
    """
    source_values = base_values + outer_values + price_values

    filters = []
    filter_values = []
    product = params.get("product", [""])[0].strip()
    if product:
        filters.append("coalesce(x.naimenovanie, '') = %s")
        filter_values.append(product)
    article = params.get("article", [""])[0].strip()
    if article:
        filters.append("coalesce(x.artikul_wb::text, '') ILIKE %s")
        filter_values.append(f"%{article}%")
    filterable_columns = [column["key"] for column in SEO_MONITORING_COLUMNS if column["key"] != "selected"]
    column_filter_clauses, column_filter_values = column_filters_sql(
        parsed.query,
        filterable_columns,
        alias="x",
    )
    filters.extend(column_filter_clauses)
    filter_values.extend(column_filter_values)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    query_values = source_values + filter_values

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM ({source_sql}) x{where}", query_values)
        total = int(cur.fetchone()["total"])
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        cur.execute(
            f"""
                SELECT *
                FROM ({source_sql}) x
                {where}
                ORDER BY {sort_column} {direction} {nulls}, naimenovanie, artikul_wb
                LIMIT %s OFFSET %s
            """,
            query_values + [page_size, offset],
        )
        rows = normalize_rows(cur.fetchall())

    return {
        "rows": rows,
        "columns": SEO_MONITORING_COLUMNS,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_column,
        "sort_dir": sort_dir,
        "valuation_note": (
            "Остатки в рублях рассчитаны по последней доступной средней цене заказа "
            "товара в выбранном периоде; если такой цены нет — по средней цене периода."
        ),
    }


def handle_abc_product_options(parsed):
    marketplace = marketplace_from_query(parsed.query)
    base_views = abc_base_views_for_marketplace(marketplace)
    params = parse_qs(parsed.query)
    q = params.get("q", [""])[0].strip()

    source_queries = []
    values = []
    for alias, view_name, include_date in (
        ("s", base_views["stock"], False),
        ("o", base_views["orders"], True),
    ):
        source_filter, source_values = ozon_abc_mv_filters(parsed.query, alias, include_date=include_date)
        category_filters, category_values = abc_source_category_filters_from_query(parsed.query, alias)
        filters = [source_filter, *category_filters]
        source_values.extend(category_values)
        if q:
            filters.append(
                f"(coalesce({alias}.naimenovanie, '') ILIKE %s OR "
                f"coalesce({alias}.artikul_wb::text, '') ILIKE %s)"
            )
            source_values.extend([f"%{q}%"] * 2)
        source_queries.append(
            f"""
                SELECT DISTINCT nullif(trim({alias}.naimenovanie), '') AS product_name
                FROM public.{view_name} {alias}
                WHERE {" AND ".join(filters)}
            """
        )
        values.extend(source_values)

    query = f"""
        SELECT product_name
        FROM (
            {" UNION ".join(source_queries)}
        ) products
        WHERE product_name IS NOT NULL
        ORDER BY product_name
        LIMIT 120
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        return {"product_names": [row["product_name"] for row in cur.fetchall()]}


def handle_funnel_products(parsed):
    params = parse_qs(parsed.query)
    if params.get("dashboard", [""])[0] in {"abc", "product", "sku", "seoMonitoring"}:
        return handle_abc_product_options(parsed)

    marketplace = marketplace_from_query(parsed.query)
    products_view = funnel_view_for_query(parsed.query, "products")
    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    q = params.get("q", [""])[0].strip()
    filters = []
    values = []
    if categories:
        filters.append("category_name = ANY(%s)")
        values.append(categories)
    if q:
        filters.append("product_name ILIKE %s")
        values.append(f"%{q}%")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    query = sql.SQL(
        """
        SELECT product_name
        FROM public.{view} v
        {join}
        {where}
        ORDER BY product_name
        LIMIT 120
        """
    ).format(
        view=sql.Identifier(products_view),
        join=sql.SQL(""),
        where=sql.SQL(where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        return {"product_names": [row["product_name"] for row in cur.fetchall()]}


def planfact_view_available(view_name):
    if current_client_key() in {"km_trade", "sportmaster"}:
        return True
    with get_conn() as conn, conn.cursor() as cur:
        return relation_exists(cur, view_name)


def empty_planfact_filters_payload(parsed):
    return {
        "category_names": [],
        "product_names": [],
        "date_from": "",
        "date_to": "",
        "months": [],
        "marketplaces": client_marketplaces_payload(),
        "marketplace": planfact_marketplace_from_query(parsed.query),
        "abc_orders": [],
        "abc_sales": [],
        "abc_stock": [],
        "abc_combined": [],
        "view": "",
        "data_available": False,
    }

def handle_planfact_filters(parsed):
    from yandex_planfact import filters as yandex_filters, is_yandex
    if is_yandex(parsed.query):
        return yandex_filters(sys.modules[__name__], parsed)
    if not planfact_view_available(PLANFACT_DAILY_VIEW):
        return empty_planfact_filters_payload(parsed)
    if current_client_key() == "km_trade":
        from km_trade_planfact import filters_payload

        return filters_payload(read_db_config("km_trade"), parsed)
    if current_client_key() == "sportmaster":
        from sportmaster_run_rate import handle_filters as handle_sportmaster_run_rate_filters

        return handle_sportmaster_run_rate_filters(parsed, get_conn)
    history_start = PLANFACT_HISTORY_START_BY_CLIENT.get(current_client_key())
    base_where = sql.SQL("WHERE report_date >= %s") if history_start else sql.SQL("")
    query = sql.SQL(
        """
        WITH base AS (
            SELECT *
            FROM public.{view}
            {base_where}
        ),
        months AS (
            SELECT
                plan_month,
                plan_month::date AS date_from,
                (date_trunc('month', plan_month)::date + interval '1 month - 1 day')::date AS date_to
            FROM base
            GROUP BY plan_month
        )
        SELECT
            (SELECT min(report_date) FROM base) AS date_from,
            (SELECT max(report_date) FROM base) AS date_to,
            (SELECT array_remove(array_agg(DISTINCT marketplace_label ORDER BY marketplace_label), NULL) FROM base) AS category_names,
            (SELECT json_agg(json_build_object('month', plan_month, 'date_from', date_from, 'date_to', date_to) ORDER BY plan_month) FROM months) AS months
        """
    ).format(view=sql.Identifier(PLANFACT_DAILY_VIEW), base_where=base_where)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, [history_start] if history_start else [])
        payload = dict(cur.fetchone())
        payload["date_from"] = normalize_value(payload.get("date_from"))
        payload["date_to"] = normalize_value(payload.get("date_to"))
        payload["months"] = normalize_value(payload.get("months") or [])
        payload["marketplaces"] = [
            {"id": "ozon", "label": "Ozon"},
            {"id": "wb", "label": "WB"},
            {"id": "total", "label": "Итого"},
        ]
        if "yandex_market" in (ADMIN_CLIENTS[current_client_key()].get("marketplaces") or []):
            payload["marketplaces"].insert(2, {"id": "yandex_market", "label": "Яндекс Маркет"})
        payload["marketplace"] = planfact_marketplace_from_query(parsed.query)
        payload["product_names"] = []
        payload["abc_orders"] = []
        payload["abc_sales"] = []
        payload["abc_stock"] = []
        payload["abc_combined"] = []
        payload["view"] = f"public.{PLANFACT_DAILY_VIEW}"
        return payload


def _handle_planfact_summary_source(parsed):
    if not planfact_view_available(PLANFACT_DAILY_VIEW):
        return empty_planfact_payload("/api/planfact-summary")
    if current_client_key() == "km_trade":
        from km_trade_planfact import summary_payload

        return summary_payload(read_db_config("km_trade"), parsed)
    if current_client_key() == "sportmaster":
        from sportmaster_run_rate import handle_summary as handle_sportmaster_run_rate_summary

        return handle_sportmaster_run_rate_summary(parsed, get_conn)
    where, values = planfact_filters_from_query(parsed.query)
    query = sql.SQL(
        """
        WITH daily AS (
            SELECT *
            FROM public.{daily_view}
            {where}
        ),
        totals AS (
            SELECT
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                count(DISTINCT report_date) AS days_count,
                coalesce(sum(orders_rub), 0) AS orders_rub,
                coalesce(sum(sales_rub), 0) AS sales_rub,
                coalesce(sum(ad_spend_rub), 0) AS ad_spend_rub
            FROM daily
        ),
        plans AS (
            SELECT
                coalesce(sum(sales_plan_rub), 0) AS sales_plan_rub,
                coalesce(sum(ad_spend_plan_rub), 0) AS ad_spend_plan_rub
            FROM (
                SELECT DISTINCT marketplace, plan_month, sales_plan_rub, ad_spend_plan_rub
                FROM daily
            ) p
        )
        SELECT
            t.date_from,
            t.date_to,
            t.days_count,
            t.orders_rub,
            t.sales_rub,
            p.sales_plan_rub,
            t.ad_spend_rub,
            p.ad_spend_plan_rub,
            CASE WHEN p.sales_plan_rub <> 0 THEN round(t.sales_rub / p.sales_plan_rub * 100, 2) ELSE 0 END AS sales_plan_fact_pct,
            CASE WHEN p.ad_spend_plan_rub <> 0 THEN round(t.ad_spend_rub / p.ad_spend_plan_rub * 100, 2) ELSE 0 END AS ad_spend_budget_used_pct,
            CASE WHEN t.sales_rub <> 0 THEN round(t.ad_spend_rub / t.sales_rub * 100, 2) ELSE 0 END AS tacos_fact_pct,
            CASE WHEN p.sales_plan_rub <> 0 THEN round(p.ad_spend_plan_rub / p.sales_plan_rub * 100, 2) ELSE 0 END AS tacos_plan_pct
        FROM totals t CROSS JOIN plans p
        """
    ).format(
        daily_view=sql.Identifier(PLANFACT_DAILY_VIEW),
        where=sql.SQL(where),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = dict(cur.fetchone())
        return {key: normalize_value(value) for key, value in row.items()}


def handle_planfact_products(parsed):
    if current_client_key() != "km_trade":
        return {"ok": True, "rows": [], "totals": {}}
    from km_trade_planfact import product_planfact_payload

    return product_planfact_payload(read_db_config("km_trade"), parsed)


def _handle_planfact_daily_source(parsed):
    if not planfact_view_available(PLANFACT_DAILY_VIEW):
        return empty_planfact_payload("/api/planfact-daily")
    if current_client_key() == "km_trade":
        from km_trade_planfact import daily_payload

        return daily_payload(read_db_config("km_trade"), parsed)
    if current_client_key() == "sportmaster":
        from sportmaster_run_rate import handle_daily as handle_sportmaster_run_rate_daily

        return handle_sportmaster_run_rate_daily(parsed, get_conn)
    where, values = planfact_filters_from_query(parsed.query)
    marketplace = planfact_marketplace_from_query(parsed.query)
    if marketplace == "total":
        query = sql.SQL(
            """
            WITH daily AS (
                SELECT *
                FROM public.{view}
                {where}
            ),
            grouped AS (
                SELECT
                    'total' AS marketplace,
                    'Итого' AS marketplace_label,
                    report_date,
                    plan_month,
                    sum(orders_rub) AS orders_rub,
                    sum(sales_rub) AS sales_rub,
                    sum(ad_spend_rub) AS ad_spend_rub,
                    sum(sales_plan_rub) AS sales_plan_rub,
                    sum(ad_spend_plan_rub) AS ad_spend_plan_rub,
                    sum(sales_plan_daily_rub) AS sales_plan_daily_rub,
                    sum(ad_spend_plan_daily_rub) AS ad_spend_plan_daily_rub,
                    sum(sales_plan_elapsed_rub) AS sales_plan_elapsed_rub,
                    sum(ad_spend_plan_elapsed_rub) AS ad_spend_plan_elapsed_rub,
                    sum(orders_cum_rub) AS orders_cum_rub,
                    sum(sales_cum_rub) AS sales_cum_rub,
                    sum(ad_spend_cum_rub) AS ad_spend_cum_rub
                FROM daily
                GROUP BY report_date, plan_month
            )
            SELECT
                marketplace,
                marketplace_label,
                report_date,
                plan_month,
                orders_rub,
                sales_rub,
                ad_spend_rub,
                sales_plan_rub,
                ad_spend_plan_rub,
                sales_plan_daily_rub,
                ad_spend_plan_daily_rub,
                sales_plan_elapsed_rub,
                ad_spend_plan_elapsed_rub,
                orders_cum_rub,
                sales_cum_rub,
                ad_spend_cum_rub,
                CASE WHEN sales_plan_rub <> 0 THEN round(sales_cum_rub / sales_plan_rub * 100, 2) ELSE 0 END AS sales_month_plan_fact_pct,
                CASE WHEN sales_plan_elapsed_rub <> 0 THEN round(sales_cum_rub / sales_plan_elapsed_rub * 100, 2) ELSE 0 END AS sales_elapsed_plan_fact_pct,
                CASE WHEN ad_spend_plan_rub <> 0 THEN round(ad_spend_cum_rub / ad_spend_plan_rub * 100, 2) ELSE 0 END AS ad_spend_budget_used_pct,
                CASE WHEN ad_spend_plan_elapsed_rub <> 0 THEN round(ad_spend_cum_rub / ad_spend_plan_elapsed_rub * 100, 2) ELSE 0 END AS ad_spend_elapsed_budget_pct,
                CASE WHEN sales_rub <> 0 THEN round(ad_spend_rub / sales_rub * 100, 2) ELSE 0 END AS tacos_pct,
                CASE WHEN sales_cum_rub <> 0 THEN round(ad_spend_cum_rub / sales_cum_rub * 100, 2) ELSE 0 END AS tacos_cum_pct
            FROM grouped
            ORDER BY report_date
            """
        ).format(view=sql.Identifier(PLANFACT_DAILY_VIEW), where=sql.SQL(where))
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(query, values)
            return {"rows": normalize_rows(cur.fetchall())}
    query = sql.SQL(
        """
        SELECT
            marketplace,
            marketplace_label,
            report_date,
            plan_month,
            orders_rub,
            sales_rub,
            ad_spend_rub,
            sales_plan_rub,
            ad_spend_plan_rub,
            sales_plan_daily_rub,
            ad_spend_plan_daily_rub,
            sales_plan_elapsed_rub,
            ad_spend_plan_elapsed_rub,
            orders_cum_rub,
            sales_cum_rub,
            ad_spend_cum_rub,
            sales_month_plan_fact_pct,
            sales_elapsed_plan_fact_pct,
            ad_spend_budget_used_pct,
            ad_spend_elapsed_budget_pct,
            tacos_pct,
            tacos_cum_pct
        FROM public.{view}
        {where}
        ORDER BY report_date, marketplace
        """
    ).format(view=sql.Identifier(PLANFACT_DAILY_VIEW), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        return {"rows": normalize_rows(cur.fetchall())}


def _handle_planfact_monthly_source(parsed):
    if not planfact_view_available(PLANFACT_MONTHLY_VIEW):
        return empty_planfact_payload("/api/planfact-monthly")
    if current_client_key() == "km_trade":
        from km_trade_planfact import monthly_payload

        return monthly_payload(read_db_config("km_trade"), parsed)
    if current_client_key() == "sportmaster":
        from sportmaster_run_rate import handle_monthly as handle_sportmaster_run_rate_monthly

        return handle_sportmaster_run_rate_monthly(parsed, get_conn)
    where, values = planfact_filters_from_query(parsed.query, date_column="plan_month")
    query = sql.SQL(
        """
        SELECT
            marketplace,
            marketplace_label,
            plan_month,
            date_from,
            date_to,
            days_with_fact,
            sales_plan_rub,
            sales_rub,
            sales_plan_fact_pct,
            ad_spend_plan_rub,
            ad_spend_rub,
            ad_spend_budget_used_pct,
            tacos_plan_pct,
            tacos_fact_pct,
            orders_rub
        FROM public.{view}
        {where}
        ORDER BY plan_month, marketplace
        """
    ).format(view=sql.Identifier(PLANFACT_MONTHLY_VIEW), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        return {"rows": normalize_rows(cur.fetchall())}


def _handle_planfact_scorecard_source(parsed):
    if not planfact_view_available(PLANFACT_DAILY_VIEW):
        return empty_planfact_payload("/api/planfact-scorecard")
    if current_client_key() == "km_trade":
        from km_trade_planfact import scorecard_payload

        return scorecard_payload(read_db_config("km_trade"), parsed)
    if current_client_key() == "sportmaster":
        from sportmaster_run_rate import handle_scorecard as handle_sportmaster_run_rate_scorecard

        return handle_sportmaster_run_rate_scorecard(parsed, get_conn)
    params = parse_qs(parsed.query)
    date_filters = []
    values = []
    marketplace = params.get("marketplace", [""])[0].strip().lower()
    if marketplace in MARKETPLACES:
        date_filters.append("marketplace = %s")
        values.append(marketplace)
    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        date_filters.append("report_date >= %s")
        values.append(date_from)
    if date_to:
        date_filters.append("report_date <= %s")
        values.append(date_to)
    where = "WHERE " + " AND ".join(date_filters) if date_filters else ""
    query = sql.SQL(
        """
        WITH filtered AS (
            SELECT *
            FROM public.{daily_view}
            {where}
        ),
        latest AS (
            SELECT max(plan_month) AS plan_month
            FROM filtered
            WHERE sales_rub <> 0 OR orders_rub <> 0 OR ad_spend_rub <> 0
        ),
        month_rows AS (
            SELECT f.*
            FROM filtered f
            JOIN latest l ON l.plan_month = f.plan_month
        ),
        bounds AS (
            SELECT
                marketplace,
                max(report_date) AS last_date,
                max(extract(day from report_date)::int) AS last_day,
                greatest(max(extract(day from report_date)::int) - 4, 1) AS recent_start_day,
                max(extract(day from (date_trunc('month', report_date) + interval '1 month - 1 day'))::int) AS days_in_month
            FROM month_rows
            GROUP BY marketplace
        ),
        totals AS (
            SELECT
                r.marketplace,
                max(r.marketplace_label) AS marketplace_label,
                max(r.plan_month) AS plan_month,
                coalesce(sum(r.orders_rub), 0) AS orders_rub,
                coalesce(sum(r.sales_rub), 0) AS sales_rub,
                coalesce(sum(r.ad_spend_rub), 0) AS ad_spend_rub,
                max(r.sales_plan_rub) AS sales_plan_rub,
                max(r.ad_spend_plan_rub) AS ad_spend_plan_rub,
                count(DISTINCT r.report_date) AS days_with_fact,
                b.last_date,
                b.last_day,
                b.recent_start_day,
                b.days_in_month,
                coalesce(sum(r.sales_rub) FILTER (
                    WHERE extract(day from r.report_date)::int BETWEEN b.recent_start_day AND b.last_day
                ), 0) AS recent_sales_rub,
                count(DISTINCT r.report_date) FILTER (
                    WHERE extract(day from r.report_date)::int BETWEEN b.recent_start_day AND b.last_day
                ) AS recent_sales_days,
                coalesce(sum(r.ad_spend_rub) FILTER (
                    WHERE extract(day from r.report_date)::int BETWEEN b.recent_start_day AND b.last_day
                ), 0) AS recent_ad_spend_rub,
                count(DISTINCT r.report_date) FILTER (
                    WHERE extract(day from r.report_date)::int BETWEEN b.recent_start_day AND b.last_day
                ) AS recent_ad_spend_days
            FROM month_rows r
            JOIN bounds b ON b.marketplace = r.marketplace
            GROUP BY r.marketplace, b.last_date, b.last_day, b.recent_start_day, b.days_in_month
        )
        SELECT
            marketplace,
            marketplace_label,
            plan_month,
            last_date,
            last_day,
            recent_start_day,
            days_in_month,
            orders_rub,
            sales_plan_rub,
            sales_rub,
            CASE WHEN sales_plan_rub <> 0 THEN round(sales_rub / sales_plan_rub * 100, 2) ELSE 0 END AS sales_plan_fact_pct,
            round(sales_rub / nullif(days_with_fact, 0) * days_in_month, 0) AS sales_runrate_rub,
            CASE WHEN sales_plan_rub <> 0 THEN round((sales_rub / nullif(days_with_fact, 0) * days_in_month) / sales_plan_rub * 100, 2) ELSE 0 END AS sales_runrate_pct,
            round(sales_rub + recent_sales_rub / 5 * greatest(days_in_month - last_day, 0), 0) AS sales_recent_runrate_rub,
            CASE WHEN sales_plan_rub <> 0
                THEN round((sales_rub + recent_sales_rub / 5 * greatest(days_in_month - last_day, 0)) / sales_plan_rub * 100, 2)
                ELSE 0 END AS sales_recent_runrate_pct,
            ad_spend_plan_rub,
            ad_spend_rub,
            CASE WHEN ad_spend_plan_rub <> 0 THEN round(ad_spend_rub / ad_spend_plan_rub * 100, 2) ELSE 0 END AS ad_spend_plan_fact_pct,
            round(ad_spend_rub / nullif(days_with_fact, 0) * days_in_month, 0) AS ad_spend_runrate_rub,
            CASE WHEN ad_spend_plan_rub <> 0 THEN round((ad_spend_rub / nullif(days_with_fact, 0) * days_in_month) / ad_spend_plan_rub * 100, 2) ELSE 0 END AS ad_spend_runrate_pct,
            round(ad_spend_rub + recent_ad_spend_rub / 5 * greatest(days_in_month - last_day, 0), 0) AS ad_spend_recent_runrate_rub,
            CASE WHEN ad_spend_plan_rub <> 0
                THEN round((ad_spend_rub + recent_ad_spend_rub / 5 * greatest(days_in_month - last_day, 0)) / ad_spend_plan_rub * 100, 2)
                ELSE 0 END AS ad_spend_recent_runrate_pct
        FROM totals
        ORDER BY CASE marketplace WHEN 'wb' THEN 1 WHEN 'ozon' THEN 2 ELSE 3 END
        """
    ).format(daily_view=sql.Identifier(PLANFACT_DAILY_VIEW), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())
        if marketplace == "total" and rows:
            def total_sum(key):
                return sum(float(row.get(key) or 0) for row in rows)

            first = rows[0]
            total = {
                "marketplace": "total",
                "marketplace_label": "Итого",
                "plan_month": first.get("plan_month"),
                "last_date": max(row.get("last_date") for row in rows if row.get("last_date")),
                "last_day": max(int(row.get("last_day") or 0) for row in rows),
                "recent_start_day": min(int(row.get("recent_start_day") or 0) for row in rows),
                "days_in_month": max(int(row.get("days_in_month") or 0) for row in rows),
                "orders_rub": total_sum("orders_rub"),
                "sales_plan_rub": total_sum("sales_plan_rub"),
                "sales_rub": total_sum("sales_rub"),
                "sales_runrate_rub": total_sum("sales_runrate_rub"),
                "sales_recent_runrate_rub": total_sum("sales_recent_runrate_rub"),
                "ad_spend_plan_rub": total_sum("ad_spend_plan_rub"),
                "ad_spend_rub": total_sum("ad_spend_rub"),
                "ad_spend_runrate_rub": total_sum("ad_spend_runrate_rub"),
                "ad_spend_recent_runrate_rub": total_sum("ad_spend_recent_runrate_rub"),
            }
            total["sales_plan_fact_pct"] = round(total["sales_rub"] / total["sales_plan_rub"] * 100, 2) if total["sales_plan_rub"] else 0
            total["sales_runrate_pct"] = round(total["sales_runrate_rub"] / total["sales_plan_rub"] * 100, 2) if total["sales_plan_rub"] else 0
            total["sales_recent_runrate_pct"] = round(total["sales_recent_runrate_rub"] / total["sales_plan_rub"] * 100, 2) if total["sales_plan_rub"] else 0
            total["ad_spend_plan_fact_pct"] = round(total["ad_spend_rub"] / total["ad_spend_plan_rub"] * 100, 2) if total["ad_spend_plan_rub"] else 0
            total["ad_spend_runrate_pct"] = round(total["ad_spend_runrate_rub"] / total["ad_spend_plan_rub"] * 100, 2) if total["ad_spend_plan_rub"] else 0
            total["ad_spend_recent_runrate_pct"] = round(total["ad_spend_recent_runrate_rub"] / total["ad_spend_plan_rub"] * 100, 2) if total["ad_spend_plan_rub"] else 0
            rows = [{key: normalize_value(value) for key, value in total.items()}]
        return {"rows": rows}


def _planfact_with_sales_plan(parsed, kind, source_handler):
    payload = source_handler(parsed)
    client = current_client_key()
    # These clients have dedicated plan contracts and handlers.
    if client in {"km_trade", "sportmaster"} or not client_supports_report(client, "salesPlanning"):
        return payload
    from planfact_sales_plan import apply_sales_planning
    return apply_sales_planning(payload, kind, parsed.query, client,
                                read_db_config(client), client_marketplace_ids(client))


def handle_planfact_summary(parsed):
    from yandex_planfact import is_yandex, summary as yandex_summary
    if is_yandex(parsed.query):
        return yandex_summary(sys.modules[__name__], parsed)
    return _planfact_with_sales_plan(parsed, "summary", _handle_planfact_summary_source)


def handle_planfact_daily(parsed):
    from yandex_planfact import daily as yandex_daily, is_yandex
    if is_yandex(parsed.query):
        return yandex_daily(sys.modules[__name__], parsed)
    return _planfact_with_sales_plan(parsed, "daily", _handle_planfact_daily_source)


def handle_planfact_monthly(parsed):
    from yandex_planfact import is_yandex, monthly as yandex_monthly
    if is_yandex(parsed.query):
        return yandex_monthly(sys.modules[__name__], parsed)
    return _planfact_with_sales_plan(parsed, "monthly", _handle_planfact_monthly_source)


def handle_planfact_scorecard(parsed):
    from yandex_planfact import is_yandex, scorecard as yandex_scorecard
    if is_yandex(parsed.query):
        return yandex_scorecard(sys.modules[__name__], parsed)
    return _planfact_with_sales_plan(parsed, "scorecard", _handle_planfact_scorecard_source)


def review_visible_client_keys():
    return [
        key
        for key, value in ADMIN_CLIENTS.items()
        if value.get("show_in_dashboard")
    ]


def review_available_clients_payload():
    return [
        {
            "key": key,
            "label": ADMIN_CLIENTS[key]["label"],
            "status": ADMIN_CLIENTS[key]["status"],
            "description": ADMIN_CLIENTS[key]["description"],
        }
        for key in review_visible_client_keys()
    ]


def review_metric(label, value, value_format="number", digits=0):
    return {
        "label": label,
        "value": normalize_value(value),
        "format": value_format,
        "digits": digits,
    }


def review_short_error(exc):
    message = str(exc).splitlines()[0].strip()
    lowered = message.lower()
    if "не существует" in lowered or "does not exist" in lowered or "undefinedtable" in lowered:
        return "Нет подключенной витрины или данных за период."
    return (message or exc.__class__.__name__)[:220]


def review_marketplaces_for_report(client_key, report):
    if client_key == "boiron":
        return ["ozon"]
    if report in {"abc", "adv", "mediaAdv", "funnel"}:
        return ["ozon", "wb"]
    return ["ozon"]


def review_default_period_params():
    today = marketplace_today()
    return {
        "date_from": today.replace(day=1).isoformat(),
        "date_to": today.isoformat(),
    }


def review_source_payload(client_key, handler, dashboard, marketplace="ozon", extra_params=None):
    params = {
        "client": client_key,
        "dashboard": dashboard,
        "marketplace": marketplace,
    }
    if dashboard in {"abc", "adv", "mediaAdv", "funnel", "planfact"}:
        params.update(review_default_period_params())
    if extra_params:
        params.update(extra_params)
    query = urlencode(params, doseq=True)
    token = CURRENT_CLIENT.set(client_key)
    try:
        return handler(urlparse(f"/api/review-source?{query}"))
    finally:
        CURRENT_CLIENT.reset(token)


def review_block(block_id, section, title, metrics, subtitle="", available=True, error=""):
    return {
        "id": block_id,
        "section": section,
        "title": title,
        "subtitle": subtitle,
        "available": available,
        "error": error,
        "metrics": metrics,
    }


def review_unavailable_block(block_id, section, title, exc):
    return review_block(
        block_id,
        section,
        title,
        [],
        available=False,
        error=review_short_error(exc),
    )


def append_review_abc_blocks(client_key, blocks, totals):
    if not client_supports_report(client_key, "abc"):
        return
    for marketplace in review_marketplaces_for_report(client_key, "abc"):
        label = MARKETPLACES[marketplace]["label"]
        try:
            summary = review_source_payload(client_key, handle_summary, "abc", marketplace)
            sku_count = to_float(summary.get("sku_count"))
            totals["sku_count"] = max(totals["sku_count"], sku_count)
            totals["stock_qty"] += to_float(summary.get("total_stock_qty"))
            blocks.append(
                review_block(
                    f"abc_{marketplace}",
                    "ABC",
                    f"ABC {label}",
                    [
                        review_metric("Категории", summary.get("categories"), "integer"),
                        review_metric("SKU", sku_count, "integer"),
                        review_metric("Остаток", summary.get("total_stock_qty"), "integer"),
                        review_metric("Заказы", summary.get("zakazano_rub"), "money"),
                    ],
                )
            )
        except Exception as exc:
            blocks.append(review_unavailable_block(f"abc_{marketplace}", "ABC", f"ABC {label}", exc))


def append_review_adv_blocks(client_key, blocks, totals):
    if not client_supports_report(client_key, "adv"):
        return
    for marketplace in review_marketplaces_for_report(client_key, "adv"):
        label = MARKETPLACES[marketplace]["label"]
        try:
            summary = review_source_payload(client_key, handle_adv_summary, "adv", marketplace)
            totals["adv_orders_amount_rub"] += to_float(summary.get("orders_amount_rub"))
            totals["ad_spend_rub"] += to_float(summary.get("expense_rub"))
            blocks.append(
                review_block(
                    f"adv_{marketplace}",
                    "Реклама",
                    f"Товарная реклама {label}",
                    [
                        review_metric("Продажи с рекламы", summary.get("orders_amount_rub"), "money"),
                        review_metric("Расход", summary.get("expense_rub"), "money"),
                        review_metric("ДРР", summary.get("drr_pct"), "percent", 2),
                        review_metric("Заказы", summary.get("orders_qty"), "integer"),
                    ],
                )
            )
        except Exception as exc:
            blocks.append(review_unavailable_block(f"adv_{marketplace}", "Реклама", f"Товарная реклама {label}", exc))


def append_review_media_adv_blocks(client_key, blocks, totals):
    if not client_supports_report(client_key, "mediaAdv"):
        return
    for marketplace in review_marketplaces_for_report(client_key, "mediaAdv"):
        label = MARKETPLACES[marketplace]["label"]
        extra = {"media_level": "campaign"} if marketplace == "wb" else None
        try:
            summary = review_source_payload(client_key, handle_media_adv_summary, "mediaAdv", marketplace, extra)
            totals["media_attributed_revenue_rub"] += to_float(summary.get("attributed_revenue_rub"))
            totals["ad_spend_rub"] += to_float(summary.get("expense_rub"))
            blocks.append(
                review_block(
                    f"media_{marketplace}",
                    "Медиа",
                    f"Медийная реклама {label}",
                    [
                        review_metric("Атриб. выручка", summary.get("attributed_revenue_rub"), "money"),
                        review_metric("Post-view", summary.get("post_view_revenue_rub"), "money"),
                        review_metric("Расход", summary.get("expense_rub"), "money"),
                        review_metric("ДРР атриб.", summary.get("drr_attributed_pct"), "percent", 2),
                    ],
                )
            )
        except Exception as exc:
            blocks.append(review_unavailable_block(f"media_{marketplace}", "Медиа", f"Медийная реклама {label}", exc))


def append_review_funnel_blocks(client_key, blocks, totals):
    if not client_supports_report(client_key, "funnel"):
        return
    for marketplace in review_marketplaces_for_report(client_key, "funnel"):
        label = MARKETPLACES[marketplace]["label"]
        try:
            summary = review_source_payload(client_key, handle_funnel_summary, "funnel", marketplace)
            totals["funnel_ordered_amount_rub"] += to_float(summary.get("ordered_amount_rub"))
            totals["funnel_ordered_units"] += to_float(summary.get("ordered_units"))
            totals["funnel_card_visits"] += to_float(summary.get("card_visits"))
            blocks.append(
                review_block(
                    f"funnel_{marketplace}",
                    "Воронка",
                    f"Воронка {label}",
                    [
                        review_metric("Заказано", summary.get("ordered_amount_rub"), "money"),
                        review_metric("Заказы, шт", summary.get("ordered_units"), "integer"),
                        review_metric("Показы", summary.get("impressions_total"), "integer"),
                        review_metric("Карточка -> заказ", summary.get("card_visit_to_order_pct"), "percent", 2),
                    ],
                )
            )
        except Exception as exc:
            blocks.append(review_unavailable_block(f"funnel_{marketplace}", "Воронка", f"Воронка {label}", exc))


def append_review_planfact_blocks(client_key, blocks, totals):
    if not client_supports_report(client_key, "planfact"):
        return
    try:
        payload = review_source_payload(client_key, handle_planfact_scorecard, "planfact", "total")
        row = (payload.get("rows") or [{}])[0]
        totals["planfact_sales_rub"] = to_float(row.get("sales_rub"))
        totals["planfact_ad_spend_rub"] = to_float(row.get("ad_spend_rub"))
        blocks.append(
            review_block(
                "planfact_total",
                "План/факт",
                "План/факт",
                [
                    review_metric("Продажи", row.get("sales_rub"), "money"),
                    review_metric("План продаж", row.get("sales_plan_rub"), "money"),
                    review_metric("Выполнение", row.get("sales_plan_fact_pct"), "percent", 2),
                    review_metric("Бюджет рекламы", row.get("ad_spend_plan_fact_pct"), "percent", 2),
                ],
                subtitle=f"Последняя дата: {row.get('last_date') or '-'}",
            )
        )
    except Exception as exc:
        blocks.append(review_unavailable_block("planfact_total", "План/факт", "План/факт", exc))


def build_review_hero_metrics(totals, blocks):
    sales_rub = (
        totals["planfact_sales_rub"]
        or totals["funnel_ordered_amount_rub"]
        or totals["adv_orders_amount_rub"] + totals["media_attributed_revenue_rub"]
    )
    ad_spend_rub = totals["planfact_ad_spend_rub"] or totals["ad_spend_rub"]
    conversion_pct = (
        round(totals["funnel_ordered_units"] / totals["funnel_card_visits"] * 100, 2)
        if totals["funnel_card_visits"]
        else 0
    )
    return [
        review_metric("Продажи / заказы", sales_rub, "money"),
        review_metric("Расход рекламы", ad_spend_rub, "money"),
        review_metric("Блоки метрик", sum(1 for block in blocks if block.get("available")), "integer"),
        review_metric("Конверсия карточка -> заказ", conversion_pct, "percent", 2),
    ]


def build_review_client_snapshot(client_key):
    client = ADMIN_CLIENTS[client_key]
    blocks = []
    totals = {
        "sku_count": 0,
        "stock_qty": 0,
        "adv_orders_amount_rub": 0,
        "media_attributed_revenue_rub": 0,
        "ad_spend_rub": 0,
        "funnel_ordered_amount_rub": 0,
        "funnel_ordered_units": 0,
        "funnel_card_visits": 0,
        "planfact_sales_rub": 0,
        "planfact_ad_spend_rub": 0,
    }
    append_review_planfact_blocks(client_key, blocks, totals)
    append_review_funnel_blocks(client_key, blocks, totals)
    append_review_adv_blocks(client_key, blocks, totals)
    errors = [
        {"block": block["title"], "error": block["error"]}
        for block in blocks
        if not block.get("available") and block.get("error")
    ]
    return {
        "key": client_key,
        "label": client["label"],
        "status": client["status"],
        "reports": client.get("reports", []),
        "hero_metrics": build_review_hero_metrics(totals, blocks),
        "blocks": blocks,
        "errors": errors,
        "period": review_default_period_params(),
    }


def handle_review_dashboard(parsed):
    params = parse_qs(parsed.query)
    visible_clients = review_visible_client_keys()
    requested_client = params.get("client", [""])[0]
    selected_client = requested_client if requested_client in visible_clients else visible_clients[0]
    client_keys = [selected_client] if requested_client else visible_clients
    clients = [build_review_client_snapshot(key) for key in client_keys]
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": review_default_period_params(),
        "selected_client": selected_client,
        "available_clients": review_available_clients_payload(),
        "clients": clients,
        "totals": {
            "clients_count": len(clients),
            "blocks_count": sum(len(client.get("blocks", [])) for client in clients),
            "error_blocks_count": sum(len(client.get("errors", [])) for client in clients),
        },
    }


def admin_client_from_query(query):
    return client_from_query(query)


def admin_import_specs_for_client(client):
    if client == "sportmaster":
        return SPORTMASTER_ADMIN_IMPORTS, SPORTMASTER_DAILY_IMPORT_KEYS
    if client == "boiron":
        return BOIRON_ADMIN_IMPORTS, BOIRON_DAILY_IMPORT_KEYS
    if client == "km_trade":
        return KM_TRADE_ADMIN_IMPORTS, KM_TRADE_DAILY_IMPORT_KEYS
    if client == "gloria_jeans":
        return ADMIN_IMPORTS, DAILY_IMPORT_KEYS
    generated_root = PROJECT_ROOT / "scripts" / "clients" / client
    daily_script = generated_root / "run_daily.py"
    views_script = generated_root / "rebuild_views.py"
    if client in ADMIN_CLIENTS and daily_script.exists():
        daily_key = f"{client}_daily"
        views_key = f"{client}_views"
        specs = {
            daily_key: {
                "report": f"Ежедневное обновление {ADMIN_CLIENTS[client]['label']}",
                "description": "API-выгрузка выбранного маркетплейса и обновление клиентской базы.",
                "policy": "Ежедневный процесс, созданный мастером подключения клиента.",
                "script": daily_script,
                "source": "Marketplace API",
                "destination": f"{ADMIN_CLIENTS[client]['db_name']}.public.*",
                "date_args": True,
                "daily_stage": "imports",
            },
        }
        if views_script.exists():
            specs[views_key] = {
                "report": f"Витрины BI {ADMIN_CLIENTS[client]['label']}",
                "description": "Обновление materialized views после API-импорта.",
                "policy": "Финальный этап ежедневной цепочки.",
                "script": views_script,
                "source": f"{ADMIN_CLIENTS[client]['db_name']}.public.*",
                "destination": "Dashboard materialized views",
                "daily_stage": "views",
            }
        return specs, list(specs)
    return {}, []


def admin_import_marketplaces(key):
    normalized = str(key or "").strip().lower()
    if normalized.startswith("wb_") or "_wb_" in normalized:
        return ["wb"]
    if normalized.startswith("ozon_") or "_ozon_" in normalized:
        return ["ozon"]
    return ["wb", "ozon"]


def admin_import_spec_by_key(key, client=None):
    if client:
        specs, _daily_keys = admin_import_specs_for_client(normalize_client_key(client))
        return specs.get(key)
    for specs in (ADMIN_IMPORTS, SPORTMASTER_ADMIN_IMPORTS, BOIRON_ADMIN_IMPORTS, KM_TRADE_ADMIN_IMPORTS):
        if key in specs:
            return specs[key]
    return None


def admin_query_flag(params, name):
    value = params.get(name, [""])[0]
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def admin_date_args(spec, params):
    if not spec.get("date_args"):
        return []
    values = []
    parsed_dates = {}
    for query_name, argument in (("date_from", "--date-from"), ("date_to", "--date-to")):
        value = str(params.get(query_name, [""])[0]).strip()
        if not value:
            continue
        try:
            parsed_dates[query_name] = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Некорректная дата {query_name}: {value}") from exc
        values.extend([argument, value])
    if parsed_dates.get("date_from") and parsed_dates.get("date_to"):
        if parsed_dates["date_from"] > parsed_dates["date_to"]:
            raise ValueError("Дата начала API-выгрузки позже даты окончания")
    return values


def admin_import_command(script_path, spec, params):
    command = [sys.executable, "-X", "utf8", str(script_path)]
    command.extend(spec.get("args", []))
    command.extend(admin_date_args(spec, params))
    if admin_query_flag(params, "chain") or admin_query_flag(params, "defer_views"):
        command.extend(spec.get("defer_views_args", []))
    return command


def admin_import_stream_command(script_path, spec, params):
    command = [sys.executable, "-X", "utf8", "-u", str(script_path)]
    command.extend(spec.get("args", []))
    command.extend(admin_date_args(spec, params))
    if admin_query_flag(params, "chain") or admin_query_flag(params, "defer_views"):
        command.extend(spec.get("defer_views_args", []))
    return command


def admin_all_clients_row_id(stage, report):
    normalized = re.sub(r"[^a-zа-яё0-9]+", "-", str(report or "").strip().lower(), flags=re.I).strip("-")
    return f"{stage}:{normalized or 'stage'}"


ALL_CLIENTS_MATRIX_ROWS = {
    # План/факт читает уже импортированные Ozon/WB факты, поэтому в общей
    # цепочке обязан выполняться после всех источников, а не перед ними.
    "planfact": (390, "План/факт", "imports"),
    "ozon_assortment": (20, "Ассортимент Ozon", "imports"),
    "ozon_funnel": (30, "Воронка Ozon", "imports"),
    "ozon_stock": (40, "Остатки Ozon", "imports"),
    "ozon_advertising": (50, "Товарная реклама Ozon", "imports"),
    "ozon_media_advertising": (60, "Медийная реклама Ozon", "imports"),
    "ozon_feedbacks": (65, "Отзывы Ozon", "imports"),
    "ozon_finance": (70, "Финансы и цены Ozon", "imports"),
    "ozon_funnel_views": (80, "Витрина воронки Ozon", "views"),
    "ozon_advertising_views": (90, "Витрина товарной рекламы Ozon", "views"),
    "ozon_media_advertising_views": (100, "Витрина медийной рекламы Ozon", "views"),
    "ozon_abc_views": (110, "Витрины ABC Ozon", "views"),
    "ozon_sku_views": (120, "SKU-скоринг Ozon", "views"),
    "ozon_views": (130, "Витрины BI Ozon", "views"),
    "wb_assortment": (200, "Ассортимент WB", "imports"),
    "wb_orders_sales": (210, "Заказы и продажи WB", "imports"),
    "wb_funnel": (220, "Воронка WB", "imports"),
    "wb_stock": (230, "Остатки WB", "imports"),
    "wb_stock_history": (240, "История остатков WB", "imports"),
    "wb_search": (250, "Поисковые запросы WB", "imports"),
    "wb_market_search": (260, "Спрос площадки WB · XLSX", "imports"),
    "wb_entrance": (270, "Точки входа WB", "imports"),
    "wb_advertising": (280, "Товарная реклама WB", "imports"),
    "wb_media_advertising": (290, "Медийная реклама WB", "imports"),
    "wb_feedbacks": (300, "Отзывы и вопросы WB", "imports"),
    "wb_finance": (310, "Финансы WB", "imports"),
    "wb_search_views": (320, "Витрины поисковых запросов WB", "views"),
    "wb_market_search_views": (330, "Витрина спроса площадки WB · XLSX", "views"),
    "wb_entrance_views": (340, "Витрины точек входа WB", "views"),
    "wb_advertising_views": (350, "Витрина товарной рекламы WB", "views"),
    "wb_media_advertising_views": (360, "Витрины медийной рекламы WB", "views"),
    "wb_views": (370, "Витрины BI WB", "views"),
    "yandex_orders": (375, "Заказы Яндекс Маркета", "imports"),
    "yandex_order_stats": (376, "Детализация заказов Яндекс Маркета", "imports"),
    "yandex_returns": (377, "Возвраты Яндекс Маркета", "imports"),
    "avito_advertising": (380, "Рекламная статистика Avito", "imports"),
    "all_views": (400, "Общие витрины BI", "views"),
}

ALL_CLIENTS_IMPORT_ROW_KEYS = {
    "planfact": "planfact",
    "ozon_product_categories": "ozon_assortment",
    "sportmaster_ozon_product_categories": "ozon_assortment",
    "ozon_stock": "ozon_stock",
    "sportmaster_ozon_stock": "ozon_stock",
    "ozon_funnel": "ozon_funnel",
    "sportmaster_ozon_funnel": "ozon_funnel",
    "ozon_adv_daily": "ozon_advertising",
    "sportmaster_ozon_adv_daily": "ozon_advertising",
    "boiron_ozon_adv_daily": "ozon_advertising",
    "ozon_media_adv": "ozon_media_advertising",
    "sportmaster_ozon_media_adv": "ozon_media_advertising",
    "ozon_funnel_views": "ozon_funnel_views",
    "ozon_adv_daily_view": "ozon_advertising_views",
    "ozon_media_adv_view": "ozon_media_advertising_views",
    "ozon_abc_base_views": "ozon_abc_views",
    "ozon_sku_scoring_view": "ozon_sku_views",
    "wb_funnel": "wb_funnel",
    "wb_stock": "wb_stock",
    "wb_products_characteristics": "wb_assortment",
    "sportmaster_wb_product_categories": "wb_assortment",
    "sportmaster_wb_funnel": "wb_funnel",
    "sportmaster_wb_stock": "wb_stock",
    "wb_search_queries": "wb_search",
    "sportmaster_wb_search_queries": "wb_search",
    "sportmaster_wb_market_search_queries": "wb_market_search",
    "sportmaster_wb_entrance": "wb_entrance",
    "wb_adv_daily": "wb_advertising",
    "sportmaster_wb_adv_daily": "wb_advertising",
    "wb_media_adv": "wb_media_advertising",
    "sportmaster_wb_media_adv": "wb_media_advertising",
    "wb_search_query_views": "wb_search_views",
    "sportmaster_wb_search_query_views": "wb_search_views",
    "sportmaster_wb_market_search_query_views": "wb_market_search_views",
    "sportmaster_wb_entrance_views": "wb_entrance_views",
    "wb_adv_daily_view": "wb_advertising_views",
    "wb_media_adv_views": "wb_media_advertising_views",
    "sportmaster_wb_media_adv_views": "wb_media_advertising_views",
    "wb_dashboard_views": "wb_views",
    "sportmaster_dashboard_views": "all_views",
    "km_api_funnel": "ozon_funnel",
    "km_api_stock": "ozon_stock",
    "km_api_advertising": "ozon_advertising",
    "km_api_finance": "ozon_finance",
    "km_api_views": "ozon_views",
    "km_wb_api_funnel": "wb_funnel",
    "km_wb_api_stock": "wb_stock",
    "km_wb_api_views": "wb_views",
}


def admin_all_clients_matrix_meta(key, spec, *, source_kind):
    row_key = ALL_CLIENTS_IMPORT_ROW_KEYS.get(key)
    if row_key is None:
        report = str(spec.get("report") or key)
        stage = str(spec.get("daily_stage") or "imports")
        return {
            "row_id": admin_all_clients_row_id(stage, report),
            "row_label": report,
            "row_order": 900,
            "stage": stage,
            "source_kind": source_kind,
            "source_label": "API" if source_kind == "api" else "Файл",
        }
    order, label, stage = ALL_CLIENTS_MATRIX_ROWS[row_key]
    return {
        "row_id": f"{stage}:{row_key}",
        "row_label": label,
        "row_order": order,
        "stage": stage,
        "source_kind": source_kind,
        "source_label": "API" if source_kind == "api" else "Файл",
    }


def admin_all_clients_registered_api_specs(client, client_config):
    marketplaces = [
        value for value in client_config.get("marketplaces", [])
        if value in {"ozon", "wb", "avito", "yandex_market"}
    ]
    rows = []
    if "ozon" in marketplaces:
        rows.extend([
            ("ozon_assortment", "Ассортимент Ozon"),
            ("ozon_funnel", "Воронка Ozon"),
            ("ozon_stock", "Остатки Ozon"),
        ])
        performance_id = registered_client_credential(client, "ozon_performance_client_id")
        performance_secret = registered_client_credential(client, "ozon_performance_client_secret")
        if not (performance_id and performance_secret):
            performance_id, performance_secret = ozon_performance_credentials_value(client)
        if performance_id and performance_secret:
            rows.append(("ozon_advertising", "Товарная реклама Ozon"))
        rows.extend([
            ("ozon_finance", "Финансы и цены Ozon"),
            ("ozon_views", "Витрины BI Ozon"),
        ])
        if ozon_seo_credentials_saved(client):
            rows.insert(3, ("ozon_feedbacks", "Отзывы Ozon"))
    if "wb" in marketplaces:
        rows.extend([
            ("wb_catalog", "Ассортимент WB"),
            ("wb_orders_sales", "Заказы и продажи WB"),
            ("wb_stock_current", "Остатки WB"),
            ("wb_stock_history", "История остатков WB"),
            ("wb_funnel", "Воронка WB"),
            ("wb_advertising", "Товарная реклама WB"),
            ("wb_search", "Поисковые запросы WB"),
            ("wb_feedbacks_questions", "Отзывы и вопросы WB"),
            ("wb_finance", "Финансы WB"),
            ("wb_views", "Витрины BI WB"),
        ])
    if "yandex_market" in marketplaces:
        rows.extend([
            ("yandex_orders", "Яндекс · Заказы"),
            ("yandex_order_stats", "Яндекс · Детализация заказов"),
            ("yandex_returns", "Яндекс · Невыкупы и возвраты"),
        ])
    avito_credentials = (
        registered_client_credential(client, "avito_ads_account_id"),
        registered_client_credential(client, "avito_ads_client_id"),
        registered_client_credential(client, "avito_ads_client_secret"),
    )
    if all(avito_credentials):
        rows.append(("avito_advertising", "Рекламная статистика Avito"))
    return rows


REGISTERED_API_MATRIX_ROW_KEYS = {
    "ozon_assortment": "ozon_assortment",
    "ozon_funnel": "ozon_funnel",
    "ozon_stock": "ozon_stock",
    "ozon_feedbacks": "ozon_feedbacks",
    "ozon_advertising": "ozon_advertising",
    "ozon_finance": "ozon_finance",
    "ozon_views": "ozon_views",
    "wb_catalog": "wb_assortment",
    "wb_orders_sales": "wb_orders_sales",
    "wb_stock_current": "wb_stock",
    "wb_stock_history": "wb_stock_history",
    "wb_funnel": "wb_funnel",
    "wb_advertising": "wb_advertising",
    "wb_search": "wb_search",
    "wb_feedbacks_questions": "wb_feedbacks",
    "wb_finance": "wb_finance",
    "wb_views": "wb_views",
    "yandex_orders": "yandex_orders",
    "yandex_order_stats": "yandex_order_stats",
    "yandex_returns": "yandex_returns",
    "avito_advertising": "avito_advertising",
}


def admin_all_clients_api_completeness(client, client_config):
    from api_completeness import fallback_client_api_completeness, inspect_client_api_completeness

    try:
        config = dict(read_db_config(client))
        config["database"] = client_config["db_name"]
        with psycopg2.connect(**config) as conn:
            return inspect_client_api_completeness(
                conn,
                history_date_from=str(client_config.get("history_date_from") or ""),
                client_key=client,
            )
    except Exception as exc:
        return fallback_client_api_completeness(reason=f"{type(exc).__name__}: {exc}")


def admin_all_clients_window_label(window):
    date_from = str((window or {}).get("date_from") or "")
    date_to = str((window or {}).get("date_to") or "")
    return date_from if date_from == date_to else f"{date_from}—{date_to}"


def admin_all_clients_registered_task(
    client,
    client_config,
    step_key,
    report,
    *,
    window=None,
    completeness=None,
    initial_status="queued",
):
    row_key = REGISTERED_API_MATRIX_ROW_KEYS[step_key]
    order, row_label, stage = ALL_CLIENTS_MATRIX_ROWS[row_key]
    yesterday = marketplace_today() - timedelta(days=1)
    window = dict(window or {"date_from": yesterday.isoformat(), "date_to": yesterday.isoformat()})
    command_from = window.get("date_from") or yesterday.isoformat()
    command_to = window.get("date_to") or command_from
    completeness = dict(completeness or {})
    missing_windows = list(completeness.get("missing_windows") or [])
    missing_labels = [admin_all_clients_window_label(item) for item in missing_windows]
    missing_summary = "; ".join(missing_labels[:3])
    if len(missing_labels) > 3:
        missing_summary += f"; ещё {len(missing_labels) - 3}"
    coverage_from = str(completeness.get("coverage_from") or "")
    coverage_to = str(completeness.get("coverage_to") or "")
    coverage_label = "нет данных"
    if coverage_from and coverage_to:
        coverage_label = coverage_from if coverage_from == coverage_to else f"{coverage_from}—{coverage_to}"
    missing_days = int(completeness.get("missing_days") or 0)
    cell_label = "Актуально" if not missing_windows else (
        f"Нет {missing_days} дн." if missing_days > 1 else f"Нет {missing_summary}"
    )
    task_suffix = ""
    if window and missing_windows:
        task_suffix = f":{window.get('date_from') or ''}:{window.get('date_to') or ''}"
    command = [
        sys.executable, "-X", "utf8", "-u",
        str(PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "run_client_pipeline.py"),
        "--client-key", client,
        "--database-name", client_config["db_name"],
        "--marketplaces", ",".join(client_config.get("marketplaces") or []),
        "--mode", "daily",
        "--date-from", command_from,
        "--date-to", command_to,
        "--steps", step_key,
    ]
    if step_key not in {"ozon_views", "wb_views"}:
        command.append("--defer-views")
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "DASHBOARD_CLIENT": client,
        "DASHBOARD_DB_NAME": client_config["db_name"],
        "KM_DB_NAME": client_config["db_name"],
    }
    task = {
        "id": f"{client}:api:{step_key}{task_suffix}",
        "row_id": f"{stage}:{row_key}",
        "row_label": row_label,
        "row_order": order,
        "stage": stage,
        "source_kind": "api",
        "source_label": "API · новый день" if step_key in {"ozon_stock", "wb_stock_current", "wb_stock_history"} else "API",
        "client": client,
        "client_label": client_config["label"],
        "key": f"api_{step_key}",
        "report": report,
        "initial_status": initial_status,
        "initial_detail": "Данные актуальны" if initial_status == "ok" else f"Догрузить {admin_all_clients_window_label(window)}",
        "initial_progress_text": cell_label,
        "coverage_from": coverage_from,
        "coverage_to": coverage_to,
        "expected_from": str(completeness.get("expected_from") or ""),
        "expected_to": str(completeness.get("expected_to") or ""),
        "missing_windows": missing_windows,
        "missing_days": missing_days,
        "coverage_label": coverage_label,
        "missing_label": missing_summary or "нет",
        "cell_label": cell_label,
        "snapshot": bool(completeness.get("snapshot")),
        "retention_days": int(completeness.get("retention_days") or 0),
        "inspection_error": str(completeness.get("inspection_error") or ""),
        "command": command,
        "cwd": str(PROJECT_ROOT),
        "env": env,
        # API-этапы падают в основном на транзиентных 429/сетевых сбоях — их стоит повторить.
        "failure_policy": "retry:2:60",
        "supports_resume": True,
    }
    if step_key == "ozon_advertising":
        task.update({
            "nonfatal_error_patterns": [
                "ozon api http 403",
                "у организации нет доступа к ozon performance api",
                "ozon api http 403 for get https://api-performance.ozon.ru/api/client/campaign",
            ],
            "nonfatal_status_label": "Нет доступа",
        })
    elif step_key == "ozon_feedbacks":
        task.update({
            "nonfatal_error_patterns": [
                "not available with existing subscription",
                "permissiondenied",
            ],
            "nonfatal_status_label": "Нет доступа к отзывам",
            "failure_policy": "continue",
        })
    elif step_key == "wb_feedbacks_questions":
        task.update({
            "nonfatal_error_patterns": [
                "scope is not allowed for this resource",
                "wb http 403",
            ],
            "nonfatal_status_label": "Нет доступа к отзывам",
            "failure_policy": "continue",
        })
    elif step_key == "wb_finance":
        task.update({
            "nonfatal_error_patterns": [
                "wb_finance_source_not_ready",
            ],
            "nonfatal_status_label": "Данные ещё не готовы",
            "nonfatal_on_success": True,
            # Отсутствие сформированного дневного отчёта — лаг источника, а не
            # транзиентная техническая ошибка. Повтор назначит следующий прогон.
            "failure_policy": "continue",
        })
    return task


def admin_all_clients_registered_step_tasks(client, client_config, step_key, report, completeness):
    if step_key in {"ozon_views", "wb_views"}:
        return [admin_all_clients_registered_task(client, client_config, step_key, report)]
    state = dict((completeness or {}).get(step_key) or {})
    windows = list(state.get("missing_windows") or [])
    if step_key == "wb_funnel" and int(state.get("refresh_days") or 0):
        # Buyouts belong to the order date. Refresh the whole mutable API window,
        # even when yesterday is missing; filling gaps alone freezes older days.
        refresh_to = date.fromisoformat(str(state.get("expected_to") or
                                            (marketplace_today() - timedelta(days=1)).isoformat()))
        refresh_days = min(int(state["refresh_days"]), int(state.get("retention_days") or 7))
        refresh_from = refresh_to - timedelta(days=refresh_days - 1)
        if state.get("expected_from"):
            refresh_from = max(refresh_from, date.fromisoformat(state["expected_from"]))
        # Preserve older gaps when the API horizon is longer than the rolling
        # refresh window. Otherwise a new client could never fill its history.
        older = []
        for window in windows:
            gap_from = date.fromisoformat(window["date_from"])
            gap_to = min(date.fromisoformat(window["date_to"]), refresh_from - timedelta(days=1))
            if gap_from <= gap_to:
                older.append(admin_all_clients_registered_task(
                    client, client_config, step_key, report,
                    window={"date_from": gap_from.isoformat(), "date_to": gap_to.isoformat()},
                    completeness=state,
                ))
        return older + [admin_all_clients_registered_task(
            client, client_config, step_key, report,
            window={"date_from": refresh_from.isoformat(), "date_to": refresh_to.isoformat()},
            completeness=state,
        )]
    if step_key in {"ozon_advertising", "wb_advertising", "avito_advertising"} and len(windows) > 1:
        combined = {
            "date_from": min(str(item.get("date_from") or item.get("date_to") or "") for item in windows),
            "date_to": max(str(item.get("date_to") or item.get("date_from") or "") for item in windows),
        }
        return [admin_all_clients_registered_task(
            client, client_config, step_key, report,
            window=combined, completeness=state,
        )]
    if not windows:
        refresh_days = int(state.get("refresh_days") or 0)
        expected_to = str(state.get("expected_to") or (marketplace_today() - timedelta(days=1)).isoformat())
        if refresh_days:
            refresh_to = date.fromisoformat(expected_to)
            refresh_from = refresh_to - timedelta(days=refresh_days - 1)
            return [admin_all_clients_registered_task(
                client,
                client_config,
                step_key,
                report,
                window={"date_from": refresh_from.isoformat(), "date_to": refresh_to.isoformat()},
                completeness=state,
            )]
        current_window = {
            "date_from": state.get("expected_to") or (marketplace_today() - timedelta(days=1)).isoformat(),
            "date_to": state.get("expected_to") or (marketplace_today() - timedelta(days=1)).isoformat(),
        }
        return [admin_all_clients_registered_task(
            client,
            client_config,
            step_key,
            report,
            window=current_window,
            completeness=state,
            initial_status="ok",
        )]
    execution_windows = windows[:1] if state.get("snapshot") else windows
    return [
        admin_all_clients_registered_task(
            client,
            client_config,
            step_key,
            report,
            window=window,
            completeness=state,
        )
        for window in execution_windows
    ]


def admin_all_clients_row_major_plan(clients, tasks):
    client_order = {client["key"]: index for index, client in enumerate(clients)}
    stage_order = {"imports": 0, "views": 1}
    ordered_tasks = sorted(
        tasks,
        key=lambda task: (
            stage_order.get(task.get("stage"), 2),
            int(task.get("row_order") or 900),
            client_order.get(task.get("client"), len(client_order)),
            str(task.get("id") or ""),
        ),
    )
    return {"clients": clients, "tasks": ordered_tasks}


def build_admin_all_clients_daily_plan():
    clients = []
    tasks = []
    chain_params = {"chain": ["1"], "defer_views": ["1"]}
    for client, client_config in ADMIN_CLIENTS.items():
        if client_config.get("status") != "active":
            continue
        if client not in {"gloria_jeans", "sportmaster", "boiron"}:
            completeness = admin_all_clients_api_completeness(client, client_config)
            client_tasks = []
            for step_key, report in admin_all_clients_registered_api_specs(client, client_config):
                if step_key in {"ozon_assortment", "wb_catalog"}:
                    continue
                client_tasks.extend(admin_all_clients_registered_step_tasks(
                    client, client_config, step_key, report, completeness
                ))
            if client_tasks:
                clients.append({"key": client, "label": client_config["label"]})
                tasks.extend(client_tasks)
            continue
        specs, daily_keys = admin_import_specs_for_client(client)
        legacy_completeness = None
        client_tasks = []
        for key in daily_keys:
            spec = specs.get(key)
            if not spec:
                continue
            script_path = Path(spec["script"])
            if not script_path.exists():
                continue
            report = spec["report"]
            source_kind = "api" if spec.get("api_daily") else "manual"
            matrix = admin_all_clients_matrix_meta(key, spec, source_kind=source_kind)
            has_isolated_wb_token = bool(registered_client_credential(client, "wb_api_token"))
            if client == DEFAULT_CLIENT:
                has_isolated_wb_token = wb_api_token_saved(client)
            if matrix["row_id"] == "imports:wb_advertising" and has_isolated_wb_token:
                if legacy_completeness is None:
                    legacy_completeness = admin_all_clients_api_completeness(client, client_config)
                client_tasks.extend(admin_all_clients_registered_step_tasks(
                    client,
                    client_config,
                    "wb_advertising",
                    "Товарная реклама WB",
                    legacy_completeness,
                ))
                continue
            client_tasks.append({
                "id": f"{client}:{key}",
                **matrix,
                "client": client,
                "client_label": client_config["label"],
                "key": key,
                "report": report,
                "command": admin_import_stream_command(
                    script_path,
                    spec,
                    chain_params if matrix["stage"] == "imports" else {},
                ),
                "cwd": str(PROJECT_ROOT),
                "env": admin_import_env(spec, client),
                "supports_resume": bool(spec.get("supports_resume")),
            })
        if not any(task.get("key") == "api_avito_advertising" for task in client_tasks):
            if any(
                step_key == "avito_advertising"
                for step_key, _report in admin_all_clients_registered_api_specs(client, client_config)
            ):
                if legacy_completeness is None:
                    legacy_completeness = admin_all_clients_api_completeness(client, client_config)
                client_tasks.extend(admin_all_clients_registered_step_tasks(
                    client,
                    client_config,
                    "avito_advertising",
                    "Рекламная статистика Avito",
                    legacy_completeness,
                ))
        registered_step_keys = {
            step_key for step_key, _report in admin_all_clients_registered_api_specs(client, client_config)
        }
        for step_key, report in (
            ("ozon_feedbacks", "Отзывы Ozon"),
            ("wb_feedbacks_questions", "Отзывы и вопросы WB"),
        ):
            if step_key not in registered_step_keys:
                continue
            if legacy_completeness is None:
                legacy_completeness = admin_all_clients_api_completeness(client, client_config)
            client_tasks.extend(admin_all_clients_registered_step_tasks(
                client, client_config, step_key, report, legacy_completeness
            ))
        if client_tasks:
            clients.append({"key": client, "label": client_config["label"]})
            tasks.extend(client_tasks)
    return admin_all_clients_row_major_plan(clients, tasks)


ADMIN_ALL_CLIENTS_MANUAL_ASSORTMENT_KEYS = {
    "gloria_jeans": ("ozon_product_categories", "wb_products_characteristics"),
    "sportmaster": ("sportmaster_ozon_product_categories", "sportmaster_wb_product_categories"),
    "boiron": (),
}


def build_admin_all_clients_assortment_plan():
    clients = []
    tasks = []
    for client, client_config in ADMIN_CLIENTS.items():
        if client_config.get("status") != "active":
            continue
        clients.append({"key": client, "label": client_config["label"]})
        if client not in ADMIN_ALL_CLIENTS_MANUAL_ASSORTMENT_KEYS:
            tasks.extend(
                admin_all_clients_registered_task(client, client_config, step_key, report)
                for step_key, report in admin_all_clients_registered_api_specs(client, client_config)
                if step_key in {"ozon_assortment", "wb_catalog"}
            )
            continue
        specs, _daily_keys = admin_import_specs_for_client(client)
        for key in ADMIN_ALL_CLIENTS_MANUAL_ASSORTMENT_KEYS[client]:
            spec = specs.get(key)
            if not spec:
                continue
            script_path = Path(spec["script"])
            if not script_path.exists():
                continue
            matrix = admin_all_clients_matrix_meta(key, spec, source_kind="manual")
            tasks.append({
                "id": f"{client}:{key}",
                **matrix,
                "client": client,
                "client_label": client_config["label"],
                "key": key,
                "report": spec["report"],
                "command": admin_import_stream_command(script_path, spec, {}),
                "cwd": str(PROJECT_ROOT),
                "env": admin_import_env(spec, client),
            })
    return admin_all_clients_row_major_plan(clients, tasks)


def acquire_admin_all_clients_task(task):
    acquired, state = acquire_admin_import_run(task["client"], task["key"], task["report"])
    if acquired:
        return True, ""
    return False, admin_import_running_error(task["client"], task["key"], state)["error"]


def register_admin_all_clients_process(task, process):
    set_admin_import_process(task["client"], task["key"], process)


def release_admin_all_clients_task(task):
    release_admin_import_run(task["client"], task["key"])


def admin_all_clients_daily_runner():
    global ADMIN_ALL_CLIENTS_DAILY_RUNNER
    if ADMIN_ALL_CLIENTS_DAILY_RUNNER is not None:
        return ADMIN_ALL_CLIENTS_DAILY_RUNNER
    with ADMIN_ALL_CLIENTS_DAILY_LOCK:
        if ADMIN_ALL_CLIENTS_DAILY_RUNNER is None:
            from admin_all_clients_runtime import AllClientsDailyRunner

            ADMIN_ALL_CLIENTS_DAILY_RUNNER = AllClientsDailyRunner(
                ADMIN_ALL_CLIENTS_DAILY_STATE_PATH,
                build_admin_all_clients_daily_plan,
                acquire_task=acquire_admin_all_clients_task,
                register_process=register_admin_all_clients_process,
                release_task=release_admin_all_clients_task,
                terminate_process=terminate_admin_process_tree,
                process_group_kwargs=admin_import_process_group_kwargs,
            )
    return ADMIN_ALL_CLIENTS_DAILY_RUNNER


def handle_admin_all_clients_daily_client(action, client, task_ids=None):
    """Запуск, возобновление и остановка одного аккаунта в общем прогоне."""
    client = str(client or "").strip()
    if not client:
        raise ValueError("Не указан аккаунт")
    runner = admin_all_clients_daily_runner()
    if action == "stop":
        return runner.stop_client(client)
    if action == "resume":
        return runner.resume_client(client, task_ids=task_ids)
    if action == "start":
        if ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER is not None and ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER.snapshot()["status"] in {"running", "stopping"}:
            raise RuntimeError("Сначала завершите отдельное обновление ассортимента")
        return runner.start_client(client, task_ids=task_ids)
    raise ValueError(f"Неизвестное действие: {action}")


def handle_admin_all_clients_daily(action="status", task_ids=None):
    runner = admin_all_clients_daily_runner()
    if action == "start":
        if ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER is not None and ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER.snapshot()["status"] in {"running", "stopping"}:
            raise RuntimeError("Сначала завершите отдельное обновление ассортимента")
        return runner.start(resume=False, only_task_ids=task_ids)
    if action == "resume":
        if ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER is not None and ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER.snapshot()["status"] in {"running", "stopping"}:
            raise RuntimeError("Сначала завершите отдельное обновление ассортимента")
        return runner.start(resume=True, only_task_ids=task_ids)
    if action == "stop":
        return runner.stop()
    return runner.snapshot()


def admin_all_clients_assortment_runner():
    global ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER
    if ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER is not None:
        return ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER
    with ADMIN_ALL_CLIENTS_ASSORTMENT_LOCK:
        if ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER is None:
            from admin_all_clients_runtime import AllClientsDailyRunner

            ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER = AllClientsDailyRunner(
                ADMIN_ALL_CLIENTS_ASSORTMENT_STATE_PATH,
                build_admin_all_clients_assortment_plan,
                acquire_task=acquire_admin_all_clients_task,
                register_process=register_admin_all_clients_process,
                release_task=release_admin_all_clients_task,
                terminate_process=terminate_admin_process_tree,
                process_group_kwargs=admin_import_process_group_kwargs,
            )
    return ADMIN_ALL_CLIENTS_ASSORTMENT_RUNNER


def handle_admin_all_clients_assortment(action="status"):
    runner = admin_all_clients_assortment_runner()
    if action in {"start", "resume"}:
        if ADMIN_ALL_CLIENTS_DAILY_RUNNER is not None and ADMIN_ALL_CLIENTS_DAILY_RUNNER.snapshot()["status"] in {"running", "stopping"}:
            raise RuntimeError("Сначала завершите ежедневное обновление")
        return runner.start(resume=action == "resume")
    if action == "stop":
        return runner.stop()
    return runner.snapshot()


def handle_admin_imports(parsed):
    client = admin_client_from_query(parsed.query)
    specs, daily_keys = admin_import_specs_for_client(client)
    daily_order = {key: index for index, key in enumerate(daily_keys)}
    api_daily_keys = KM_TRADE_API_DAILY_IMPORT_KEYS if client == "km_trade" else []
    api_daily_order = {key: index for index, key in enumerate(api_daily_keys)}
    yesterday = marketplace_today() - timedelta(days=1)
    api_default_from = yesterday
    if client == "km_trade":
        try:
            with psycopg2.connect(**read_db_config("km_trade")) as conn, conn.cursor() as cur:
                cur.execute("SELECT current_database(), max(report_date) FROM public.ozon_funnel_daily")
                actual_db, last_date = cur.fetchone()
                if actual_db != "km_trade_products":
                    raise RuntimeError(f"Expected km_trade_products, got {actual_db}")
                if last_date:
                    api_default_from = min(last_date + timedelta(days=1), yesterday)
        except Exception:
            api_default_from = yesterday
    return {
        "client": client,
        "client_label": ADMIN_CLIENTS[client]["label"],
        "client_status": ADMIN_CLIENTS[client]["status"],
        "client_description": ADMIN_CLIENTS[client]["description"],
        "admin_sections": ADMIN_SECTION_CATALOG,
        "allowed_admin_sections": ordered_admin_sections(current_admin_section_ids()),
        "clients": [
            {
                "key": key,
                "label": value["label"],
                "status": value["status"],
                "description": value["description"],
                "db_name": value["db_name"],
            }
            for key, value in ADMIN_CLIENTS.items()
        ],
        "wb_api": {
            "token_saved": wb_api_token_saved(client),
            "token_env": wb_api_token_env(client),
            "client": client,
            "env_file": str(WB_API_ENV_FILE),
            "log_output_dir": str(WB_API_LOG_OUTPUT_DIR),
            "last_logs": dict(WB_API_LAST_LOG_FILES),
            "count_output_dir": str(WB_MEDIA_COUNT_OUTPUT_DIR),
            "adverts_output_dir": str(WB_MEDIA_ADVERTS_OUTPUT_DIR),
            "stats_output_dir": str(WB_MEDIA_STATS_OUTPUT_DIR),
            "promotion_count_output_dir": str(WB_PROMOTION_COUNT_OUTPUT_DIR),
            "promotion_adverts_output_dir": str(WB_PROMOTION_ADVERTS_OUTPUT_DIR),
            "promotion_stats_output_dir": str(WB_PROMOTION_STATS_OUTPUT_DIR),
            "content_categories_output_dir": str(WB_CONTENT_CATEGORIES_OUTPUT_DIR),
            "content_cards_output_dir": str(WB_CONTENT_CARDS_OUTPUT_DIR),
            "content_characteristics_output_dir": str(WB_CONTENT_CHARACTERISTICS_OUTPUT_DIR),
        },
        "ozon_seo": ozon_seo_admin_payload(client),
        "ozon_performance": ozon_performance_admin_payload(client),
        "api_daily_enabled": client == "km_trade",
        "api_daily_keys": api_daily_keys,
        "api_default_date_from": api_default_from.isoformat(),
        "api_default_date_to": yesterday.isoformat(),
        "rows": [
            {
                "key": key,
                "report": spec["report"],
                "description": spec.get("description", ""),
                "policy": spec.get("policy", ""),
                "script": str(spec["script"]),
                "source": spec["source"],
                "destination": spec["destination"],
                "script_exists": Path(spec["script"]).exists(),
                "daily": key in daily_order,
                "daily_order": daily_order.get(key),
                "api_daily": key in api_daily_order,
                "api_daily_order": api_daily_order.get(key),
                "daily_stage": spec.get("daily_stage", "imports"),
                "marketplaces": admin_import_marketplaces(key),
            }
            for key, spec in specs.items()
        ],
        "daily_keys": daily_keys,
    }


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def python_import_env():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def admin_import_env(spec, client):
    env = {
        **python_import_env(),
        "DASHBOARD_CLIENT": client,
        "DASHBOARD_DB_NAME": ADMIN_CLIENTS[client]["db_name"],
    }
    for key, value in (spec.get("env") or {}).items():
        env[str(key)] = str(value)
    return env


def admin_import_running_error(client, key, state):
    started_at = state.get("started_at", "")
    pid = state.get("pid")
    detail = f"Импорт уже запущен: {state.get('report', key)}"
    if started_at:
        detail += f", старт {started_at}"
    if pid:
        detail += f", PID {pid}"
    return {
        "ok": False,
        "key": key,
        "client": client,
        "report": state.get("report", key),
        "returncode": None,
        "error": detail,
        "summary": {"date": "", "rows": None, "errors": 1, "tail": detail},
    }


def acquire_admin_import_run(client, key, report):
    run_key = (client, key)
    with ADMIN_IMPORT_RUNNING_LOCK:
        current = ADMIN_IMPORT_RUNNING.get(run_key)
        if current:
            return False, current
        state = {
            "client": client,
            "key": key,
            "report": report,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid": None,
            "process": None,
            "stop_requested": False,
        }
        ADMIN_IMPORT_RUNNING[run_key] = state
        return True, state


def admin_import_process_group_kwargs():
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_admin_process_tree(process, timeout_seconds=ADMIN_IMPORT_STOP_TIMEOUT_SECONDS):
    if process is None:
        return {"requested": True, "stopped": False, "returncode": None, "error": "Процесс еще не создан"}
    pid = getattr(process, "pid", None)
    returncode = process.poll()
    if returncode is not None:
        return {"requested": True, "stopped": True, "returncode": returncode, "error": ""}

    errors = []
    if os.name == "nt" and pid:
        try:
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0 and process.poll() is None:
                detail = (completed.stderr or completed.stdout or "taskkill завершился с ошибкой").strip()
                errors.append(detail)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
    else:
        try:
            if pid:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                process.terminate()
        except (OSError, ProcessLookupError) as exc:
            errors.append(str(exc))
            try:
                process.terminate()
            except OSError as fallback_exc:
                errors.append(str(fallback_exc))

    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt" and pid:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                process.kill()
            returncode = process.wait(timeout=2)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
            returncode = process.poll()

    stopped = returncode is not None or process.poll() is not None
    return {
        "requested": True,
        "stopped": stopped,
        "returncode": returncode,
        "error": " | ".join(error for error in errors if error),
    }


def set_admin_import_process(client, key, process):
    should_stop = False
    with ADMIN_IMPORT_RUNNING_LOCK:
        state = ADMIN_IMPORT_RUNNING.get((client, key))
        if state:
            state["pid"] = process.pid
            state["process"] = process
            should_stop = bool(state.get("stop_requested"))
    if should_stop and process.poll() is None:
        result = terminate_admin_process_tree(process)
        with ADMIN_IMPORT_RUNNING_LOCK:
            state = ADMIN_IMPORT_RUNNING.get((client, key))
            if state:
                state["stop_result"] = result


def release_admin_import_run(client, key):
    with ADMIN_IMPORT_RUNNING_LOCK:
        ADMIN_IMPORT_RUNNING.pop((client, key), None)


def handle_admin_stop_import(payload):
    payload = payload if isinstance(payload, dict) else {}
    client = str(payload.get("client") or "gloria_jeans")
    if client not in ADMIN_CLIENTS:
        client = "gloria_jeans"
    requested_key = str(payload.get("key") or "")
    if not requested_key:
        return {"ok": False, "client": client, "key": requested_key, "state": "idle", "error": "Не выбран текущий импорт для остановки"}

    with ADMIN_IMPORT_RUNNING_LOCK:
        run_key = (client, requested_key)
        state = ADMIN_IMPORT_RUNNING.get(run_key)
        if not state:
            client_runs = [
                (candidate_key, candidate_state)
                for candidate_key, candidate_state in ADMIN_IMPORT_RUNNING.items()
                if candidate_key[0] == client
            ]
            if len(client_runs) == 1:
                run_key, state = client_runs[0]
            else:
                return {
                    "ok": False,
                    "client": client,
                    "key": requested_key,
                    "state": "idle",
                    "error": "Нет однозначно определенного запущенного импорта",
                }
        state["stop_requested"] = True
        process = state.get("process")
        pid = state.get("pid")
        report = state.get("report", run_key[1])
        key = state.get("key", run_key[1])

    result = terminate_admin_process_tree(process) if process else {
        "requested": True,
        "stopped": False,
        "returncode": None,
        "error": "",
    }
    with ADMIN_IMPORT_RUNNING_LOCK:
        current = ADMIN_IMPORT_RUNNING.get(run_key)
        if current:
            current["stop_result"] = result

    stopped = bool(result.get("stopped"))
    failed = bool(process and not stopped)
    return {
        "ok": not failed,
        "client": client,
        "key": key,
        "requested_key": requested_key,
        "report": report,
        "pid": pid,
        "state": "stopped" if stopped else "stopping",
        "returncode": result.get("returncode"),
        "message": "Процесс остановлен" if stopped else "Остановка запрошена",
        "error": result.get("error", "") if failed else "",
    }


def handle_admin_run_import(parsed):
    return handle_admin_run_import_sync(parsed)


def admin_output_events_from_chunks(chunks):
    buffer = []
    for chunk in chunks:
        if chunk is None:
            continue
        for char in str(chunk):
            if char in {"\r", "\n"}:
                line = "".join(buffer).strip()
                buffer = []
                if line:
                    yield {"kind": "progress" if char == "\r" else "output", "line": line}
                continue
            buffer.append(char)
    line = "".join(buffer).strip()
    if line:
        yield {"kind": "output", "line": line}


def admin_process_output_chunks(stream):
    while True:
        chunk = stream.read(1)
        if chunk == "":
            break
        yield chunk


def summarize_import_output(output):
    text = output or ""
    dates = re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    row_matches = re.findall(r"(?i)(?:rows|records|строк|запис(?:ей|и|ь)|raw_rows|imported_rows)\D{0,25}([0-9][0-9\s,]*)", text)
    error_matches = re.findall(r"(?i)(?:errors?|ошиб(?:ок|ки|ка))\D{0,25}([0-9][0-9\s,]*)", text)

    def clean_number(value):
        if not value:
            return None
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else None

    return {
        "date": max(dates) if dates else "",
        "rows": clean_number(row_matches[-1]) if row_matches else None,
        "errors": clean_number(error_matches[-1]) if error_matches else 0,
        "tail": text[-5000:],
    }


def handle_admin_run_import_sync(parsed):
    params = parse_qs(parsed.query)
    client = admin_client_from_query(parsed.query)
    key = params.get("key", [""])[0]
    spec = admin_import_spec_by_key(key, client)
    if not spec:
        raise RuntimeError(f"Unknown import key for {client}: {key}")

    script_path = Path(spec["script"])
    if not script_path.exists():
        raise FileNotFoundError(f"Import script not found: {script_path}")

    acquired, run_state = acquire_admin_import_run(client, key, spec["report"])
    if not acquired:
        return admin_import_running_error(client, key, run_state)

    try:
        try:
            command = admin_import_command(script_path, spec, params)
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=admin_import_env(spec, client),
                timeout=60 * 60 * 2,
            )
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(part for part in [exc.stdout or "", exc.stderr or ""] if part)
            return {
                "ok": False,
                "key": key,
                "client": client,
                "report": spec["report"],
                "error": "Импорт остановлен по таймауту 2 часа",
                "summary": summarize_import_output(output),
            }
    finally:
        release_admin_import_run(client, key)

    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    return {
        "ok": completed.returncode == 0,
        "key": key,
        "client": client,
        "report": spec["report"],
        "returncode": completed.returncode,
        "error": "" if completed.returncode == 0 else f"Скрипт завершился с кодом {completed.returncode}",
        "summary": summarize_import_output(output),
    }


def handle_adv_filters(parsed):
    params = parse_qs(parsed.query)
    marketplace = adv_marketplace_from_query(parsed.query)
    view_name = adv_view_for_marketplace(marketplace)
    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    brand_names = boiron_brand_values_from_params(params)
    product_filters = []
    product_values = []
    if categories:
        product_filters.append("category_name = ANY(%s)")
        product_values.append(categories)
    if current_client_is_boiron() and brand_names:
        product_filters.append("brand_name = ANY(%s)")
        product_values.append(brand_names)
    product_where = sql.SQL("WHERE " + " AND ".join(product_filters)) if product_filters else sql.SQL("")
    query = sql.SQL(
        """
        SELECT
            min(report_date) AS date_from,
            max(report_date) AS date_to,
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(DISTINCT category_name) AS categories
        FROM public.{view}
        """
    ).format(view=sql.Identifier(view_name))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        payload = dict(cur.fetchone())
        product_query = sql.SQL(
            """
            SELECT array_remove(array_agg(product_name ORDER BY product_name), NULL) AS product_names
            FROM (
                SELECT DISTINCT product_name
                FROM public.{view}
                {where}
                WHERE_MARKER
            ) p
            """
        ).format(view=sql.Identifier(view_name), where=product_where)
        product_sql = product_query.as_string(cur).replace("WHERE_MARKER", "")
        cur.execute(product_sql, product_values)
        payload["product_names"] = cur.fetchone()["product_names"] or []
        payload["adv_campaign_ids"] = []
        payload["adv_campaign_id_values"] = []
        if marketplace == "ozon":
            campaign_query = sql.SQL(
                """
                SELECT array_remove(array_agg(campaign_id ORDER BY campaign_id), NULL) AS campaign_ids
                FROM (
                    SELECT DISTINCT nullif(to_jsonb(v)->>'campaign_id', '') AS campaign_id
                    FROM public.{view} v
                    {where}
                ) campaigns
                """
            ).format(view=sql.Identifier(view_name), where=product_where)
            cur.execute(campaign_query, product_values)
            campaign_ids = (cur.fetchone() or {}).get("campaign_ids") or []
            payload["adv_campaign_ids"] = campaign_ids
            payload["adv_campaign_id_values"] = campaign_ids
        payload["date_from"] = normalize_value(payload.get("date_from"))
        payload["date_to"] = normalize_value(payload.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{view_name}"
        if current_client_is_boiron():
            cur.execute(
                sql.SQL(
                    """
                    SELECT array_remove(array_agg(DISTINCT brand_name ORDER BY brand_name), NULL) AS brand_names
                    FROM public.{view}
                    """
                ).format(view=sql.Identifier(view_name))
            )
            payload["brand_names"] = (cur.fetchone() or {}).get("brand_names") or []
            payload["category_names"] = payload["brand_names"]
            payload["collection_status_values"] = []
            payload["seo_status_values"] = []
        else:
            add_collection_status_options(cur, payload, marketplace)
            add_seo_status_options(cur, payload, marketplace)
            add_mapping_options(cur, payload, report="adv", marketplace=marketplace)
            add_ozon_product_attribute_options(cur, payload, marketplace)
        return payload


def boiron_planfact_where_from_query(query, alias="pf"):
    params = parse_qs(query)
    brand_names = boiron_brand_values_from_params(params)
    if not brand_names:
        return "", []
    return f" WHERE {alias}.brand_name = ANY(%s)", [brand_names]


def aggregate_boiron_planfact_rows(rows):
    if not rows:
        return {}
    if len(rows) > 1:
        for row in rows:
            if str(row.get("brand_name") or "").strip().lower() in {"общий итог", "итого"}:
                return row
    numeric_keys = [key for key in PLANFACT_COLUMNS if key != "brand_name"] if "PLANFACT_COLUMNS" in globals() else []
    if not numeric_keys:
        numeric_keys = [
            "budget_plan_rub",
            "expense_fact_rub",
            "orders_plan_qty",
            "orders_fact_qty",
            "revenue_plan_rub",
            "revenue_fact_rub",
            "impressions_plan",
            "impressions_fact",
            "clicks_plan",
            "clicks_fact",
        ]
    summary = {"brand_name": "Итого"}
    for key in numeric_keys:
        if key.endswith("_pct") or key.endswith("_fact_pct"):
            continue
        summary[key] = sum(to_float(row.get(key)) for row in rows)

    def ratio(num, den):
        den = to_float(den)
        return round(to_float(num) / den, 4) if den else 0

    summary["budget_plan_fact_pct"] = ratio(summary.get("expense_fact_rub"), summary.get("budget_plan_rub"))
    summary["orders_plan_fact_pct"] = ratio(summary.get("orders_fact_qty"), summary.get("orders_plan_qty"))
    summary["revenue_plan_fact_pct"] = ratio(summary.get("revenue_fact_rub"), summary.get("revenue_plan_rub"))
    summary["drr_plan_pct"] = ratio(summary.get("budget_plan_rub"), summary.get("revenue_plan_rub"))
    summary["drr_fact_pct"] = ratio(summary.get("expense_fact_rub"), summary.get("revenue_fact_rub"))
    summary["drr_plan_fact_pct"] = ratio(summary.get("drr_fact_pct"), summary.get("drr_plan_pct"))
    summary["impressions_plan_fact_pct"] = ratio(summary.get("impressions_fact"), summary.get("impressions_plan"))
    summary["clicks_plan_fact_pct"] = ratio(summary.get("clicks_fact"), summary.get("clicks_plan"))
    summary["ctr_plan_pct"] = ratio(summary.get("clicks_plan"), summary.get("impressions_plan"))
    summary["ctr_fact_pct"] = ratio(summary.get("clicks_fact"), summary.get("impressions_fact"))
    summary["ctr_plan_fact_pct"] = ratio(summary.get("ctr_fact_pct"), summary.get("ctr_plan_pct"))
    summary["cr_plan_pct"] = ratio(summary.get("orders_plan_qty"), summary.get("clicks_plan"))
    summary["cr_fact_pct"] = ratio(summary.get("orders_fact_qty"), summary.get("clicks_fact"))
    summary["cpo_plan_rub"] = ratio(summary.get("budget_plan_rub"), summary.get("orders_plan_qty"))
    summary["cpo_fact_rub"] = ratio(summary.get("expense_fact_rub"), summary.get("orders_fact_qty"))
    summary["cpo_plan_fact_pct"] = ratio(summary.get("cpo_fact_rub"), summary.get("cpo_plan_rub"))
    summary["cpc_plan_rub"] = ratio(summary.get("budget_plan_rub"), summary.get("clicks_plan"))
    summary["cpc_fact_rub"] = ratio(summary.get("expense_fact_rub"), summary.get("clicks_fact"))
    summary["cpc_plan_fact_pct"] = ratio(summary.get("cpc_fact_rub"), summary.get("cpc_plan_rub"))
    summary["avg_check_plan_rub"] = ratio(summary.get("revenue_plan_rub"), summary.get("orders_plan_qty"))
    summary["avg_check_fact_rub"] = ratio(summary.get("revenue_fact_rub"), summary.get("orders_fact_qty"))
    summary["avg_check_plan_fact_pct"] = ratio(summary.get("avg_check_fact_rub"), summary.get("avg_check_plan_rub"))
    return summary


def boiron_planfact_columns():
    return [
        "brand_name",
        "budget_plan_rub",
        "expense_fact_rub",
        "budget_plan_fact_pct",
        "orders_plan_qty",
        "orders_fact_qty",
        "orders_plan_fact_pct",
        "revenue_plan_rub",
        "revenue_fact_rub",
        "revenue_plan_fact_pct",
        "drr_plan_pct",
        "drr_fact_pct",
        "drr_plan_fact_pct",
        "impressions_plan",
        "impressions_fact",
        "impressions_plan_fact_pct",
        "clicks_plan",
        "clicks_fact",
        "clicks_plan_fact_pct",
        "ctr_plan_pct",
        "ctr_fact_pct",
        "ctr_plan_fact_pct",
        "cr_plan_pct",
        "cr_fact_pct",
        "cpo_plan_rub",
        "cpo_fact_rub",
        "cpo_plan_fact_pct",
        "cpc_plan_rub",
        "cpc_fact_rub",
        "cpc_plan_fact_pct",
        "avg_check_plan_rub",
        "avg_check_fact_rub",
        "avg_check_plan_fact_pct",
    ]


def handle_boiron_adv_planfact(parsed):
    if not current_client_is_boiron():
        return {"rows": [], "summary": {}, "available": False}
    where, values = boiron_planfact_where_from_query(parsed.query)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS table_name", ("public.boiron_adv_planfact_brand",))
        if not cur.fetchone()["table_name"]:
            return {"rows": [], "summary": {}, "available": False}
        query = sql.SQL(
            """
            SELECT {columns}
            FROM public.boiron_adv_planfact_brand pf
            {where}
            ORDER BY
                CASE WHEN lower(pf.brand_name) IN ('общий итог', 'итого') THEN 1 ELSE 0 END,
                pf.brand_name
            """
        ).format(
            columns=sql.SQL(", ").join(sql.Identifier(column) for column in boiron_planfact_columns()),
            where=sql.SQL(where),
        )
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())
    return {"rows": rows, "summary": aggregate_boiron_planfact_rows(rows), "available": True}


def boiron_analysis_metrics(summary):
    impressions = to_float(summary.get("impressions"))
    clicks = to_float(summary.get("clicks"))
    expense = to_float(summary.get("expense_rub"))
    orders = to_float(summary.get("orders_qty"))
    revenue = to_float(summary.get("orders_amount_rub"))
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr_pct": round(clicks / impressions * 100, 2) if impressions else 0,
        "expense_rub": expense,
        "orders_qty": orders,
        "orders_amount_rub": revenue,
        "drr_pct": round(expense / revenue * 100, 2) if revenue else 0,
        "cpc_rub": round(expense / clicks, 2) if clicks else 0,
        "cpo_rub": round(expense / orders, 2) if orders else 0,
        "cr_pct": round(orders / clicks * 100, 2) if clicks else 0,
    }


def build_boiron_base_note(metrics, planfact):
    pf = planfact.get("summary") or {}

    def pct(value):
        return f"{to_float(value) * 100:.1f}%"

    lines = [
        (
            "Товарная реклама Ozon: "
            f"расход {to_float(metrics.get('expense_rub')):,.0f} руб, "
            f"продажи {to_float(metrics.get('orders_amount_rub')):,.0f} руб, "
            f"заказы {to_float(metrics.get('orders_qty')):,.0f} шт, "
            f"ДРР {to_float(metrics.get('drr_pct')):.1f}%."
        ).replace(",", " "),
        (
            "Эффективность трафика: "
            f"CTR {to_float(metrics.get('ctr_pct')):.2f}%, "
            f"CR {to_float(metrics.get('cr_pct')):.2f}%, "
            f"CPC {to_float(metrics.get('cpc_rub')):.2f} руб, "
            f"CPO {to_float(metrics.get('cpo_rub')):.2f} руб."
        ),
    ]
    if pf:
        lines.append(
            "План/факт PF: "
            f"бюджет {pct(pf.get('budget_plan_fact_pct'))}, "
            f"заказы {pct(pf.get('orders_plan_fact_pct'))}, "
            f"выручка {pct(pf.get('revenue_plan_fact_pct'))}, "
            f"ДРР факт {pct(pf.get('drr_fact_pct'))}."
        )
    return lines


def handle_boiron_adv_analysis(parsed):
    if not current_client_is_boiron():
        return {"ok": False, "error": "Boiron analysis is available only for client=boiron"}
    summary = handle_adv_summary(parsed)
    metrics = boiron_analysis_metrics(summary)
    planfact = handle_boiron_adv_planfact(parsed)
    return {
        "ok": True,
        "metrics": metrics,
        "planfact": planfact,
        "note": build_boiron_base_note(metrics, planfact),
    }


def lm_studio_chat_endpoint():
    explicit = os.environ.get("LM_STUDIO_CHAT_URL")
    if explicit:
        return explicit
    base_url = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def call_lm_studio(prompt):
    endpoint = lm_studio_chat_endpoint()
    payload = {
        "model": os.environ.get("LM_STUDIO_MODEL", "local-model"),
        "temperature": float(os.environ.get("LM_STUDIO_TEMPERATURE", "0.2")),
        "max_tokens": int(os.environ.get("LM_STUDIO_MAX_TOKENS", "900")),
        "messages": [
            {
                "role": "system",
                "content": "Ты аналитик маркетплейсов. Пиши краткую аналитическую записку на русском по данным товарной рекламы.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=int(os.environ.get("LM_STUDIO_TIMEOUT_SECONDS", "120"))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"LM Studio unavailable: {exc}", "endpoint": endpoint}
    message = (data.get("choices") or [{}])[0].get("message") or {}
    return {"ok": True, "text": message.get("content", "").strip(), "endpoint": endpoint, "model": payload["model"]}


def build_boiron_lm_prompt(analysis):
    payload = {
        "metrics": analysis.get("metrics") or {},
        "planfact_summary": (analysis.get("planfact") or {}).get("summary") or {},
        "planfact_rows": (analysis.get("planfact") or {}).get("rows") or [],
        "base_note": analysis.get("note") or [],
    }
    return (
        "Сформируй аналитическую записку по товарной рекламе Ozon клиента Boiron. "
        "Структура: 1) краткий вывод, 2) основные метрики, 3) план/факт PF, "
        "4) что проверить/сделать дальше. Не выдумывай данных, используй только JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def handle_boiron_adv_analysis_generate(payload):
    query = str(payload.get("query") or "")
    client_token = CURRENT_CLIENT.set(client_from_query(query))
    try:
        analysis = handle_boiron_adv_analysis(urlparse(f"/api/boiron-adv-analysis?{query}"))
        if not analysis.get("ok"):
            return analysis
        prompt = str(payload.get("prompt") or "").strip() or build_boiron_lm_prompt(analysis)
        result = call_lm_studio(prompt)
        result["analysis"] = analysis
        return result
    finally:
        CURRENT_CLIENT.reset(client_token)


def handle_media_adv_filters(parsed):
    params = parse_qs(parsed.query)
    marketplace = marketplace_from_query(parsed.query)
    media_level = media_adv_level_from_query(parsed.query)
    view_name = media_adv_view_for_query(parsed.query)
    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    product_where = sql.SQL("WHERE category_name = ANY(%s)") if categories else sql.SQL("")
    product_values = [categories] if categories else []
    query = sql.SQL(
        """
        SELECT
            min(report_date) AS date_from,
            max(report_date) AS date_to,
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(DISTINCT category_name) AS categories
        FROM public.{view}
        """
    ).format(view=sql.Identifier(view_name))
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, view_name):
            return {
                "date_from": None,
                "date_to": None,
                "category_names": [],
                "categories": 0,
                "product_names": [],
                "media_statuses": [],
                "media_segments": [],
                "media_campaign_ids": [],
                "media_group_ids": [],
                "media_creative_ids": [],
                "marketplaces": client_marketplaces_payload(),
                "marketplace": marketplace,
                "media_levels": [
                    {"id": "campaign", "label": "По кампаниям"},
                    {"id": "group", "label": "По группам объявлений"},
                    {"id": "creative", "label": "По креативам"},
                ],
                "media_level": media_level,
                "view": f"public.{view_name}",
                "available": False,
                "unavailable_reason": "source_not_loaded",
            }
        cur.execute(query)
        payload = dict(cur.fetchone())
        if marketplace == "wb":
            payload["product_names"] = []
        else:
            product_query = sql.SQL(
                """
                SELECT array_remove(array_agg(campaign_name ORDER BY campaign_name), NULL) AS product_names
                FROM (
                    SELECT DISTINCT campaign_name
                    FROM public.{view}
                    {where}
                    WHERE_MARKER
                ) p
                """
            ).format(view=sql.Identifier(view_name), where=product_where)
            product_sql = product_query.as_string(cur).replace("WHERE_MARKER", "")
            cur.execute(product_sql, product_values)
            payload["product_names"] = cur.fetchone()["product_names"] or []
        option_fields = sql.SQL(",\n                    ").join(
            sql.SQL(field) for field in media_adv_option_select_fields(marketplace)
        )
        cur.execute(
            sql.SQL(
                """
                SELECT
                    {fields}
                FROM public.{view}
                """
            ).format(fields=option_fields, view=sql.Identifier(view_name))
        )
        media_options = cur.fetchone() or {}
        payload["media_statuses"] = media_options.get("media_statuses") or []
        payload["media_segments"] = media_options.get("media_segments") or []
        payload["media_campaign_ids"] = media_options.get("media_campaign_ids") or []
        payload["media_group_ids"] = media_options.get("media_group_ids") or []
        payload["media_creative_ids"] = media_options.get("media_creative_ids") or []
        payload["date_from"] = normalize_value(payload.get("date_from"))
        payload["date_to"] = normalize_value(payload.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["media_levels"] = [
            {"id": "campaign", "label": "По кампаниям"},
            {"id": "group", "label": "По группам объявлений"},
            {"id": "creative", "label": "По креативам"},
        ]
        payload["media_level"] = media_level
        payload["view"] = f"public.{view_name}"
        return payload


def chart_export_json_param(params, name):
    raw = params.get(name, [""])[0].strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def normalize_chart_metrics(metric_keys, metric_catalog, axes=None, types=None):
    axes = axes or {}
    types = types or {}
    metrics = []
    seen = set()
    for key in metric_keys:
        key = str(key or "").strip()
        if not key or key in seen or key not in metric_catalog:
            continue
        seen.add(key)
        metrics.append(
            {
                "key": key,
                "label": metric_catalog[key],
                "axis": "right" if axes.get(key) == "right" else "left",
                "chart_type": "bar" if types.get(key) == "bar" else "line",
            }
        )
    return metrics


def chart_export_metrics(params, dashboard, chart):
    catalog = CHART_METRIC_CATALOGS.get(dashboard, {})
    metric_keys = [
        item.strip()
        for item in params.get("chart_metrics", [""])[0].split(",")
        if item.strip()
    ]
    if not metric_keys:
        metric_keys = CHART_SECONDARY_METRICS.get(dashboard, []) if chart == "secondary" else CHART_DEFAULT_METRICS.get(dashboard, [])
    axes = chart_export_json_param(params, "chart_axes")
    types = chart_export_json_param(params, "chart_types")
    metrics = normalize_chart_metrics(metric_keys, catalog, axes, types)
    if not metrics:
        metrics = normalize_chart_metrics(CHART_DEFAULT_METRICS.get(dashboard, []), catalog, axes, types)
    return metrics


def safe_ratio_pct(numerator, denominator):
    numerator = to_float(numerator)
    denominator = to_float(denominator)
    return round(numerator / denominator * 100, 2) if denominator else 0


def enrich_chart_export_rows(rows, dashboard):
    enriched = []
    for row in rows:
        item = dict(row)
        if dashboard == "adv":
            impressions = item.get("impressions", 0)
            clicks = item.get("clicks", 0)
            carts = item.get("added_to_cart", 0)
            orders = item.get("orders_qty", 0)
            total_orders = item.get("total_orders_qty", 0)
            expense = item.get("expense_rub", 0)
            revenue = item.get("orders_amount_rub", 0)
            direct_revenue = item.get("direct_orders_amount_rub", 0)
            indirect_revenue = item.get("indirect_orders_amount_rub", 0)
            total_revenue = item.get("total_orders_amount_rub", 0)
            item["direct_drr_pct"] = safe_ratio_pct(expense, direct_revenue)
            item["indirect_drr_pct"] = safe_ratio_pct(expense, indirect_revenue)
            item["adv_sales_to_total_sales_pct"] = safe_ratio_pct(revenue, total_revenue)
            item["adv_orders_to_total_orders_pct"] = safe_ratio_pct(orders, total_orders)
            item["ctr_calc_pct"] = safe_ratio_pct(clicks, impressions)
            item["click_to_cart_pct"] = safe_ratio_pct(carts, clicks)
            item["cart_to_order_pct"] = safe_ratio_pct(orders, carts)
            item["impression_to_order_pct"] = safe_ratio_pct(orders, impressions)
            item["click_to_order_pct"] = safe_ratio_pct(orders, clicks)
            item["cpc_calc_rub"] = round(to_float(expense) / to_float(clicks), 2) if to_float(clicks) else 0
            item["cpa_calc_rub"] = round(to_float(expense) / to_float(orders), 2) if to_float(orders) else 0
            item["cpm_calc_rub"] = round(to_float(expense) / to_float(impressions) * 1000, 2) if to_float(impressions) else 0
            item["avg_ad_order_value_rub"] = round(to_float(revenue) / to_float(orders), 2) if to_float(orders) else 0
            item["drr_pct"] = safe_ratio_pct(expense, revenue)
            item["total_drr_pct"] = safe_ratio_pct(expense, total_revenue)
        elif dashboard == "mediaAdv":
            expense = item.get("expense_rub", 0)
            impressions = item.get("impressions", 0)
            clicks = item.get("clicks", 0)
            orders = item.get("orders_qty", 0)
            direct_revenue = item.get("orders_amount_rub", 0)
            attributed_revenue = item.get("attributed_revenue_rub", 0)
            post_view_revenue = item.get("post_view_revenue_rub", 0)
            post_view_orders = item.get("post_view_orders_qty", 0)
            item["ctr_calc_pct"] = safe_ratio_pct(clicks, impressions)
            item["click_to_order_pct"] = safe_ratio_pct(orders, clicks)
            item["drr_direct_pct"] = safe_ratio_pct(expense, direct_revenue)
            item["drr_attributed_pct"] = safe_ratio_pct(expense, attributed_revenue)
            item["attributed_roas"] = round(to_float(attributed_revenue) / to_float(expense), 2) if to_float(expense) else 0
            item["post_view_revenue_share_pct"] = safe_ratio_pct(post_view_revenue, attributed_revenue)
            item["cpc_calc_rub"] = round(to_float(expense) / to_float(clicks), 2) if to_float(clicks) else 0
            item["cpm_calc_rub"] = round(to_float(expense) / to_float(impressions) * 1000, 2) if to_float(impressions) else 0
            item["post_view_orders_per_1000_impressions"] = round(to_float(post_view_orders) / to_float(impressions) * 1000, 3) if to_float(impressions) else 0
        enriched.append(item)
    return enriched


def aggregate_chart_rows(rows, dashboard, period_group):
    if period_group not in {"week", "month"}:
        return rows
    summed_fields_by_dashboard = {
        "adv": [
            "expense_rub",
            "orders_amount_rub",
            "direct_orders_amount_rub",
            "indirect_orders_amount_rub",
            "direct_orders_qty",
            "indirect_orders_qty",
            "total_orders_amount_rub",
            "impressions",
            "promoted_sku_count",
            "ordered_sku_count",
            "clicks",
            "added_to_cart",
            "orders_qty",
            "total_orders_qty",
        ],
        "mediaAdv": [
            "expense_rub",
            "impressions",
            "clicks",
            "orders_qty",
            "orders_amount_rub",
            "post_view_orders_qty",
            "post_view_revenue_rub",
            "attributed_orders_qty",
            "attributed_revenue_rub",
        ],
        "funnel": [
            "impressions_total",
            "impressions_search_catalog",
            "card_visits",
            "cart_adds",
            "ordered_units",
            "ordered_amount_rub",
            "bought_units",
            "bought_amount_rub",
            "cohort_bought_units",
            "cohort_bought_amount_rub",
            "favorites_adds",
            "cancelled_units",
            "cancelled_amount_rub",
            "wb_club_ordered_units",
            "wb_club_bought_units",
            "wb_club_ordered_amount_rub",
            "wb_club_bought_amount_rub",
            "adv_impressions",
            "adv_clicks",
            "adv_cart_adds",
            "adv_orders",
            "adv_orders_amount_rub",
            "adv_expense_rub",
            "organic_impressions",
            "organic_card_visits",
            "organic_cart_adds",
            "organic_orders",
        ],
    }
    max_fields_by_dashboard = {
        "adv": ["total_sku_count"],
    }
    summed_fields = summed_fields_by_dashboard.get(dashboard)
    if not summed_fields:
        return rows
    max_fields = max_fields_by_dashboard.get(dashboard, [])
    buckets = {}
    for row in rows:
        raw_date = str(row.get("report_date") or "")
        try:
            parsed_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            parsed_date = None
        if period_group == "month" and parsed_date:
            key = parsed_date.strftime("%Y-%m")
        elif parsed_date:
            key = (parsed_date - timedelta(days=parsed_date.weekday())).isoformat()
        else:
            key = raw_date
        bucket = buckets.setdefault(key, {"report_date": key})
        for field in summed_fields:
            bucket[field] = to_float(bucket.get(field, 0)) + to_float(row.get(field, 0))
        for field in max_fields:
            bucket[field] = max(to_float(bucket.get(field, 0)), to_float(row.get(field, 0)))
    if dashboard in {"adv", "mediaAdv"}:
        return sorted(enrich_chart_export_rows(buckets.values(), dashboard), key=lambda item: str(item.get("report_date") or ""))
    aggregated = []
    for row in buckets.values():
        row["search_to_card_visit_pct"] = safe_ratio_pct(row.get("card_visits"), row.get("impressions_search_catalog"))
        row["total_impression_to_card_visit_pct"] = safe_ratio_pct(row.get("card_visits"), row.get("impressions_total"))
        row["card_visit_to_cart_pct"] = safe_ratio_pct(row.get("cart_adds"), row.get("card_visits"))
        row["cart_to_order_pct"] = safe_ratio_pct(row.get("ordered_units"), row.get("cart_adds"))
        row["card_visit_to_order_pct"] = safe_ratio_pct(row.get("ordered_units"), row.get("card_visits"))
        row["ordered_amount_per_unit_rub"] = round(to_float(row.get("ordered_amount_rub")) / to_float(row.get("ordered_units")), 2) if to_float(row.get("ordered_units")) else 0
        row["favorite_to_card_visit_pct"] = safe_ratio_pct(row.get("favorites_adds"), row.get("card_visits"))
        row["buyout_pct"] = safe_ratio_pct(row.get("cohort_bought_units"), row.get("ordered_units"))
        row["cancellation_pct"] = safe_ratio_pct(row.get("cancelled_units"), row.get("ordered_units"))
        row["wb_club_order_share_pct"] = safe_ratio_pct(row.get("wb_club_ordered_units"), row.get("ordered_units"))
        row["adv_ctr_pct"] = safe_ratio_pct(row.get("adv_clicks"), row.get("adv_impressions"))
        row["adv_click_to_cart_pct"] = safe_ratio_pct(row.get("adv_cart_adds"), row.get("adv_clicks"))
        row["adv_cart_to_order_pct"] = safe_ratio_pct(row.get("adv_orders"), row.get("adv_cart_adds"))
        row["adv_click_to_order_pct"] = safe_ratio_pct(row.get("adv_orders"), row.get("adv_clicks"))
        row["adv_cpc_rub"] = round(to_float(row.get("adv_expense_rub")) / to_float(row.get("adv_clicks")), 2) if to_float(row.get("adv_clicks")) else 0
        row["adv_cpa_rub"] = round(to_float(row.get("adv_expense_rub")) / to_float(row.get("adv_orders")), 2) if to_float(row.get("adv_orders")) else 0
        row["adv_cpm_rub"] = round(to_float(row.get("adv_expense_rub")) / to_float(row.get("adv_impressions")) * 1000, 2) if to_float(row.get("adv_impressions")) else 0
        row["acos_pct"] = safe_ratio_pct(row.get("adv_expense_rub"), row.get("adv_orders_amount_rub"))
        row["tacos_pct"] = safe_ratio_pct(row.get("adv_expense_rub"), row.get("ordered_amount_rub"))
        aggregated.append(row)
    return sorted(aggregated, key=lambda item: str(item.get("report_date") or ""))


def kokoc_excel_font(*, bold=False, size=10, color=KOKOC_EXCEL_TEXT):
    return Font(name=KOKOC_EXCEL_FONT, bold=bold, size=size, color=color)


def apply_kokoc_table_style(ws, *, header_row=1, max_width=42):
    header_fill = PatternFill("solid", fgColor=KOKOC_EXCEL_TIFFANY_LIGHT)
    header_border = Border(bottom=Side(style="thin", color=KOKOC_EXCEL_TIFFANY))
    body_border = Border(bottom=Side(style="thin", color=KOKOC_EXCEL_LINE))
    ws.sheet_view.showGridLines = False
    for row in ws.iter_rows():
        for cell in row:
            cell.font = kokoc_excel_font()
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            cell.border = body_border
            if cell.row == header_row:
                cell.font = kokoc_excel_font(bold=True)
                cell.fill = header_fill
                cell.border = header_border
    if ws.max_row >= header_row and ws.max_column:
        end_col = get_column_letter(ws.max_column)
        ws.freeze_panes = f"A{header_row + 1}"
        ws.auto_filter.ref = f"A{header_row}:{end_col}{ws.max_row}"
        for index in range(1, ws.max_column + 1):
            values = [
                str(ws.cell(row=row_index, column=index).value or "")
                for row_index in range(1, min(ws.max_row, 201) + 1)
            ]
            width = min(max(max((len(value) for value in values), default=10) + 2, 12), max_width)
            ws.column_dimensions[get_column_letter(index)].width = width


def apply_kokoc_workbook_style(wb):
    for ws in wb.worksheets:
        apply_kokoc_table_style(ws)


def decode_chart_snapshot_data_url(data_url):
    if not data_url:
        return None
    match = re.match(r"^data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=\s]+)$", str(data_url).strip())
    if not match:
        return None
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    return BytesIO(raw)


def add_chart_snapshot_to_sheet(ws, chart_image_data_url, *, anchor):
    image_bytes = decode_chart_snapshot_data_url(chart_image_data_url)
    if not image_bytes:
        return False
    image = XLImage(image_bytes)
    max_width = 880
    if image.width and image.width > max_width:
        ratio = max_width / image.width
        image.width = max_width
        image.height = int(image.height * ratio)
    ws.add_image(image, anchor)
    return True


def style_native_chart_series(chart_obj, *, offset=0, is_bar_chart=False):
    palette = [KOKOC_EXCEL_TIFFANY, "1D9BD1", "F59E0B", "EF4444", "7C3AED", "16A34A"]
    for index, series in enumerate(chart_obj.series):
        color = palette[(offset + index) % len(palette)]
        if is_bar_chart:
            series.graphicalProperties.solidFill = color
        else:
            series.graphicalProperties.line.solidFill = color


def add_metric_series(chart_obj, ws, metric_indexes, *, header_row, max_row):
    for metric_index in metric_indexes:
        metric_col = metric_index + 2
        data_ref = Reference(ws, min_col=metric_col, max_col=metric_col, min_row=header_row, max_row=max_row)
        chart_obj.add_data(data_ref, titles_from_data=True)
    cats_ref = Reference(ws, min_col=1, min_row=header_row + 1, max_row=max_row)
    chart_obj.set_categories(cats_ref)


def add_native_metric_chart_to_sheet(ws, title, metrics, label_header, *, anchor, header_row=1, max_row=None):
    if not metrics:
        return False
    max_row = max_row or ws.max_row
    if max_row <= header_row:
        return False
    bar_metric_indexes = [index for index, metric in enumerate(metrics) if metric.get("chart_type") == "bar"]
    line_metric_indexes = [index for index, metric in enumerate(metrics) if metric.get("chart_type") != "bar"]
    if bar_metric_indexes and line_metric_indexes:
        chart_obj = BarChart()
        chart_obj.type = "col"
        chart_obj.title = title
        chart_obj.style = 13
        chart_obj.y_axis.title = "Значение"
        chart_obj.x_axis.title = label_header
        chart_obj.height = 14
        chart_obj.width = 30
        add_metric_series(chart_obj, ws, bar_metric_indexes, header_row=header_row, max_row=max_row)
        style_native_chart_series(chart_obj, is_bar_chart=True)

        line_chart = LineChart()
        line_chart.y_axis.axId = 200
        line_chart.y_axis.title = "Доп. ось"
        line_chart.y_axis.crosses = "max"
        add_metric_series(line_chart, ws, line_metric_indexes, header_row=header_row, max_row=max_row)
        style_native_chart_series(line_chart, offset=len(bar_metric_indexes), is_bar_chart=False)
        chart_obj += line_chart
    else:
        is_bar_chart = bool(bar_metric_indexes)
        chart_obj = BarChart() if is_bar_chart else LineChart()
        chart_obj.title = title
        chart_obj.style = 13
        chart_obj.y_axis.title = "Значение"
        chart_obj.x_axis.title = label_header
        chart_obj.height = 14
        chart_obj.width = 30
        if is_bar_chart:
            chart_obj.type = "bar"
        add_metric_series(chart_obj, ws, range(len(metrics)), header_row=header_row, max_row=max_row)
        style_native_chart_series(chart_obj, is_bar_chart=is_bar_chart)
    ws.add_chart(chart_obj, anchor)
    return True


def build_chart_export_workbook(
    title,
    rows,
    metrics,
    filters,
    *,
    label_key="report_date",
    label_header="Дата",
    chart_image_data_url="",
):
    wb = Workbook()
    data_ws = wb.active
    data_ws.title = "Экспорт"
    filters_ws = wb.create_sheet("Фильтры")

    headers = [label_header] + [metric["label"] for metric in metrics]
    data_ws.append(headers)
    for row in rows:
        data_ws.append([row.get(label_key)] + [to_float(row.get(metric["key"], 0)) for metric in metrics])
    apply_kokoc_table_style(data_ws, max_width=34)

    filters_ws.append(["Параметр", "Значение"])
    for key, value in filters.items():
        filters_ws.append([key, value])
    apply_kokoc_table_style(filters_ws, max_width=70)

    chart_anchor = f"{get_column_letter(len(headers) + 2)}2"
    snapshot_added = add_chart_snapshot_to_sheet(data_ws, chart_image_data_url, anchor=chart_anchor)

    if not snapshot_added:
        add_native_metric_chart_to_sheet(data_ws, title, metrics, label_header, anchor=chart_anchor)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def append_metric_table(ws, rows, metrics, *, label_key="report_date", label_header="Дата", header_row=1):
    headers = [label_header] + [metric["label"] for metric in metrics]
    for column_index, header in enumerate(headers, start=1):
        ws.cell(row=header_row, column=column_index, value=header)
    for row_index, row in enumerate(rows, start=header_row + 1):
        ws.cell(row=row_index, column=1, value=row.get(label_key))
        for column_index, metric in enumerate(metrics, start=2):
            ws.cell(row=row_index, column=column_index, value=to_float(row.get(metric["key"], 0)))
    return headers


def style_report_table(ws, *, header_row, max_width=34):
    header_fill = PatternFill("solid", fgColor=KOKOC_EXCEL_TIFFANY_LIGHT)
    header_border = Border(bottom=Side(style="thin", color=KOKOC_EXCEL_TIFFANY))
    body_border = Border(bottom=Side(style="thin", color=KOKOC_EXCEL_LINE))
    for row in ws.iter_rows(min_row=header_row, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            cell.font = kokoc_excel_font(bold=cell.row == header_row)
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            cell.border = header_border if cell.row == header_row else body_border
            if cell.row == header_row:
                cell.fill = header_fill
    end_col = get_column_letter(max(ws.max_column, 1))
    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:{end_col}{ws.max_row}"
    for index in range(1, ws.max_column + 1):
        values = [
            str(ws.cell(row=row_index, column=index).value or "")
            for row_index in range(header_row, min(ws.max_row, header_row + 200) + 1)
        ]
        width = min(max(max((len(value) for value in values), default=10) + 2, 12), max_width)
        ws.column_dimensions[get_column_letter(index)].width = width


def build_report_export_workbook(
    title,
    rows,
    metrics,
    filters,
    *,
    raw_rows=None,
    label_key="report_date",
    label_header="Дата",
):
    wb = Workbook()
    report_ws = wb.active
    report_ws.title = "Отчет"
    period_ws = wb.create_sheet("Данные периода")
    filters_ws = wb.create_sheet("Фильтры")

    report_ws.sheet_view.showGridLines = False
    report_ws["A1"] = title
    report_ws["A1"].font = kokoc_excel_font(bold=True, size=14)
    report_ws["A2"] = filters.get("Период", "")
    report_ws["A2"].font = kokoc_excel_font(size=9, color="50646A")
    if metrics:
        report_ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(len(metrics) + 1, 6))
        report_ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=min(len(metrics) + 1, 6))
    table_header_row = 5
    headers = append_metric_table(
        report_ws,
        rows,
        metrics,
        label_key=label_key,
        label_header=label_header,
        header_row=table_header_row,
    )
    style_report_table(report_ws, header_row=table_header_row, max_width=34)
    chart_anchor = f"{get_column_letter(len(headers) + 3)}5"
    add_native_metric_chart_to_sheet(
        report_ws,
        title,
        metrics,
        label_header,
        anchor=chart_anchor,
        header_row=table_header_row,
        max_row=table_header_row + len(rows),
    )

    period_rows = raw_rows if raw_rows is not None else rows
    append_metric_table(
        period_ws,
        period_rows,
        metrics,
        label_key=label_key,
        label_header=label_header,
        header_row=1,
    )
    apply_kokoc_table_style(period_ws, max_width=38)

    filters_ws.append(["Параметр", "Значение"])
    for key, value in filters.items():
        filters_ws.append([key, value])
    apply_kokoc_table_style(filters_ws, max_width=70)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


WEEKLY_DYNAMICS_EXPORT_CONFIG = {
    "category_growth_qty": ("Топ роста категорий, шт", "change_units", "16A34A", "Категория"),
    "category_growth_pct": ("Топ роста категорий, %", "change_pct", "16A34A", "Категория"),
    "category_growth_amount": ("Топ роста категорий, руб", "change_amount_rub", "00AFAA", "Категория"),
    "category_decline_qty": ("Топ падения категорий, шт", "change_units", "DC2626", "Категория"),
    "category_decline_pct": ("Топ падения категорий, %", "change_pct", "DC2626", "Категория"),
    "category_decline_amount": ("Топ падения категорий, руб", "change_amount_rub", "EA580C", "Категория"),
    "sku_growth_qty": ("Топ роста SKU, шт", "change_units", "16A34A", "SKU"),
    "sku_growth_pct": ("Топ роста SKU, %", "change_pct", "16A34A", "SKU"),
    "sku_growth_amount": ("Топ роста SKU, руб", "change_amount_rub", "00AFAA", "SKU"),
    "sku_decline_qty": ("Топ падения SKU, шт", "change_units", "DC2626", "SKU"),
    "sku_decline_pct": ("Топ падения SKU, %", "change_pct", "DC2626", "SKU"),
    "sku_decline_amount": ("Топ падения SKU, руб", "change_amount_rub", "EA580C", "SKU"),
}


def weekly_dynamics_export_filters(params, title, *, previous_week="", current_week=""):
    client = current_client_key()
    marketplace = params.get("marketplace", [DEFAULT_MARKETPLACE])[0]
    return {
        "Клиент": ADMIN_CLIENTS.get(client, {}).get("label", client),
        "Отчет": title,
        "Маркетплейс": MARKETPLACES.get(marketplace, {}).get("label", marketplace),
        "Период": f"{params.get('date_from', [''])[0] or 'начало'} - {params.get('date_to', [''])[0] or 'конец'}",
        "Сравниваемые недели": f"{previous_week or 'нет'} -> {current_week or 'нет'}",
        "Категории": ", ".join(params.get("categories", [])) or "Все",
        "Наименование": params.get("product", ["Все"])[0] or "Все",
        "Артикул": params.get("article", ["Все"])[0] or "Все",
        "Приоритетная коллекция": ", ".join(params.get(COLLECTION_STATUS_PARAM, [])) or "Все",
        "Тег SKU": ", ".join(params.get(SEO_STATUS_PARAM, [])) or "Все",
    }


def build_weekly_trend_export_workbook(title, rows, metrics, filters):
    wb = Workbook()
    ws = wb.active
    ws.title = "Недельная динамика"
    filters_ws = wb.create_sheet("Фильтры")
    headers = ["Неделя"] + [metric["label"] for metric in metrics] + [f"{metric['label']}: к пред. неделе, %" for metric in metrics]
    ws.append(headers)
    for index, row in enumerate(rows):
        values = [to_float(row.get(metric["key"], 0)) for metric in metrics]
        changes = []
        previous = rows[index - 1] if index > 0 else None
        for metric, current_value in zip(metrics, values):
            previous_value = to_float(previous.get(metric["key"], 0)) if previous else 0
            changes.append(round((current_value - previous_value) / abs(previous_value) * 100, 2) if previous_value else None)
        ws.append([row.get("report_date")] + values + changes)
    apply_kokoc_table_style(ws, max_width=38)
    for row_index in range(2, ws.max_row + 1):
        for metric_index, metric in enumerate(metrics, start=2):
            ws.cell(row=row_index, column=metric_index).number_format = '0.00"%"' if metric["key"].endswith("_pct") else '#,##0.00'
        for change_index in range(2 + len(metrics), 2 + len(metrics) * 2):
            ws.cell(row=row_index, column=change_index).number_format = '0.00"%"'

    if metrics and ws.max_row > 1:
        chart = BarChart() if all(metric.get("chart_type") == "bar" for metric in metrics) else LineChart()
        chart.title = title
        chart.style = 13
        chart.y_axis.title = "Значение"
        chart.x_axis.title = "Неделя"
        if isinstance(chart, BarChart):
            chart.type = "col"
        chart.add_data(Reference(ws, min_col=2, max_col=1 + len(metrics), min_row=1, max_row=ws.max_row), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
        chart.height = 14
        chart.width = 30
        palette = [KOKOC_EXCEL_TIFFANY, "1D9BD1", "F59E0B", "EF4444", "7C3AED", "16A34A"]
        for series_index, series in enumerate(chart.series):
            if isinstance(chart, BarChart):
                series.graphicalProperties.solidFill = palette[series_index % len(palette)]
            else:
                series.graphicalProperties.line.solidFill = palette[series_index % len(palette)]
        ws.add_chart(chart, f"{get_column_letter(len(headers) + 2)}2")

    filters_ws.append(["Параметр", "Значение"])
    for key, value in filters.items():
        filters_ws.append([key, value])
    apply_kokoc_table_style(filters_ws, max_width=70)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def build_weekly_ranking_export_workbook(title, rows, filters, *, metric_key, color, label_header):
    wb = Workbook()
    ws = wb.active
    ws.title = "Рейтинг"
    filters_ws = wb.create_sheet("Фильтры")
    headers = [
        label_header, "ID / SKU", "Предыдущая неделя", "Текущая неделя",
        "Заказы предыдущей недели, шт", "Заказы текущей недели, шт", "Изменение заказов, шт",
        "Заказы предыдущей недели, руб", "Заказы текущей недели, руб", "Изменение заказов, руб", "Изменение, %",
    ]
    ws.append(headers)
    for row in rows:
        ws.append([
            row.get("entity_label"), row.get("entity_key"), row.get("previous_week"), row.get("current_week"),
            to_float(row.get("previous_units")), to_float(row.get("current_units")), to_float(row.get("change_units")),
            to_float(row.get("previous_amount_rub")), to_float(row.get("current_amount_rub")),
            to_float(row.get("change_amount_rub")), to_float(row.get("change_pct")),
        ])
    apply_kokoc_table_style(ws, max_width=48)
    direction_fill = PatternFill("solid", fgColor="E8F8EE" if "growth" in title.lower() or "рост" in title.lower() else "FDECEC")
    for row_index in range(2, ws.max_row + 1):
        for column_index in (5, 6, 7, 8, 9, 10):
            ws.cell(row=row_index, column=column_index).number_format = '#,##0.00'
        ws.cell(row=row_index, column=11).number_format = '0.00"%"'
        for column_index in (7, 10, 11):
            ws.cell(row=row_index, column=column_index).fill = direction_fill
    if rows:
        value_column = 7 if metric_key == "change_units" else (11 if metric_key == "change_pct" else 10)
        chart = BarChart()
        chart.type = "bar"
        chart.style = 13
        chart.title = title
        chart.y_axis.title = label_header
        chart.x_axis.title = "Изменение заказов, шт" if metric_key == "change_units" else ("Изменение, %" if metric_key == "change_pct" else "Изменение заказов, руб")
        chart.add_data(Reference(ws, min_col=value_column, min_row=1, max_row=ws.max_row), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
        chart.height = 11
        chart.width = 25
        if chart.series:
            chart.series[0].graphicalProperties.solidFill = color
        ws.add_chart(chart, "M2")
    filters_ws.append(["Параметр", "Значение"])
    for key, value in filters.items():
        filters_ws.append([key, value])
    apply_kokoc_table_style(filters_ws, max_width=70)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def handle_weekly_dynamics_export(parsed, payload=None):
    payload = payload or {}
    query = str(payload.get("query") or parsed.query or "")
    parsed = parsed._replace(query=query)
    params = parse_qs(query)
    if params.get("weekly_view", [""])[0] == "decisions":
        from weekly_decisions import load_report, export_report
        return export_report(load_report(parsed, globals())), "weekly_decisions.xlsx"
    kind = str(payload.get("kind") or params.get("kind", ["trend"])[0]).strip()
    if kind == "trend":
        daily_payload = handle_funnel_daily(parsed)
        rows = aggregate_chart_rows(normalize_rows(daily_payload.get("rows", [])), "funnel", "week")
        metrics = chart_export_metrics(params, "funnel", "primary")
        filters = weekly_dynamics_export_filters(params, "Еженедельная динамика")
        filters["Метрики"] = ", ".join(metric["label"] for metric in metrics)
        return build_weekly_trend_export_workbook("Еженедельная динамика", rows, metrics, filters), "weekly_dynamics_trend.xlsx"

    config = WEEKLY_DYNAMICS_EXPORT_CONFIG.get(kind)
    if not config:
        raise RuntimeError(f"Weekly dynamics export is not supported: {kind}")
    title, metric_key, color, label_header = config
    rankings = handle_weekly_dynamics(parsed)
    rows = rankings.get(kind, [])
    filters = weekly_dynamics_export_filters(
        params,
        title,
        previous_week=rankings.get("previous_week", ""),
        current_week=rankings.get("current_week", ""),
    )
    body = build_weekly_ranking_export_workbook(
        title,
        rows,
        filters,
        metric_key=metric_key,
        color=color,
        label_header=label_header,
    )
    return body, f"weekly_dynamics_{kind}.xlsx"


def chart_export_context(parsed, payload=None):
    source_payload = payload or {}
    query = str(source_payload.get("query") or parsed.query or "")
    parsed = parsed._replace(query=query)
    params = parse_qs(query)
    chart_image_data_url = str(source_payload.get("chart_image_data_url") or "")
    dashboard = params.get("dashboard", [""])[0]
    if dashboard not in CHART_METRIC_CATALOGS:
        raise RuntimeError(f"Chart export is not supported for dashboard: {dashboard}")
    chart = params.get("chart", ["primary"])[0]
    chart = "secondary" if chart == "secondary" else "primary"
    marketplace = marketplace_from_query(parsed.query)
    period_group = params.get("period_group", ["day"])[0]
    period_group_label = {"day": "по дням", "week": "по неделям", "month": "по месяцам"}.get(period_group, "по дням")
    label_key = "report_date"
    label_header = "Дата"
    rows = []
    if dashboard == "abc":
        data_payload = handle_stats(parsed)
        rows = normalize_rows(data_payload.get("rows", []))
        title = "Топ категорий" if chart == "primary" else "ABC итог"
        label_key = "category_name"
        label_header = data_payload.get("category_label") or abc_category_title(abc_category_level_from_query(parsed.query))
    elif dashboard == "product":
        data_payload = handle_product_stats(parsed)
        rows = normalize_rows(data_payload.get("rows", []))
        title = "Топ продуктов" if chart == "primary" else "ABC итог"
        label_key = "naimenovanie"
        label_header = "Продукт"
    elif dashboard == "sku":
        data_payload = handle_sku_stats(parsed)
        rows = normalize_rows(data_payload.get("rows", []))
        title = "Топ наименований по остаткам" if chart == "primary" else "Топ наименований по заказам"
        label_key = "naimenovanie"
        label_header = "Наименование"
    elif dashboard == "adv":
        data_payload = handle_adv_daily(parsed)
        title = f"Товарная реклама {period_group_label}" if chart == "primary" else "Показатели товарной рекламы"
    elif dashboard == "mediaAdv":
        data_payload = handle_media_adv_daily(parsed)
        title = f"Медийная реклама {period_group_label}" if chart == "primary" else "Показатели медийной рекламы"
    else:
        data_payload = handle_funnel_daily(parsed)
        title = "Воронка продаж" if chart == "primary" else "Показатели воронки"
    metrics = chart_export_metrics(params, dashboard, chart)
    raw_rows = rows
    if dashboard in {"adv", "mediaAdv", "funnel"}:
        raw_rows = enrich_chart_export_rows(normalize_rows(data_payload.get("rows", [])), dashboard)
        rows = aggregate_chart_rows(raw_rows, dashboard, period_group)
    elif dashboard in {"abc", "product"} and chart == "secondary" and metrics:
        metric_key = metrics[0]["key"]
        totals = {}
        for row in rows:
            key = row.get("abc_combined") or "Без ABC"
            totals[key] = to_float(totals.get(key, 0)) + to_float(row.get(metric_key, 0))
        rows = [
            {"chart_label": key, metric_key: value}
            for key, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        ][:6]
        raw_rows = rows
        label_key = "chart_label"
        label_header = "ABC итог"
    client = current_client_key()
    filters = {
        "Клиент": ADMIN_CLIENTS.get(client, {}).get("label", client),
        "Отчет": title,
        "Маркетплейс": MARKETPLACES.get(marketplace, {}).get("label", marketplace),
        "Период": f"{params.get('date_from', [''])[0] or 'начало'} - {params.get('date_to', [''])[0] or 'конец'}",
        "Отобразить по": period_group_label,
        "Категории": ", ".join(params.get("categories", [])) or "Все",
        "Наименование": params.get("product", ["Все"])[0] or "Все",
        "Артикул": params.get("article", ["Все"])[0] or "Все",
        "Метрики": ", ".join(metric["label"] for metric in metrics),
        "Оси": json.dumps({metric["key"]: metric["axis"] for metric in metrics}, ensure_ascii=False),
        "Типы": json.dumps({metric["key"]: metric["chart_type"] for metric in metrics}, ensure_ascii=False),
    }
    payload = {
        "dashboard": dashboard,
        "chart": chart,
        "marketplace": marketplace,
        "title": title,
        "rows": rows,
        "raw_rows": raw_rows,
        "metrics": metrics,
        "filters": filters,
        "label_key": label_key,
        "label_header": label_header,
        "chart_image_data_url": chart_image_data_url,
    }


def handle_chart_export(parsed, payload=None):
    context = chart_export_context(parsed, payload)
    body = build_chart_export_workbook(
        context["title"],
        context["rows"],
        context["metrics"],
        context["filters"],
        label_key=context["label_key"],
        label_header=context["label_header"],
        chart_image_data_url=context["chart_image_data_url"],
    )
    filename = f"{context['dashboard']}_{context['chart']}_chart_{context['marketplace']}.xlsx"
    return body, filename


def handle_report_export(parsed, payload=None):
    context = chart_export_context(parsed, payload)
    body = build_report_export_workbook(
        context["title"],
        context["rows"],
        context["metrics"],
        context["filters"],
        raw_rows=context["raw_rows"],
        label_key=context["label_key"],
        label_header=context["label_header"],
    )
    filename = f"{context['dashboard']}_report_{context['marketplace']}.xlsx"
    return body, filename


def handle_export(parsed):
    params = parse_qs(parsed.query)
    dashboard = params.get("dashboard", ["abc"])[0]
    if dashboard == "planfact":
        workbook = Workbook()
        workbook.remove(workbook.active)
        for title, handler, columns in (
            ("План-факт", handle_planfact_scorecard, ["marketplace", "plan_month", "sales_plan_rub", "orders_rub", "sales_rub", "sales_plan_fact_pct", "sales_runrate_rub", "sales_runrate_pct", "sales_recent_runrate_rub", "sales_recent_runrate_pct", "ad_spend_plan_rub", "ad_spend_rub"]),
            ("По месяцам", handle_planfact_monthly, ["marketplace", "plan_month", "sales_plan_rub", "sales_rub", "sales_plan_fact_pct", "ad_spend_plan_rub", "ad_spend_rub"]),
            ("По дням", handle_planfact_daily, ["marketplace", "report_date", "sales_plan_rub", "sales_plan_daily_rub", "sales_plan_elapsed_rub", "sales_rub", "sales_cum_rub", "sales_month_plan_fact_pct", "sales_elapsed_plan_fact_pct", "ad_spend_plan_rub", "ad_spend_rub"]),
        ):
            sheet = workbook.create_sheet(title)
            labels = {"marketplace": "Площадка", "plan_month": "Месяц", "report_date": "Дата", "sales_plan_rub": "План продаж, руб", "sales_plan_daily_rub": "План на день, руб", "sales_plan_elapsed_rub": "План накопительно, руб", "sales_rub": "Продажи, руб", "sales_cum_rub": "Продажи накопительно, руб", "orders_rub": "Заказы, руб", "sales_plan_fact_pct": "Выполнение плана, %", "sales_month_plan_fact_pct": "Выполнение месячного плана, %", "sales_elapsed_plan_fact_pct": "Выполнение плана на дату, %", "sales_runrate_rub": "Run-Rate месяца, руб", "sales_runrate_pct": "Run-Rate / план, %", "sales_recent_runrate_rub": "Run-Rate 5 дней, руб", "sales_recent_runrate_pct": "Run-Rate 5 дней / план, %", "ad_spend_plan_rub": "План расходов, руб", "ad_spend_rub": "Расходы, руб"}
            sheet.append([labels.get(column, column) for column in columns])
            for row in handler(parsed).get("rows", []):
                sheet.append([row.get(column) for column in columns])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            apply_kokoc_table_style(sheet)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue(), "planfact.xlsx"
    if dashboard in {"avitoOverview", "avitoCampaigns", "avitoGroups", "avitoCreatives", "avitoDaily"}:
        from avito_ads_dashboard import export_workbook

        client = current_client_key()
        if "avito" not in set(ADMIN_CLIENTS[client].get("marketplaces") or []):
            raise ValueError("Avito Ads не подключён для выбранного клиента")
        return export_workbook(parsed, read_db_config(client))
    is_sku = dashboard == "sku"
    is_product = dashboard == "product"
    is_adv = dashboard == "adv"
    is_media_adv = dashboard == "mediaAdv"
    is_funnel = dashboard == "funnel"
    marketplace = marketplace_from_query(parsed.query)
    if is_product and marketplace in ABC_BASE_VIEWS:
        outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query)
        base_sql, base_values = ozon_abc_product_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)
        columns = abc_product_columns_for_marketplace(marketplace) + ABC_PRODUCT_MAPPING_COLUMNS
        column_where, column_values = outer_column_filter_clause(parsed.query, columns)
        sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
        sort_dir = "asc" if sort_dir == "asc" else "desc"
        sort_column = params.get("sort_col", ["zakazano_rub"])[0]
        if sort_column not in columns:
            sort_column = "zakazano_rub"
        direction = "ASC" if sort_dir == "asc" else "DESC"
        nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"
        query = (
            f"SELECT * FROM ({base_sql}) x "
            f"{column_where} "
            f"ORDER BY {sort_column} {direction} {nulls}, category_name, artikul_wb"
        )
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(query, base_values + outer_values + column_values)
            rows = normalize_rows(cur.fetchall())

        wb = Workbook()
        ws = wb.active
        ws.title = "ABC products"
        ws.append([COLUMN_LABELS.get(column, column) for column in columns])
        for row in rows:
            ws.append([row.get(column) for column in columns])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for index, column in enumerate(columns, start=1):
            values_for_width = [COLUMN_LABELS.get(column, column)]
            values_for_width.extend(str(row.get(column, "")) for row in rows[:200])
            width = min(max(len(value) for value in values_for_width) + 2, 42)
            ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
        apply_kokoc_table_style(ws)
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue(), f"abc_products_{marketplace}.xlsx"
    if not is_product and not is_sku and not is_adv and not is_media_adv and marketplace in ABC_BASE_VIEWS:
        category_level = abc_category_level_from_query(parsed.query)
        outer_where, outer_values = ozon_abc_outer_filters_from_query(parsed.query, include_product=False)
        base_sql, base_values = ozon_abc_category_sql(parsed.query, outer_where=outer_where, marketplace=marketplace)
        columns = list(OZON_ABC_COLUMNS)
        column_where, column_values = outer_column_filter_clause(parsed.query, columns)
        sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
        sort_dir = "asc" if sort_dir == "asc" else "desc"
        sort_column = params.get("sort_col", [DEFAULT_SORT_COLUMN])[0]
        if sort_column not in columns:
            sort_column = DEFAULT_SORT_COLUMN
        direction = "ASC" if sort_dir == "asc" else "DESC"
        nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"
        query = (
            f"SELECT * FROM ({base_sql}) x "
            f"{column_where} "
            f"ORDER BY {sort_column} {direction} {nulls}, category_name"
        )
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(query, base_values + outer_values + column_values)
            rows = normalize_rows(cur.fetchall())

        wb = Workbook()
        ws = wb.active
        ws.title = "ABC categories"
        category_label = abc_category_title(category_level)
        ws.append([
            category_label if column == "category_name" else COLUMN_LABELS.get(column, column)
            for column in columns
        ])
        for row in rows:
            ws.append([row.get(column) for column in columns])
        apply_kokoc_table_style(ws)
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue(), f"abc_categories_{marketplace}.xlsx"
    if is_sku:
        view_name = sku_view_for_query(parsed.query)
        where, values = filters_from_query(
            parsed.query,
            include_abc=False,
            include_mapping=True,
            seo_marketplace=marketplace,
            seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
            relation_alias="v",
        )
        sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
        sort_dir = "asc" if sort_dir == "asc" else "desc"
        with get_conn() as conn, conn.cursor() as cur:
            base_columns = get_view_columns(cur, view_name)
            columns = base_columns + [COLLECTION_STATUS_COLUMN, SEO_STATUS_COLUMN] + MAPPING_COLUMNS
            source_sql, source_values = sku_scoring_source_sql(parsed.query, marketplace, base_columns)
            where, values = append_column_filters(where, values, parsed.query, base_columns, alias="v")
            sort_column = params.get("sort_col", ["total_stock_qty"])[0]
            if sort_column not in columns:
                sort_column = "total_stock_qty" if "total_stock_qty" in columns else columns[0]
            sku_join = sql.SQL("").join(
                [
                    mapping_join_for_report("sku", marketplace),
                    collection_status_join_sql(marketplace, seo_sku_expr_for_mapping(marketplace)),
                    seo_status_join_sql(marketplace, seo_sku_expr_for_mapping(marketplace)),
                ]
            )
            direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
            nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
            query = sql.SQL(
                """
                SELECT
                    v.*
                    {collection_status_col}
                    {seo_status_col}
                    {mapping_cols}
                FROM ({source}) v
                {join}
                {where}
                ORDER BY {sort_col} {direction} {nulls}, artikul_wb
                """
            ).format(
                source=sql.SQL(source_sql),
                collection_status_col=collection_status_select_sql(),
                seo_status_col=seo_status_select_sql(),
                mapping_cols=mapping_select_sql(),
                join=sku_join,
                where=sql.SQL(where),
                sort_col=sql.Identifier(sort_column),
                direction=direction,
                nulls=nulls,
            )
            cur.execute(query, source_values + values)
            rows = normalize_rows(cur.fetchall())

        wb = Workbook()
        ws = wb.active
        ws.title = "SKU scoring"
        ws.append([COLUMN_LABELS.get(column, column) for column in columns])
        for row in rows:
            ws.append([row.get(column) for column in columns])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for index, column in enumerate(columns, start=1):
            values_for_width = [COLUMN_LABELS.get(column, column)]
            values_for_width.extend(str(row.get(column, "")) for row in rows[:200])
            width = min(max(len(value) for value in values_for_width) + 2, 42)
            ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
        apply_kokoc_table_style(ws)
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue(), f"sku_card_scoring_{marketplace}.xlsx"
    if is_funnel:
        return handle_funnel_export(parsed)
    if is_adv:
        where, values = adv_filters_from_query(parsed.query)
        view_name = adv_view_for_marketplace(marketplace)
        where = append_adv_product_grain_filter(where, marketplace)
    elif is_media_adv:
        where, values = media_adv_filters_from_query(parsed.query)
        view_name = media_adv_view_for_query(parsed.query)
    elif is_funnel:
        where, values = funnel_filters_from_query(parsed.query)
        view_name = funnel_view_for_query(parsed.query)
    else:
        where, values = filters_from_query(parsed.query, include_abc=not is_sku, relation_alias="v")
    if is_product:
        view_name = product_view_for_query(parsed.query)
    elif is_sku:
        view_name = sku_view_for_query(parsed.query)
    elif not is_adv and not is_media_adv and not is_funnel:
        view_name = view_for_query(parsed.query)
    sort_dir = params.get("sort_dir", [DEFAULT_SORT_DIRECTION])[0].lower()
    sort_dir = "asc" if sort_dir == "asc" else "desc"

    with get_conn() as conn, conn.cursor() as cur:
        columns = adv_product_columns(get_view_columns(cur, view_name), marketplace) if is_adv else (media_adv_columns(cur, view_name) if is_media_adv else (funnel_columns(cur, marketplace) if is_funnel else get_view_columns(cur, view_name)))
        if is_adv:
            filter_columns = get_view_columns(cur, view_name)
        elif is_media_adv:
            filter_columns = get_view_columns(cur, view_name)
        elif is_funnel:
            filter_columns = get_view_columns(cur, view_name)
        else:
            filter_columns = get_view_columns(cur, view_name)
        where, values = append_column_filters(where, values, parsed.query, filter_columns, alias="v")
        sort_column = params.get("sort_col", [DEFAULT_SORT_COLUMN])[0]
        if sort_column not in columns:
            fallback = "report_date" if (is_adv or is_media_adv or is_funnel) else ("total_stock_qty" if "total_stock_qty" in columns else columns[0])
            sort_column = fallback

        direction = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
        nulls = sql.SQL("NULLS FIRST") if sort_dir == "asc" else sql.SQL("NULLS LAST")
        query = sql.SQL(
            """
            SELECT
                v.*{adv_extra}{collection_status_col}{seo_status_col}{mapping_cols}
            FROM public.{view} v
            {join}
            {where}
            ORDER BY {sort_col} {direction} {nulls}, category_name
            """
        ).format(
            adv_extra=sql.SQL(", {ratio} AS adv_sales_to_total_sales_pct").format(ratio=adv_ratio_sql()) if is_adv else sql.SQL(""),
            collection_status_col=collection_status_select_sql() if is_adv else sql.SQL(""),
            seo_status_col=seo_status_select_sql() if is_adv else sql.SQL(""),
            mapping_cols=mapping_select_sql() if is_adv else sql.SQL(""),
            view=sql.Identifier(view_name),
            join=sql.SQL("").join([
                mapping_join_for_report("adv", marketplace),
                collection_status_join_sql(marketplace, "v.sku"),
                seo_status_join_sql(marketplace, "v.sku"),
            ]) if is_adv else sql.SQL(""),
            where=sql.SQL(where),
            sort_col=sql.Identifier(sort_column),
            direction=direction,
            nulls=nulls,
        )
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())

    wb = Workbook()
    ws = wb.active
    ws.title = "ABC categories"
    ws.append([COLUMN_LABELS.get(column, column) for column in columns])
    for row in rows:
        ws.append([row.get(column) for column in columns])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(columns, start=1):
        values_for_width = [COLUMN_LABELS.get(column, column)]
        values_for_width.extend(str(row.get(column, "")) for row in rows[:200])
        width = min(max(len(value) for value in values_for_width) + 2, 42)
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
    apply_kokoc_table_style(ws)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    prefix = "ozon_funnel_daily" if is_funnel else (f"{marketplace}_media_adv_daily" if is_media_adv else (f"{marketplace}_adv_daily" if is_adv else ("abc_products" if is_product else ("sku_card_scoring" if is_sku else "abc_categories"))))
    filename = f"{prefix}_{marketplace}.xlsx"
    return output.getvalue(), filename


def handle_funnel_export(parsed):
    marketplace = marketplace_from_query(parsed.query)
    view_name = funnel_view_for_query(parsed.query)
    where, values = funnel_filters_from_query(parsed.query)
    bought_select = sql.SQL(
        """
            coalesce(sum(v.bought_units), 0) AS bought_units,
            coalesce(sum(v.bought_amount_rub), 0) AS bought_amount_rub,
            coalesce(sum(v.cohort_bought_units), 0) AS cohort_bought_units,
            coalesce(sum(v.cohort_bought_amount_rub), 0) AS cohort_bought_amount_rub,
        """
    ) if marketplace == "wb" else sql.SQL("")
    columns = [
        "report_date",
        "category_name",
        "product_artikul",
        "product_name",
        "priority_collection",
        "seo_status",
        "sku_count",
        "impressions_total",
        "impressions_search_catalog",
        "card_visits",
        "cart_adds",
        "ordered_units",
        "ordered_amount_rub",
        *(["bought_units", "bought_amount_rub", "cohort_bought_units", "cohort_bought_amount_rub"] if marketplace == "wb" else []),
        "search_to_card_visit_pct",
        "total_impression_to_card_visit_pct",
        "card_visit_to_cart_pct",
        "cart_to_order_pct",
        "card_visit_to_order_pct",
        "ordered_amount_per_unit_rub",
    ]
    query = sql.SQL(
        """
        SELECT
            v.report_date,
            v.category_name,
            v.product_artikul,
            v.product_name,
            coalesce(
                string_agg(DISTINCT collection.priority_collection, ', ' ORDER BY collection.priority_collection)
                    FILTER (WHERE collection.priority_collection IS NOT NULL),
                {no_tag}
            ) AS priority_collection,
            coalesce(
                string_agg(DISTINCT seo.seo_status, ', ' ORDER BY seo.seo_status)
                    FILTER (WHERE seo.seo_status IS NOT NULL),
                {no_tag}
            ) AS seo_status,
            count(DISTINCT coalesce(v.sku, v.barcode, v.seller_article, v.product_artikul)) AS sku_count,
            coalesce(sum(v.impressions_total), 0) AS impressions_total,
            coalesce(sum(v.impressions_search_catalog), 0) AS impressions_search_catalog,
            coalesce(sum(v.card_visits), 0) AS card_visits,
            coalesce(sum(v.cart_adds), 0) AS cart_adds,
            coalesce(sum(v.ordered_units), 0) AS ordered_units,
            coalesce(sum(v.ordered_amount_rub), 0) AS ordered_amount_rub,
            {bought_select}
            CASE WHEN coalesce(sum(v.impressions_search_catalog), 0) <> 0
                THEN round(coalesce(sum(v.card_visits), 0)::numeric / coalesce(sum(v.impressions_search_catalog), 0)::numeric * 100, 2)
                ELSE 0 END AS search_to_card_visit_pct,
            CASE WHEN coalesce(sum(v.impressions_total), 0) <> 0
                THEN round(coalesce(sum(v.card_visits), 0)::numeric / coalesce(sum(v.impressions_total), 0)::numeric * 100, 2)
                ELSE 0 END AS total_impression_to_card_visit_pct,
            CASE WHEN coalesce(sum(v.card_visits), 0) <> 0
                THEN round(coalesce(sum(v.cart_adds), 0)::numeric / coalesce(sum(v.card_visits), 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_cart_pct,
            CASE WHEN coalesce(sum(v.cart_adds), 0) <> 0
                THEN round(coalesce(sum(v.ordered_units), 0)::numeric / coalesce(sum(v.cart_adds), 0)::numeric * 100, 2)
                ELSE 0 END AS cart_to_order_pct,
            CASE WHEN coalesce(sum(v.card_visits), 0) <> 0
                THEN round(coalesce(sum(v.ordered_units), 0)::numeric / coalesce(sum(v.card_visits), 0)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_order_pct,
            CASE WHEN coalesce(sum(v.ordered_units), 0) <> 0
                THEN round(coalesce(sum(v.ordered_amount_rub), 0)::numeric / coalesce(sum(v.ordered_units), 0)::numeric, 2)
                ELSE 0 END AS ordered_amount_per_unit_rub
        FROM public.{view} v
        {join}
        {where}
        GROUP BY v.report_date, v.category_name, v.product_artikul, v.product_name
        ORDER BY v.report_date, v.category_name, v.product_artikul, v.product_name
        """
    ).format(
        view=sql.Identifier(view_name),
        join=funnel_join_sql(marketplace, include_seo=True, include_collection=True),
        where=sql.SQL(where),
        bought_select=bought_select,
        no_tag=sql.Literal(NO_TAG_LABEL),
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        rows = normalize_rows(cur.fetchall())

    wb = Workbook()
    ws = wb.active
    ws.title = f"{marketplace.upper()} funnel"
    ws.append([COLUMN_LABELS.get(column, column) for column in columns])
    for row in rows:
        ws.append([row.get(column) for column in columns])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for index, column in enumerate(columns, start=1):
        values_for_width = [COLUMN_LABELS.get(column, column)]
        values_for_width.extend(str(row.get(column, "")) for row in rows[:200])
        width = min(max(len(value) for value in values_for_width) + 2, 42)
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
    apply_kokoc_table_style(ws)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue(), f"{marketplace}_funnel_daily_{marketplace}.xlsx"


def handle_summary(parsed):
    if is_ozon_abc_query(parsed.query):
        return handle_ozon_abc_summary(parsed)
    if has_mapping_filters(parsed.query):
        return handle_mapped_category_summary(parsed)
    where, values = filters_from_query(parsed.query)
    view_name = view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            count(*) AS categories,
            coalesce(sum(sku_count), 0) AS sku_count,
            coalesce(sum(total_stock_qty), 0) AS total_stock_qty,
            coalesce(sum(category_attribute_count), 0) AS category_attribute_count,
            coalesce(sum(zakazano_sht), 0) AS zakazano_sht,
            coalesce(sum(zakazano_rub), 0) AS zakazano_rub,
            coalesce(sum(vykupleno_sht), 0) AS vykupleno_sht
        FROM public.{view}
        {where}
        """
    ).format(view=sql.Identifier(view_name), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        row["vykup_pct_sht"] = (
            row["vykupleno_sht"] / row["zakazano_sht"] * 100 if row["zakazano_sht"] else 0
        )
        return row


def handle_mapped_category_summary(parsed):
    marketplace = marketplace_from_query(parsed.query)
    where, values = filters_from_query(
        parsed.query,
        include_abc=False,
        include_mapping=True,
        seo_marketplace=marketplace,
        seo_sku_expr=seo_sku_expr_for_mapping(marketplace),
        relation_alias="v",
    )
    view_name = sku_view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            count(DISTINCT v.category_name) AS categories,
            count(DISTINCT v.artikul_wb) AS sku_count,
            coalesce(sum(v.total_stock_qty), 0) AS total_stock_qty,
            coalesce(sum(v.category_attrs_total), 0) AS category_attribute_count,
            coalesce(sum(v.zakazano_sht), 0) AS zakazano_sht,
            coalesce(sum(v.zakazano_rub), 0) AS zakazano_rub,
            0 AS vykupleno_sht
        FROM public.{view} v
        {join}
        {where}
        """
    ).format(view=sql.Identifier(view_name), join=mapping_join_for_report("sku", marketplace), where=sql.SQL(where))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, values)
        row = dict(cur.fetchone())
        for key in row:
            row[key] = to_float(row[key])
        row["vykup_pct_sht"] = 0
        return row


def handle_sku_filters(parsed):
    marketplace = marketplace_from_query(parsed.query)
    view_name = sku_view_for_query(parsed.query)
    query = sql.SQL(
        """
        SELECT
            array[]::text[] AS abc_orders,
            array[]::text[] AS abc_sales,
            array[]::text[] AS abc_stock,
            array[]::text[] AS abc_combined,
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(DISTINCT category_name) AS categories
        FROM public.{view}
        """
    ).format(view=sql.Identifier(view_name))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        payload = dict(cur.fetchone())
        if marketplace in ABC_BASE_VIEWS:
            order_view = abc_base_views_for_marketplace(marketplace)["orders"]
            cur.execute(f"SELECT min(report_date) AS date_from, max(report_date) AS date_to FROM public.{order_view}")
            dates = dict(cur.fetchone())
            payload["date_from"] = normalize_value(dates.get("date_from"))
            payload["date_to"] = normalize_value(dates.get("date_to"))
        payload["marketplaces"] = client_marketplaces_payload()
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{view_name}"
        add_collection_status_options(cur, payload, marketplace)
        add_seo_status_options(cur, payload, marketplace)
        add_ozon_product_attribute_options(cur, payload, marketplace)
        add_sportmaster_filter_options(cur, payload, marketplace, parsed.query)
        if current_client_key() != "sportmaster" and not compact_filters_requested(parsed.query):
            add_mapping_options(cur, payload, marketplace=marketplace)
        return payload


def handle_filters(parsed):
    params = parse_qs(parsed.query)
    dashboard = params.get("dashboard", ["abc"])[0]
    if dashboard == "planfact" and (
        not client_supports_report(current_client_key(), "planfact")
        or not planfact_view_available(PLANFACT_DAILY_VIEW)
    ):
        return empty_planfact_filters_payload(parsed)
    if dashboard == "adv":
        return handle_adv_filters(parsed)
    if dashboard == "mediaAdv":
        return handle_media_adv_filters(parsed)
    if dashboard in {"funnel", "weeklyDynamics"}:
        return handle_funnel_filters(parsed)
    if dashboard == "inventoryHistory":
        from inventory_history import handle_filters as handle_inventory_history_filters

        payload = handle_inventory_history_filters(
            parsed, get_conn, marketplace_from_query, client_marketplaces_payload, funnel_view_for_marketplace
        )
        return normalize_rows([payload])[0]
    if dashboard == "wbSearchQueries":
        from wb_search_queries_dashboard import handle_filters as handle_wb_search_queries_filters
        return handle_wb_search_queries_filters(parsed, get_conn, current_client_key())
    if dashboard == "wbEntrance":
        from wb_entrance_dashboard import handle_filters as handle_wb_entrance_filters
        return handle_wb_entrance_filters(parsed, get_conn, current_client_key())
    if dashboard == "seoMonitoring":
        return handle_seo_monitoring_filters(parsed)
    if dashboard in {
        "commercialRadar",
        "mediaPlan",
        "profitLoss",
        "reviews",
        "salesPlanning",
        "unitEconomics",
        "wbAdSearchQueries",
    }:
        return handle_sku_filters(parsed)
    if dashboard == "planfact":
        return handle_planfact_filters(parsed)
    if dashboard == "abc" and is_ozon_abc_query(parsed.query):
        return handle_ozon_abc_filters(parsed)
    if dashboard == "product" and is_ozon_abc_query(parsed.query):
        return handle_ozon_product_filters(parsed)
    is_sku = dashboard == "sku"
    if is_sku:
        return handle_sku_filters(parsed)
    is_product = dashboard == "product"
    if is_product:
        view_name = product_view_for_query(parsed.query)
    elif is_sku:
        view_name = sku_view_for_query(parsed.query)
    else:
        view_name = view_for_query(parsed.query)
    if is_sku:
        query = sql.SQL(
            """
            SELECT
                array[]::text[] AS abc_orders,
                array[]::text[] AS abc_sales,
                array[]::text[] AS abc_stock,
                array[]::text[] AS abc_combined,
                array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
                count(DISTINCT category_name) AS categories
            FROM public.{view}
            """
        ).format(view=sql.Identifier(view_name))
    else:
        query = sql.SQL(
            """
            SELECT
                array_remove(array_agg(DISTINCT abc_orders ORDER BY abc_orders), NULL) AS abc_orders,
                array_remove(array_agg(DISTINCT abc_sales ORDER BY abc_sales), NULL) AS abc_sales,
                array_remove(array_agg(DISTINCT abc_stock ORDER BY abc_stock), NULL) AS abc_stock,
                array_remove(array_agg(DISTINCT abc_combined ORDER BY abc_combined), NULL) AS abc_combined,
                array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
                {category_count} AS categories
            FROM public.{view}
            """
        ).format(
            view=sql.Identifier(view_name),
            category_count=sql.SQL("count(DISTINCT category_name)") if is_product else sql.SQL("count(*)"),
        )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        payload = dict(cur.fetchone())
        payload["marketplaces"] = client_marketplaces_payload()
        marketplace = marketplace_from_query(parsed.query)
        payload["marketplace"] = marketplace
        payload["view"] = f"public.{view_name}"
        add_collection_status_options(cur, payload, marketplace)
        add_seo_status_options(cur, payload, marketplace)
        if not compact_filters_requested(parsed.query):
            add_mapping_options(cur, payload, marketplace=marketplace)
        return payload


def api_error_payload(exc):
    message = str(exc)
    if isinstance(exc, psycopg2.errors.QueryCanceled) or "statement timeout" in message.lower():
        return {
            "ok": False,
            "error": "Запрос не успел выполниться. Повторите позже.",
            "kind": "statement_timeout",
        }
    if isinstance(exc, psycopg2.Error):
        return {
            "ok": False,
            "error": "Источник данных временно недоступен.",
            "kind": "database_error",
        }
    return {
        "ok": False,
        "error": "Не удалось выполнить запрос.",
        "kind": "internal_error",
    }


REPORT_DATA_API_PATHS = {
    "/api/summary", "/api/product-summary", "/api/sku-summary",
    "/api/adv-summary", "/api/media-adv-summary", "/api/funnel-summary",
    "/api/weekly-dynamics", "/api/inventory-history-summary",
    "/api/planfact-summary", "/api/seo-monitoring-products",
    "/api/lamoda/dashboard",
}


def unavailable_report_source_payload(parsed, exc):
    params = parse_qs(parsed.query)
    return {
        "ok": True,
        "available": False,
        "data_status": "unavailable",
        "reason_code": "report_source_not_ready",
        "client": normalize_client_key((params.get("client") or [current_client_key()])[0]),
        "marketplace": (params.get("marketplace") or [""])[0],
        "message": "Витрина для выбранного отчёта и аккаунта ещё не сформирована.",
    }


def empty_planfact_payload(path):
    if path == "/api/planfact-summary":
        return {
            "days_count": 0,
            "orders_rub": 0,
            "sales_rub": 0,
            "sales_plan_rub": 0,
            "sales_plan_fact_pct": 0,
            "ad_spend_rub": 0,
            "ad_spend_plan_rub": 0,
            "ad_spend_plan_fact_pct": 0,
        }
    if path == "/api/planfact-daily":
        return {"rows": []}
    if path == "/api/planfact-monthly":
        return {"rows": []}
    if path == "/api/planfact-scorecard":
        return {"rows": []}
    return {"rows": []}


def validate_client_paths(payload):
    paths = payload.get("paths") if isinstance(payload, dict) else {}
    if not isinstance(paths, dict):
        paths = {}
    defaults = {
        "ozon_case_cat": rf"{SPORTMASTER_OZON_ROOT}\Case\Cat",
        "ozon_case_prod": rf"{SPORTMASTER_OZON_ROOT}\Case\Prod",
        "wb_case_cat": rf"{SPORTMASTER_WB_ROOT}\Case\Cat",
        "wb_case_prod": rf"{SPORTMASTER_WB_ROOT}\Case\Prod",
        "ozon_funnel": rf"{SPORTMASTER_OZON_ROOT}\Funel",
        "wb_funnel": rf"{SPORTMASTER_WB_ROOT}\Funel",
        "ozon_stock": rf"{SPORTMASTER_OZON_ROOT}\Stok",
        "wb_stock": rf"{SPORTMASTER_WB_ROOT}\Stock",
        "wb_adv": rf"{SPORTMASTER_WB_ROOT}\Adv",
        "wb_media_adv": rf"{SPORTMASTER_WB_ROOT}\Media_adv",
        "ozon_adv": rf"{SPORTMASTER_OZON_ROOT}\adv",
        "ozon_media_adv": rf"{SPORTMASTER_OZON_ROOT}\Media_adv",
    }
    rows = []
    for key, default in defaults.items():
        value = str(paths.get(key) or default).strip()
        target = Path(value)
        rows.append({"key": key, "path": value, "exists": target.exists(), "is_dir": target.is_dir()})
    return {"ok": all(row["exists"] for row in rows), "rows": rows}


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        if not str(os.environ.get("DASHBOARD_HTTP_LOGS") or "").strip():
            return
        try:
            super().log_message(format, *args)
        except OSError:
            return

    def send_static_file(self, relative_path, cache_control="no-cache"):
        file_path = STATIC_DIR / relative_path
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "static file not found")
            return
        body = file_path.read_bytes()
        if file_path.suffix.lower() == ".html" and b"/api/access/session/activity.js" not in body:
            body = body.replace(b"</head>", b'<script defer src="/api/access/session/activity.js"></script></head>', 1)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(file_path)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static_file_headers(self, relative_path, cache_control="no-cache"):
        file_path = STATIC_DIR / relative_path
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "static file not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(file_path)))
        self.send_header("Cache-Control", cache_control)
        size = file_path.stat().st_size
        if file_path.suffix.lower() == ".html":
            body = file_path.read_bytes()
            if b"/api/access/session/activity.js" not in body:
                body = body.replace(b"</head>", b'<script defer src="/api/access/session/activity.js"></script></head>', 1)
            size = len(body)
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, body, filename, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def send_ndjson_event(self, payload):
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.wfile.write(body)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(body) if body.strip() else {}

    def dashboard_access_prefix(self):
        prefix = str(self.headers.get("X-Forwarded-Prefix") or "").strip().rstrip("/")
        if not prefix.startswith("/") or any(character in prefix for character in "\r\n") or ".." in prefix:
            return ""
        return prefix

    def dashboard_access_cookie_header(self, token="", clear=False):
        cookie = SimpleCookie()
        cookie[DASHBOARD_ACCESS_COOKIE_NAME] = "" if clear else token
        morsel = cookie[DASHBOARD_ACCESS_COOKIE_NAME]
        prefix = self.dashboard_access_prefix()
        morsel["path"] = f"{prefix}/" if prefix else "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        morsel["max-age"] = "0" if clear else str(DASHBOARD_ACCESS_SESSION_TTL_SECONDS)
        if clear:
            morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        forwarded_proto = str(self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        if forwarded_proto == "https":
            morsel["secure"] = True
        return cookie.output(header="").strip()

    def dashboard_access_granted(self):
        if self.admin_authenticated():
            return True
        managed = managed_user_access_enabled()
        if not dashboard_access_configured() and not managed:
            # Opted-in source mode must remain closed when the user registry is
            # empty/unavailable. A loaded config also survives later env removal.
            if os.environ.get("PULSE_GALACTICA_STARTUP_FILE") or getattr(
                sys.modules[__name__], "GALACTICA_CONSENT_CONFIG", None
            ) is not None:
                return False
            return True
        if dashboard_access_configured() and verify_dashboard_access_header(self.headers.get("Authorization")):
            return True
        token = dashboard_access_session_from_cookie(self.headers.get("Cookie"))
        if managed and verify_managed_access_token(token):
            return True
        return verify_dashboard_access_session_token(token)

    def dashboard_access_identity(self):
        if self.admin_authenticated():
            return {"username": "admin", "is_admin": True, "clients": [], "reports": [], "admin_sections": list(ADMIN_SECTION_IDS)}
        token = dashboard_access_session_from_cookie(self.headers.get("Cookie"))
        identity = verify_managed_access_token(token) if managed_user_access_enabled() else None
        if identity:
            return identity
        if dashboard_access_configured() and (verify_dashboard_access_header(self.headers.get("Authorization")) or verify_dashboard_access_session_token(token)):
            return {"username": "dashboard", "is_admin": True, "clients": [], "reports": [], "admin_sections": list(ADMIN_SECTION_IDS)}
        return None

    def dashboard_access_default_path(self):
        return "/"

    def dashboard_access_return_path(self, value):
        from dashboard_access_navigation import safe_return_path
        return safe_return_path(value, self.dashboard_access_default_path())

    def dashboard_access_redirect(self, path):
        prefix = self.dashboard_access_prefix()
        location = f"{prefix}{path}" if prefix else path
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_dashboard_access_required(self, parsed):
        if str(parsed.path or "").startswith("/api/"):
            self.send_json(
                {
                    "ok": False,
                    "authenticated": False,
                    "error": "Требуется вход в аналитику",
                    "kind": "dashboard_access_required",
                },
                status=401,
            )
            return
        target = self.dashboard_access_return_path(parsed.path + ("?" + parsed.query if parsed.query else ""))
        self.dashboard_access_redirect("/login?" + urlencode({"next": target}))

    def handle_dashboard_access_login(self):
        from galactica_entitlement import SourceDenied, require_access_origin, register_session
        try:
            require_access_origin(self.headers)
        except (SourceDenied, ValueError):
            self.send_json({"ok": False, "reason_code": "SOURCE_ACCESS_DENIED"}, status=403)
            return
        managed = managed_user_access_enabled()
        if not dashboard_access_configured() and not managed:
            self.send_json(
                {"ok": False, "authenticated": False, "error": "Авторизация аналитики не настроена"},
                status=503,
            )
            return
        client_key = self.admin_request_key()
        direct_loopback = not self.headers.get("X-Forwarded-For") and client_key in {"127.0.0.1", "::1"}
        request_key = f"dashboard-access:{client_key}"
        retry_after = 0 if direct_loopback else admin_login_retry_after(request_key)
        if retry_after:
            self.send_json(
                {
                    "ok": False,
                    "authenticated": False,
                    "error": "Слишком много попыток. Попробуйте позже",
                },
                status=429,
                headers={"Retry-After": retry_after},
            )
            return
        payload = self.read_json_body()
        source_return = None
        if "source_request" in payload:
            from galactica_consent import resume_login_continuation
            try:
                source_return = resume_login_continuation(
                    GALACTICA_CONSENT_CONFIG, payload["source_request"]
                )
            except Exception:
                self.send_json({"ok": False, "error": "Запрос подключения истёк или недействителен. Начните подключение заново в SFERA."}, status=403)
                return
        username = str(payload.get("username") or "")[:256]
        password = str(payload.get("password") or "")[:1024]
        user = verify_managed_credentials(username, password) if managed else None
        if not source_return and not user and verify_dashboard_access_credentials(username, password):
            user = {"username": username, "is_admin": True}
        if not source_return and not user and verify_admin_credentials(username, password):
            user = {"username": username, "is_admin": True}
        if not user or (source_return and user.get("is_admin")):
            if not direct_loopback:
                record_admin_login_failure(request_key)
            self.send_json(
                {"ok": False, "authenticated": False, "error": "Неверный логин или пароль"},
                status=401,
            )
            return
        clear_admin_login_failures(request_key)
        token = create_managed_access_token(user) if managed else create_dashboard_access_session_token()
        if managed and not user.get("is_admin"):
            try:
                register_session(sys.modules[__name__], token)
            except Exception:
                self.send_json({"ok": False, "reason_code": "SOURCE_AUTHORITY_UNAVAILABLE"}, status=503)
                return
        self.send_json(
            {"ok": True, "authenticated": True, **({"source_return": source_return} if source_return else {"return_to": self.dashboard_access_prefix() + self.dashboard_access_return_path(payload.get("next"))})},
            headers={"Set-Cookie": self.dashboard_access_cookie_header(token)},
        )

    def handle_dashboard_access_logout(self):
        from galactica_entitlement import handle_logout
        handle_logout(sys.modules[__name__], self)

    def reject_locked_dashboard_feature(self, path):
        if str(path or "").startswith("/api/"):
            self.send_json({"ok": False, "error": "path not found"}, status=404)
        else:
            self.send_error(404, "page not found")

    def admin_request_key(self):
        forwarded_for = str(self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        if forwarded_for:
            return forwarded_for
        if isinstance(self.client_address, tuple) and self.client_address:
            return str(self.client_address[0])
        return "unknown"

    def admin_cookie_header(self, token="", clear=False):
        cookie = SimpleCookie()
        cookie[ADMIN_AUTH_COOKIE_NAME] = "" if clear else token
        morsel = cookie[ADMIN_AUTH_COOKIE_NAME]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        morsel["max-age"] = "0" if clear else str(ADMIN_AUTH_SESSION_TTL_SECONDS)
        if clear:
            morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        forwarded_proto = str(self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        if forwarded_proto == "https":
            morsel["secure"] = True
        return cookie.output(header="").strip()

    def admin_authenticated(self):
        token = admin_session_from_cookie(self.headers.get("Cookie"))
        return verify_admin_session_token(token)

    def send_admin_auth_required(self):
        self.send_json(
            {
                "ok": False,
                "authenticated": False,
                "error": "Требуется вход в админку",
                "kind": "admin_auth_required",
            },
            status=401,
        )

    def handle_admin_auth_status(self):
        self.send_json(admin_access_status_payload(self.admin_authenticated()))

    def handle_admin_auth_login(self):
        if not admin_auth_configured():
            self.send_json(
                {"ok": False, "authenticated": False, "error": "Авторизация админки не настроена"},
                status=503,
            )
            return
        request_key = self.admin_request_key()
        retry_after = admin_login_retry_after(request_key)
        if retry_after:
            self.send_json(
                {
                    "ok": False,
                    "authenticated": False,
                    "error": "Слишком много попыток. Попробуйте позже",
                },
                status=429,
                headers={"Retry-After": retry_after},
            )
            return
        payload = self.read_json_body()
        username = str(payload.get("username") or "")[:256]
        password = str(payload.get("password") or "")[:1024]
        if not verify_admin_credentials(username, password):
            record_admin_login_failure(request_key)
            self.send_json(
                {"ok": False, "authenticated": False, "error": "Неверный логин или пароль"},
                status=401,
            )
            return
        clear_admin_login_failures(request_key)
        self.send_json(
            {"ok": True, "authenticated": True},
            headers={"Set-Cookie": self.admin_cookie_header(create_admin_session_token())},
        )

    def handle_admin_auth_logout(self):
        self.send_json(
            {"ok": True, "authenticated": False},
            headers={"Set-Cookie": self.admin_cookie_header(clear=True)},
        )

    def stream_admin_run_import(self, parsed):
        params = parse_qs(parsed.query)
        client = admin_client_from_query(parsed.query)
        key = params.get("key", [""])[0]
        spec = admin_import_spec_by_key(key, client)
        if not spec:
            raise RuntimeError(f"Unknown import key for {client}: {key}")

        script_path = Path(spec["script"])
        if not script_path.exists():
            raise FileNotFoundError(f"Import script not found: {script_path}")

        acquired, run_state = acquire_admin_import_run(client, key, spec["report"])
        if not acquired:
            self.send_json(admin_import_running_error(client, key, run_state), status=409)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        output_parts = []
        command = admin_import_stream_command(script_path, spec, params)
        env = admin_import_env(spec, client)
        client_connected = self.send_ndjson_event(
            {
                "event": "start",
                "key": key,
                "client": client,
                "client_label": ADMIN_CLIENTS[client]["label"],
                "db_name": ADMIN_CLIENTS[client]["db_name"],
                "report": spec["report"],
                "script": str(script_path),
                "source": spec["source"],
                "destination": spec["destination"],
                "cwd": str(PROJECT_ROOT),
                "command": " ".join(command),
            }
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **admin_import_process_group_kwargs(),
            )
        except Exception:
            release_admin_import_run(client, key)
            raise
        set_admin_import_process(client, key, process)
        stdout_queue = queue.Queue()
        stdout_done = object()

        def enqueue_stdout():
            try:
                if process.stdout:
                    for output_event in admin_output_events_from_chunks(admin_process_output_chunks(process.stdout)):
                        stdout_queue.put(output_event)
            finally:
                stdout_queue.put(stdout_done)

        stdout_thread = threading.Thread(target=enqueue_stdout, daemon=True)
        stdout_thread.start()
        started_at = time.monotonic()
        try:
            while True:
                try:
                    output_event = stdout_queue.get(timeout=ADMIN_IMPORT_STREAM_HEARTBEAT_SECONDS)
                except queue.Empty:
                    if process.poll() is None and client_connected:
                        client_connected = self.send_ndjson_event(
                            {
                                "event": "heartbeat",
                                "key": key,
                                "elapsed_sec": int(time.monotonic() - started_at),
                            }
                        )
                    continue
                if output_event is stdout_done:
                    break
                output_parts.append(output_event["line"] + "\n")
                if client_connected:
                    client_connected = self.send_ndjson_event(
                        {
                            "event": output_event["kind"],
                            "key": key,
                            "line": output_event["line"],
                        }
                    )
        finally:
            returncode = process.wait()
            stdout_thread.join(timeout=1)
            stopped = bool(run_state.get("stop_requested"))
            release_admin_import_run(client, key)
        output = "".join(output_parts)
        if client_connected:
            self.send_ndjson_event(
                {
                    "event": "done",
                    "key": key,
                    "client": client,
                    "report": spec["report"],
                    "ok": returncode == 0,
                    "returncode": returncode,
                    "stopped": stopped,
                    "error": "" if returncode == 0 else ("Скрипт остановлен пользователем" if stopped else f"Скрипт завершился с кодом {returncode}"),
                    "summary": summarize_import_output(output),
                }
            )

    def stream_wb_promotion_fullstats(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        previous_sender = getattr(WB_API_STREAM_CONTEXT, "sender", None)
        WB_API_STREAM_CONTEXT.sender = self.send_ndjson_event
        client_connected = self.send_ndjson_event(
            {
                "event": "start",
                "method": "promotion_fullstats",
                "message": "WB Promotion Fullstats: потоковый запуск GET /adv/v3/fullstats",
            }
        )
        try:
            result = run_wb_api_export(
                "promotion_fullstats",
                handle_wb_promotion_fullstats_export,
                payload,
            )
            if client_connected:
                self.send_ndjson_event({"event": "result", "result": result})
        except Exception as exc:
            if client_connected:
                state = getattr(WB_API_RUN_CONTEXT, "state", None)
                error_payload = {"event": "error", **api_error_payload(exc)}
                if state:
                    error_payload["log_file"] = state.get("log_file", "")
                else:
                    error_payload["log_file"] = wb_api_last_log_file("promotion_fullstats")
                self.send_ndjson_event(error_payload)
        finally:
            if previous_sender is None:
                try:
                    delattr(WB_API_STREAM_CONTEXT, "sender")
                except AttributeError:
                    pass
            else:
                WB_API_STREAM_CONTEXT.sender = previous_sender

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/galactica/database":
            from trend_database import handle
            handle(sys.modules[__name__], self)
            return
        if parsed.path == "/api/galactica/authorize":
            from galactica_consent import handle_consent
            handle_consent(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/galactica/exchange":
            from galactica_exchange_http import handle_exchange
            handle_exchange(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/galactica/delivery/finish":
            from galactica_reports import handle_delivery_finish
            handle_delivery_finish(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/access/session/activity":
            from session_idle import handle
            handle(sys.modules[__name__], self, touch=True)
            return
        if parsed.path == "/api/access/login":
            try:
                self.handle_dashboard_access_login()
            except Exception as exc:
                self.send_json(api_error_payload(exc), status=500)
            return
        if parsed.path == "/api/access/logout":
            self.handle_dashboard_access_logout()
            return
        if not self.dashboard_access_granted():
            self.send_dashboard_access_required(parsed)
            return
        if locked_dashboard_feature_disabled(parsed.path):
            self.reject_locked_dashboard_feature(parsed.path)
            return
        access_token = CURRENT_ACCESS_USER.set(self.dashboard_access_identity())
        try:
            report_id = report_id_for_request(parsed)
            if report_id and parsed.path.startswith('/api/') and CURRENT_ACCESS_USER.get() and not __import__('data_access').report_permitted(CURRENT_ACCESS_USER.get(), current_client_key(), report_id):
                self.send_json({"ok": False, "error": "Нет доступа к данным этого отчёта"}, status=403)
                return

            access_user = CURRENT_ACCESS_USER.get()
            if report_id and access_user and not access_user.get("is_admin") and report_id not in set(access_user.get("reports") or []):
                self.send_json({"ok": False, "error": "Нет доступа к отчёту"}, status=403)
                return
            if parsed.path.startswith('/api/cluster-supply/'):
                from cluster_supply_api import handle as handle_cluster_supply
                handle_cluster_supply(sys.modules[__name__], self, parsed, 'POST')
                return
            if parsed.path == "/api/reviews-workbench":
                from review_workbench import dispatch, start_worker
                try:
                    origin = self.headers.get("Origin")
                    if origin and urlparse(origin).netloc != self.headers.get("Host"):
                        self.send_json({"ok": False, "error": "Недопустимый источник запроса"}, status=403)
                        return
                    payload = self.read_json_body()
                    requested = str(payload.get("client") or "").strip().lower()
                    if not review_client_allowed(requested):
                        self.send_json({"ok": False, "error": "Нет доступа к выбранному аккаунту"}, status=403)
                        return
                    config = read_db_config(requested)
                    actor = str((CURRENT_ACCESS_USER.get() or {}).get("username") or "local-owner")
                    result = dispatch(config, payload, actor)
                    start_worker(config, ADMIN_CLIENTS[requested].get("label", requested), lambda: review_wb_token(requested))
                    self.send_json(result)
                except (ValueError, TypeError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if parsed.path in {"/api/reviews-reply/generate", "/api/reviews-reply/save", "/api/reviews-reply/publish"}:
                from review_replies import generate, save, publish
                try:
                    origin = self.headers.get("Origin")
                    if origin and urlparse(origin).netloc != self.headers.get("Host"):
                        self.send_json({"ok": False, "error": "Недопустимый источник запроса"}, status=403)
                        return
                    payload = self.read_json_body()
                    requested_client = str(payload.get("client") or "").strip().lower()
                    client = normalize_client_key(requested_client)
                    if not review_client_allowed(requested_client):
                        self.send_json({"ok": False, "error": "Нет доступа к выбранному аккаунту"}, status=403)
                        return
                    config = read_db_config(client)
                    if parsed.path.endswith("/generate"):
                        result = generate(config, payload, ADMIN_CLIENTS[client].get("label", client))
                    elif parsed.path.endswith("/save"):
                        result = save(config, payload)
                    else:
                        token_env = wb_api_token_env(client)
                        token = os.environ.get(token_env) or read_app_env_file().get(token_env) or registered_client_credential(client, "wb_api_token")
                        result = publish(config, payload, token)
                    from review_workbench import ensure_schema, event
                    from contextlib import closing
                    from km_trade_finance import connect_km
                    ensure_schema(config)
                    with closing(connect_km(config)) as review_conn, review_conn, review_conn.cursor() as review_cur:
                        event(review_cur, parsed.path.rsplit('/', 1)[-1], str((CURRENT_ACCESS_USER.get() or {}).get('username') or 'local-owner'),
                              payload.get('review_key'), revision=(result.get('draft') or {}).get('revision'), status=(result.get('draft') or {}).get('status'))
                    self.send_json(result)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if parsed.path == "/api/seo-source/products":
                try:
                    self.send_json(handle_seo_source_products(self.read_json_body()))
                except (ValueError, RuntimeError, HTTPError, URLError) as exc:
                    self.send_json(api_error_payload(exc), status=400)
                return
            if parsed.path in {"/api/portfolio-config", "/glory/api/portfolio-config"}:
                try:
                    self.send_json(handle_portfolio_config(self.read_json_body()))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/auth/login":
                self.handle_admin_auth_login()
            elif parsed.path == "/api/admin/auth/logout":
                self.handle_admin_auth_logout()
            elif (
                parsed.path.startswith("/api/admin/")
                and parsed.path not in ADMIN_AUTH_PUBLIC_PATHS
                and not admin_request_permitted(parsed.path, "POST")
            ):
                if current_admin_section_ids():
                    self.send_json({"ok": False, "error": "Нет доступа к разделу админки"}, status=403)
                else:
                    self.send_admin_auth_required()
            elif parsed.path == "/api/weekly-dynamics-export":
                payload = self.read_json_body()
                query = str(payload.get("query") or parsed.query or "")
                client_token = CURRENT_CLIENT.set(client_from_query(query))
                try:
                    body, filename = handle_weekly_dynamics_export(parsed, payload)
                    self.send_file(body, filename)
                finally:
                    CURRENT_CLIENT.reset(client_token)
            elif parsed.path == "/api/chart-export":
                payload = self.read_json_body()
                query = str(payload.get("query") or parsed.query or "")
                client_token = CURRENT_CLIENT.set(client_from_query(query))
                try:
                    body, filename = handle_chart_export(parsed, payload)
                    self.send_file(body, filename)
                finally:
                    CURRENT_CLIENT.reset(client_token)
            elif parsed.path == "/api/report-export":
                payload = self.read_json_body()
                query = str(payload.get("query") or parsed.query or "")
                client_token = CURRENT_CLIENT.set(client_from_query(query))
                try:
                    body, filename = handle_report_export(parsed, payload)
                    self.send_file(body, filename)
                finally:
                    CURRENT_CLIENT.reset(client_token)
            elif parsed.path == "/api/planfact-plan":
                payload = self.read_json_body()
                client_token = CURRENT_CLIENT.set(normalize_client_key(payload.get("client")))
                try:
                    from sportmaster_run_rate import save_plan as save_sportmaster_run_rate_plan

                    self.send_json(save_sportmaster_run_rate_plan(payload, get_conn))
                finally:
                    CURRENT_CLIENT.reset(client_token)
            elif parsed.path == "/api/admin/clients":
                payload = self.read_json_body()
                try:
                    self.send_json(save_admin_client(payload))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/yandex-market/discover":
                payload = self.read_json_body()
                try:
                    self.send_json(discover_admin_yandex_market(payload))
                except (ValueError, RuntimeError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/connections":
                try:
                    self.send_json(save_admin_connection(self.read_json_body()))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/integrations":
                try:
                    self.send_json(save_admin_integration(self.read_json_body()))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-onboarding/start":
                from client_onboarding_runtime import start_provision

                payload = self.read_json_body()
                try:
                    self.send_json(start_provision(payload), status=202)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/users":
                payload = self.read_json_body()
                try:
                    self.send_json(save_admin_user(payload))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-onboarding/history":
                from client_onboarding_runtime import start_history

                payload = self.read_json_body()
                try:
                    self.send_json(start_history(payload), status=202)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-onboarding/history/stop":
                from client_onboarding_runtime import stop_history

                payload = self.read_json_body()
                try:
                    self.send_json(stop_history(payload), status=202)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-assortment/start":
                from client_onboarding_runtime import start_assortment

                payload = self.read_json_body()
                try:
                    self.send_json(start_assortment(payload), status=202)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-assortment/stop":
                from client_onboarding_runtime import stop_assortment

                payload = self.read_json_body()
                try:
                    self.send_json(stop_assortment(payload), status=202)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/wb-api/token":
                payload = self.read_json_body()
                self.send_json(save_wb_api_token(payload.get("token", ""), payload.get("client")))
            elif parsed.path == "/api/admin/wb-api/media/count":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("media_count", handle_wb_media_count_export, payload))
            elif parsed.path == "/api/admin/wb-api/media/adverts":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("media_adverts", handle_wb_media_adverts_export, payload))
            elif parsed.path == "/api/admin/wb-api/media/stats":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("media_stats", handle_wb_media_stats_export, payload))
            elif parsed.path == "/api/admin/wb-api/promotion/count":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("promotion_count", handle_wb_promotion_count_export, payload))
            elif parsed.path == "/api/admin/wb-api/promotion/adverts":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("promotion_adverts", handle_wb_promotion_adverts_export, payload))
            elif parsed.path == "/api/admin/wb-api/promotion/fullstats-stream":
                payload = self.read_json_body()
                self.stream_wb_promotion_fullstats(payload)
            elif parsed.path == "/api/admin/wb-api/promotion/fullstats":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("promotion_fullstats", handle_wb_promotion_fullstats_export, payload))
            elif parsed.path == "/api/admin/wb-api/content/categories":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("content_categories", handle_wb_content_categories_export, payload))
            elif parsed.path == "/api/admin/wb-api/content/cards":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("content_cards", handle_wb_content_cards_export, payload))
            elif parsed.path == "/api/admin/wb-api/content/characteristics":
                payload = self.read_json_body()
                self.send_json(run_wb_api_export("content_characteristics", handle_wb_content_characteristics_export, payload))
            elif parsed.path == "/api/admin/wb-api/analytics-report":
                payload = self.read_json_body()
                self.send_json(handle_wb_analytics_export(payload))
            elif parsed.path == "/api/admin/wb-api/analytics-excel/download":
                payload = self.read_json_body()
                body, filename = handle_wb_analytics_excel_download(payload)
                self.send_file(body, filename)
            elif parsed.path == "/api/admin/wb-api/analytics-csv/create":
                payload = self.read_json_body()
                self.send_json(handle_wb_analytics_csv_create(payload))
            elif parsed.path == "/api/admin/wb-api/analytics-csv/list":
                payload = self.read_json_body()
                self.send_json(handle_wb_analytics_csv_list(payload))
            elif parsed.path == "/api/admin/wb-api/analytics-csv/retry":
                payload = self.read_json_body()
                self.send_json(handle_wb_analytics_csv_retry(payload))
            elif parsed.path == "/api/admin/wb-api/analytics-csv/download":
                payload = self.read_json_body()
                body, filename, content_type = handle_wb_analytics_csv_download(payload)
                self.send_file(body, filename, content_type=content_type)
            elif parsed.path == "/api/admin/wb-api/stop":
                payload = self.read_json_body()
                self.send_json(handle_wb_api_stop(payload))
            elif parsed.path == "/api/admin/wb-api/open-file":
                payload = self.read_json_body()
                self.send_json(open_wb_api_result_file(payload.get("file", "")))
            elif parsed.path == "/api/admin/ozon-seo/credentials":
                payload = self.read_json_body()
                self.send_json(
                    save_ozon_seo_credentials(
                        payload.get("client", DEFAULT_CLIENT),
                        payload.get("client_id", ""),
                        payload.get("api_key", ""),
                    )
                )
            elif parsed.path == "/api/admin/ozon-seo/product-queries/details":
                payload = self.read_json_body()
                self.send_json(handle_ozon_seo_product_queries_details(payload))
            elif parsed.path == "/api/seo-projects":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                marketplace = str(payload.get("marketplace") or "").strip().lower()
                if marketplace not in client_marketplace_ids(client):
                    raise ValueError("Маркетплейс недоступен для выбранного аккаунта")
                try:
                    from ozon_category_dashboard.seo_projects import create_project
                except ModuleNotFoundError:
                    from seo_projects import create_project

                self.send_json(create_project(read_db_config(client), payload))
            elif parsed.path == "/api/seo-ai-settings":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import save_ai_settings
                except ModuleNotFoundError:
                    from seo_projects import save_ai_settings
                self.send_json(save_ai_settings(read_db_config(client), payload))
            elif parsed.path == "/api/seo-projects/refresh":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import refresh_project
                except ModuleNotFoundError:
                    from seo_projects import refresh_project

                def fetch_ozon_project_sku(request_payload):
                    return handle_ozon_seo_product_queries_details({"client": client, **request_payload})

                self.send_json(refresh_project(read_db_config(client), payload, fetch_ozon_project_sku))
            elif parsed.path == "/api/seo-projects/collect-mpstats":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import refresh_project_mpstats
                except ModuleNotFoundError:
                    from seo_projects import refresh_project_mpstats

                self.send_json(refresh_project_mpstats(read_db_config(client), payload))
            elif parsed.path == "/api/seo-project-customer-messages/collect":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import collect_project_customer_messages
                except ModuleNotFoundError:
                    from seo_projects import collect_project_customer_messages

                self.send_json(collect_project_customer_messages(
                    read_db_config(client), payload,
                    lambda skus, date_from, date_to: fetch_ozon_project_questions(
                        client, skus, date_from, date_to
                    ),
                ))
            elif parsed.path == "/api/seo-project-questions/collect":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import collect_project_questions
                except ModuleNotFoundError:
                    from seo_projects import collect_project_questions
                self.send_json(collect_project_questions(
                    read_db_config(client), payload,
                    lambda skus, date_from, date_to: fetch_ozon_project_questions(
                        client, skus, date_from, date_to
                    ),
                ))
            elif parsed.path == "/api/seo-project-customer-voice/analyze":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import analyze_project_customer_voice
                except ModuleNotFoundError:
                    from seo_projects import analyze_project_customer_voice
                self.send_json(analyze_project_customer_voice(
                    read_db_config(client), payload, analyze_seo_customer_voice_claims
                ))
            elif parsed.path == "/api/seo-project-semantic-context/prepare":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import prepare_project_semantic_context
                except ModuleNotFoundError:
                    from seo_projects import prepare_project_semantic_context
                self.send_json(prepare_project_semantic_context(read_db_config(client), payload))
            elif parsed.path == "/api/seo-project-content-allocation/generate":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import generate_project_content_allocation
                except ModuleNotFoundError:
                    from seo_projects import generate_project_content_allocation
                self.send_json(generate_project_content_allocation(
                    read_db_config(client), payload, analyze_seo_content_allocation
                ))
            elif parsed.path == "/api/seo-project-content-draft/generate":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import generate_project_content_draft
                except ModuleNotFoundError:
                    from seo_projects import generate_project_content_draft
                self.send_json(generate_project_content_draft(
                    read_db_config(client), payload, generate_seo_content_draft
                ))
            elif parsed.path == "/api/seo-project-content-review/generate":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import generate_project_content_review
                except ModuleNotFoundError:
                    from seo_projects import generate_project_content_review
                self.send_json(generate_project_content_review(
                    read_db_config(client), payload, review_seo_content_draft, revise_seo_content_draft
                ))
            elif parsed.path == "/api/seo-project-keywords/analyze":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import analyze_project_keywords
                except ModuleNotFoundError:
                    from seo_projects import analyze_project_keywords
                self.send_json(analyze_project_keywords(
                    read_db_config(client), payload, analyze_seo_keywords_with_openrouter
                ))
            elif parsed.path == "/api/seo-project-intents/generate":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import generate_project_intents
                except ModuleNotFoundError:
                    from seo_projects import generate_project_intents
                self.send_json(generate_project_intents(
                    read_db_config(client), payload, infer_seo_product_intents_with_openrouter
                ))
            elif parsed.path == "/api/seo-project-niche-competitors/collect":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import collect_project_niche_competitors
                except ModuleNotFoundError:
                    from seo_projects import collect_project_niche_competitors
                self.send_json(collect_project_niche_competitors(read_db_config(client), payload))
            elif parsed.path == "/api/seo-project-competitor-keywords/collect":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import collect_project_competitor_keywords
                except ModuleNotFoundError:
                    from seo_projects import collect_project_competitor_keywords
                self.send_json(collect_project_competitor_keywords(read_db_config(client), payload))
            elif parsed.path == "/api/seo-project-competitor-keywords/analyze-job/start":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import start_competitor_analysis_job
                except ModuleNotFoundError:
                    from seo_projects import start_competitor_analysis_job
                result = start_competitor_analysis_job(read_db_config(client), payload)
                if result["job"].get("status") in {"queued", "running"}:
                    ensure_seo_competitor_analysis_job_thread(client, result["job"]["job_id"])
                self.send_json(result, status=202)
            elif parsed.path == "/api/seo-project-competitor-keywords/analyze-job/stop":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import stop_competitor_analysis_job
                except ModuleNotFoundError:
                    from seo_projects import stop_competitor_analysis_job
                self.send_json(stop_competitor_analysis_job(read_db_config(client), payload), status=202)
            elif parsed.path == "/api/seo-project-full-run/start":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import start_full_seo_run_job
                except ModuleNotFoundError:
                    from seo_projects import start_full_seo_run_job
                result = start_full_seo_run_job(read_db_config(client), payload)
                if result.get("job") and result["job"].get("status") in {"queued", "running", "stopping"}:
                    ensure_seo_full_run_job_thread(client, result["job"]["job_id"])
                self.send_json(result, status=202)
            elif parsed.path == "/api/seo-project-full-run/stop":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import stop_full_seo_run_job
                except ModuleNotFoundError:
                    from seo_projects import stop_full_seo_run_job
                self.send_json(stop_full_seo_run_job(read_db_config(client), payload), status=202)
            elif parsed.path == "/api/seo-project-competitor-keywords/analyze":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import analyze_project_competitor_keywords
                except ModuleNotFoundError:
                    from seo_projects import analyze_project_competitor_keywords
                self.send_json(analyze_project_competitor_keywords(
                    read_db_config(client), payload, analyze_seo_competitor_keywords
                ))
            elif parsed.path == "/api/seo-projects/collect-days":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import collect_project_days
                except ModuleNotFoundError:
                    from seo_projects import collect_project_days

                def fetch_ozon_project_sku(request_payload):
                    return handle_ozon_seo_product_queries_details({"client": client, **request_payload})

                self.send_json(collect_project_days(read_db_config(client), payload, fetch_ozon_project_sku))
            elif parsed.path == "/api/seo-projects/rename":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import rename_project
                except ModuleNotFoundError:
                    from seo_projects import rename_project

                self.send_json(rename_project(read_db_config(client), payload))
            elif parsed.path == "/api/seo-projects/skus":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import update_project_skus
                except ModuleNotFoundError:
                    from seo_projects import update_project_skus

                self.send_json(update_project_skus(read_db_config(client), payload))
            elif parsed.path == "/api/seo-projects/delete":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import delete_project
                except ModuleNotFoundError:
                    from seo_projects import delete_project

                self.send_json(delete_project(read_db_config(client), payload))
            elif parsed.path == "/api/seo-project-content/export":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import export_project_content
                except ModuleNotFoundError:
                    from seo_projects import export_project_content

                body, filename, _ = export_project_content(read_db_config(client), payload)
                self.send_file(
                    body,
                    filename,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            elif parsed.path == "/api/seo-project-template/parse":
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client") or DEFAULT_CLIENT)
                try:
                    from ozon_category_dashboard.seo_projects import parse_project_template
                except ModuleNotFoundError:
                    from seo_projects import parse_project_template

                self.send_json(parse_project_template(read_db_config(client), payload))
            elif parsed.path == "/api/admin/ozon-performance/credentials":
                payload = self.read_json_body()
                self.send_json(
                    save_ozon_performance_credentials(
                        payload.get("client", DEFAULT_CLIENT),
                        payload.get("client_id", ""),
                        payload.get("client_secret", ""),
                    )
                )
            elif parsed.path == "/api/admin/client-paths/validate":
                payload = self.read_json_body()
                self.send_json(validate_client_paths(payload))
            elif parsed.path == "/api/admin/all-clients-daily/start":
                payload = self.read_json_body()
                self.send_json(handle_admin_all_clients_daily("start", payload.get("task_ids")), status=202)
            elif parsed.path == "/api/admin/all-clients-daily/resume":
                payload = self.read_json_body()
                self.send_json(handle_admin_all_clients_daily("resume", payload.get("task_ids")), status=202)
            elif parsed.path == "/api/admin/all-clients-daily/stop":
                self.read_json_body()
                self.send_json(handle_admin_all_clients_daily("stop"), status=202)
            elif parsed.path in {
                "/api/admin/all-clients-daily/client/start",
                "/api/admin/all-clients-daily/client/resume",
                "/api/admin/all-clients-daily/client/stop",
            }:
                payload = self.read_json_body()
                self.send_json(
                    handle_admin_all_clients_daily_client(
                        parsed.path.rsplit("/", 1)[-1],
                        payload.get("client", ""),
                        payload.get("task_ids"),
                    ),
                    status=202,
                )
            elif parsed.path == "/api/admin/all-clients-assortment/start":
                self.read_json_body()
                self.send_json(handle_admin_all_clients_assortment("start"), status=202)
            elif parsed.path == "/api/admin/all-clients-assortment/resume":
                self.read_json_body()
                self.send_json(handle_admin_all_clients_assortment("resume"), status=202)
            elif parsed.path == "/api/admin/all-clients-assortment/stop":
                self.read_json_body()
                self.send_json(handle_admin_all_clients_assortment("stop"), status=202)
            elif parsed.path == "/api/review-kpi-plan":
                payload = self.read_json_body()
                self.send_json(handle_review_kpi_plan_save(payload))
            elif parsed.path in ("/api/km-trade/unit-scenario-calculate", "/api/km-trade/unit-scenario-save", "/api/km-trade/unit-cogs-barcode", "/api/km-trade/unit-portfolio-save", "/api/km-trade/unit-yandex-tariffs", "/api/km-trade/unit-yandex-prices"):
                payload = self.read_json_body()
                from unit_economics_workspace import checked_config, save_scenario, import_cogs, save_portfolio
                from unit_economics_engine import calculate
                client = normalize_client_key(payload.get("client", ""))
                try:
                    config = checked_config(read_db_config(client), client)
                    if parsed.path.endswith("unit-yandex-prices"):
                        from unit_yandex_prices import prices
                        result = prices(config, client, payload, registered_client_credential)
                    elif parsed.path.endswith("unit-yandex-tariffs"):
                        from unit_yandex_tariffs import quote
                        result = quote(config, client, payload, registered_client_credential)
                    elif parsed.path.endswith("unit-portfolio-save"):
                        result = save_portfolio(config, client, payload)
                    elif parsed.path.endswith("-calculate"):
                        result = calculate(payload.get("inputs") or {})
                    elif parsed.path.endswith("-save"):
                        result = save_scenario(config, client, payload)
                    else:
                        result = import_cogs(config, client, payload)
                    self.send_json(result)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/unit-price-evaluate":
                from unit_price_api import evaluate
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", ""))
                try:
                    self.send_json(evaluate(read_db_config(client), client, payload))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/unit-planner-inputs":
                from unit_planner_settings import save
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", ""))
                try:
                    self.send_json(save(read_db_config(client), client, payload))
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/unit-settings":
                payload = self.read_json_body()
                from km_trade_finance import save_unit_settings
                client = normalize_client_key(payload.get("client", "km_trade"))
                self.send_json(save_unit_settings(read_db_config(client), payload))
            elif parsed.path == "/api/km-trade/unit-cogs-import":
                payload = self.read_json_body()
                try:
                    filename = str(payload.get("filename") or "")
                    if not filename.lower().endswith(".xlsx"):
                        raise ValueError("Выберите файл XLSX")
                    encoded = str(payload.get("file_base64") or "")
                    workbook_bytes = base64.b64decode(encoded, validate=True)
                    from km_trade_finance import import_cogs_workbook
                    client = normalize_client_key(payload.get("client", "km_trade"))
                    self.send_json(import_cogs_workbook(read_db_config(client), workbook_bytes))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/sales-promotion":
                payload = self.read_json_body()
                try:
                    from km_trade_sales_planning import save_promotion_coefficients
                    client = normalize_client_key(payload.get("client") or current_client_key())
                    self.send_json(
                        save_promotion_coefficients(read_db_config(client), payload)
                    )
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/sales-activity":
                from sales_activity import save
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", current_client_key()))
                if payload.get("client") and client != payload["client"]:
                    self.send_json({"ok": False, "error": "Клиент недоступен"}, status=403)
                    return
                try:
                    self.send_json(save(read_db_config(client), payload))
                except (ValueError, TypeError, KeyError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/sales-versions":
                from pulse_sales_model import save
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", current_client_key()))
                if payload.get("client") and client != payload["client"]:
                    self.send_json({"ok": False, "error": "Клиент недоступен"}, status=403)
                    return
                try:
                    self.send_json(save(read_db_config(client), payload))
                except (ValueError, TypeError, KeyError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/sales-plan":
                payload = self.read_json_body()
                from km_trade_planfact import save_sales_plan
                client = normalize_client_key(payload.get("client") or current_client_key())
                self.send_json(save_sales_plan(read_db_config(client), payload))
            elif parsed.path == "/api/km-trade/media-plan":
                payload = self.read_json_body()
                try:
                    from km_trade_media_plan import save_media_plan_settings
                    client = normalize_client_key(payload.get("client") or current_client_key())
                    self.send_json(save_media_plan_settings(read_db_config(client), payload, client))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/inventory-history-planning":
                from pulse_supply import save
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", current_client_key()))
                if payload.get("client") and client != payload["client"]:
                    self.send_json({"ok": False, "error": "Клиент недоступен"}, status=403)
                    return
                try:
                    self.send_json(save(read_db_config(client), payload))
                except (ValueError, TypeError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/pl-monthly-budget":
                from pl_monthly_budgets import save
                payload = self.read_json_body()
                client = normalize_client_key(payload.get("client", ""))
                if not payload.get("client") or client != payload["client"]:
                    self.send_json({"ok": False, "error": "Клиент недоступен"}, status=403)
                    return
                try:
                    self.send_json(save(read_db_config(client), client, payload))
                except (ValueError, TypeError) as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/pl-expenses":
                payload = self.read_json_body()
                from km_trade_finance import save_pl_expenses
                client = normalize_client_key(payload.get("client", "km_trade"))
                self.send_json(save_pl_expenses(read_db_config(client), payload))
            elif parsed.path == "/api/admin/stop-import":
                payload = self.read_json_body()
                self.send_json(handle_admin_stop_import(payload))
            elif parsed.path == "/api/boiron-adv-analysis/generate":
                payload = self.read_json_body()
                self.send_json(handle_boiron_adv_analysis_generate(payload))
            elif parsed.path == "/api/sku-content-scoring/run":
                payload = self.read_json_body()
                self.send_json(handle_sku_content_scoring(payload))
            else:
                self.send_json({"ok": False, "error": "path not found"}, status=404)
        except Exception as exc:
            self.send_json(api_error_payload(exc), status=500)
        finally:
            CURRENT_ACCESS_USER.reset(access_token)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/access/session/activity.js":
            from session_idle import script
            script(self)
            return
        if parsed.path == "/api/access/session/status":
            from session_idle import handle
            handle(sys.modules[__name__], self)
            return
        if parsed.path == "/api/galactica/authorize":
            from galactica_consent import handle_consent
            handle_consent(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/galactica/identity":
            from galactica_identity import handle_identity
            handle_identity(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/galactica/entitlement":
            from galactica_entitlement import handle_entitlement
            handle_entitlement(sys.modules[__name__], self, parsed)
            return
        if parsed.path == "/api/galactica/report":
            from galactica_reports import handle_report
            handle_report(sys.modules[__name__], self, parsed)
            return
        if parsed.path in {"/login", "/login/"}:
            if parsed.query != "source=1" and self.dashboard_access_granted():
                self.dashboard_access_redirect(self.dashboard_access_return_path(parse_qs(parsed.query).get("next", [None])[0]))
            else:
                self.send_static_file("access-login.html", cache_control="no-store")
            return
        if not self.dashboard_access_granted():
            self.send_dashboard_access_required(parsed)
            return
        if locked_dashboard_feature_disabled(parsed.path):
            self.reject_locked_dashboard_feature(parsed.path)
            return
        access_token = CURRENT_ACCESS_USER.set(self.dashboard_access_identity())
        client_token = CURRENT_CLIENT.set(client_from_query(parsed.query))
        try:
            report_id = report_id_for_request(parsed)
            if report_id and parsed.path.startswith('/api/') and CURRENT_ACCESS_USER.get() and not __import__('data_access').report_permitted(CURRENT_ACCESS_USER.get(), current_client_key(), report_id):
                self.send_json({"ok": False, "error": "Нет доступа к данным этого отчёта"}, status=403)
                return

            access_user = CURRENT_ACCESS_USER.get()
            if report_id and access_user and not access_user.get("is_admin") and report_id not in set(access_user.get("reports") or []):
                self.send_json({"ok": False, "error": "Нет доступа к отчёту"}, status=403)
                return
            from assortment_api import handle as handle_assortment
            if handle_assortment(sys.modules[__name__], self, parsed):
                return
            from trend_retail import handle as handle_retail
            if handle_retail(sys.modules[__name__], self, parsed):
                return
            if parsed.path == "/api/autobidder/overview":
                from autobidder.routes import get_payload
                status, result = get_payload(sys.modules[__name__], parsed)
                self.send_json(result, status=status)
                return
            if parsed.path.startswith('/api/cluster-supply/'):
                from cluster_supply_api import handle as handle_cluster_supply
                handle_cluster_supply(sys.modules[__name__], self, parsed, 'GET')
                return
            if is_shared_portfolio_request(parsed):
                body = render_portfolio_page().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/admin/auth/status":
                self.handle_admin_auth_status()
            elif (
                parsed.path.startswith("/api/admin/")
                and parsed.path not in ADMIN_AUTH_PUBLIC_PATHS
                and not admin_request_permitted(parsed.path, "GET")
            ):
                if current_admin_section_ids():
                    self.send_json({"ok": False, "error": "Нет доступа к разделу админки"}, status=403)
                else:
                    self.send_admin_auth_required()
            elif parsed.path in {"/api/portfolio-overview", "/glory/api/portfolio-overview"}:
                self.send_json(handle_portfolio_overview(parsed))
            elif parsed.path == "/api/admin/yandex-market/analytics":
                import yandex_analytics
                try:
                    self.send_json(yandex_analytics.handle(sys.modules[__name__], parsed))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/yandex-market/analytics":
                import yandex_analytics
                try:
                    self.send_json(cached_read_only_report(
                        "yandex-analytics", parsed,
                        lambda: yandex_analytics.handle(sys.modules[__name__], parsed),
                    ))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path in {
                "/api/planfact-funnel-matrix", "/glory/api/planfact-funnel-matrix",
                "/api/health-check-hypothesis-analysis", "/glory/api/health-check-hypothesis-analysis",
            }:
                matrix_runtime = None
                try:
                    matrix_runtime = load_planfact_funnel_matrix_runtime()
                    handler = (matrix_runtime.handle_hypothesis_analysis
                               if parsed.path.endswith("/health-check-hypothesis-analysis")
                               else matrix_runtime.handle)
                    def build_matrix_payload():
                        payload = handler(sys.modules[__name__], parsed, project_root=PLANFACT_MATRIX_PROJECT_ROOT)
                        if parsed.path.endswith('/planfact-funnel-matrix'):
                            from health_supporting_market import attach_market_history
                            payload = attach_market_history(payload, read_db_config(current_client_key()))
                        return payload
                    matrix_payload = (
                        cached_read_only_report("planfact-matrix", parsed, build_matrix_payload)
                        if parsed.path.endswith('/planfact-funnel-matrix')
                        else build_matrix_payload()
                    )
                    self.send_json(matrix_payload)
                except Exception as exc:
                    if matrix_runtime is not None and isinstance(exc, matrix_runtime.PlanFactMatrixRequestError):
                        self.send_json({"ok": False, "error": "invalid_request", "message": str(exc)}, status=400)
                        return
                    print("health-check request failed:", type(exc).__name__, flush=True)
                    self.send_json(
                        {
                            "ok": False,
                            "error": "health_check_failed",
                            "message": "Не удалось сформировать Health Check. Повторите запрос после проверки источников.",
                        },
                        status=500,
                    )
            elif parsed.path == "/api/health":
                marketplace = marketplace_from_query(parsed.query)
                client = current_client_key()
                self.send_json(
                    {
                        "ok": True,
                        "client": client,
                        "client_label": ADMIN_CLIENTS[client]["label"],
                        "clients": dashboard_clients_payload(),
                        "client_locked": dashboard_client_locked(),
                        "locked_client": dashboard_locked_client(),
                        "marketplace": marketplace,
                        "view": f"public.{MARKETPLACES[marketplace]['view']}",
                        "marketplaces": client_marketplaces_payload(client),
                    }
                )
            elif parsed.path == "/api/database-status":
                permitted = {item["key"] for item in dashboard_clients_payload()}
                databases = []
                for key, label in (("toptop", "TOPTOP"), ("lera_nena", "LERA NENA")):
                    if key not in permitted:
                        continue
                    connected = False
                    conn = None
                    try:
                        config = read_db_config(key)
                        config["connect_timeout"] = 2
                        conn = psycopg2.connect(**config)
                        with conn.cursor() as cursor:
                            cursor.execute("SELECT 1")
                            connected = cursor.fetchone()[0] == 1
                    except Exception:
                        connected = False
                    finally:
                        if conn is not None:
                            conn.close()
                    databases.append({"client": key, "label": label, "connected": connected})
                self.send_json({
                    "ok": True,
                    "checked_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                    "databases": databases,
                })
            elif parsed.path == "/api/service-status":
                if "toptop" not in {item["key"] for item in dashboard_clients_payload()}:
                    self.send_json({"ok": False, "error": "Нет доступа"}, status=403)
                else:
                    self.send_json(service_connection_status_payload())
            elif parsed.path == "/api/review-dashboard":
                self.send_json(handle_review_dashboard(parsed))
            elif parsed.path in {"/api/reviews-workbench", "/api/reviews-insights"}:
                from review_workbench import queue_payload, insights, start_worker
                params = parse_qs(parsed.query)
                requested = (params.get('client') or [''])[0].strip().lower()
                if not review_client_allowed(requested):
                    self.send_json({"ok": False, "error": "Нет доступа к выбранному аккаунту"}, status=403)
                    return
                try:
                    config = read_db_config(requested)
                    if parsed.path.endswith('insights'):
                        result = insights(config, parsed)
                    else:
                        start_worker(config, ADMIN_CLIENTS[requested].get('label', requested), lambda: review_wb_token(requested))
                        result = queue_payload(config, parsed)
                    self.send_json(result)
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/reviews-dashboard":
                from marketplace_reviews_dashboard import dashboard_payload

                self.send_json(dashboard_payload(parsed, get_conn, current_client_key()))
            elif parsed.path == "/api/reviews-reply":
                from review_replies import load
                params = parse_qs(parsed.query)
                requested_client = (params.get("client") or [""])[0].strip().lower()
                if not review_client_allowed(requested_client):
                    self.send_json({"ok": False, "error": "Нет доступа к выбранному аккаунту"}, status=403)
                    return
                try:
                    self.send_json(load(read_db_config(requested_client), (params.get("review_key") or [""])[0]))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/reviews-export":
                from marketplace_reviews_dashboard import export_workbook

                body, filename = export_workbook(parsed, get_conn, current_client_key())
                self.send_file(body, filename)
            elif parsed.path == "/api/review-proposal/download":
                if not GLORIA_JEANS_PROPOSAL_PATH.exists():
                    self.send_json({"ok": False, "error": "proposal file not found"}, status=404)
                else:
                    self.send_file(GLORIA_JEANS_PROPOSAL_PATH.read_bytes(), GLORIA_JEANS_PROPOSAL_PATH.name)
            elif parsed.path == "/api/review-proposal/open-folder":
                payload = handle_review_proposal_open_folder()
                self.send_json(payload, status=200 if payload.get("ok") else 404)
            elif parsed.path == "/api/review-post-meeting-reports":
                self.send_json(handle_review_post_meeting_reports(parsed))
            elif parsed.path == "/api/review-post-meeting-reports/open-folder":
                payload = handle_review_post_meeting_reports_open_folder()
                self.send_json(payload, status=200 if payload.get("ok") else 404)
            elif parsed.path == "/api/review-post-meeting-reports/open":
                payload = handle_review_post_meeting_report_open(parsed)
                self.send_json(payload, status=200 if payload.get("ok") else 404)
            elif parsed.path == "/api/review-kpi-plan":
                self.send_json(handle_review_kpi_plan(parsed))
            elif parsed.path == "/api/filters":
                self.send_json(handle_filters(parsed))
            elif parsed.path == "/api/avito-ads-dashboard":
                from avito_ads_dashboard import dashboard_payload

                client = current_client_key()
                if "avito" not in set(ADMIN_CLIENTS[client].get("marketplaces") or []):
                    self.send_json({"ok": False, "error": "Avito Ads не подключён для выбранного клиента"}, status=409)
                else:
                    self.send_json(dashboard_payload(parsed, read_db_config(client)))
            elif parsed.path == "/api/lamoda/dashboard":
                from lamoda_dashboard import dashboard_payload

                client = current_client_key()
                if "lamoda" not in set(ADMIN_CLIENTS[client].get("marketplaces") or []):
                    self.send_json({"ok": False, "error": "Lamoda не подключена для выбранного клиента"}, status=409)
                else:
                    self.send_json(dashboard_payload(parsed, read_db_config(client)))
            elif parsed.path == "/api/km-trade/sales-plan":
                from km_trade_planfact import sales_plan_payload
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(
                    sales_plan_payload(
                        read_db_config(client),
                        (params.get("date_from") or [None])[0],
                        (params.get("date_to") or [None])[0],
                        client,
                    )
                )
            elif parsed.path == "/api/km-trade/sales-forecast":
                from km_trade_sales_planning import sales_forecast_payload
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or ["ozon"])[0].strip().lower()
                supported_marketplaces = client_marketplace_ids(client)
                if marketplace not in supported_marketplaces:
                    self.send_json(
                        {
                            "ok": False,
                            "available": False,
                            "data_status": "unavailable",
                            "reason_code": "marketplace_not_supported",
                            "client": client,
                            "marketplace": marketplace,
                            "marketplaces": client_marketplaces_payload(client),
                            "error": "Площадка не подключена для выбранного клиента.",
                        },
                        status=409,
                    )
                    return
                params["client"] = [client]
                self.send_json(
                    sales_forecast_payload(read_db_config(client), urlencode(params, doseq=True))
                )
            elif parsed.path == "/api/km-trade/media-plan":
                from km_trade_media_plan import media_plan_payload
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or ["ozon"])[0].strip().lower()
                self.send_json(cached_read_only_report(
                    "media-plan", parsed,
                    lambda: media_plan_payload(read_db_config(client), client, marketplace),
                ))
            elif parsed.path == "/api/km-trade/unit-planner-inputs":
                from unit_planner_settings import load
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [""])[0])
                self.send_json(load(read_db_config(client), client))
            elif parsed.path == "/api/km-trade/unit-ozon-cluster-mix":
                from unit_ozon_cluster_mix import load
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(load(read_db_config(client), client,
                    (params.get('date_from') or [''])[0], (params.get('date_to') or [''])[0], registered_client_credential))
            elif parsed.path == "/api/km-trade/unit-price-conditions":
                from unit_price_conditions import load
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(load(read_db_config(client), client, registered_client_credential,
                    refresh=(params.get('refresh') or [''])[0] == '1'))
            elif parsed.path in ("/api/km-trade/pl-expense-analysis", "/api/km-trade/pl-expense-operations"):
                from pl_expense_analysis import ledger, summarize, operation_page, operation_csv
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                market = (params.get("marketplace") or ["wb"])[0]
                start = (params.get("date_from") or [""])[0]
                end = (params.get("date_to") or [""])[0]
                try:
                    source = ledger(read_db_config(client), client, market, start, end)
                    if parsed.path.endswith("operations"):
                        key = (params.get("article") or [""])[0]
                        query = (params.get("q") or [""])[0]
                        if (params.get("format") or [""])[0] == "csv":
                            self.send_file(operation_csv(source, key, query), "pulse-expense-operations.csv", content_type="text/csv; charset=utf-8")
                        else:
                            self.send_json(operation_page(source,key,query,(params.get("page") or ["1"])[0]))
                    else:
                        result = {"current": summarize(source), "previous": None}
                        if (params.get("compare") or [""])[0] == "1":
                            import datetime as expense_datetime
                            first,last = map(expense_datetime.date.fromisoformat,(start,end))
                            length=(last-first).days+1
                            result["previous"]=summarize(ledger(read_db_config(client),client,market,str(first-expense_datetime.timedelta(days=length)),str(first-expense_datetime.timedelta(days=1))))
                        self.send_json(result)
                except ValueError:
                    self.send_json({"ok": False, "error": "Проверьте период и фильтры анализа расходов"}, status=400)
            elif parsed.path == "/api/km-trade/pl-monthly-budget":
                from pl_monthly_budgets import load
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                try:
                    self.send_json(load(read_db_config(client), client, (params.get("month") or [""])[0]))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/km-trade/pl-cost-registry":
                from pl_cost_registry import load_registry
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                try:
                    self.send_json(load_registry(read_db_config(client), client))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path in ("/api/km-trade/unit-workspace", "/api/km-trade/unit-catalog"):
                from unit_economics_workspace import workspace
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                unit_payload = cached_read_only_report(
                    "unit-catalog" if parsed.path.endswith('unit-catalog') else "unit-workspace",
                    parsed,
                    lambda: workspace(read_db_config(client), client,
                        (params.get("date_from") or [None])[0] or None,
                        (params.get("date_to") or [None])[0] or None,
                        catalog_mode=parsed.path.endswith('unit-catalog'),
                        query=(params.get('q') or [''])[0],marketplace=(params.get('marketplace') or [''])[0],
                        cabinet=(params.get('cabinet') or [''])[0]),
                    ignore_params=("compact",),
                )
                if (params.get("compact") or [""])[0] == "1" and not parsed.path.endswith('unit-catalog'):
                    unit_payload = dict(unit_payload)
                    unit_payload.pop("rows", None)
                self.send_json(unit_payload)
            elif parsed.path == "/api/km-trade/unit-economics":
                from km_trade_finance import unit_payload
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(
                    unit_payload(
                        read_db_config(client),
                        params.get("date_from", [""])[0] or None,
                        params.get("date_to", [""])[0] or None,
                        client,
                        (params.get("marketplace") or ["ozon"])[0],
                        page=max(1, int((params.get("page") or ["1"])[0])),
                        page_size=max(
                            25,
                            min(300, int((params.get("limit") or ["100"])[0])),
                        ),
                    )
                )
            elif parsed.path == "/api/km-trade/pl-operations":
                from pl_workbench import operations
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                if "wb" not in client_marketplace_ids(client):
                    self.send_json({"error": "WB не подключён"}, status=400)
                    return
                self.send_json(operations(read_db_config(client),
                    (params.get("date_from") or [""])[0], (params.get("date_to") or [""])[0],
                    (params.get("article") or ["deduction"])[0], (params.get("q") or [""])[0],
                    (params.get("sku") or [""])[0], (params.get("page") or ["1"])[0]))
            elif parsed.path == "/api/km-trade/pl":
                from marketplace_pl import selected_payload, connected_marketplaces, LABELS
                from km_trade_planning_pl import planned_pl_payload
                params = parse_qs(parsed.query)
                date_from = params.get("date_from", [""])[0] or None
                date_to = params.get("date_to", [""])[0] or None
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or [""])[0].strip().lower()
                supported_marketplaces = client_marketplace_ids(client)
                if not marketplace:
                    marketplace = supported_marketplaces[0]
                config = read_db_config(client)
                supported_marketplaces = connected_marketplaces(config, client, supported_marketplaces)
                def build_pl_payload():
                    home_summary = (
                        bool(date_from and date_to)
                        and (params.get("workbench") or [""])[0] != "1"
                        and (params.get("financial_model") or [""])[0] != "1"
                        and (params.get("include_plan") or [""])[0].strip().lower()
                            not in {"1", "true", "yes", "on"}
                    )
                    payload = None
                    if home_summary:
                        try:
                            from home_marts import home_pl_payload
                            payload = home_pl_payload(config, date_from, date_to, client, marketplace)
                        except Exception as exc:
                            print(f"home P&L mart fallback: {exc}", flush=True)
                    if payload is None:
                        payload = selected_payload(config, date_from, date_to, client, marketplace, supported_marketplaces)
                    payload["marketplaces"] = [{"id": key, "label": LABELS[key]} for key in supported_marketplaces if key in LABELS]
                    if (params.get("workbench") or [""])[0] == "1":
                        from pl_workbench import attach_workbench
                        payload = attach_workbench(config, payload, (params.get("cost_mode") or ["dated"])[0])
                    if (params.get("financial_model") or [""])[0] == "1":
                        from pl_financial_model import attach_financial_model
                        payload = attach_financial_model(config, payload, None)
                    include_plan = (params.get("include_plan") or [""])[0].strip().lower() in {
                        "1", "true", "yes", "on",
                    }
                    if marketplace == "ozon" and include_plan:
                        payload["plan"] = planned_pl_payload(config, date_from, date_to, client)
                    return payload
                self.send_json(cached_read_only_report("profit-loss-r87", parsed, build_pl_payload))
            elif parsed.path == "/api/km-trade/pl-export":
                from marketplace_pl import selected_payload, workbook_bytes, connected_marketplaces
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or [""])[0].strip().lower()
                supported_marketplaces = client_marketplace_ids(client)
                if not marketplace:
                    marketplace = supported_marketplaces[0]
                config = read_db_config(client)
                supported_marketplaces = connected_marketplaces(config, client, supported_marketplaces)
                data = selected_payload(
                    config,
                    params.get("date_from", [""])[0] or None,
                    params.get("date_to", [""])[0] or None,
                    client,
                    marketplace,
                    supported_marketplaces,
                )
                if data.get("available") is False:
                    self.send_json(
                        {
                            "ok": False,
                            "error": data.get("message") or "P&L для выбранной площадки пока не сформирован.",
                        },
                        status=409,
                    )
                    return
                if (params.get("workbench") or [""])[0] == "1":
                    from pl_workbench import attach_workbench
                    data = attach_workbench(config, data, (params.get("cost_mode") or ["dated"])[0])
                if (params.get("financial_model") or [""])[0] == "1":
                    from pl_financial_model import attach_financial_model
                    data = attach_financial_model(config, data, None)
                filename = f"{client}_{marketplace}_pl_{data['date_from']}_{data['date_to']}.xlsx"
                if data.get("workbench") or data.get("financial_model"):
                    from pl_workbench import workbench_workbook
                    self.send_file(workbench_workbook(data), filename)
                else:
                    self.send_file(workbook_bytes(data), filename)
            elif parsed.path == "/api/summary":
                self.send_json(handle_summary(parsed))
            elif parsed.path == "/api/stats":
                self.send_json(handle_stats(parsed))
            elif parsed.path == "/api/sku-summary":
                self.send_json(handle_sku_summary(parsed))
            elif parsed.path == "/api/sku-stats":
                self.send_json(handle_sku_stats(parsed))
            elif parsed.path == "/api/sku-card":
                self.send_json(handle_sku_card(parsed))
            elif parsed.path == "/api/sku-content-scoring/providers":
                self.send_json(handle_sku_content_scoring_providers())
            elif parsed.path == "/api/sku-content-scoring/saved":
                self.send_json(handle_saved_sku_content_scoring(parsed))
            elif parsed.path == "/api/product-summary":
                self.send_json(handle_product_summary(parsed))
            elif parsed.path == "/api/product-stats":
                self.send_json(handle_product_stats(parsed))
            elif parsed.path == "/api/adv-summary":
                self.send_json(handle_adv_summary(parsed))
            elif parsed.path == "/api/adv-daily":
                self.send_json(handle_adv_daily(parsed))
            elif parsed.path == "/api/adv-waterfalls":
                self.send_json(handle_adv_waterfalls(parsed))
            elif parsed.path == "/api/adv-stats":
                self.send_json(handle_adv_stats(parsed))
            elif parsed.path == "/api/adv-campaigns":
                self.send_json(handle_adv_campaigns(parsed))
            elif parsed.path == "/api/boiron-adv-planfact":
                self.send_json(handle_boiron_adv_planfact(parsed))
            elif parsed.path == "/api/boiron-adv-analysis":
                self.send_json(handle_boiron_adv_analysis(parsed))
            elif parsed.path == "/api/media-adv-summary":
                self.send_json(handle_media_adv_summary(parsed))
            elif parsed.path == "/api/media-adv-daily":
                self.send_json(handle_media_adv_daily(parsed))
            elif parsed.path == "/api/media-adv-waterfalls":
                self.send_json(handle_media_adv_waterfalls(parsed))
            elif parsed.path == "/api/media-adv-stats":
                self.send_json(handle_media_adv_stats(parsed))
            elif parsed.path == "/api/funnel-summary":
                self.send_json(handle_funnel_summary(parsed))
            elif parsed.path == "/api/funnel-daily":
                self.send_json(handle_funnel_daily(parsed))
            elif parsed.path in {"/api/order-feed", "/api/weekly-sku-inventory"}:
                from order_feed import order_feed, inventory_evidence
                params = parse_qs(parsed.query)
                requested = (params.get('client') or [''])[0]
                if requested != current_client_key() or 'wb' not in client_marketplace_ids():
                    self.send_json({'error': 'Нет доступа к выбранному аккаунту WB'}, status=403)
                    return
                try:
                    result = (order_feed if parsed.path == '/api/order-feed' else inventory_evidence)(parsed, get_conn)
                    self.send_json(normalize_rows([result])[0])
                except ValueError as exc:
                    self.send_json({'error': str(exc)}, status=400)
            elif parsed.path == "/api/weekly-sku-advertising":
                from weekly_sku_advertising import load
                params = parse_qs(parsed.query)
                requested = (params.get('client') or [''])[0]
                if requested != current_client_key() or 'wb' not in client_marketplace_ids():
                    self.send_json({'error': 'Нет доступа к выбранному аккаунту WB'}, status=403)
                    return
                try:
                    self.send_json(load(parsed, get_conn))
                except ValueError as exc:
                    self.send_json({'error': str(exc)}, status=400)
            elif parsed.path == "/api/sales-order-days":
                from sales_orders import load, MARKETS as SALES_ORDER_MARKETS
                params = parse_qs(parsed.query)
                requested = (params.get('client') or [''])[0]
                market = (params.get('marketplace') or ['wb'])[0]
                configured_markets = ADMIN_CLIENTS[current_client_key()].get('marketplaces') or []
                if requested != current_client_key() or market not in SALES_ORDER_MARKETS or market not in configured_markets:
                    self.send_json({'error': 'Нет доступа к выбранному аккаунту и площадке'}, status=403)
                    return
                try:
                    self.send_json(load(parsed, get_conn, current_client_key()))
                except ValueError as exc:
                    self.send_json({'error': str(exc)}, status=400)
            elif parsed.path == "/api/inventory-history":
                self.send_json(handle_inventory_history(parsed))
            elif parsed.path == "/api/inventory-history-summary":
                self.send_json(handle_inventory_history_summary(parsed))
            elif parsed.path == "/api/inventory-history-products":
                self.send_json(cached_read_only_report(
                    "inventory-products", parsed, lambda: handle_inventory_history_products(parsed)
                ))
            elif parsed.path == "/api/weekly-dynamics":
                self.send_json(cached_read_only_report(
                    "weekly-dynamics", parsed, lambda: handle_weekly_dynamics(parsed)
                ))
            elif parsed.path == "/api/funnel-waterfalls":
                self.send_json(handle_funnel_waterfalls(parsed))
            elif parsed.path == "/api/funnel-stats":
                self.send_json(handle_funnel_stats(parsed))
            elif parsed.path == "/api/funnel-products":
                self.send_json(handle_funnel_products(parsed))
            elif parsed.path == "/api/seo-monitoring-products":
                self.send_json(handle_seo_monitoring_products(parsed))
            elif parsed.path == "/api/seo-projects":
                try:
                    from ozon_category_dashboard.seo_projects import list_projects
                except ModuleNotFoundError:
                    from seo_projects import list_projects

                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or [""])[0].strip().lower()
                project_kind = (params.get("project_kind") or ["monitoring"])[0]
                self.send_json(list_projects(read_db_config(client), marketplace or None, project_kind))
            elif parsed.path == "/api/seo-ai-settings":
                try:
                    from ozon_category_dashboard.seo_projects import get_ai_settings
                except ModuleNotFoundError:
                    from seo_projects import get_ai_settings
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                settings = get_ai_settings(read_db_config(client))
                settings["providers"] = seo_ai_provider_catalog()
                self.send_json(settings)
            elif parsed.path == "/api/seo-project-keyword-stats":
                try:
                    from ozon_category_dashboard.seo_projects import project_keyword_stats
                except ModuleNotFoundError:
                    from seo_projects import project_keyword_stats

                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_keyword_stats(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "date_from": (params.get("date_from") or [""])[0],
                    "date_to": (params.get("date_to") or [""])[0],
                    "query": (params.get("query") or [""])[0],
                    "sku": (params.get("sku") or [""])[0],
                    "segment": (params.get("segment") or [""])[0],
                    "frequency": (params.get("frequency") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-keywords":
                try:
                    from ozon_category_dashboard.seo_projects import project_keywords
                except ModuleNotFoundError:
                    from seo_projects import project_keywords

                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_keywords(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "date_from": (params.get("date_from") or [""])[0],
                    "date_to": (params.get("date_to") or [""])[0],
                    "query": (params.get("query") or [""])[0],
                    "sku": (params.get("sku") or [""])[0],
                    "segment": (params.get("segment") or [""])[0],
                    "frequency": (params.get("frequency") or [""])[0],
                    "relevance": (params.get("relevance") or [""])[0],
                    "priority_label": (params.get("priority_label") or [""])[0],
                    "source_group": (params.get("source_group") or [""])[0],
                    "page": (params.get("page") or ["1"])[0],
                    "limit": (params.get("limit") or ["50"])[0],
                    "sort_col": (params.get("sort_col") or ["traffic"])[0],
                    "sort_dir": (params.get("sort_dir") or ["desc"])[0],
                    "column_filters": (params.get("column_filters") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-niche-competitors":
                try:
                    from ozon_category_dashboard.seo_projects import project_niche_competitors
                except ModuleNotFoundError:
                    from seo_projects import project_niche_competitors
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_niche_competitors(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-competitor-keywords/analyze-job/status":
                try:
                    from ozon_category_dashboard.seo_projects import competitor_analysis_job_status
                except ModuleNotFoundError:
                    from seo_projects import competitor_analysis_job_status
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                result = competitor_analysis_job_status(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "job_id": (params.get("job_id") or [""])[0],
                })
                if result.get("job") and result["job"].get("status") in {"queued", "running"}:
                    ensure_seo_competitor_analysis_job_thread(client, result["job"]["job_id"])
                self.send_json(result)
            elif parsed.path == "/api/seo-project-full-run/status":
                try:
                    from ozon_category_dashboard.seo_projects import full_seo_run_job_status
                except ModuleNotFoundError:
                    from seo_projects import full_seo_run_job_status
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                result = full_seo_run_job_status(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "job_id": (params.get("job_id") or [""])[0],
                })
                if result.get("job") and result["job"].get("status") in {"queued", "running"}:
                    ensure_seo_full_run_job_thread(client, result["job"]["job_id"])
                self.send_json(result)
            elif parsed.path == "/api/seo-project-competitor-keywords":
                try:
                    from ozon_category_dashboard.seo_projects import project_competitor_keywords
                except ModuleNotFoundError:
                    from seo_projects import project_competitor_keywords
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_competitor_keywords(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-customer-messages":
                try:
                    from ozon_category_dashboard.seo_projects import project_customer_messages
                except ModuleNotFoundError:
                    from seo_projects import project_customer_messages
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_customer_messages(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                    "message_type": (params.get("message_type") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-competitor-reviews":
                try:
                    from ozon_category_dashboard.seo_projects import project_competitor_reviews
                except ModuleNotFoundError:
                    from seo_projects import project_competitor_reviews
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_competitor_reviews(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                    "competitor_sku": (params.get("competitor_sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-customer-voice/claims":
                try:
                    from ozon_category_dashboard.seo_projects import project_customer_voice_claims
                except ModuleNotFoundError:
                    from seo_projects import project_customer_voice_claims
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_customer_voice_claims(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-semantic-context":
                try:
                    from ozon_category_dashboard.seo_projects import project_semantic_context
                except ModuleNotFoundError:
                    from seo_projects import project_semantic_context
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_semantic_context(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-content-allocation":
                try:
                    from ozon_category_dashboard.seo_projects import project_content_allocation
                except ModuleNotFoundError:
                    from seo_projects import project_content_allocation
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_content_allocation(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-content-draft":
                try:
                    from ozon_category_dashboard.seo_projects import project_content_draft
                except ModuleNotFoundError:
                    from seo_projects import project_content_draft
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_content_draft(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-content-review":
                try:
                    from ozon_category_dashboard.seo_projects import project_content_review
                except ModuleNotFoundError:
                    from seo_projects import project_content_review
                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_content_review(read_db_config(client), {
                    "project_id": (params.get("project_id") or [""])[0],
                    "project_kind": (params.get("project_kind") or ["monitoring"])[0],
                    "sku": (params.get("sku") or [""])[0],
                }))
            elif parsed.path == "/api/seo-project-candidates":
                try:
                    from ozon_category_dashboard.seo_projects import project_candidates
                except ModuleNotFoundError:
                    from seo_projects import project_candidates

                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                marketplace = (params.get("marketplace") or [DEFAULT_MARKETPLACE])[0].strip().lower()
                self.send_json(project_candidates(
                    read_db_config(client), marketplace,
                    (params.get("q") or [""])[0],
                    (params.get("limit") or ["100"])[0],
                    (params.get("category") or [""])[0],
                    (params.get("subcategory") or [""])[0],
                    (params.get("page") or ["1"])[0],
                    (params.get("gj_model") or [""])[0],
                    (params.get("assortment_bia") or [""])[0],
                    (params.get("tg") or [""])[0],
                    (params.get("tg_plus") or [""])[0],
                    (params.get("cg") or [""])[0],
                    (params.get("season") or [""])[0],
                    (params.get("selection") or [""])[0],
                    brand=(params.get("brand") or [""])[0],
                    gender=(params.get("gender") or [""])[0],
                    age=(params.get("age") or [""])[0],
                    collection=(params.get("collection") or [""])[0],
                    style=(params.get("style") or [""])[0],
                    color=(params.get("color") or [""])[0],
                    material=(params.get("material") or [""])[0],
                    material_composition=(params.get("material_composition") or [""])[0],
                    russian_size=(params.get("russian_size") or [""])[0],
                    manufacturer_size=(params.get("manufacturer_size") or [""])[0],
                    target_audience=(params.get("target_audience") or [""])[0],
                    availability=(params.get("availability") or [""])[0],
                    rating_min=(params.get("rating_min") or [""])[0],
                    catalog_category=(params.get("catalog_category") or [""])[0],
                    sort_col=(params.get("sort_col") or ["product_name"])[0],
                    sort_dir=(params.get("sort_dir") or ["asc"])[0],
                    column_filters=(params.get("column_filters") or [""])[0],
                    categories=(params.get("categories") or [""])[0],
                    project_id=(params.get("project_id") or [""])[0],
                    client=client,
                ))
            elif parsed.path == "/api/seo-project-template":
                try:
                    from ozon_category_dashboard.seo_projects import build_project_template
                except ModuleNotFoundError:
                    from seo_projects import build_project_template

                body, filename = build_project_template()
                self.send_file(body, filename)
            elif parsed.path == "/api/seo-project":
                try:
                    from ozon_category_dashboard.seo_projects import project_detail
                except ModuleNotFoundError:
                    from seo_projects import project_detail

                params = parse_qs(parsed.query)
                client = normalize_client_key((params.get("client") or [current_client_key()])[0])
                self.send_json(project_detail(
                    read_db_config(client),
                    (params.get("project_id") or [""])[0],
                    (params.get("project_kind") or ["monitoring"])[0],
                ))
            elif parsed.path == "/api/wb-search-query-products":
                from wb_search_queries_dashboard import handle_products as handle_wb_search_query_products

                self.send_json(handle_wb_search_query_products(parsed, get_conn, current_client_key()))
            elif parsed.path in {
                "/api/wb-ad-search-queries-dashboard",
                "/glory/api/wb-ad-search-queries-dashboard",
                "/api/konstex/wb-ad-search-queries",
                "/glory/api/konstex/wb-ad-search-queries",
            }:
                from wb_ad_search_queries_dashboard import payload as wb_ad_search_queries_payload

                params = parse_qs(parsed.query)
                requested_client = normalize_client_key(
                    (params.get("client") or [current_client_key()])[0]
                )
                if (
                    requested_client not in ADMIN_CLIENTS
                    or not client_supports_report(requested_client, "wbAdSearchQueries")
                ):
                    self.send_json(
                        {"ok": False, "error": "client_report_unavailable"},
                        status=404,
                    )
                else:
                    requested_client_token = CURRENT_CLIENT.set(requested_client)
                    try:
                        self.send_json(wb_ad_search_queries_payload(get_conn, params))
                    finally:
                        CURRENT_CLIENT.reset(requested_client_token)
            elif parsed.path == "/api/wb-search-queries-dashboard":
                from wb_search_queries_dashboard import handle_dashboard as handle_wb_search_queries_dashboard

                self.send_json(handle_wb_search_queries_dashboard(parsed, get_conn, current_client_key()))
            elif parsed.path == "/api/wb-entrance-products":
                from wb_entrance_dashboard import handle_products as handle_wb_entrance_products

                self.send_json(handle_wb_entrance_products(parsed, get_conn, current_client_key()))
            elif parsed.path == "/api/wb-entrance-dashboard":
                from wb_entrance_dashboard import handle_dashboard as handle_wb_entrance_dashboard

                self.send_json(handle_wb_entrance_dashboard(parsed, get_conn, current_client_key()))
            elif parsed.path == "/api/planfact-summary":
                self.send_json(handle_planfact_summary(parsed) if client_supports_report(current_client_key(), "planfact") else empty_planfact_payload(parsed.path))
            elif parsed.path == "/api/planfact-daily":
                self.send_json(handle_planfact_daily(parsed) if client_supports_report(current_client_key(), "planfact") else empty_planfact_payload(parsed.path))
            elif parsed.path == "/api/planfact-monthly":
                self.send_json(handle_planfact_monthly(parsed) if client_supports_report(current_client_key(), "planfact") else empty_planfact_payload(parsed.path))
            elif parsed.path == "/api/planfact-scorecard":
                self.send_json(handle_planfact_scorecard(parsed) if client_supports_report(current_client_key(), "planfact") else empty_planfact_payload(parsed.path))
            elif parsed.path == "/api/planfact-products":
                self.send_json(handle_planfact_products(parsed) if client_supports_report(current_client_key(), "planfact") else {"ok": True, "rows": [], "totals": {}})
            elif parsed.path == "/api/admin/clients":
                self.send_json(admin_clients_payload())
            elif parsed.path == "/api/admin/client-onboarding/inspection":
                from client_onboarding_runtime import inspect_history_data

                params = parse_qs(parsed.query)
                try:
                    self.send_json(inspect_history_data(
                        params.get("client", [""])[0],
                        params.get("marketplace", [""])[0],
                        params.get("date_from", [""])[0],
                        params.get("date_to", [""])[0],
                    ))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=400)
            elif parsed.path == "/api/admin/client-onboarding":
                from client_onboarding_runtime import payload as onboarding_payload

                params = parse_qs(parsed.query)
                try:
                    self.send_json(onboarding_payload(params.get("client", [""])[0]))
                except ValueError as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=404)
            elif parsed.path == "/api/admin/users":
                self.send_json(admin_users_payload())
            elif parsed.path == "/api/admin/database-overview":
                self.send_json(admin_database_overview_payload())
            elif parsed.path == "/api/admin/connections":
                self.send_json(admin_connections_payload())
            elif parsed.path == "/api/admin/integrations":
                self.send_json(admin_integrations_payload())
            elif parsed.path == "/api/admin/imports":
                self.send_json(handle_admin_imports(parsed))
            elif parsed.path == "/api/admin/all-clients-daily":
                self.send_json(handle_admin_all_clients_daily())
            elif parsed.path == "/api/admin/all-clients-assortment":
                self.send_json(handle_admin_all_clients_assortment())
            elif parsed.path == "/api/admin/wb-api/progress":
                self.send_json(handle_wb_api_progress(parsed))
            elif parsed.path == "/api/admin/run-import-stream":
                self.stream_admin_run_import(parsed)
            elif parsed.path == "/api/admin/run-import-sync":
                self.send_json(handle_admin_run_import_sync(parsed))
            elif parsed.path == "/api/admin/run-import":
                self.send_json(handle_admin_run_import(parsed))
            elif parsed.path == "/api/chart-export":
                body, filename = handle_chart_export(parsed)
                self.send_file(body, filename)
            elif parsed.path == "/api/report-export":
                body, filename = handle_report_export(parsed)
                self.send_file(body, filename)
            elif parsed.path == "/api/export":
                body, filename = handle_export(parsed)
                self.send_file(body, filename)
            elif parsed.path in {"/review", "/review/", "/review/proposal", "/review/proposal/", "/review/tasks", "/review/tasks/", "/review/reports", "/review/reports/"}:
                self.send_static_file(Path("react") / "index.html")
            else:
                super().do_GET()
        except Exception as exc:
            traceback.print_exc()
            if isinstance(exc, psycopg2.errors.UndefinedTable) and parsed.path in REPORT_DATA_API_PATHS:
                self.send_json(unavailable_report_source_payload(parsed, exc))
            else:
                self.send_json(api_error_payload(exc), status=500)
        finally:
            CURRENT_CLIENT.reset(client_token)
            CURRENT_ACCESS_USER.reset(access_token)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/login", "/login/"}:
            if parsed.query != "source=1" and self.dashboard_access_granted():
                self.dashboard_access_redirect(self.dashboard_access_return_path(parse_qs(parsed.query).get("next", [None])[0]))
            else:
                self.send_static_file_headers("access-login.html", cache_control="no-store")
            return
        if not self.dashboard_access_granted():
            self.send_dashboard_access_required(parsed)
            return
        if locked_dashboard_feature_disabled(parsed.path):
            self.reject_locked_dashboard_feature(parsed.path)
            return
        if parsed.path in {"/review", "/review/", "/review/proposal", "/review/proposal/", "/review/tasks", "/review/tasks/", "/review/reports", "/review/reports/"}:
            self.send_static_file_headers(Path("react") / "index.html")
            return
        if is_shared_portfolio_request(parsed):
            access_token = CURRENT_ACCESS_USER.set(self.dashboard_access_identity())
            client_token = CURRENT_CLIENT.set(client_from_query(parsed.query))
            try:
                body = render_portfolio_page().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
            finally:
                CURRENT_CLIENT.reset(client_token)
                CURRENT_ACCESS_USER.reset(access_token)
            return
        super().do_HEAD()


def review_client_allowed(client):
    identity = CURRENT_ACCESS_USER.get()
    return bool(client and normalize_client_key(client) == client and
                (not identity or identity.get('is_admin') or client in (identity.get('clients') or [])))


def review_wb_token(client):
    token_env = wb_api_token_env(client)
    return os.environ.get(token_env) or read_app_env_file().get(token_env) or registered_client_credential(client, 'wb_api_token')


def resume_review_workbenches():
    from contextlib import closing
    from km_trade_finance import connect_km
    from review_workbench import start_worker
    for client in list(ADMIN_CLIENTS):
        try:
            config = read_db_config(client)
            with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.review_reply_jobs') relation")
                exists = bool(cur.fetchone()['relation'])
            if exists:
                start_worker(config, ADMIN_CLIENTS[client].get('label', client), lambda c=client: review_wb_token(c))
        except Exception:
            pass  # An unavailable client cannot prevent other dashboards from starting.


def main():
    load_app_env()
    from galactica_startup import install_from_environment

    source_enabled = install_from_environment(sys.modules[__name__])

    def startup_log(message=""):
        try:
            print(message, flush=True)
        except OSError:
            return

    try:
        loaded_clients = hydrate_registered_clients()
        if loaded_clients:
            startup_log(f"Client registry: loaded {loaded_clients} client(s)")
    except Exception as exc:
        if source_enabled:
            raise RuntimeError("PULSE source client registry unavailable") from None
        startup_log(f"Client registry unavailable: {type(exc).__name__}: {exc}")
    if source_enabled:
        from galactica_entitlement import require_drained_report_startup
        require_drained_report_startup(sys.modules[__name__])
    port = int(os.environ.get("DASHBOARD_PORT", "8050"))
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    threading.Thread(target=resume_review_workbenches, daemon=True, name='resume-review-workbenches').start()
    display_host = "127.0.0.1" if host in {"", "0.0.0.0"} else host
    startup_log(f"BI dashboard: http://{display_host}:{port}")
    locked_client = dashboard_locked_client()
    if locked_client:
        startup_log(f"Locked client: {locked_client}; access auth: {'enabled' if dashboard_access_configured() else 'disabled'}")
    startup_log("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        startup_log("\nStopped.")


if __name__ == "__main__":
    main()
