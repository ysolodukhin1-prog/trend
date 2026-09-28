"""Read-only inputs for the inventory decision workbench; missing money stays unknown."""
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import parse_qs
from psycopg2 import sql

COSTS = ('commission_cost','acquiring_cost','logistics_cost','reverse_cost','storage_cost','advertising_cost','fulfillment_cost')


def aggregate_finance(rows, cogs):
    grouped = defaultdict(list)
    for row in rows:
        row=dict(row)
        # An Ozon group containing only service rows has no revenue event;
        # this is an observed zero flow, unlike a revenue event with null money.
        if 'kinds' in row and 'revenue' not in row['kinds']:
            row['revenue']=0; row['units']=0
        grouped[str(row['sku'])].append(row)
    result = {}
    for sku, group in grouped.items():
        def total(key):
            values = [r.get(key) for r in group]
            return sum(float(x) for x in values) if all(x is not None for x in values) else None
        revenue, net, units = total('revenue'), total('net'), total('units')
        costs = {k:total(k) for k in COSTS}
        # Unavailable source articles are absent, not zero. Residual includes all
        # other deductions and positive corrections, preserving settlement totals.
        known = sum(v for v in costs.values() if v is not None)
        costs['other_cost'] = revenue-net-known if revenue is not None and net is not None else None
        weighted, weight, complete = 0., 0., True
        for row in group:
            bars = row.get('barcodes') or []
            matches = [cogs[b] for b in bars if b in cogs]
            values = set(matches)
            qty = row.get('units')
            if qty == 0: continue
            if not bars or len(matches)!=len(bars) or len(values)!=1 or qty is None or qty<0:
                complete=False; continue
            weighted += float(qty)*float(matches[0]); weight += float(qty)
        result[sku] = dict(revenue=revenue,net=net,units=units,costs=costs,
            price=revenue/units if revenue is not None and units and units>0 else None,
            cogs=weighted/weight if complete and weight>0 else None,
            cogs_basis='Текущая стоимость по штрихкодам; средняя по структуре продаж',
            source_rows=sum(int(r.get('source_rows') or 0) for r in group))
    return result


def enrich(payload, parsed, get_conn, funnel_view):
    if payload.get('marketplace') not in {'wb','ozon'} or not payload.get('rows'): return payload
    params=parse_qs(parsed.query); market=payload['marketplace']; client=payload.get('client','')
    end=date.fromisoformat(params.get('date_to',[str(date.today())])[0])
    start=date.fromisoformat(params.get('date_from',[str(end-timedelta(days=29))])[0])
    end=min(end,date.today())
    finance={}; financial_status='Нет финансовых данных'; recent={}
    through=payload.get('demand_through_date')
    with get_conn() as conn, conn.cursor() as cur:
        if through:
            cur.execute(sql.SQL('''SELECT sku,
              sum(ordered_units) FILTER(WHERE report_date>%s::date-7) recent_units,
              sum(ordered_units) FILTER(WHERE report_date<=%s::date-7) prior_units,
              count(DISTINCT report_date) observed_days
              FROM public.{} WHERE report_date BETWEEN %s::date-13 AND %s::date GROUP BY sku''').format(sql.Identifier(funnel_view)),(through,through,through,through))
            recent={str(r['sku']):dict(recent_units=float(r['recent_units'] or 0),prior_units=float(r['prior_units'] or 0),observed_days=r['observed_days']) for r in cur.fetchall()}
        # Financial integration is available for registered unit-economics clients.
        # Keep the stock report available if their optional finance tables are absent.
        if client in {'toptop','lera_nena'}:
            cur.execute('SAVEPOINT inventory_finance')
            try:
                from unit_economics_workspace import _wb, _ozon
                rows=_wb(cur,start,end,client) if market=='wb' else _ozon(cur,start,end,client)[0]
                cur.execute("SELECT to_regclass('public.unit_cogs_versions') relation")
                cogs={}
                if cur.fetchone()['relation']:
                    cur.execute('''SELECT DISTINCT ON(barcode) barcode,amount FROM unit_cogs_versions
                      WHERE valid_from<=%s ORDER BY barcode,valid_from DESC,created_at DESC''',(date.today(),))
                    cogs={r['barcode']:float(r['amount']) for r in cur.fetchall()}
                finance=aggregate_finance(rows,cogs)
                financial_status='Учтённые начисления; полнота расходов не подтверждена'
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT inventory_finance')
                financial_status='Финансовые источники недоступны для расчёта; можно задать расходы вручную'
            finally: cur.execute('RELEASE SAVEPOINT inventory_finance')
    for row in payload['rows']:
        row['management_finance']=finance.get(row['sku'])
        row['recent_demand']=recent.get(row['sku'])
    unallocated=finance.get('__unallocated__')
    payload['management']={'finance_from':str(start),'finance_to':str(end),'finance_status':financial_status,
        'finance_sku_count':sum(r['sku'] in finance for r in payload['rows']),
        'cogs_as_of':str(date.today()),'unallocated_cost':(unallocated['revenue']-unallocated['net']) if unallocated and unallocated['revenue'] is not None and unallocated['net'] is not None else None,
        'notice':'Скорость — заказы, а не выкупы. Расходы — начисления выбранного периода. Себестоимость текущая, прибыль сценарная до налогов. Потребность по SKU; распределение по складам требует регионального спроса.'}
    return payload
