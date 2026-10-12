"""Read-only source brand evidence and database metadata; no secrets printed."""
import json
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import pulse_vps_admin as rt
import ff_platform as ff
rt.configure_scope()
rt._USE_WRITER_CONFIG.set(True)
app=rt.app
token=ff.token_value(app,'toptop')
changes=ff.collection(ff.request_json(token,'/changed_stocks',{'sklad_id':2,'timestamp':0}),('data','items','stocks','result'))
by_shop={}
for r in changes:
    n=r['nomenclature']; shop=str(n.get('idShop'))
    by_shop[shop]=by_shop.get(shop,0)+1
print(json.dumps({'observed_shop_ids_counts':by_shop}))
catalogs={}
for client in ('toptop','lera_nena'):
    with psycopg2.connect(**app.read_db_config(client),cursor_factory=RealDictCursor) as db:
        with db.cursor() as cur:
            cur.execute('SELECT brend,count(*) rows FROM public.ozon_products GROUP BY brend ORDER BY count(*) DESC LIMIT 15')
            print(json.dumps({'client':client,'catalog_brand_counts':cur.fetchall()},default=str))
            cur.execute('SELECT DISTINCT artikul FROM public.ozon_products WHERE artikul IS NOT NULL')
            catalogs[client]={str(row['artikul']).strip() for row in cur.fetchall()}
with app.client_registry_connection() as db:
    with db.cursor() as cur:
        cur.execute("SELECT client_article,available_qty,defect_qty,at_work_qty FROM ff_platform.current_stocks WHERE client_key='toptop'")
        rows=cur.fetchall()
counts={}
for row in rows:
    matches=tuple(k for k,articles in catalogs.items() if row['client_article'].strip() in articles)
    key=','.join(matches) or 'unmatched'
    record=counts.setdefault(key,{'rows':0,'nonzero':0})
    record['rows']+=1
    record['nonzero']+=int(any(row[k]!=0 for k in ('available_qty','defect_qty','at_work_qty')))
print(json.dumps({'ff_article_catalog_overlap':counts}))
