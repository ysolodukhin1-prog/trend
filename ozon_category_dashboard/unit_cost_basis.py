"""Strict common catalogue-to-cost matching; partial price coverage is not uniform."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

def wb_cost_catalog(cur):
    cur.execute("SELECT DISTINCT nm_id::text sku,trim(sku) barcode FROM wb_finance_lines WHERE nm_id>0 AND nullif(trim(sku),'') IS NOT NULL")
    return [dict(marketplace='wb',sku=r['sku'],barcodes=[r['barcode']]) for r in cur.fetchall()]


def catalog_cost_resolver(catalog, versions, as_of=None):
    as_of=str(as_of or date.today());by_barcode=defaultdict(list)
    for v in versions:
        if str(v['valid_from'])<=as_of:by_barcode[str(v['barcode'])].append(v)
    current={}
    for barcode,rows in by_barcode.items():
        latest=max(str(v['valid_from']) for v in rows);eligible=[v for v in rows if str(v['valid_from'])==latest]
        if len({Decimal(str(v['amount'])) for v in eligible})==1:current[barcode]=eligible[0]
    skus=defaultdict(set);articles=defaultdict(set)
    for p in catalog:
        barcodes=set(str(b) for b in p.get('barcodes',[]))
        # An empty identifier cannot contribute a shared match.
        if p.get('sku'):skus[(p['marketplace'],str(p['sku']))].update(barcodes)
        article=str(p.get('article') or '').strip().casefold()
        if article:articles[article].update(barcodes)
    def resolve(market,sku,article):
        candidates=skus.get((market,str(sku)))
        source='Единая цена SKU по всем EAN справочника'
        if not candidates:
            candidates=articles.get(str(article or sku).strip().casefold());source='Точный артикул · единая цена всех EAN'
        if not candidates:return None,'Нет однозначных EAN',[]
        if any(b not in current for b in candidates):return None,'Неполная себестоимость EAN',sorted(candidates)
        prices={Decimal(str(current[b]['amount'])) for b in candidates}
        if len(prices)!=1:return None,'Несколько цен EAN',sorted(candidates)
        return next(iter(prices)),source,sorted(candidates)
    return resolve,current
