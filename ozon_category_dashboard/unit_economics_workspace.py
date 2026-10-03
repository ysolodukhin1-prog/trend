"""Client-isolated unit workspace: existing settlement facts + versioned assumptions."""
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4

from psycopg2.extras import Json
from km_trade_finance import connect_km
from unit_economics_engine import FIELDS, calculate, decimal, json_numbers, validate
from unit_yandex_services import apply_service_components
from unit_contributions import dated_contribution
import unit_hypotheses
import unit_order_activity
from unit_cost_allocation import allocated_products, allocation_summary

CLIENTS = {'toptop', 'lera_nena'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS unit_scenario_versions (
 id uuid PRIMARY KEY, scenario_key text NOT NULL, revision integer NOT NULL,
 marketplace text NOT NULL, cabinet text NOT NULL, sku text NOT NULL, scheme text NOT NULL,
 name text NOT NULL, inputs jsonb NOT NULL, provenance jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(scenario_key, revision));
CREATE TABLE IF NOT EXISTS unit_cogs_versions (
 id uuid PRIMARY KEY, barcode text NOT NULL, valid_from date NOT NULL,
 amount numeric NOT NULL CHECK(amount>=0), source_ref text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS unit_cogs_lookup ON unit_cogs_versions(barcode,valid_from DESC,created_at DESC);
CREATE TABLE IF NOT EXISTS unit_portfolio_versions (
 id uuid PRIMARY KEY, name text NOT NULL, settings jsonb NOT NULL,
 source_period jsonb NOT NULL, model_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now());
'''


def checked_config(config, client):
    if client not in CLIENTS or config.get('database') != client:
        raise ValueError('Юнит-экономика: клиент и база должны точно совпадать')
    return config


def install(config, client):
    with connect_km(checked_config(config, client)) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(unit_hypotheses.SCHEMA)


def key_for(marketplace, cabinet, sku, scheme):
    return '|'.join([marketplace, cabinet, sku, scheme])


def barcode_list(value):
    values = value if isinstance(value, list) else re.split(r'[,;\s]+', str(value or ''))
    return sorted({str(v).strip() for v in values if re.fullmatch(r'\d{8,14}', str(v).strip())})


def _ozon(cur, start, end, client, totals_only=False):
    cur.execute('''WITH posting_scheme AS (
      SELECT posting_number,min(delivery_schema) scheme
      FROM ozon_finance_lines WHERE nullif(posting_number,'') IS NOT NULL
      GROUP BY posting_number HAVING count(DISTINCT delivery_schema)=1)
    SELECT coalesce(nullif(l.sku,''),'__unallocated__') sku,
      coalesce(l.delivery_schema,p.scheme,'UNKNOWN') scheme,
      max(l.article) article,max(l.product_name) name,
      sum(l.amount) FILTER(WHERE line_kind='revenue') revenue,
      sum(CASE WHEN l.amount<0 THEN -abs(l.quantity) ELSE l.quantity END) FILTER(WHERE line_kind='revenue') units,
      count(*) FILTER(WHERE line_kind='revenue' AND l.quantity<>trunc(l.quantity)) fractional_quantity_rows,
      sum(l.amount) net, count(*) source_rows,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='commission'),0) commission_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='acquiring'),0) acquiring_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='logistics'),0) logistics_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='reverse_logistics'),0) reverse_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='storage'),0) storage_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='advertising'),0) advertising_cost,
      -coalesce(sum(l.amount) FILTER(WHERE line_kind='fulfillment_ozon'),0) fulfillment_cost,
      min(l.operation_date) first_date,max(l.operation_date) last_date,
      jsonb_object_agg(l.line_kind,0) kinds,
      count(*) FILTER(WHERE allocation_scope<>'exact') allocated_rows
    FROM ozon_finance_lines l LEFT JOIN posting_scheme p USING(posting_number)
    WHERE l.operation_date BETWEEN %s AND %s GROUP BY 1,2''', (start,end))
    rows = [dict(r, marketplace='ozon',cabinet=client,source='ozon_finance_lines') for r in cur.fetchall()]
    if totals_only:
        for r in rows:
            if r.get('fractional_quantity_rows'):r['units']=None
        return rows, []
    cur.execute('''SELECT coalesce(nullif(sku,''),'__unallocated__') sku,
      coalesce(delivery_schema,'UNKNOWN') scheme,line_kind,sum(amount) amount
      FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s GROUP BY 1,2,3''',(start,end))
    # Keep original scheme breakdown for the reconciliation export.
    breakdown = [dict(r) for r in cur.fetchall()]
    cur.execute("""WITH posting_scheme AS (
      SELECT posting_number,min(delivery_schema) scheme FROM ozon_finance_lines
      WHERE nullif(posting_number,'') IS NOT NULL GROUP BY posting_number HAVING count(DISTINCT delivery_schema)=1)
    SELECT coalesce(nullif(l.sku,''),'__unallocated__') sku,coalesce(l.delivery_schema,p.scheme,'UNKNOWN') scheme,
      l.line_kind,coalesce(nullif(l.type_name,''),l.line_kind) article_name,-sum(l.amount) amount
    FROM ozon_finance_lines l LEFT JOIN posting_scheme p USING(posting_number)
    WHERE l.operation_date BETWEEN %s AND %s AND l.line_kind<>'revenue'
    GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""",(start,end))
    article_rows=defaultdict(list)
    components={'commission':'commission','acquiring':'acquiring','logistics':'logistics','reverse_logistics':'reverse',
                'storage':'storage','advertising':'advertising','fulfillment_ozon':'fulfillment'}
    for a in cur.fetchall():
        article_rows[(a['sku'],a['scheme'])].append({'name':a['article_name'],'component':components.get(a['line_kind'],'marketOther'),'amount':a['amount'],'source_field':a['line_kind']})
    for r in rows:r['report_articles']=article_rows[(r['sku'],r['scheme'])]

    cur.execute('''SELECT sku,artikul,nazvanie_tovara,barcodes_json,shtrihkod_seriynyy_nomer_ean,dlina_upakovki_mm,shirina_upakovki_mm,vysota_upakovki_mm,ves_v_upakovke_g
      FROM ozon_cat_products WHERE nullif(sku,'') IS NOT NULL''')
    catalog = {}; metadata = {}; dimensions = {}
    for r in cur.fetchall():
        catalog.setdefault(str(r['sku']),set()).update(barcode_list(r['barcodes_json']) + barcode_list(r['shtrihkod_seriynyy_nomer_ean']))
        metadata[str(r['sku'])] = (r['artikul'],r['nazvanie_tovara'])
        dimensions[str(r['sku'])] = {k:r.get(f) for k,f in [('length_mm','dlina_upakovki_mm'),('width_mm','shirina_upakovki_mm'),('height_mm','vysota_upakovki_mm'),('weight_g','ves_v_upakovke_g')]} 
    cur.execute('''SELECT DISTINCT ON(sku) sku,offer_id,price,marketing_seller_price,snapshot_date,
      sales_percent_fbo,sales_percent_fbs,sales_percent_rfbs,
      fbo_logistics_min,fbo_logistics_max,fbo_return_amount,
      fbs_logistics_min,fbs_logistics_max,fbs_return_amount,acquiring
      FROM ozon_product_price_snapshots ORDER BY sku,snapshot_date DESC''')
    prices = {str(r['sku']):dict(r) for r in cur.fetchall()}
    prices_by_offer={}
    for p in prices.values():
        offer=p.get('offer_id')
        if offer and (offer not in prices_by_offer or p['snapshot_date']>prices_by_offer[offer]['snapshot_date']):
            prices_by_offer[offer]=p
    for r in rows:
        r['barcodes'] = sorted(catalog.get(r['sku'],set()))
        r['dimensions'] = dimensions.get(r['sku'])
        article,name=metadata.get(r['sku'],(None,None))
        r['article']=r['article'] or article
        r['name']=r['name'] or name
        r['tariff_snapshot'] = prices.get(r['sku']) or prices_by_offer.get(r['article'])
        r['source_warnings'] = ['Начисления требуют сверки с закрывающим отчётом; комиссии Select сохраняются отдельно.']
        if r['scheme']=='UNKNOWN': r['source_warnings'].append('Часть операций не привязана к схеме доставки')
        if r['allocated_rows']: r['source_warnings'].append('Есть распределённые или общие операции')
        if r['fractional_quantity_rows']:
            r['units']=None
            r['source_warnings'].append('Количество в импорте рассчитано через суммы и цены, есть дробные значения; физические единицы не подтверждены')
    return rows, breakdown


def _wb(cur,start,end,client,totals_only=False):
    # Sale sign is meaningful only on actual sale/return operations, not on PVZ rewards.
    cur.execute('''WITH b AS (SELECT *,
      CASE WHEN lower(seller_oper_name) IN ('возврат','return') THEN -1
           WHEN lower(seller_oper_name) IN ('продажа','sale') THEN 1 ELSE 0 END sign,
      coalesce(nullif(raw_payload->>'deliveryMethod',''),'UNKNOWN') scheme
      FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s)
    SELECT coalesce(nullif(nm_id::text,'0'),'__unallocated__') sku,
      CASE WHEN sku ~ '^[0-9]{8,14}$' THEN sku ELSE '' END barcode,scheme,max(vendor_code) article,max(title) name,
      sum(retail_amount*sign) revenue,sum(quantity*sign) units,
      sum(for_pay*sign-delivery_service-paid_storage-paid_acceptance-deduction-penalty
          +additional_payment-cashback_amount-rebill_logistic_cost
          +coalesce(nullif(coalesce(raw_payload->>'cashbackDiscount',raw_payload->>'cashback_discount'),'')::numeric,0)
            *CASE WHEN lower(coalesce(raw_payload->>'docTypeName',raw_payload->>'doc_type_name')) IN ('возврат','return') THEN -1 ELSE 1 END) net,
      count(*) source_rows,min(operation_date) first_date,max(operation_date) last_date,
      sum(delivery_service) delivery,sum(paid_storage) storage,sum(deduction+penalty) deductions,
      sum((retail_amount-for_pay-acquiring_fee)*sign) commission_cost,
      sum(acquiring_fee*sign) acquiring_cost,sum(delivery_service) logistics_cost,
      sum(paid_storage) storage_cost,sum(paid_acceptance) fulfillment_cost,
      sum(deduction) deduction_cost,sum(penalty) penalty_cost,sum(rebill_logistic_cost) rebill_cost,
      sum(cashback_amount) cashback_cost,-sum(additional_payment) additional_cost,
      -sum(coalesce(nullif(coalesce(raw_payload->>'cashbackDiscount',raw_payload->>'cashback_discount'),'')::numeric,0)
        *CASE WHEN lower(coalesce(raw_payload->>'docTypeName',raw_payload->>'doc_type_name')) IN ('возврат','return') THEN -1 ELSE 1 END) cashback_discount_cost,
      count(*) FILTER(WHERE sign=0 AND for_pay<>0) unclassified_payout_rows
    FROM b GROUP BY 1,2,3''',(start,end))
    rows=[]
    for src in cur.fetchall():
        r=dict(src,marketplace='wb',cabinet=client,source='wb_finance_lines')
        r['report_articles']=[{'name':name,'component':component,'amount':r.get(field) or 0,'source_field':field} for name,component,field in [
          ('Вознаграждение WB','commission','commission_cost'),('Эквайринг','acquiring','acquiring_cost'),
          ('Логистика','logistics','logistics_cost'),('Хранение','storage','storage_cost'),('Платная приёмка','fulfillment','fulfillment_cost'),
          ('Удержания','marketOther','deduction_cost'),('Штрафы','marketOther','penalty_cost'),
          ('Возмещение издержек по перевозке','marketOther','rebill_cost'),('Участие в программе лояльности','marketOther','cashback_cost'),
          ('Доплаты','marketOther','additional_cost'),('Компенсация скидки по программе лояльности','marketOther','cashback_discount_cost')]]
        r['barcodes']=barcode_list(r.pop('barcode'))
        # WB size EAN belongs in the identity: nmID alone would collapse variants.
        r['variant']=r['barcodes'][0] if r['barcodes'] else 'unknown'
        r['source_warnings']=['Реализация WB: цена покупателя и база комиссии различаются; нет автоматического тарифного прогноза.',
                              'Сверка сторно, компенсаций и рекламных расходов с отчётом ещё не принята.']
        if r['unclassified_payout_rows']:r['source_warnings'].append('Есть выплаты с операцией вне продажи/возврата')
        rows.append(r)
    if totals_only:return rows
    # Latest WB Content card belongs to this exact client database and nmID.
    cur.execute("""SELECT DISTINCT ON (coalesce(payload->>'nmID',payload->>'nmId'))
      coalesce(payload->>'nmID',payload->>'nmId') sku,payload->'dimensions' dimensions,captured_at
      FROM wb_api_entities WHERE source_key='content.cards'
      ORDER BY coalesce(payload->>'nmID',payload->>'nmId'),captured_at DESC,entity_key DESC""")
    cards={str(c['sku']):c for c in cur.fetchall()}
    for r in rows:
        card=cards.get(r['sku']);d=(card or {}).get('dimensions') or {}
        def positive(value,factor):
            try:
                value=float(value)
                return value*factor if math.isfinite(value) and value>0 else None
            except (TypeError,ValueError):return None
        r['dimensions']={
            'length_mm':positive(d.get('length'),10),'width_mm':positive(d.get('width'),10),
            'height_mm':positive(d.get('height'),10),'weight_g':positive(d.get('weightBrutto'),1000)
        } if d else None
        # WB's isValid is a category-anomaly warning, not absence of dimensions.
        # Preserve the declared measurements and expose the warning separately.
        r['dimensions_warning']='WB: габариты отличаются от типичных для категории; требуется проверка' if d.get('isValid') is False else None
        if r['dimensions_warning']:r['source_warnings'].append(r['dimensions_warning'])
        r['dimensions_source']='WB Content card' if card else None
        r['dimensions_date']=card['captured_at'].isoformat() if card and card['captured_at'] else None
    return rows


def _yandex(cur,start,end,client,totals_only=False):
    cur.execute('SELECT campaign_id,store_name,placement_type FROM yandex_dim_store WHERE client_key=%s',(client,))
    stores={r['campaign_id']:dict(r) for r in cur.fetchall()}
    cur.execute('''WITH flows AS (
      SELECT campaign_id,offer_id sku,
        CASE WHEN event_type='returned' THEN -amount ELSE amount END revenue,
        CASE WHEN event_type='returned' THEN -units ELSE units END units,
        CASE WHEN event_type='returned' THEN -amount ELSE amount END net,event_date dt,
        (amount IS NULL)::int missing_amount,'realization' source,'revenue' kind
      FROM yandex_fact_realization WHERE client_key=%s AND event_date BETWEEN %s AND %s
      UNION ALL
      SELECT campaign_id,offer_id,NULL,NULL,-service_amount,coalesce(service_date,act_date),
        (service_amount IS NULL)::int,'services',service_type
      FROM yandex_fact_services WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN %s AND %s)
    SELECT coalesce(campaign_id,'__unallocated__') cabinet,
      coalesce(nullif(sku,''),'__unallocated__') sku,sum(revenue) revenue,sum(units) units,sum(net) net,
      count(*) source_rows,sum(missing_amount) missing_amount,min(dt) first_date,max(dt) last_date
    FROM flows GROUP BY 1,2''',(client,start,end,client,start,end))
    rows=[]
    for src in cur.fetchall():
        r=dict(src,marketplace='yandex',source='yandex_fact_realization + yandex_fact_services')
        s=stores.get(r['cabinet'],{})
        r.update(scheme=s.get('placement_type','UNKNOWN'),cabinet_label=s.get('store_name',r['cabinet']),
                 article=r['sku'],name='',barcodes=[],source_warnings=[
                     'Услуги без SKU/магазина вынесены отдельно; прибыль до распределения не подтверждена.',
                     'Полнота дат услуг, компенсаций и закрывающих документов требует сверки.'])
        rows.append(r)
    cur.execute('''SELECT coalesce(campaign_id,'__unallocated__') cabinet,
      coalesce(nullif(offer_id,''),'__unallocated__') sku,service_type,service_name,
      sum(service_amount) amount,count(*) source_rows,
      count(*) FILTER(WHERE service_amount IS NULL) missing_amount
      FROM yandex_fact_services WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN %s AND %s
      GROUP BY 1,2,3,4 ORDER BY 1,2,3,4''',(client,start,end))
    apply_service_components(rows,cur.fetchall())
    # A business report may be downloaded under several campaigns. Deduplicate
    # economic events before attribution; the download campaign is not a sale cabinet.
    cur.execute('''WITH raw AS (
      SELECT business_id,payload->>'orderId' order_id,payload->>'yourSku' sku,
        ya_date(payload->>'compensationDate') dt,ya_number(payload->>'compensationAmount') amount,'compensation' kind
      FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items'
      UNION ALL
      SELECT business_id,payload->>'orderId',payload->>'yourSku',
        ya_date(payload->>'decompensationDate'),-ya_number(payload->>'decompensationAmount'),'reversal'
      FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items'),
    events AS (SELECT business_id,order_id,sku,dt,kind,
      CASE WHEN count(DISTINCT amount)=1 THEN min(amount) END amount,
      count(DISTINCT amount)>1 conflict FROM raw WHERE dt BETWEEN %s AND %s
      GROUP BY 1,2,3,4,5),
    attribution AS (SELECT business_id,order_id,offer_id,
      CASE WHEN count(DISTINCT campaign_id)=1 THEN min(campaign_id) END cabinet
      FROM yandex_fact_realization WHERE client_key=%s GROUP BY 1,2,3)
    SELECT coalesce(a.cabinet,'__unallocated__') cabinet,coalesce(e.sku,'__unallocated__') sku,
      sum(e.amount) net,sum(e.amount) FILTER(WHERE kind='compensation') compensation,
      sum(e.amount) FILTER(WHERE kind='reversal') compensation_reversal,
      count(*) source_rows,count(*) FILTER(WHERE conflict) conflicts,
      count(*) FILTER(WHERE e.amount IS NULL) missing_amount,min(dt) first_date,max(dt) last_date
    FROM events e LEFT JOIN attribution a ON a.business_id=e.business_id AND a.order_id=e.order_id AND a.offer_id=e.sku
    GROUP BY 1,2''',(client,client,start,end,client))
    existing={(r['cabinet'],r['sku']):r for r in rows}
    for event in cur.fetchall():
        identity=(event['cabinet'],event['sku'])
        if identity not in existing:
            s=stores.get(event['cabinet'],{})
            r=dict(marketplace='yandex',source='yandex lost_items deduplicated economic events',
                cabinet=event['cabinet'],sku=event['sku'],article=event['sku'],name='',barcodes=[],
                scheme=s.get('placement_type','UNKNOWN'),cabinet_label=s.get('store_name','Без подтверждённого магазина'),
                revenue=None,units=None,net=0,source_rows=0,first_date=event['first_date'],last_date=event['last_date'],source_warnings=[])
            existing[identity]=r;rows.append(r)
        r=existing[identity]
        r['net']=(r['net'] or 0)+(event['net'] or 0)
        r['source_rows']+=event['source_rows']
        r['missing_amount']=(r.get('missing_amount') or 0)+event['missing_amount']
        r['compensation']=event['compensation'];r['compensation_reversal']=event['compensation_reversal']
        r['first_date']=min(r['first_date'],event['first_date']);r['last_date']=max(r['last_date'],event['last_date'])
        r['source_warnings'].append('Компенсации утрат и сторно учитываются отдельно, повторы отчётов магазинов удалены.')
        if event['conflicts']:r['source_warnings'].append('Конфликт сумм компенсации: спорные события исключены из суммы.')
        if event['missing_amount']:r['source_warnings'].append('Есть неизвестная или конфликтная сумма компенсации: доходность не определяется до сверки.')
    if totals_only:return rows,list(stores.values())
    # Exact campaign + offer identity; latest completed catalogue supplies cm/kg.
    cur.execute('''SELECT DISTINCT ON(c->>'campaignId',r.payload->'offer'->>'offerId')
      c->>'campaignId' cabinet,r.payload->'offer'->>'offerId' sku,
      r.payload->'offer'->'weightDimensions' dimensions,j.finished_at
      FROM yandex_analytics_raw r JOIN yandex_analytics_jobs j USING(job_key)
      CROSS JOIN LATERAL jsonb_array_elements(coalesce(r.payload->'offer'->'campaigns','[]'::jsonb)) c
      WHERE j.client_key=%s AND j.source_key='catalog' AND j.state='completed'
        AND r.payload->'offer'->>'offerId'=ANY(%s)
        AND c->>'campaignId'=ANY(%s)
      ORDER BY c->>'campaignId',r.payload->'offer'->>'offerId',j.finished_at DESC,r.row_no DESC''',
      (client,sorted({r['sku'] for r in rows}),sorted({str(r['cabinet']) for r in rows})))
    sizes={(d['cabinet'],d['sku']):d for d in cur.fetchall()}
    for r in rows:
        source=sizes.get((str(r['cabinet']),r['sku']))
        d=(source or {}).get('dimensions') or {}
        def converted(key,multiplier):
            try:
                n=float(d.get(key));return n*multiplier if math.isfinite(n) and n>0 else None
            except (ValueError,TypeError):return None
        r['dimensions']={key:converted(field,multiplier) for key,field,multiplier in
            [('length_mm','length',10),('width_mm','width',10),('height_mm','height',10),('weight_g','weight',1000)]} if source else None
        r['dimensions_source']='Yandex offer.weightDimensions' if source else None
        r['dimensions_date']=source['finished_at'].isoformat() if source and source['finished_at'] else None
    return rows, list(stores.values())


def _wb_storage_totals(cur, start, end, client):
    # Storage history only consumes units and storage. Preserve the exact WB
    # grouping and sale/return signs, without calculating the detailed ledger.
    cur.execute('''SELECT coalesce(nullif(nm_id::text,'0'),'__unallocated__') sku,
      CASE WHEN sku ~ '^[0-9]{8,14}$' THEN sku ELSE '' END barcode,
      coalesce(nullif(raw_payload->>'deliveryMethod',''),'UNKNOWN') scheme,
      sum(quantity * CASE WHEN lower(seller_oper_name) IN ('возврат','return') THEN -1
        WHEN lower(seller_oper_name) IN ('продажа','sale') THEN 1 ELSE 0 END) units,
      sum(paid_storage) storage_cost
      FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s GROUP BY 1,2,3''',(start,end))
    return [dict(r,marketplace='wb',cabinet=client) for r in cur.fetchall()]


def storage_history(cur, start, client, marketplace=''):
    """Account-wide historical reserve. Never mix cabinets or infer missing storage as zero."""
    month_index = start.year * 12 + start.month - 1
    periods = []
    totals = {}
    for offset in (3, 2, 1):
        idx = month_index - offset
        first = date(idx // 12, idx % 12 + 1, 1)
        following = date((idx + 1) // 12, (idx + 1) % 12 + 1, 1)
        last = date.fromordinal(following.toordinal() - 1)
        periods.append((first, last))
        ozon = _ozon(cur, first, last, client, totals_only=True)[0] if marketplace in ('', 'ozon') else []
        ya = _yandex(cur, first, last, client, totals_only=True)[0] if marketplace in ('', 'yandex') else []
        wb = _wb_storage_totals(cur, first, last, client) if marketplace in ('', 'wb') else []
        monthly = {}
        for row in ozon + wb + ya:
            key = (row['marketplace'], row['cabinet'])
            entry = monthly.setdefault(key, {'expense': 0., 'units': 0.})
            entry['expense'] += float(row.get('storage_cost') or 0)
            entry['units'] += float(row.get('units') or 0)
        for key, entry in monthly.items():
            totals.setdefault(key, []).append(dict(entry, month=first.isoformat()[:7]))
    result = []
    for (marketplace, cabinet), months in totals.items():
        expense = sum(m['expense'] for m in months)
        units = sum(m['units'] for m in months)
        complete = len(months) == 3 and units > 0 and expense > 0
        result.append(dict(marketplace=marketplace, cabinet=cabinet, months=months,
            expense=expense, units=units, per_unit=expense / units if complete else None,
            status='estimate' if complete else 'missing',
            note='Сумма хранения кабинета / продажи за вычетом возвратов. Нулевое хранение без отдельного подтверждения полноты источника не считается бесплатным.'))
    # Summary reports can confirm zero storage even when detailed lines were not imported.
    cur.execute("SELECT payload FROM wb_api_entities WHERE source_key='finance.sales_reports'")
    covered=set(); zero_reports=True; reports_seen=0
    first,last=periods[0][0],periods[-1][1]
    for record in cur.fetchall():
        p=record['payload']
        try:
            a=max(first,date.fromisoformat(p['dateFrom'][:10]));b=min(last,date.fromisoformat(p['dateTo'][:10]))
            if a>b:continue
            amount=float(p['paidStorageSum'])
        except (KeyError,TypeError,ValueError):continue
        reports_seen+=1
        zero_reports=zero_reports and amount==0
        covered.update(range(a.toordinal(),b.toordinal()+1))
    if marketplace in ('', 'wb') and reports_seen and zero_reports and len(covered)==(last-first).days+1:
        result=[r for r in result if not(r['marketplace']=='wb' and r['cabinet']==client)]
        result.append(dict(marketplace='wb',cabinet=client,months=[],expense=0,units=None,per_unit=0,
            status='confirmed_zero',note='Сводные финансовые отчёты WB полностью покрывают период и явно содержат 0 ₽ хранения.'))
    return dict(date_from=periods[0][0].isoformat(), date_to=periods[-1][1].isoformat(), accounts=result)

def workspace(config,client,start=None,end=None,catalog_mode=False,query="",marketplace="",cabinet=""):
    today=date.today(); default_end=today.replace(day=1).toordinal()-1
    end=date.fromisoformat(end) if end else date.fromordinal(default_end)
    start=date.fromisoformat(start) if start else end.replace(day=1)
    if start>end or (end-start).days>366:raise ValueError('Период должен быть от 1 до 367 дней')
    marketplace={'yandex_market':'yandex','wildberries':'wb'}.get(marketplace,marketplace)
    if marketplace not in ('', 'ozon', 'wb', 'yandex'): raise ValueError('Неизвестная площадка')
    with connect_km(checked_config(config,client)) as conn,conn.cursor() as cur:
        storage_baseline = storage_history(cur, start, client, marketplace)
        rows=[];breakdown={};stores=[]
        if marketplace in ('', 'ozon'):
            rows,breakdown=_ozon(cur,start,end,client)
        if marketplace in ('', 'wb'):
            rows+=_wb(cur,start,end,client)
        ya=[]
        if marketplace in ('', 'yandex'):
            ya,stores=_yandex(cur,start,end,client)
        article_barcodes=defaultdict(set)
        for r in rows:
            if r.get('article'):
                article_barcodes[r['article']].update(r['barcodes'])
        for r in ya:
            candidates=article_barcodes.get(r['sku'],set())
            if len(candidates)==1:
                r['barcodes']=sorted(candidates)
                r['barcode_source']='exact seller article, unique barcode in same client catalogue'
        rows+=ya
        # Yandex offers do not carry barcodes. Preserve the existing exact
        # cross-market article match there; other scoped views only need their
        # own catalogue.
        cost_catalog=planning_catalog(cur,client,stores,'' if marketplace=='yandex' else marketplace)
        if marketplace == 'yandex':
            for item in cost_catalog:
                if item.get('marketplace') != 'yandex' and item.get('article'):
                    article_barcodes[item['article']].update(item.get('barcodes') or [])
            for r in ya:
                candidates=article_barcodes.get(r['sku'],set())
                if len(candidates)==1:
                    r['barcodes']=sorted(candidates)
                    r['barcode_source']='exact seller article, unique barcode in same client catalogue'
        catalog=cost_catalog if catalog_mode else []
        catalog_total=len(catalog)
        # Catalogue identities are separate from finance rows, including products
        # with no operations. Never manufacture zero sales for such products.
        for r in catalog:
            if not r['barcodes']:
                candidates=article_barcodes.get(r.get('article'),set())
                if len(candidates)==1:r['barcodes']=sorted(candidates)
        if catalog_mode:
            catalog=[r for r in catalog if (not marketplace or r["marketplace"]==marketplace) and (not cabinet or r["cabinet"]==cabinet) and query.casefold() in (str(r["sku"])+" "+str(r.get("article"))+" "+str(r.get("name"))+" "+" ".join(r["barcodes"])).casefold()]
            catalog_total=len(catalog);catalog=catalog[:250]
        cur.execute('''SELECT barcode,amount,valid_from,source_ref
          FROM unit_cogs_versions WHERE valid_from<=%s ORDER BY barcode,valid_from DESC,created_at DESC''',(today,))
        from unit_cost_basis import catalog_cost_resolver
        resolve_cost,cogs=catalog_cost_resolver(cost_catalog,[dict(r) for r in cur.fetchall()],today)
        from unit_cost_basis import wb_cost_catalog
        resolve_wb_cost,_=catalog_cost_resolver(wb_cost_catalog(cur),list(cogs.values()),today)
        cur.execute('''SELECT DISTINCT ON(scenario_key) * FROM unit_scenario_versions
          ORDER BY scenario_key,revision DESC''')
        scenarios=[dict(r) for r in cur.fetchall()]
        cur.execute('SELECT * FROM unit_portfolio_versions ORDER BY created_at DESC LIMIT 50')
        portfolios=[dict(r,id=str(r['id']),created_at=str(r['created_at'])) for r in cur.fetchall()]
        hypotheses=unit_hypotheses.list_projects(cur)
        order_activity=unit_order_activity.load(cur,client,start,end,marketplace)
        metadata={(r['marketplace'],str(r['cabinet']),str(r['sku'])):r for r in order_activity}
        for r in rows:
            meta=metadata.get((r['marketplace'],str(r['cabinet']),str(r['sku'])),{})
            r['brand']=meta.get('brand');r['category']=meta.get('category')
        for s in scenarios:
            s['id']=str(s['id']);s['created_at']=str(s['created_at']);s['result']=calculate(s['inputs'])
        from unit_current_stocks import attach as attach_current_stocks
        attach_current_stocks(cur,client,rows+catalog,marketplace)
        for r in rows+catalog:
            if not r['barcodes'] and r['sku']!='__unallocated__':
                resolver=resolve_wb_cost if r['marketplace']=='wb' else resolve_cost
                cost,match_source,matched_barcodes=resolver(r['marketplace'],r['sku'],r.get('article'))
                if cost is not None:
                    r['barcodes']=matched_barcodes;r['cogs_match_source']=match_source
            identity=r['sku'] + ('@'+r['variant'] if r.get('variant') else '')
            r['key']=key_for(r['marketplace'],r['cabinet'],identity,r['scheme'])
            matches=[cogs[b] for b in r['barcodes'] if b in cogs]
            values={m['amount'] for m in matches}
            r['cogs']=matches[0] if len(values)==1 and len(matches)==len(r['barcodes']) else None
            r['cogs_status']='sourced' if r['cogs'] else 'conflict' if len(values)>1 else 'missing'
            r['status']='partial'
            r['source_warnings'].append('Факт не равен сценарию; итоговая прибыль до независимой сверки не подтверждена.')
            r['average_price']=r['revenue']/r['units'] if r['revenue'] is not None and r['units'] and r['units']>0 else None
            for k in ('first_date','last_date'):r[k]=str(r[k]) if r.get(k) else None
            if r.get('tariff_snapshot'):
                r['tariff_snapshot']['snapshot_date']=str(r['tariff_snapshot']['snapshot_date'])
            if r['cogs']:r['cogs']['valid_from']=str(r['cogs']['valid_from'])
            r['cogs_matches']=[{**m,'valid_from':str(m['valid_from'])} for m in matches]
            # A source cost dated after the operations cannot establish historic COGS.
            r['historical_cogs_covered']=bool(r['cogs'] and r.get('first_date') and all(str(m['valid_from'])<=r['first_date'] for m in matches))
            r['contribution_actual']=dated_contribution(r)
        if catalog_mode:
            return json_numbers({'ok':True,'client':client,'catalog':catalog,'total':catalog_total,'limit':250})
        rows.sort(key=lambda r: (r['sku']=='__unallocated__',-(r['revenue'] or 0),r['key']))
        observed_sql=[];observed_params=[]
        if marketplace in ('', 'ozon'):
            observed_sql.append("SELECT 'ozon' marketplace,operation_date dt FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s")
            observed_params.extend((start,end))
        if marketplace in ('', 'wb'):
            observed_sql.append("SELECT 'wb' marketplace,operation_date dt FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s")
            observed_params.extend((start,end))
        if marketplace in ('', 'yandex'):
            observed_sql.append("SELECT 'yandex' marketplace,event_date dt FROM yandex_fact_realization WHERE client_key=%s AND event_date BETWEEN %s AND %s")
            observed_params.extend((client,start,end))
        cur.execute(' UNION '.join(observed_sql),tuple(observed_params))
        observed=defaultdict(set)
        for item in cur.fetchall():observed[item['marketplace']].add(str(item['dt']))
        summary=[]
        for mp in ((marketplace,) if marketplace else ('ozon','wb','yandex')):
            group=[r for r in rows if r['marketplace']==mp]
            summary.append({'marketplace':mp,'rows':len(group),'source_rows':sum(r['source_rows'] for r in group),
                'revenue':sum(r['revenue'] or 0 for r in group) if group else None,
                'net':sum(r['net'] or 0 for r in group) if group else None,
                'cogs_rows':sum(r['cogs_status']=='sourced' for r in group),
                'unallocated_net':sum(r['net'] or 0 for r in group if r['sku']=='__unallocated__' or r['cabinet']=='__unallocated__'),
                'status':'partial'})
            summary[-1]['observed_days']=len(observed[mp])
            summary[-1]['period_days']=(end-start).days+1
            summary[-1]['operation_dates']=sorted(observed[mp])
    products=allocated_products(rows)
    allocation=allocation_summary(rows,products)
    return json_numbers({'ok':True,'client':client,'date_from':str(start),'date_to':str(end),'rows':rows,
        'allocated_products':products,'allocation':allocation,'allocation_version':'product-costs-2026-09-13.1',
        'summary':summary,'stores':stores,'scenarios':scenarios,'portfolios':portfolios,'hypotheses':hypotheses,'order_activity':order_activity,'catalog':catalog,'catalog_total':catalog_total,'storage_history':storage_baseline,'cogs_as_of':str(today),
        'fields':{k:{'label':v[0],'default':v[1]} for k,v in FIELDS.items()},
        'status':'partial','notice':'Начисления доступны. Прибыль и выбор выгодной площадки требуют подтверждённой себестоимости, тарифов, налогов и сверки полноты расходов.',
        'generated_at':datetime.now(timezone.utc).isoformat()})


def planning_catalog(cur,client,stores,marketplace=''):
    rows=[]
    if marketplace in ('', 'ozon'):
        cur.execute('''SELECT DISTINCT ON(sku) sku,artikul article,nazvanie_tovara name,
          barcodes_json,shtrihkod_seriynyy_nomer_ean ean FROM ozon_cat_products
          WHERE nullif(sku,'') IS NOT NULL ORDER BY sku''')
        for p in cur.fetchall():
            rows.append(dict(marketplace='ozon',cabinet=client,sku=str(p['sku']),article=p['article'],name=p['name'],
              scheme='UNKNOWN',barcodes=sorted(set(barcode_list(p['barcodes_json'])+barcode_list(p['ean'])))))
    if marketplace in ('', 'wb'):
        cur.execute('''SELECT artikul_wb sku,artikul_prodavtsa article,naimenovanie name,barkod
          FROM products WHERE nullif(artikul_wb::text,'') IS NOT NULL''')
        for p in cur.fetchall():
            for b in barcode_list(p['barkod']) or ['']:
                rows.append(dict(marketplace='wb',cabinet=client,sku=str(p['sku']),article=p['article'],name=p['name'],
                  scheme='UNKNOWN',barcodes=[b] if b else [],variant=b or 'unknown'))
    if marketplace in ('', 'yandex'):
        # Deduplicate catalogue versions before joining cabinets. The former
        # join multiplied historical offer versions, only to discard them later.
        cur.execute('''WITH offers AS MATERIALIZED (
          SELECT DISTINCT business_id,offer_id,offer_name FROM yandex_dim_offer
          WHERE client_key=%s AND coalesce(archived,false)=false),
        cabinets AS MATERIALIZED (
          SELECT DISTINCT business_id,campaign_id,placement_type,store_name
          FROM yandex_dim_store WHERE client_key=%s)
        SELECT DISTINCT o.offer_id,o.offer_name,s.campaign_id,s.placement_type,s.store_name
          FROM offers o JOIN cabinets s USING(business_id)''',(client,client))
        for p in cur.fetchall():
            rows.append(dict(marketplace='yandex',cabinet=p['campaign_id'],cabinet_label=p['store_name'],sku=p['offer_id'],
              article=p['offer_id'],name=p['offer_name'],scheme=p['placement_type'] or 'UNKNOWN',barcodes=[]))
    unique={}
    for r in rows:
        identity=(r['marketplace'],r['cabinet'],r['sku'],r.get('variant',''))
        r.update(source='client scoped planning catalogue',revenue=None,units=None,net=None,source_rows=None,
          source_warnings=['Товар каталога для планирования. Наличие листинга и операций на этой схеме не утверждается.'])
        unique[identity]=r
    return list(unique.values())


def save_portfolio(config,client,payload):
    """Append-only account-wide scenario assumptions; facts are never overwritten."""
    settings=payload.get('settings');name=str(payload.get('name') or '').strip()
    period=payload.get('source_period') or {};version=str(payload.get('model_version') or '')
    if not name or len(name)>100 or not isinstance(settings,dict):raise ValueError('Нужны название и параметры сценария')
    if version not in ('portfolio-2026-09-12.4','portfolio-2026-09-12.5','portfolio-2026-09-12.6'):raise ValueError('Неподдерживаемая версия модели')
    if len(json.dumps(settings))>2000000:raise ValueError('Слишком большой сценарий')
    begin=date.fromisoformat(str(period.get('from')));end=date.fromisoformat(str(period.get('to')))
    if begin>end or (end-begin).days>366:raise ValueError('Некорректный период источника')
    allowed=set('mrcMargin rrcMargin targetPrice priceChange cogsOverride cogsChange commissionOverride commissionChange acquiringChange logisticsChange reverseChange fulfillmentChange storageChange adChange packaging inbound externalFulfillment externalDelivery other taxReserve baseBuyout buyout baseReturn returns reversePerEvent discount platformDiscount uplift promoAdChange yandexQuoteMode yandexFeeCount yandexAcceptCount yandexTransferCount yandexDeliveryCount yandexMiddleCount yandexPaymentFrequency yandexPaymentDelay'.split())
    allowed.update('incomeTaxPct taxMode minimumTaxPct taxNondeductible vatPct inputVat inputVatVariablePct cogsVatPct capitalPct capitalDays capitalBase adOverride acquiringOverride eventCostMode forwardPerShipment handlingPerShipment rejectDelivery returnDelivery packagingPerShipment lossPct commissionRefund acquiringRefund'.split())
    def check(values):
        if not isinstance(values,dict) or not set(values)<=allowed:raise ValueError('Некорректные параметры сценария')
        for value in values.values():
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not decimal(value).is_finite()):
                raise ValueError('Параметры должны быть конечными числами или неизвестными значениями')
        if 'yandexQuoteMode' in values and values['yandexQuoteMode'] not in (0,1):raise ValueError('Неизвестный режим тарифа')
        if 'yandexPaymentFrequency' in values and values['yandexPaymentFrequency'] not in (0,1,2,3,4):raise ValueError('Неизвестный график выплат')
        if values.get('yandexPaymentDelay') is not None and values['yandexPaymentDelay'] not in (0,1,2,4):raise ValueError('Неизвестная отсрочка выплат')
        for key in ('yandexFeeCount','yandexAcceptCount','yandexTransferCount','yandexDeliveryCount','yandexMiddleCount'):
            if values.get(key) is not None and not 0<=values[key]<=1000:raise ValueError('Число начислений должно быть от 0 до 1000')
    check(settings.get('global'))
    for group in ('markets','products'):
        if not isinstance(settings.get(group),dict):raise ValueError('Некорректная область сценария')
        for key,values in settings[group].items():
            if not isinstance(key,str) or len(key)>300:raise ValueError('Некорректный идентификатор товара')
            if group=='markets' and key not in ('wb','ozon','yandex'):raise ValueError('Некорректная площадка')
            if group=='products' and not re.match(r'^(wb|ozon|yandex)\|[^|]+\|(barcode:|sku:)',key):raise ValueError('Требуется идентификатор товара новой версии')
            check(values)
    settings={**settings,'name':name}
    with connect_km(checked_config(config,client)) as conn,conn.cursor() as cur:
        if payload.get('hypothesis') is not None:
            return unit_hypotheses.save(cur,client,{**payload,'name':name},settings)
        cur.execute('''INSERT INTO unit_portfolio_versions(id,name,settings,source_period,model_version)
          VALUES (%s,%s,%s,%s,%s) RETURNING *''',(str(uuid4()),name,Json(settings),Json(period),version))
        row=cur.fetchone()
    return dict(row,ok=True,id=str(row['id']),created_at=str(row['created_at']))


def save_scenario(config,client,payload):
    inputs=payload.get('inputs') or {};validate(inputs)
    name=str(payload.get('name') or '').strip()
    if not name or len(name)>100:raise ValueError('Название сценария: от 1 до 100 символов')
    key=str(payload.get('scenario_key') or uuid4())
    fields=[str(payload.get(k) or '') for k in ('marketplace','cabinet','sku','scheme')]
    if fields[0] not in ('ozon','wb','yandex') or not all(fields):raise ValueError('Нужны площадка, кабинет, SKU и схема')
    provenance=payload.get('provenance') or {}
    if not isinstance(provenance,dict):raise ValueError('Укажите источники параметров')
    if len(json.dumps(provenance))>10000:raise ValueError('Описание источников слишком длинное')
    with connect_km(checked_config(config,client)) as conn,conn.cursor() as cur:
        if fields[0]=='yandex':
            cur.execute('SELECT 1 FROM yandex_dim_store WHERE client_key=%s AND campaign_id=%s',(client,fields[1]))
            if not cur.fetchone():raise ValueError('Магазин не принадлежит клиенту')
        elif fields[1]!=client:raise ValueError('Кабинет не принадлежит клиенту')
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(key,))
        cur.execute('SELECT coalesce(max(revision),0) revision FROM unit_scenario_versions WHERE scenario_key=%s',(key,))
        revision=cur.fetchone()['revision']+1
        expected=int(payload.get('expected_revision',0))
        if expected!=revision-1:raise ValueError('Сценарий изменился. Обновите данные перед сохранением')
        cur.execute('''INSERT INTO unit_scenario_versions
          (id,scenario_key,revision,marketplace,cabinet,sku,scheme,name,inputs,provenance)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
          (str(uuid4()),key,revision,*fields,name,Json(inputs),Json(provenance)))
    return {'ok':True,'scenario_key':key,'revision':revision,'result':calculate(inputs)}


def import_cogs(config,client,payload):
    rows=payload.get('rows') or []
    if not isinstance(rows,list) or not 1<=len(rows)<=50000:raise ValueError('От 1 до 50000 строк себестоимости')
    validated=[];seen={}
    for r in rows:
        barcode=str(r.get('barcode') or '').strip();amount=decimal(r.get('amount'))
        valid=date.fromisoformat(str(r.get('valid_from') or ''))
        source=str(r.get('source_ref') or '').strip()
        if not re.fullmatch(r'\d{8,14}',barcode) or amount is None or amount<0 or not source or len(source)>1000:
            raise ValueError('Нужны штрихкод, неотрицательная себестоимость, дата и источник')
        identity=(barcode,valid)
        if identity in seen and seen[identity]!=amount:raise ValueError('Конфликт себестоимости в файле: '+barcode)
        if identity not in seen:validated.append((str(uuid4()),barcode,valid,amount,source))
        seen[identity]=amount
    with connect_km(checked_config(config,client)) as conn,conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('unit_cogs_import',))
        inserts=[]
        for row in validated:
            cur.execute('SELECT amount FROM unit_cogs_versions WHERE barcode=%s AND valid_from=%s',(row[1],row[2]))
            existing=[r['amount'] for r in cur.fetchall()]
            if any(amount!=row[3] for amount in existing):
                raise ValueError('Конфликт с сохранённой себестоимостью на эту дату: '+row[1])
            if not existing:inserts.append(row)
        cur.executemany('INSERT INTO unit_cogs_versions(id,barcode,valid_from,amount,source_ref) VALUES (%s,%s,%s,%s,%s)',inserts)
    return {'ok':True,'imported':len(inserts),'unchanged':len(validated)-len(inserts)}
