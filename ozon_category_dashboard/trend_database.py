"""Personal TOPTOP database gateway. Only generated, parameterized SELECTs."""
import json,re,time,hashlib
import subprocess,sys
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,UTC
from decimal import Decimal
from galactica_entitlement import configured_subject,require_current_session,require_access_origin,SourceDenied
from data_access import permits

DATABASES={'1c_retail_prod','1c_ut_prod','1c_erp_prod'}

class DatabaseUnavailable(Exception):
    def __init__(self,reason):self.reason=reason
def ident(value):
    if not isinstance(value,str) or not re.fullmatch(r'[\w]{1,128}',value,re.UNICODE):raise ValueError('Invalid identifier')
    return '['+value+']'
def table_parts(value):
    if not isinstance(value,str) or value.count('.')!=1:raise ValueError('Use schema.table')
    schema,table=value.split('.');ident(schema);ident(table)
    if schema.lower() in {'sys','information_schema'}:raise SourceDenied()
    return schema,table

def authority(app,subject):
    if app.dashboard_locked_client() not in ('','toptop',None) or 'toptop' in app.dashboard_excluded_clients():raise SourceDenied()
    with app.client_registry_connection() as db:
        with db.cursor() as c:
            c.execute("SET TRANSACTION READ ONLY")
            c.execute("SET LOCAL statement_timeout='2s'")
        require_current_session(db,subject)
        with db.cursor() as c:
            c.execute("SELECT data_access FROM public.bi_users u WHERE user_id=%s AND is_active AND EXISTS(SELECT 1 FROM public.bi_user_clients x WHERE x.user_id=u.user_id AND client_key='toptop')",(subject[0],))
            row=c.fetchone()
        if not row or not permits(row['data_access'],'galactica:toptop','context'):raise SourceDenied()
        return row['data_access']

def sources(app,grants):
    # Configuration names and connection secrets never leave the source server.
    with app.client_registry_connection() as db:
        with db.cursor() as c:
            c.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%' ORDER BY service_key")
            keys=[r['service_key'] for r in c.fetchall()]
    result=[]
    for key in keys:
        resources=next((g['resources'] for g in grants if g.get('source')==key),[])
        if not resources:continue
        name=app.service_credential(key,'database')
        if name in DATABASES:result.append({'source':key,'database':name,'kind':'sqlserver','resources':resources})
    return result

@contextmanager
def sql_connection(app,source):
    import pymssql
    values={k:app.service_credential(source,k) for k in ('host','port','database','username','password')}
    from one_c_endpoint import sql_endpoint
    if values['database'] not in DATABASES:raise DatabaseUnavailable('registered_connection_outside_toptop_database_scope')
    try:sql_host,sql_port=sql_endpoint(values['host'],values['port'])
    except ValueError:raise DatabaseUnavailable('registered_connection_outside_toptop_database_scope')
    db=pymssql.connect(server=sql_host,port=sql_port,database=values['database'],user=values['username'],password=values['password'],login_timeout=5,timeout=10,appname='GALACTICA personal read',autocommit=False)
    try:
        with db.cursor() as c:c.execute('SET LOCK_TIMEOUT 1500')
        yield db
    finally:
        try:db.rollback()
        finally:db.close()

def table_columns(db,table):
    schema,name=table_parts(table)
    with db.cursor(as_dict=True) as c:
        c.execute("SELECT c.name,t.name AS data_type,c.max_length,c.is_nullable FROM sys.tables tb JOIN sys.schemas s ON s.schema_id=tb.schema_id JOIN sys.columns c ON c.object_id=tb.object_id JOIN sys.types t ON t.user_type_id=c.user_type_id WHERE s.name=%s AND tb.name=%s AND tb.is_ms_shipped=0 ORDER BY c.column_id",(schema,name))
        rows=list(c.fetchall())
    if not rows:raise ValueError('Table not found')
    return rows

def query_sql(args,columns):
    """No SQL text, expressions, subqueries, database names or user functions accepted."""
    if set(args)-{'operation','source','table','columns','filters','aggregates','group_by','order_by','limit','offset'}:raise ValueError('Unknown parameters')
    schema,table=table_parts(args['table']);known={r['name'] for r in columns}
    def col(x):
        if x not in known:raise ValueError('Unknown column')
        return ident(x)
    limit=args.get('limit',100);offset=args.get('offset',0)
    if type(limit) is not int or not 1<=limit<=500 or type(offset) is not int or not 0<=offset<=100000:raise ValueError('Invalid page')
    selected=args.get('columns',[]);groups=args.get('group_by',[]);aggregates=args.get('aggregates',[])
    if not all(isinstance(x,list) and len(x)<=40 for x in (selected,groups,aggregates)):raise ValueError('Invalid projection')
    fields=[col(x) for x in selected];aliases=set(selected);params=[]
    for a in aggregates:
        if not isinstance(a,dict) or set(a)-{'function','column','alias'}:raise ValueError('Invalid aggregate')
        fn=a.get('function','').upper();alias=a.get('alias')
        if fn not in {'COUNT','SUM','AVG','MIN','MAX'} or alias in aliases:raise ValueError('Invalid aggregate')
        expression='*' if fn=='COUNT' and a.get('column')=='*' else col(a.get('column'))
        fields.append(fn+'('+expression+') AS '+ident(alias));aliases.add(alias)
    if aggregates and set(selected)!=set(groups):raise ValueError('Selected columns must match group_by')
    if groups and not aggregates:raise ValueError('group_by needs aggregates')
    if not fields:raise ValueError('Select explicit columns or aggregates')
    filters=args.get('filters',[])
    if not isinstance(filters,list) or len(filters)>30:raise ValueError('Invalid filters')
    conditions=[]
    for f in filters:
        if not isinstance(f,dict) or set(f)-{'column','operator','value'}:raise ValueError('Invalid filter')
        column=col(f.get('column'));op=f.get('operator');value=f.get('value')
        if op in ('is_null','not_null'):conditions.append(column+(' IS NULL' if op=='is_null' else ' IS NOT NULL'));continue
        if op not in {'eq','ne','gt','gte','lt','lte','like','in'}:raise ValueError('Invalid operator')
        values=value if op=='in' else [value]
        if not isinstance(values,list) or not 1<=len(values)<=50 or any(not isinstance(v,(str,int,float,bool)) or isinstance(v,str) and len(v)>1000 for v in values):raise ValueError('Invalid value')
        params.extend(values)
        conditions.append(column+(' IN ('+','.join(['%s']*len(values))+')' if op=='in' else ' '+{'eq':'=','ne':'<>','gt':'>','gte':'>=','lt':'<','lte':'<=','like':'LIKE'}[op]+' %s'))
    sql='SELECT '+', '.join(fields)+' FROM '+ident(schema)+'.'+ident(table)
    if conditions:sql+=' WHERE '+' AND '.join(conditions)
    if groups:sql+=' GROUP BY '+', '.join(col(g) for g in groups)
    orders=args.get('order_by',[])
    if not isinstance(orders,list) or len(orders)>8:raise ValueError('Invalid ordering')
    parts=[]
    for o in orders:
        if not isinstance(o,dict) or set(o)-{'column','direction'} or o.get('direction','asc') not in ('asc','desc'):raise ValueError('Invalid ordering')
        name=o.get('column')
        if name not in aliases:col(name)
        parts.append(ident(name)+' '+o.get('direction','asc').upper())
    sql+=' ORDER BY '+(', '.join(parts) if parts else '(SELECT NULL)')+' OFFSET %s ROWS FETCH NEXT %s ROWS ONLY'
    params.extend([offset,limit+1]);return sql,params,limit

def clean(value):
    if isinstance(value,(bytes,bytearray)):return '0x'+value.hex()
    if isinstance(value,(datetime,Decimal)):return str(value)
    return value

def execute(app,subject,args):
    if not isinstance(args,dict):raise ValueError('Invalid request')
    operation=args.get('operation');grants=authority(app,subject);available=sources(app,grants)
    if operation=='catalog':
        if set(args)!={'operation'}:raise ValueError('Unknown parameters')
        return {'sources':available}
    source=args.get('source');entry=next((s for s in available if s['source']==source),None)
    if not entry:raise SourceDenied()
    if operation not in ('tables','schema','query'):raise ValueError('Unknown operation')
    if operation=='query' and set(args)-{'operation','source','table','columns','filters','aggregates','group_by','order_by','limit','offset'}:raise ValueError('Unknown query field')
    table=args.get('table')
    if operation in ('schema','query'):
        table_parts(table)
        if not permits(grants,source,table):raise SourceDenied()
    with sql_connection(app,source) as db:
        if operation=='tables':
            if set(args)-{'operation','source','offset','limit','search'}:raise ValueError('Unknown parameters')
            offset=args.get('offset',0);limit=args.get('limit',100);search=args.get('search','')
            if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=500 or not isinstance(search,str) or len(search)>128:raise ValueError('Invalid page')
            with db.cursor(as_dict=True) as c:
                c.execute("SELECT s.name AS schema_name,t.name AS table_name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE t.is_ms_shipped=0 ORDER BY s.name,t.name")
                permitted=[r for r in c.fetchall() if permits(grants,source,r['schema_name']+'.'+r['table_name']) and search.lower() in r['table_name'].lower()]
            result={'tables':permitted[offset:offset+limit],'total':len(permitted),'offset':offset,'limit':limit}
        elif operation=='schema':
            if set(args)!={'operation','source','table'}:raise ValueError('Unknown parameters')
            result={'table':table,'columns':table_columns(db,table)}
        else:
            sql,params,limit=query_sql(args,table_columns(db,table))
            with db.cursor() as c:
                c.execute(sql,tuple(params));names=[x[0] for x in c.description];rows=c.fetchmany(limit+1)
            result={'table':table,'columns':names,'rows':[[clean(v) for v in r] for r in rows[:limit]],'has_more':len(rows)>limit,'limit':limit,'offset':args.get('offset',0)}
    # Authority checked again after SQL and before response; revocation wins.
    latest=authority(app,subject)
    if operation in ('schema','query') and not permits(latest,source,table):raise SourceDenied()
    if operation=='tables':result['tables']=[r for r in result['tables'] if permits(latest,source,r['schema_name']+'.'+r['table_name'])]
    result.update(source=source,database=entry['database']);return result

def audit(app,subject,args,status,duration,rows=0):
    with app.client_registry_connection() as db:
        with db.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS public.bi_galactica_database_events(id bigserial PRIMARY KEY,user_id bigint,source text,operation text,status text,duration_ms integer,row_count integer,created_at timestamptz NOT NULL DEFAULT now())''')
            c.execute('INSERT INTO public.bi_galactica_database_events(user_id,source,operation,status,duration_ms,row_count) VALUES(%s,%s,%s,%s,%s,%s)',(subject[0] if subject else None,str(args.get('source',''))[:120],str(args.get('operation',''))[:30],status,int(duration),rows))
        db.commit()

def handle(app,handler):
    start=time.monotonic();subject=None;args={};status='error';rows=0
    try:
        require_access_origin(handler.headers)
        length=int(handler.headers.get('Content-Length','0'))
        if not 0<length<=24000:raise ValueError('Invalid request size')
        args=json.loads(handler.rfile.read(length))
        if not isinstance(args,dict):raise ValueError('Invalid request')
        token=app.dashboard_access_session_from_cookie(handler.headers.get('Cookie'))
        subject=configured_subject(app,token)
        if args.get('operation')=='catalog':
            result=execute(app,subject,args)
        else:
            # FreeTDS timeouts have process-wide effects. Isolate each SQL read
            # from TREND background probes and terminate an overdue worker.
            authority(app,subject)
            worker=subprocess.run([sys.executable,str(Path(__file__).with_name('trend_database_worker.py'))],
                input=json.dumps({'subject':subject,'args':args}),text=True,encoding='utf-8',
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=17)
            response=json.loads(worker.stdout)
            if response.get('error')=='denied':raise SourceDenied()
            if response.get('error')=='invalid':raise ValueError('Invalid query')
            if 'error' in response:
                print('GALACTICA SQL unavailable',response.get('error_class'),response.get('codes'),flush=True)
                codes=response.get('codes',[])
                reason=response.get('reason_code') or ('sql_login_denied' if any(x in codes for x in ['18456','18452']) else 'sql_database_access_denied' if any(x in codes for x in ['4060','916']) else 'sql_tunnel_connection_failed' if any(x in codes for x in ['20009','20002']) else 'sql_'+str(response.get('error_class','worker_failed')))
                raise DatabaseUnavailable(reason)
            result=response['result']
            latest=authority(app,subject)
            if args.get('operation') in ('schema','query') and not permits(latest,args.get('source'),args.get('table')):raise SourceDenied()
        result.update(client_key='toptop',queried_at=datetime.now(UTC).isoformat(),read_only=True)
        if len(json.dumps(result,default=str).encode())>800000:raise ValueError('Result too large; select fewer columns or rows')
        rows=len(result.get('rows',[]));status='allowed'
        audit(app,subject,args,status,(time.monotonic()-start)*1000,rows)
        handler.send_json(result,headers={'Cache-Control':'no-store'})
    except SourceDenied:
        audit(app,subject,args if isinstance(args,dict) else {},'denied',(time.monotonic()-start)*1000)
        handler.send_json({'error':'DATABASE_ACCESS_DENIED'},status=403)
    except (ValueError,TypeError,KeyError):
        audit(app,subject,args if isinstance(args,dict) else {},'invalid',(time.monotonic()-start)*1000)
        handler.send_json({'error':'INVALID_DATABASE_REQUEST'},status=422)
    except Exception as error:
        try:audit(app,subject,args if isinstance(args,dict) else {},'unavailable',(time.monotonic()-start)*1000)
        except Exception:pass
        reason=error.reason if isinstance(error,DatabaseUnavailable) else 'sql_worker_deadline_17s' if isinstance(error,subprocess.TimeoutExpired) else 'sql_gateway_unavailable'
        handler.send_json({'error':'DATABASE_SOURCE_UNAVAILABLE','reason_code':reason},status=503)
