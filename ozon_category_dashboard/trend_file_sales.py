"""TOPTOP analyst-file sales. Independent from 1C and marketplace facts."""
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs
import psycopg2
from psycopg2.extras import RealDictCursor
CHANNELS={'retail':'Розница','wholesale':'Опт','corners':'Корнеры','online':'Интернет-магазин','networks':'Сети'}
SORTS={'sale_date':'s.sale_date','location':'s.location','article':'s.article','quantity':'s.quantity','revenue':'s.revenue','product':'s.product','revenue_basis':'s.revenue_basis'}
def allowed(app,identity,client):
    if client!='toptop' or app.current_client_key()!='toptop' or not identity:return False
    if identity.get('is_admin'):return True
    from data_access import permits
    return ('toptop' in identity.get('clients',[]) and 'funnel' in identity.get('reports',[]) and permits(identity.get('data_access'),'marketplace:toptop','funnel'))
def clean(v):
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,date):return v.isoformat()
    if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)):return [clean(x) for x in v]
    return v
def handle(app,handler,parsed):
    if parsed.path!='/api/offline-sales':return False
    q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=24)
    get=lambda k,d='':q.get(k,[d])[0]
    if not allowed(app,app.CURRENT_ACCESS_USER.get(),get('client')):
        handler.send_json({'ok':False,'error':'Нет доступа к продажам TOPTOP'},status=403);return True
    try:handler.send_json(clean(build(app,get)))
    except ValueError as exc:handler.send_json({'ok':False,'error':str(exc)},status=400)
    except psycopg2.Error:handler.send_json({'ok':False,'error':'Источник продаж из файлов временно недоступен'},status=503)
    return True
def build(app,get):
    channel=get('channel');basis=get('basis');sort=get('sort','sale_date');direction=get('direction','desc')
    if channel and channel not in CHANNELS:raise ValueError('Неизвестный канал')
    if basis not in ('','ex_vat','unspecified'):raise ValueError('Неизвестная база выручки')
    if sort not in SORTS or direction not in ('asc','desc'):raise ValueError('Некорректная сортировка')
    try:page=max(1,int(get('page','1')));limit=max(1,min(int(get('limit','50')),200))
    except ValueError:raise ValueError('Некорректная страница')
    where=['TRUE'];params=[]
    for key,column in [('channel','s.channel'),('location','s.location'),('basis','s.revenue_basis')]:
        if get(key):where.append(column+'=%s');params.append(get(key))
    for key,op in [('from','>='),('to','<=')]:
        if get(key):
            try:d=date.fromisoformat(get(key))
            except ValueError:raise ValueError('Некорректная дата')
            where.append('s.sale_date'+op+'%s');params.append(d)
    if get('from') and get('to') and get('from')>get('to'):raise ValueError('Начало периода позже окончания')
    for key,column in [('article','s.article'),('product','s.product')]:
        if get(key):where.append(column+' ILIKE %s');params.append('%'+get(key)[:180]+'%')
    for key in ('quantity','revenue'):
        if get(key):
            op={'eq':'=','gt':'>','lt':'<','gte':'>=','lte':'<='}.get(get(key+'_op','gte'))
            if not op:raise ValueError('Некорректное условие фильтра')
            try:n=Decimal(get(key))
            except Exception:raise ValueError('Некорректное число')
            if not n.is_finite():raise ValueError('Некорректное число')
            where.append('s.'+key+op+'%s');params.append(n)
    predicate=' AND '.join(where)
    with psycopg2.connect(**app.read_db_config('toptop'),cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as c:
            c.execute('SET TRANSACTION READ ONLY');c.execute("SET LOCAL statement_timeout='5s'")
            c.execute('SELECT channel,min(sale_date) first_date,max(sale_date) last_date,count(*) rows,count(DISTINCT sale_date) observed_days FROM offline_sales.sales GROUP BY channel ORDER BY channel');coverage=[dict(x) for x in c.fetchall()]
            c.execute('SELECT DISTINCT channel,location FROM offline_sales.sales ORDER BY channel,location');locations=[dict(x) for x in c.fetchall()]
            c.execute('SELECT count(*) total FROM offline_sales.sales s WHERE '+predicate,params);total=c.fetchone()['total']
            c.execute('SELECT s.revenue_basis,sum(s.quantity) quantity,sum(s.revenue) revenue,count(*) rows FROM offline_sales.sales s WHERE '+predicate+' GROUP BY s.revenue_basis ORDER BY s.revenue_basis',params);totals=[dict(x) for x in c.fetchall()]
            c.execute('SELECT s.*,src.source_path,src.loaded_at FROM offline_sales.sales s JOIN offline_sales.sources src USING(source_id) WHERE '+predicate+' ORDER BY '+SORTS[sort]+' '+direction+' NULLS LAST,s.source_id,s.source_sheet,s.source_row LIMIT %s OFFSET %s',params+[limit,(page-1)*limit]);rows=[dict(x) for x in c.fetchall()]
            for row in rows:row['source_file']=row.pop('source_path').split('\\')[-1]
    return {'ok':True,'client':'toptop','source':'Файлы аналитиков','channel':channel,'channels':[{'id':k,'label':v} for k,v in CHANNELS.items()],
        'coverage':coverage,'locations':locations,'rows':rows,'totals':totals,'total':total,'page':page,'page_size':limit,'total_pages':max(1,(total+limit-1)//limit),
        'notice':'Показаны загруженные строки. Пропуски между датами не означают нулевые продажи. Выручка без НДС и с неуказанной базой НДС рассчитана раздельно.'}
