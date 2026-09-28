"""PULSE HTTP adapter; strict client/report scope before any DB connection."""
from datetime import date,timedelta
from urllib.parse import parse_qs,urlparse
import os
from cluster_supply_sources import load,MARKETS
from cluster_supply_routes import read_routes,save_route
from cluster_supply_store import save_plan,read_plans
from cluster_supply_jobs import submit,read as read_job


def authorized_client(app,requested):
    if not isinstance(requested,str) or requested not in app.ADMIN_CLIENTS:return False
    user=app.CURRENT_ACCESS_USER.get()
    if user and not user.get('is_admin') and requested not in set(user.get('clients') or []):return False
    return app.normalize_client_key(requested)==requested


def enabled(client):
    pilots={s.strip() for s in os.environ.get('PULSE_CLUSTER_SUPPLY_CLIENTS','toptop').split(',') if s.strip()}
    return '*' in pilots or client in pilots


def handle(app,handler,parsed,method):
    if not parsed.path.startswith('/api/cluster-supply/'):return False
    try:
        params=parse_qs(parsed.query)
        payload={}
        if method=='POST':
            origin=handler.headers.get('Origin')
            if origin and urlparse(origin).netloc!=handler.headers.get('Host'):
                handler.send_json({'error':'Недопустимый источник запроса'},status=403);return True
            if 'application/json' not in handler.headers.get('Content-Type',''):
                handler.send_json({'error':'Требуется JSON'},status=415);return True
            length=int(handler.headers.get('Content-Length','0'))
            if length<1 or length>2000000:
                handler.send_json({'error':'Недопустимый размер запроса'},status=413);return True
            payload=handler.read_json_body()
            if not isinstance(payload,dict):raise ValueError('Ожидается объект запроса')
        client=payload.get('client') if method=='POST' else (params.get('client') or [''])[0]
        if not authorized_client(app,client):
            handler.send_json({'error':'Нет доступа к выбранному клиенту'},status=403);return True
        if method=='GET' and parsed.path=='/api/cluster-supply/status':
            handler.send_json({'enabled':enabled(client)});return True
        if not enabled(client):
            handler.send_json({'error':'Предварительная версия поставок не включена для клиента','enabled':False},status=403);return True
        markets=set(app.client_marketplace_ids(client))&MARKETS
        market=(params.get('marketplace') or ['wb'])[0]
        path=parsed.path.removeprefix('/api/cluster-supply/')
        if path in {'demand','readiness'} and market not in markets:
            handler.send_json({'error':'Площадка не подключена к клиенту'},status=403);return True
        if method=='POST':
            item_markets={i.get('marketplace') for i in payload.get('items') or [] if isinstance(i,dict)}
            if path=='routes':item_markets.add(payload.get('marketplace'))
            if not item_markets<=markets:
                handler.send_json({'error':'Площадка не подключена к клиенту'},status=403);return True
        config=app.read_db_config(client)
        actor=str((app.CURRENT_ACCESS_USER.get() or {}).get('username') or 'local-owner')
        if method=='GET' and path in {'demand','readiness'}:
            end=(params.get('date_to') or [date.today().isoformat()])[0]
            start=(params.get('date_from') or [(date.fromisoformat(end)-timedelta(days=29)).isoformat()])[0]
            sku=(params.get('sku') or [''])[0]
            data=load(config,client,market,start,end,sku)
            rows=data['rows'];needle=(params.get('search') or [''])[0].strip().lower()
            region=(params.get('region') or [''])[0]
            if needle:rows=[r for r in rows if needle in (str(r['sku'])+' '+str(r['product_name'])).lower()]
            if region:rows=[r for r in rows if r['buyer_region']==region]
            sort=(params.get('sort') or ['orders'])[0]
            if sort not in {'orders','sku','buyer_region','size'}:raise ValueError('Недопустимый столбец сортировки')
            desc=(params.get('direction') or ['desc'])[0]=='desc'
            rows.sort(key=lambda r:(r.get(sort) is None,r.get(sort) if r.get(sort) is not None else (0 if sort=='orders' else ''),r['key']),reverse=desc)
            page=max(1,int((params.get('page') or ['1'])[0]));size=max(1,min(100,int((params.get('page_size') or ['25'])[0])))
            data.update(total=len(rows),page=page,page_size=size,rows=rows[(page-1)*size:page*size],available_markets=sorted(markets),enabled=True)
            if not sku:data.pop('stock_rows',None)
            if path=='readiness':data.pop('rows',None)
            handler.send_json(data)
        elif method=='GET' and path=='routes':handler.send_json({'routes':read_routes(config,client),'available_markets':sorted(markets),'enabled':True})
        elif method=='POST' and path=='routes':handler.send_json({'ok':True,'route':save_route(config,client,payload,actor)})
        elif method=='POST' and path=='scenarios/calculate':handler.send_json({'ok':True,**submit(config,client,payload)})
        elif method=='GET' and path.startswith('jobs/'):
            job=read_job(client,path[5:]);handler.send_json(job or {'error':'Расчёт не найден'},status=200 if job else 404)
        elif method=='POST' and path=='plans':handler.send_json(save_plan(config,client,payload,actor))
        elif method=='GET' and path=='plans':handler.send_json({'plans':read_plans(config,client)})
        elif method=='GET' and path.startswith('plans/'):
            parts=path.split('/');record=read_plans(config,client,parts[1])
            if record is None:handler.send_json({'error':'План не найден'},status=404)
            elif len(parts)==3 and parts[2]=='export':
                from cluster_supply_export import export_plan
                body,name=export_plan(record);handler.send_file(body,name)
            elif len(parts)==2:handler.send_json(record)
            else:handler.send_json({'error':'Адрес не найден'},status=404)
        else:handler.send_json({'error':'Адрес не найден'},status=404)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        handler.send_json({'error':str(exc)},status=400)
    except Exception:
        handler.send_json({'error':'Источники поставок временно недоступны. Повторите запрос.'},status=502)
    return True
