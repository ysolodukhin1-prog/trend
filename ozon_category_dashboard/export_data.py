import ast
import datetime as dt
import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "static" / "data.js"
VIEW_NAME = "vw_ozon_category_stock_sku_attribute_stats"
DEFAULT_CONFIG_SOURCE = (
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Скоринг ассортимнетной матрицы\Выгрузки\Ассортиментная матрица\WB\wb_data_import.py"
)


def read_config():
    source = Path(os.environ.get("DB_CONFIG_SOURCE", DEFAULT_CONFIG_SOURCE))
    text = source.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"DB_CONFIG\s*=\s*(\{.*?\})", text, re.S)
    if not match:
        raise SystemExit(f"DB_CONFIG not found in {source}")
    config = ast.literal_eval(match.group(1))
    config["connect_timeout"] = 5
    return config


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def main():
    query = f"""
        SELECT
            category_name,
            total_stock_qty,
            sku_count,
            category_attribute_count,
            zakazano_sht,
            zakazano_rub,
            vykupleno_sht,
            vykup_pct_sht,
            vykup_pct_rub,
            orders_share_pct,
            orders_cumulative_pct,
            abc_orders,
            sales_share_pct,
            sales_cumulative_pct,
            abc_sales,
            stock_share_pct,
            stock_cumulative_pct,
            abc_stock,
            abc_combined
        FROM public.{VIEW_NAME}
        ORDER BY total_stock_qty DESC NULLS LAST, category_name
    """
    with psycopg2.connect(**read_config(), cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = [
                {key: clean_value(value) for key, value in dict(row).items()}
                for row in cur.fetchall()
            ]

    payload = {
        "view": f"public.{VIEW_NAME}",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(rows),
        "rows": rows,
    }
    OUTPUT.write_text(
        'if (window.location.protocol === "file:") window.DASHBOARD_DATA = '
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(f"Exported {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
