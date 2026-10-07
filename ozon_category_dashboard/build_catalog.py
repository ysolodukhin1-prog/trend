"""Build a stable client-local variant crosswalk from PostgreSQL source snapshots."""
import json,re,uuid
from collections import defaultdict,Counter
from datetime import datetime,timezone
import psycopg2
from psycopg2.extras import RealDictCursor,Json,execute_values
import pulse_vps_admin as rt

def barcode(v):
    # Keep leading zeros. GTIN padding is semantic normalization, not int conversion.
    v=str(v or '').strip()
    if len(v) not in (8,12,13,14) or not v.isascii() or not v.isdigit() or not v.strip('0'):return None
    if (sum(int(x)*(3 if i%2==0 else 1) for i,x in enumerate(v[-2::-1]))+int(v[-1]))%10:return None
    return v.zfill(14)

def collect(c,client):
    result=[]
    def add(channel,key,name,article,variant,codes,stamp,parent='',price=None):
        if not key:return
        raw=sorted({str(x).strip() for x in codes if str(x or '').strip()})
        result.append(dict(channel=channel,key=str(key),name=name or '',article=article or '',variant=variant or '',
          barcodes=raw,valid_barcodes=sorted({barcode(x) for x in raw if barcode(x)}),source_at=str(stamp or ''),parent=str(parent or key),price=price))
    c.execute("SELECT DISTINCT ON (payload->>'nmID') payload,captured_at FROM wb_api_entities WHERE source_key='content.cards' ORDER BY payload->>'nmID',captured_at DESC")
    for r in c.fetchall():
        p=r['payload']; nm=str(p.get('nmID') or '')
        for size in p.get('sizes') or [{}]:
            chrt=str(size.get('chrtID') or 'card')
            add('wb',nm+':'+chrt,p.get('title'),p.get('vendorCode'),size.get('techSize'),size.get('skus') or [],r['captured_at'],nm)
    c.execute('SELECT DISTINCT ON (sku) sku,artikul,nazvanie_tovara,barcodes_json,shtrihkod_seriynyy_nomer_ean,coalesce(api_updated_at,updated_at,imported_at) AS source_at FROM ozon_cat_products WHERE sku IS NOT NULL ORDER BY sku,coalesce(api_updated_at,updated_at,imported_at) DESC NULLS LAST')
    for r in c.fetchall():
        codes=r['barcodes_json'] or []
        if isinstance(codes,str):
            try:codes=json.loads(codes)
            except ValueError:codes=[codes]
        add('ozon',r['sku'],r['nazvanie_tovara'],r['artikul'],'',list(codes)+[r['shtrihkod_seriynyy_nomer_ean']],r['source_at'])
    c.execute('SELECT DISTINCT ON(offer_id) offer_id,offer_name,snapshot_at FROM yandex_dim_offer ORDER BY offer_id,snapshot_at DESC')
    for r in c.fetchall():add('yandex_market',r['offer_id'],r['offer_name'],r['offer_id'],'',[],r['snapshot_at'])
    c.execute("SELECT DISTINCT ON(record_key) seller_sku,lamoda_sku,payload->>'name' AS name,payload,synced_at FROM lamoda_v2_entities WHERE dataset IN ('catalog','prices') ORDER BY record_key,synced_at DESC,(dataset='prices') DESC")
    for r in c.fetchall():
        p=r['payload'] or {};codes=p.get('barcodes') or ([p.get('barcode')] if p.get('barcode') else [])
        if isinstance(codes,str):codes=[codes]
        add('lamoda',r['lamoda_sku'] or r['seller_sku'],r['name'],r['seller_sku'],str(p.get('externalSize') or ''),codes,r['synced_at'])
    if client=='toptop':
        c.execute('SELECT DISTINCT ON(product_id,variant_id) product_id,variant_id,product_name,article,period FROM retail_1c.sales ORDER BY product_id,variant_id,period DESC')
        for r in c.fetchall():add('retail',r['product_id']+':'+r['variant_id'],r['product_name'],r['article'],r['variant_id'],[],r['period'])
        c.execute("SELECT product_id,variant_id,payload,loaded_at FROM one_c_import.current_catalog WHERE database_name='1c_retail_prod'")
        for r in c.fetchall():
            p=r['payload']
            add('retail',r['product_id']+':'+r['variant_id'],p['name'],p['article'],p['variant'],p['barcodes'],r['loaded_at'])
    # A duplicate source key must not create a second entity.
    unique={}
    for r in result:
        key=(r['channel'],r['key'])
        if key not in unique or r['source_at']>unique[key]['source_at']:unique[key]=r
    return list(unique.values())

def components(rows):
    codes=defaultdict(list)
    for i,r in enumerate(rows):
        for code in r['valid_barcodes']:codes[code].append(i)
    parent=list(range(len(rows)));conflicts=set();members_by_root={i:{i} for i in range(len(rows))}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for code,ids in codes.items():
        if max(Counter(rows[i]['channel'] for i in ids).values())>1:
            conflicts.update(ids);continue
        roots={find(i) for i in ids};members=set().union(*(members_by_root[x] for x in roots))
        if max(Counter(rows[i]['channel'] for i in members).values())>1:conflicts.update(members);continue
        root=min(roots)
        for x in roots:
            parent[x]=root
            if x!=root:members_by_root.pop(x)
        members_by_root[root]=members
    groups=defaultdict(list)
    for i in range(len(rows)):groups[find(i)].append(i)
    return list(groups.values()),conflicts

def run(client):
    rt._USE_WRITER_CONFIG.set(True)
    with psycopg2.connect(**rt.app.read_db_config(client),cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as c:
            c.execute("SET statement_timeout='20s'; SET lock_timeout='2s'")
            rows=collect(c,client);groups,conflicts=components(rows)
            c.execute('''CREATE TABLE IF NOT EXISTS assortment_master.variants(
              master_id uuid PRIMARY KEY,created_at timestamptz NOT NULL DEFAULT now())''')
            c.execute('''CREATE TABLE IF NOT EXISTS assortment_master.links(
              channel text NOT NULL,source_key text NOT NULL,master_id uuid NOT NULL REFERENCES assortment_master.variants,
              payload jsonb NOT NULL,status text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY(channel,source_key))''')
            c.execute('''CREATE TABLE IF NOT EXISTS assortment_master.runs(
              id bigserial PRIMARY KEY,finished_at timestamptz NOT NULL DEFAULT now(),summary jsonb NOT NULL)''')
            c.execute('SELECT channel,source_key,master_id FROM assortment_master.links')
            previous={(r['channel'],r['source_key']):str(r['master_id']) for r in c.fetchall()}
            linked=0; variant_ids=set(); link_rows=[]
            for ids in groups:
                old={previous[(rows[i]['channel'],rows[i]['key'])] for i in ids if (rows[i]['channel'],rows[i]['key']) in previous}
                # Never silently merge two established masters when a new barcode arrives.
                split=len(old)>1
                master=next(iter(old)) if len(old)==1 else str(uuid.uuid4())
                variant_ids.add(master)
                for i in ids:
                    r=rows[i]; chosen=previous.get((r['channel'],r['key']),master) if split else master
                    status='conflict' if split or i in conflicts else 'matched' if len(ids)>1 else 'unmatched'
                    linked+=status=='matched'
                    link_rows.append((r['channel'],r['key'],chosen,Json(r),status))
            execute_values(c,'INSERT INTO assortment_master.variants(master_id) VALUES %s ON CONFLICT DO NOTHING',[(v,) for v in variant_ids],page_size=2000)
            execute_values(c,'''INSERT INTO assortment_master.links(channel,source_key,master_id,payload,status)
                VALUES %s ON CONFLICT(channel,source_key) DO UPDATE SET payload=EXCLUDED.payload,status=EXCLUDED.status,updated_at=now()''',link_rows,page_size=2000)
            if client=='toptop':
                current_retail=[r['key'] for r in rows if r['channel']=='retail']
                c.execute("DELETE FROM assortment_master.links WHERE channel='retail' AND NOT(source_key=ANY(%s))",(current_retail,))
            summary={'client':client,'source_variants':len(rows),'groups':len(groups),'matched_links':linked,'conflict_links':len(conflicts),'channels':dict(Counter(r['channel'] for r in rows)),'method':'unique valid GTIN, variant grain; no name/article fuzzy joins'}
            c.execute('INSERT INTO assortment_master.runs(summary) VALUES(%s)',(Json(summary),))
            c.execute('GRANT USAGE ON SCHEMA assortment_master TO pulse_reader; GRANT SELECT ON ALL TABLES IN SCHEMA assortment_master TO pulse_reader')
            print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    rt.configure_scope()
    for client in ('toptop','lera_nena'):run(client)
