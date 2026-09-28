from __future__ import annotations


ADV_VIEW = "mv_wb_adv_daily_by_article_category"


def _relation_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    row = cur.fetchone()
    if not row:
        return False
    value = next(iter(row.values())) if hasattr(row, "values") else row[0]
    return bool(value)


def rebuild_wb_advertising_view(cur) -> None:
    """Builds one file-compatible advertising view from API plus historic XLSX rows.

    API dates take precedence over raw file dates. All-account order metrics come
    from the WB funnel and remain NULL when that source has no matching SKU/day.
    """
    has_api = _relation_exists(cur, "wb_api_entities")
    has_raw = _relation_exists(cur, "wb_adv_daily_raw")
    has_funnel = _relation_exists(cur, "wb_funnel_daily")
    has_stock = _relation_exists(cur, "wb_stock_api_current")
    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{ADV_VIEW}")

    ctes = []
    selects = []
    if has_api:
        if has_funnel:
            funnel_cte = """
            funnel AS (
                SELECT report_date, wb_nmid::text AS wb_nmid,
                       max(nullif(seller_article, '')) AS seller_article,
                       max(nullif(product_name, '')) AS product_name,
                       max(nullif(category_name, '')) AS category_name,
                       max(nullif(brand, '')) AS brand,
                       sum(ordered_units)::numeric AS total_orders_qty,
                       sum(ordered_amount_rub)::numeric AS total_orders_amount_rub
                FROM public.wb_funnel_daily
                WHERE wb_nmid IS NOT NULL
                GROUP BY report_date, wb_nmid::text
            )
            """
        else:
            funnel_cte = """
            funnel AS (
                SELECT NULL::date report_date, NULL::text wb_nmid, NULL::text seller_article,
                       NULL::text product_name, NULL::text category_name, NULL::text brand,
                       NULL::numeric total_orders_qty, NULL::numeric total_orders_amount_rub
                WHERE false
            )
            """
        if has_stock:
            stock_cte = """
            stock AS (
                SELECT wb_nmid::text AS wb_nmid, seller_article, product_name, category_name, brand
                FROM public.wb_stock_api_current
            )
            """
        else:
            stock_cte = """
            stock AS (
                SELECT NULL::text wb_nmid, NULL::text seller_article, NULL::text product_name,
                       NULL::text category_name, NULL::text brand WHERE false
            )
            """
        ctes.extend([
            """
            expanded AS (
                SELECT (day_payload->>'date')::timestamptz::date AS report_date,
                       coalesce(entity.payload->>'advertId', split_part(entity.entity_key, ':', 1)) AS campaign_id,
                       nullif(nm_payload->>'nmId', '') AS wb_nmid,
                       nm_payload, entity.captured_at
                FROM public.wb_api_entities entity
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(entity.payload->'days') = 'array'
                         THEN entity.payload->'days' ELSE '[]'::jsonb END
                ) day_payload
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(day_payload->'apps') = 'array'
                         THEN day_payload->'apps' ELSE '[]'::jsonb END
                ) app_payload
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(app_payload->'nms') = 'array'
                         THEN app_payload->'nms' ELSE '[]'::jsonb END
                ) nm_payload
                WHERE entity.source_key = 'promotion.fullstats'
                  AND nullif(day_payload->>'date', '') IS NOT NULL
                  AND nullif(nm_payload->>'nmId', '') IS NOT NULL
            )
            """,
            """
            api_daily AS (
                SELECT report_date, wb_nmid,
                       max(nullif(nm_payload->>'name', '')) AS api_product_name,
                       sum(coalesce(nullif(nm_payload->>'views', '')::numeric, 0)) AS impressions,
                       sum(coalesce(nullif(nm_payload->>'clicks', '')::numeric, 0)) AS clicks,
                       sum(coalesce(nullif(nm_payload->>'sum', '')::numeric, 0)) AS expense_rub,
                       sum(coalesce(nullif(nm_payload->>'atbs', '')::numeric, 0)) AS added_to_cart,
                       sum(coalesce(nullif(nm_payload->>'orders', '')::numeric, 0)) AS orders_qty,
                       sum(coalesce(nullif(nm_payload->>'sum_price', '')::numeric, 0)) AS orders_amount_rub,
                       string_agg(DISTINCT campaign_id, ',') AS campaign_ids,
                       max(captured_at) AS imported_at
                FROM expanded
                GROUP BY report_date, wb_nmid
            )
            """,
            funnel_cte,
            stock_cte,
        ])
        selects.append("""
            SELECT a.report_date, a.wb_nmid AS wb_marketplace_article,
                   a.wb_nmid AS ozon_marketplace_article, a.wb_nmid AS sku,
                   coalesce(stock.seller_article, funnel.seller_article) AS seller_article,
                   coalesce(stock.seller_article, funnel.seller_article, a.wb_nmid) AS product_artikul,
                   coalesce(stock.product_name, funnel.product_name, a.api_product_name, a.wb_nmid) AS product_name,
                   coalesce(stock.category_name, funnel.category_name, 'Без категории') AS category_name,
                   NULL::text AS subcategory_name,
                   coalesce(stock.brand, funnel.brand) AS brand, NULL::text AS barcode,
                   a.impressions, a.clicks,
                   CASE WHEN a.impressions <> 0 THEN round(a.clicks / a.impressions * 100, 4) END AS ctr_pct,
                   a.expense_rub, a.expense_rub AS fact_expense_rub,
                   CASE WHEN a.clicks <> 0 THEN round(a.expense_rub / a.clicks, 4) END AS cpc_rub,
                   CASE WHEN a.impressions <> 0 THEN round(a.expense_rub / a.impressions * 1000, 4) END AS cpm_rub,
                   a.added_to_cart, a.orders_qty,
                   CASE WHEN a.clicks <> 0 THEN round(a.orders_qty / a.clicks * 100, 4) END AS cr_pct,
                   a.orders_amount_rub,
                   CASE WHEN a.orders_qty <> 0 THEN round(a.expense_rub / a.orders_qty, 4) END AS cpa_rub,
                   CASE WHEN a.orders_amount_rub <> 0 THEN round(a.expense_rub / a.orders_amount_rub * 100, 4) END AS drr_pct,
                   funnel.total_orders_qty, funnel.total_orders_amount_rub,
                   CASE WHEN funnel.total_orders_amount_rub <> 0 THEN round(a.expense_rub / funnel.total_orders_amount_rub * 100, 4) END AS total_drr_pct,
                   CASE WHEN funnel.total_orders_qty <> 0 THEN round(a.expense_rub / funnel.total_orders_qty, 4) END AS total_cpa_rub,
                   'api://wb/promotion/fullstats/' || a.campaign_ids AS source_file,
                   'nm-daily'::text AS source_sheet,
                   row_number() OVER (ORDER BY a.report_date, a.wb_nmid)::integer AS source_row_num,
                   NULL::bigint AS file_size_bytes, NULL::timestamp AS file_mtime,
                   a.imported_at::timestamp AS imported_at
            FROM api_daily a
            LEFT JOIN stock ON stock.wb_nmid = a.wb_nmid
            LEFT JOIN funnel ON funnel.report_date = a.report_date AND funnel.wb_nmid = a.wb_nmid
        """)

    if has_raw:
        raw_filter = """
            WHERE NOT EXISTS (SELECT 1 FROM api_daily a WHERE a.report_date = r.report_date)
        """ if has_api else ""
        selects.append(f"""
            SELECT r.report_date, r.wb_marketplace_article, r.wb_marketplace_article AS ozon_marketplace_article,
                   r.wb_marketplace_article AS sku, r.seller_article,
                   coalesce(r.seller_article, r.wb_marketplace_article) AS product_artikul,
                   coalesce(r.seller_article, r.wb_marketplace_article) AS product_name,
                   'Без категории'::text AS category_name, NULL::text AS subcategory_name,
                   NULL::text AS brand, NULL::text AS barcode,
                   r.impressions, r.clicks, r.ctr_pct, r.expense_rub, r.fact_expense_rub,
                   r.cpc_rub, r.cpm_rub, r.added_to_cart, r.orders_qty, r.cr_pct,
                   r.orders_amount_rub, r.cpa_rub, r.drr_pct, r.total_orders_qty,
                   r.total_orders_amount_rub, r.total_drr_pct, r.total_cpa_rub,
                   r.source_file, r.source_sheet, r.source_row_num, r.file_size_bytes,
                   r.file_mtime, r.imported_at
            FROM public.wb_adv_daily_raw r
            {raw_filter}
        """)

    if not selects:
        selects.append("""
            SELECT NULL::date report_date, NULL::text wb_marketplace_article,
                   NULL::text ozon_marketplace_article, NULL::text sku, NULL::text seller_article,
                   NULL::text product_artikul, NULL::text product_name, NULL::text category_name,
                   NULL::text subcategory_name, NULL::text brand, NULL::text barcode,
                   NULL::numeric impressions, NULL::numeric clicks, NULL::numeric ctr_pct,
                   NULL::numeric expense_rub, NULL::numeric fact_expense_rub, NULL::numeric cpc_rub,
                   NULL::numeric cpm_rub, NULL::numeric added_to_cart, NULL::numeric orders_qty,
                   NULL::numeric cr_pct, NULL::numeric orders_amount_rub, NULL::numeric cpa_rub,
                   NULL::numeric drr_pct, NULL::numeric total_orders_qty,
                   NULL::numeric total_orders_amount_rub, NULL::numeric total_drr_pct,
                   NULL::numeric total_cpa_rub, NULL::text source_file, NULL::text source_sheet,
                   NULL::integer source_row_num, NULL::bigint file_size_bytes,
                   NULL::timestamp file_mtime, NULL::timestamp imported_at WHERE false
        """)

    with_sql = "WITH " + ",".join(ctes) if ctes else ""
    cur.execute(f"CREATE MATERIALIZED VIEW public.{ADV_VIEW} AS {with_sql} " + " UNION ALL ".join(selects))
    for query in (
        f"CREATE INDEX IF NOT EXISTS idx_mv_wb_adv_date ON public.{ADV_VIEW}(report_date)",
        f"CREATE INDEX IF NOT EXISTS idx_mv_wb_adv_article ON public.{ADV_VIEW}(wb_marketplace_article)",
        f"CREATE INDEX IF NOT EXISTS idx_mv_wb_adv_category ON public.{ADV_VIEW}(category_name)",
        f"CREATE INDEX IF NOT EXISTS idx_mv_wb_adv_expense ON public.{ADV_VIEW}(expense_rub)",
        f"CREATE INDEX IF NOT EXISTS idx_mv_wb_adv_orders_amount ON public.{ADV_VIEW}(orders_amount_rub)",
    ):
        cur.execute(query)
