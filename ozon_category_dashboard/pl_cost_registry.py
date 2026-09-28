"""Account cost catalogue, independent of finance date filters. No writes here."""
from collections import defaultdict
from datetime import date
from km_trade_finance import connect_km
from unit_economics_workspace import checked_config, planning_catalog


def registry_rows(catalog, versions, as_of):
    products = {}
    for item in catalog:
        for barcode in item.get('barcodes') or ['']:
            key = barcode or '|'.join((item['marketplace'], str(item['sku'])))
            row = products.setdefault(key, dict(id=key, barcode=barcode, articles=set(), names=set(), skus=set(), markets=set()))
            for field, value in [('articles', item.get('article')), ('names', item.get('name')), ('skus', item.get('sku')), ('markets', item['marketplace'])]:
                if value: row[field].add(str(value))
    history = defaultdict(list)
    for version in versions:
        b = str(version['barcode'])
        history[b].append(dict(amount=float(version['amount']), valid_from=str(version['valid_from']), source_ref=version['source_ref']))
        products.setdefault(b, dict(id=b, barcode=b, articles=set(), names=set(), skus=set(), markets=set()))
    result = []
    for row in products.values():
        versions = sorted(history[row['barcode']], key=lambda v: v['valid_from'], reverse=True)
        active = [v for v in versions if v['valid_from'] <= as_of]
        current = active[0] if active else None
        conflict = bool(current and len({v['amount'] for v in active if v['valid_from'] == current['valid_from']}) > 1)
        result.append(dict(id=row['id'], barcode=row['barcode'], article=' / '.join(sorted(row['articles'])),
            name=' / '.join(sorted(row['names'])) or 'Без названия в каталоге', sku=' / '.join(sorted(row['skus'])),
            marketplaces=' / '.join(sorted(row['markets'])), amount=current['amount'] if current and not conflict else None,
            valid_from=current['valid_from'] if current else None,
            status='Конфликт цен' if conflict else 'Задана' if current else 'Нет штрихкода' if not row['barcode'] else 'Без себестоимости',
            history=versions))
    return sorted(result, key=lambda r: (r['article'], r['barcode'], r['id']))


def load_registry(config, client):
    with connect_km(checked_config(config, client)) as conn, conn.cursor() as cur:
        catalog = planning_catalog(cur, client, [])
        # Retain names of priced variants absent from the current card catalogue.
        known = {b for r in catalog for b in r.get('barcodes', [])}
        cur.execute("""SELECT DISTINCT ON(f.sku) f.sku AS barcode,f.nm_id::text AS sku,
            vendor_code AS article,title AS name FROM wb_finance_lines f
            WHERE nullif(sku,'') IS NOT NULL AND nm_id>0
            ORDER BY f.sku,operation_date DESC""")
        for item in cur.fetchall():
            if item['barcode'] not in known:
                catalog.append(dict(item, marketplace='wb', barcodes=[item['barcode']]))
        cur.execute("SELECT to_regclass('public.unit_cogs_versions') AS relation")
        versions = []
        if cur.fetchone()['relation']:
            cur.execute('SELECT barcode,amount,valid_from,source_ref FROM unit_cogs_versions ORDER BY valid_from DESC,created_at DESC')
            versions = cur.fetchall()
    today = date.today().isoformat()
    rows = registry_rows(catalog, versions, today)
    return dict(ok=True, client=client, as_of=today, rows=rows, total=len(rows),
                with_cost=sum(r['amount'] is not None for r in rows))
