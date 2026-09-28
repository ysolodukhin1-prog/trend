"""Explicit, reversible management estimates; never writes estimated COGS to facts."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

MODEL_NOTICE = "Расчётная себестоимость = цена / 3, если фактическая не задана. Это управленческая оценка."


def cost_basis(actual: Any, price: Any) -> tuple[Decimal | None, str]:
    if actual is not None and actual != "":
        value = Decimal(str(actual))
        if not value.is_finite() or value < 0:
            raise ValueError("Себестоимость должна быть неотрицательной")
        return value, "actual"
    if price is not None and Decimal(str(price)) > 0:
        return Decimal(str(price)) / 3, "estimated_price_div_3"
    return None, "missing"


def allocate_expense(amount: Any, start: date, end: date, selected_start: date, selected_end: date) -> Decimal:
    days = (min(end, selected_end) - max(start, selected_start)).days + 1
    return Decimal(str(amount)) * max(days, 0) / ((end - start).days + 1)


def ensure_expense_scope(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE public.ozon_pl_expenses ADD COLUMN IF NOT EXISTS marketplace text NOT NULL DEFAULT 'ozon'")


def finance_model_schema_available(cur: Any) -> bool:
    """Check the optional model schema without mutating it during a GET request."""
    cur.execute(
        """
        SELECT
            to_regclass('public.ozon_pl_expenses') AS expenses_relation,
            to_regclass('public.ozon_unit_global_settings') AS settings_relation,
            EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ozon_pl_expenses'
                  AND column_name = 'marketplace'
            ) AS expenses_have_marketplace
        """
    )
    row = cur.fetchone() or {}
    return bool(
        row.get('expenses_relation')
        and row.get('settings_relation')
        and row.get('expenses_have_marketplace')
    )


def read_expenses(cur: Any, marketplace: str, start: date, end: date) -> list[dict]:
    cur.execute("""SELECT expense_id, date_from, date_to, expense_name, amount,
                   allocation_method, notes FROM public.ozon_pl_expenses
                   WHERE marketplace=%s AND date_from<=%s AND date_to>=%s ORDER BY date_from,expense_id""",
                (marketplace, end, start))
    rows = []
    for source in cur.fetchall():
        row = dict(source)
        row['source_amount'] = float(row['amount'])
        row['amount'] = float(allocate_expense(row['amount'], row['date_from'], row['date_to'], start, end))
        row['date_from'], row['date_to'] = str(row['date_from']), str(row['date_to'])
        rows.append(row)
    return rows


def enrich_pl(config: dict, payload: dict) -> dict:
    if payload.get('available') is False:
        return payload
    from km_trade_finance import connect_km, get_global_settings
    marketplace = payload['marketplace']
    start, end = date.fromisoformat(payload['date_from']), date.fromisoformat(payload['date_to'])
    with connect_km(config) as conn:
        with conn.cursor() as cur:
            if not finance_model_schema_available(cur):
                payload['model_read_only'] = True
                payload['model_notice'] = (
                    'Фактические данные площадки доступны, но модель себестоимости, '
                    'налогов и общих расходов для кабинета не настроена. '
                    'Пропуски не заменены нулями.'
                )
                payload.setdefault('global_settings', {})
                payload.setdefault('all_expenses', [])
                payload.setdefault('expenses', [])
                return payload
            settings = get_global_settings(cur)
            expenses = read_expenses(cur, marketplace, start, end)
            if marketplace == 'ozon':
                cur.execute("""SELECT to_char(l.operation_date,'YYYY-MM') AS month, l.sku,
                    sum(CASE WHEN s.cogs_per_unit IS NOT NULL THEN l.quantity*s.cogs_per_unit
                        ELSE l.amount/3 END) AS cogs,
                    sum(l.amount/3) FILTER (WHERE s.cogs_per_unit IS NULL) AS estimated_cogs,
                    bool_or(s.cogs_per_unit IS NULL) AS estimated
                    FROM public.ozon_finance_lines l
                    LEFT JOIN public.ozon_unit_product_settings s ON s.sku=l.sku
                    WHERE l.line_kind='revenue' AND l.operation_date BETWEEN %s AND %s
                    GROUP BY 1,2""", (start,end))
                costs = [dict(row) for row in cur.fetchall()]
            else:
                cur.execute("""SELECT to_char(operation_date,'YYYY-MM') AS month, nm_id::text AS sku,
                    sum(retail_amount * CASE WHEN lower(coalesce(doc_type_name,'')) LIKE '%%возврат%%'
                    OR lower(coalesce(doc_type_name,'')) LIKE '%%return%%' THEN -1 ELSE 1 END)/3 AS cogs,
                    true AS estimated FROM public.wb_finance_lines
                    WHERE operation_date BETWEEN %s AND %s GROUP BY 1,2""", (start,end))
                costs = [dict(row) for row in cur.fetchall()]
    monthly_costs: dict[str, Decimal] = {}
    sku_costs: dict[str, Decimal] = {}
    estimated_skus = set()
    for row in costs:
        amount = Decimal(str(row.get('cogs') or 0))
        monthly_costs[row['month']] = monthly_costs.get(row['month'], Decimal(0)) + amount
        sku_costs[str(row['sku'])] = sku_costs.get(str(row['sku']), Decimal(0)) + amount
        if row['estimated']: estimated_skus.add(str(row['sku']))
    totals = payload['totals']
    cogs = sum(monthly_costs.values(), Decimal(0))
    manual = sum((Decimal(str(x['amount'])) for x in expenses), Decimal(0))
    tax_pct = settings.get('tax_pct')
    vat_pct = Decimal(str(settings.get('vat_pct') or 0))
    revenue = Decimal(str(totals.get('revenue') or 0))
    vat = revenue * vat_pct / 100
    tax = revenue * Decimal(str(tax_pct)) / 100 if tax_pct is not None else None
    before = Decimal(str(totals.get('marketplace_net') or 0))-cogs-Decimal(str(totals.get('seller_unit_costs') or 0))-manual-vat
    result = before-tax if tax is not None else before
    totals.update(cogs=float(cogs), gross_profit=float(revenue-cogs), manual_expenses=float(manual),
        vat=float(vat), tax=float(tax) if tax is not None else None, profit_before_tax=float(before),
        management_result=float(result), management_margin_pct=float(result/revenue*100) if revenue else None,
        net_profit=float(result) if tax is not None else None,
        margin_pct=float(result/revenue*100) if revenue and tax is not None else None,
        profit_ready=tax is not None, tax_configured=tax is not None, cogs_model_coverage_pct=100,
        estimated_sku_count=len(estimated_skus), result_basis='after_configured_taxes' if tax is not None else 'before_income_tax')
    payload['model_notice'] = MODEL_NOTICE
    payload['global_settings'] = settings
    payload['taxes'] = {'income_tax_pct':tax_pct,'vat_pct':float(vat_pct)}
    payload['all_expenses'] = expenses
    # Existing editors replace only an exact date range. Do not expose overlapping
    # source rows as editable copies, which would duplicate them on save.
    payload['expenses'] = [x for x in expenses if x['date_from']==str(start) and x['date_to']==str(end)]
    payload.pop('expense_items', None)
    payload['products_total'] = len(payload.get('products',[]))
    payload['products_limited'] = False
    for row in payload.get('monthly', []):
        month = str(row['month'])[:7]
        month_start = date.fromisoformat(month+'-01')
        next_month = (month_start.replace(day=28)+timedelta(days=4)).replace(day=1)
        expense = sum((allocate_expense(x['source_amount'],date.fromisoformat(x['date_from']),date.fromisoformat(x['date_to']),
                         max(start,month_start),min(end,next_month-timedelta(days=1))) for x in expenses),Decimal(0))
        rev = Decimal(str(row.get('revenue') or 0)); cost=monthly_costs.get(month,Decimal(0))
        mtax = rev*Decimal(str(tax_pct))/100 if tax_pct is not None else None
        mvat = rev*vat_pct/100
        pre = Decimal(str(row.get('marketplace_net') or 0))-cost-Decimal(str(row.get('seller_costs') or 0))-expense-mvat
        value = pre-mtax if mtax is not None else pre
        row.update(cogs=float(cost),gross_profit=float(rev-cost),manual_expenses=float(expense),vat=float(mvat),
            taxes=float(mtax+mvat) if mtax is not None else None,profit_before_tax=float(pre),management_result=float(value),
            net_profit=float(value) if mtax is not None else None,
            margin_pct=float(value/rev*100) if rev else None,cogs_complete=True)
    for row in payload.get('products',[]):
        sku=str(row['sku']); cost=sku_costs.get(sku,Decimal(0)); rev=Decimal(str(row.get('revenue') or 0))
        if marketplace=='ozon': component=Decimal(str(row.get('marketplace_component_net') or 0))
        else: component=rev-Decimal(str(row.get('marketplace_costs_before_cogs') or 0))
        value=component-cost-Decimal(str(row.get('seller_costs') or 0))-rev*vat_pct/100
        if tax_pct is not None: value-=rev*Decimal(str(tax_pct))/100
        row.update(cogs=float(cost),cogs_known=sku not in estimated_skus,cogs_source='estimated_price_div_3' if sku in estimated_skus else 'actual',
            gross_profit=float(rev-cost),profit_before_common_costs=float(value),management_result=float(value),
            income_tax=float(rev*Decimal(str(tax_pct))/100) if tax_pct is not None else None, vat=float(rev*vat_pct/100),
            net_profit=None,margin_pct=float(value/rev*100) if rev else None)
    updates={'cogs':-float(cogs),'gross_profit':float(revenue-cogs),'manual_expenses':-float(manual),
             'profit_before_tax':float(before),'tax':-float(tax) if tax is not None else None,
             'net_profit':float(result) if tax is not None else None}
    for row in payload.get('statement',[]):
        if row['key'] in updates: row['amount']=updates[row['key']]
        if row['key']=='cogs': row.update(label='Себестоимость (факт / цена ÷ 3)',is_partial=False,is_estimated=bool(estimated_skus))
    existing={x['key'] for x in payload['statement']}
    for key,label,value in [('manual_expenses','Дополнительные расходы',-float(manual)),('vat_model','НДС модели',-float(vat)),
                             ('management_result','Результат модели после настроенных налогов' if tax is not None else 'Результат модели до налога с дохода',float(result))]:
        if key not in existing: payload['statement'].append({'key':key,'label':label,'amount':value,'kind':'total' if key=='management_result' else 'expense'})
    for row in payload['statement']:
        row['revenue_pct']=float(Decimal(str(row['amount']))/revenue*100) if row.get('amount') is not None and revenue else None
    payload['methodology'].update(cogs=MODEL_NOTICE,
        tax='Без ставки налога показан результат до налога с дохода. НДС берётся только из явно сохранённой модели.',
        expenses='Общие расходы разделены по площадке; пересечение периодов распределяется по календарным дням.',
        sku='По SKU показан вклад до общих расходов. Чистая прибыль SKU не подменяется вкладом.',
        warning='Историческая фактическая себестоимость берётся из сохранённых настроек; расчётная треть выручки не записывается как факт.')
    payload['months'] = payload.get('monthly', [])
    payload['reconciliation']={'monthly_management_result_difference':round(float(result)-sum(x['management_result'] for x in payload.get('monthly',[])),6)}
    return payload


def wb_unit_model(config:dict,raw_from=None,raw_to=None,client_key='km_trade',page=1,page_size=100)->dict:
    from km_trade_finance import pl_payload
    pl=pl_payload(config,raw_from,raw_to,client_key,'wb')
    if pl.get('available') is False: return {**pl,'rows':[]}
    rows=[]
    for product in pl['products']:
        units=abs(float(product.get('units') or 0)); revenue=float(product.get('revenue') or 0)
        price=abs(revenue)/units if units else None
        product_cogs = product.get('cogs')
        management_result = product.get('management_result')
        row={**product,'cogs_per_unit':float(product_cogs)/float(product['units']) if product_cogs is not None and product.get('units') else None,
             'actual_units':product['units'],'actual_revenue':revenue,'actual_average_price':price,
             'actual_cogs':product_cogs, 'actual_commission':-float(product.get('commission') or 0),
             'actual_acquiring':-float(product.get('acquiring') or 0),
             'actual_forward_logistics':-float(product.get('logistics') or 0),
             'actual_tax':product.get('income_tax'),'actual_vat':product.get('vat'),
             'fulfillment_per_unit':0,'inbound_per_unit':0,'other_per_unit':0,
             'actual_profit_per_unit':float(management_result)/units if management_result is not None and units else None,'actual_margin_pct':product.get('margin_pct'),
             'actual_commission_per_unit':-float(product.get('commission') or 0)/units if units else None,
             'actual_acquiring_per_unit':-float(product.get('acquiring') or 0)/units if units else None,
             'actual_forward_logistics_per_unit':-float(product.get('logistics') or 0)/units if units else None,
             'actual_reverse_logistics_per_unit':None,'actual_ad_per_unit':None,
             'model_read_only':True,'cogs_status':product.get('cogs_source') or ('actual' if product_cogs is not None else 'missing'),'current_scenario':{},'rrc_scenario':{}}
        rows.append(row)
    limit=max(25,min(int(page_size or 100),300)); pages=max(1,(len(rows)+limit-1)//limit); page=max(1,min(int(page or 1),pages))
    return {**pl,'rows':rows[(page-1)*limit:page*limit], 'model_read_only':True,
            'model_notice':pl.get('model_notice') or MODEL_NOTICE+' WB: вклад SKU до общих расходов; тарифные сценарии не подменяются Ozon.',
            'pagination':{'page':page,'page_size':limit,'total':len(rows),'total_pages':pages}}
