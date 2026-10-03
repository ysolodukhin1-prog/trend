"""Server-owned activity events. Never stores bodies, cookies or raw query strings."""
from __future__ import annotations
import ipaddress
import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_recent = {}
_proxy_ips = set()
_proxy_at = 0
QUIET = {'/api/health', '/api/database-status', '/api/service-status', '/api/filters',
         '/api/admin/auth/status', '/api/admin/activity-log'}
SELECT = ('id','occurred_at','username','ip_address','action','target','client_key',
          'marketplace','channel','status_code','success','duration_ms','method')
SORT = {x:x for x in SELECT}

def ensure_schema(app):
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            # Import workers bootstrap concurrently. Serialize schema/ACL DDL
            # through the transaction so REVOKE cannot race on pg_class.
            cur.execute('SELECT pg_advisory_xact_lock(20260929, 1)')
            cur.execute('''CREATE TABLE IF NOT EXISTS public.trend_activity_log (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                occurred_at timestamptz NOT NULL DEFAULT now(),
                user_id bigint, username varchar(256) NOT NULL DEFAULT '',
                ip_address inet, action varchar(80) NOT NULL,
                target varchar(256) NOT NULL DEFAULT '',
                client_key varchar(80) NOT NULL DEFAULT '',
                marketplace varchar(80) NOT NULL DEFAULT '', channel varchar(80) NOT NULL DEFAULT '',
                method varchar(8) NOT NULL, status_code integer NOT NULL,
                success boolean NOT NULL, duration_ms integer NOT NULL DEFAULT 0
            )''')
            cur.execute('CREATE INDEX IF NOT EXISTS trend_activity_time_idx ON public.trend_activity_log (occurred_at DESC, id DESC)')
            cur.execute('CREATE INDEX IF NOT EXISTS trend_activity_user_time_idx ON public.trend_activity_log (username, occurred_at DESC)')
            cur.execute('CREATE INDEX IF NOT EXISTS trend_activity_ip_time_idx ON public.trend_activity_log (ip_address, occurred_at DESC)')
            # Ordinary report readers cannot inspect audit rows through SQL.
            cur.execute('REVOKE ALL ON public.trend_activity_log FROM pulse_reader, PUBLIC')

def clean(value, maximum=80):
    return ''.join(c for c in str(value or '') if c.isprintable())[:maximum]

def client_ip(handler):
    global _proxy_at, _proxy_ips
    peer = str(getattr(handler, 'client_address', ('',))[0])
    if time.monotonic() - _proxy_at > 60:
        try:
            ips = {x[4][0] for x in socket.getaddrinfo('pulse_public', None)}
            _proxy_ips = ips
            _proxy_at = time.monotonic()
        except OSError:
            pass
    candidate = handler.headers.get('X-Forwarded-For', '').split(',')[0].strip() if peer in _proxy_ips else peer
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        try: return str(ipaddress.ip_address(peer))
        except ValueError: return None

def can_read(identity):
    return bool(identity and (identity.get('is_admin') or 'users' in (identity.get('admin_sections') or [])))

def begin(app, handler, method):
    # Reset for every request; HTTP connections can be reused for several users.
    try: identity = handler.dashboard_access_identity() or {}
    except Exception: identity = {}
    handler._activity = {'identity': identity, 'started': time.monotonic(), 'method': method,
                         'status': 500, 'ok': True, 'fields': {}}

def capture_body(handler, payload):
    state = getattr(handler, '_activity', None)
    if state is None or not isinstance(payload, dict): return
    path = urlparse(handler.path).path
    # Explicit metadata whitelist: no password, key, SQL, text content or secrets.
    keys = ('client','marketplace','sales_channel','action','dashboard','admin_section')
    fields = {k:clean(payload[k]) for k in keys if isinstance(payload.get(k), (str,int))}
    if path in {'/api/access/login','/api/admin/auth/login'}:
        fields['attempted_username'] = clean(payload.get('username'),256)
    if path == '/api/admin/users':
        fields['target_user'] = clean(payload.get('username') or payload.get('user_id'), 256)
    state['fields'] = fields

def capture_response(app, handler, payload, status, headers):
    state = getattr(handler, '_activity', None)
    if state is None: return
    if isinstance(payload, dict) and payload.get('ok') is False: state['ok'] = False
    if status < 300 and urlparse(handler.path).path == '/api/access/login':
        try:
            token = app.dashboard_access_session_from_cookie((headers or {}).get('Set-Cookie'))
            identity = app.verify_managed_access_token(token)
            if identity: state['identity'] = identity
        except Exception:
            pass

def append(app, event, conn=None):
    def insert(connection):
        with connection.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '2000ms'")
            cur.execute("SET LOCAL lock_timeout = '500ms'")
            cur.execute('''INSERT INTO public.trend_activity_log
                (user_id,username,ip_address,action,target,client_key,marketplace,channel,method,status_code,success,duration_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                tuple(event[k] for k in ('user_id','username','ip_address','action','target','client_key','marketplace','channel','method','status_code','success','duration_ms')))
    if conn is not None: insert(conn)
    else:
        with app.client_registry_connection() as connection: insert(connection)

def finish(app, handler):
    state = getattr(handler, '_activity', None)
    if state is None: return
    parsed = urlparse(handler.path); path = parsed.path
    method = state['method']; fields = state['fields']; identity = state['identity']
    query = parse_qs(parsed.query)
    first = lambda k: fields.get(k) or (query.get(k) or [''])[0]
    client = clean(first('client'))
    client = client if client in {'toptop','lera_nena'} else ''
    target = clean(path,256); action = ''
    if path in {'/api/access/login','/api/admin/auth/login'}:
        action = 'login'
    elif path in {'/api/access/logout','/api/admin/auth/logout'}:
        action = 'logout'
    elif path == '/api/access/activity':
        if not identity or not state['ok'] or state['status'] >= 300: return
        action = 'page'
        target = clean(fields.get('dashboard') or 'home')
        if fields.get('admin_section'): target += '/' + clean(fields['admin_section'])
    elif path in QUIET or not path.startswith('/api/'):
        return
    elif method == 'POST':
        action = 'write'
        if fields.get('action'): target += ' · ' + clean(fields['action'])
        if fields.get('target_user'): target += ' · ' + clean(fields['target_user'],100)
    elif method == 'GET':
        action = 'export' if 'export' in path or 'open-file' in path else 'read'
    else: return
    username = clean(identity.get('username') or fields.get('attempted_username'),256)
    ip = client_ip(handler)
    success = state['status'] < 400 and state['ok']
    # Repeated background reads collapse for one minute. Writes, page opens and
    # authentication events are never collapsed. Bound the in-memory cache.
    key = (username, ip, target,client,clean(first('marketplace')),clean(first('sales_channel')),success)
    if action == 'read':
        with _lock:
            now = time.monotonic()
            if now - _recent.get(key,-60) < 60: return
            if len(_recent) > 4096:
                for old in [k for k,v in _recent.items() if now-v >= 60]: _recent.pop(old,None)
                if len(_recent) > 4096: _recent.clear()
    event = dict(user_id=identity.get('user_id'),username=username,ip_address=ip,
                 action=action,target=target,client_key=client,marketplace=clean(first('marketplace')),
                 channel=clean(first('sales_channel')),method=method,status_code=state['status'],success=success,
                 duration_ms=min(2147483647,int((time.monotonic()-state['started'])*1000)))
    try:
        append(app,event)
        if action=='read':
            with _lock: _recent[key]=time.monotonic()
    except Exception as exc:
        # Failures remain observable in service stderr without request data.
        LOG.error('activity_log_write_failed:%s',type(exc).__name__)

def date_bound(value, end=False):
    value = str(value or '')
    if not value: return None
    # User-facing dates are Moscow calendar days; end date is inclusive.
    day = datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=3)))
    return day + timedelta(days=1) if end else day

def read_events(app, query, conn=None):
    one = lambda k,default='': (query.get(k) or [default])[0]
    start=date_bound(one('date_from')) or datetime.now(timezone.utc)-timedelta(days=30)
    end=date_bound(one('date_to'),True)
    if end and start>=end: raise ValueError('Неверный период')
    limit=max(1,min(100,int(one('limit','50'))));page=max(1,min(100000,int(one('page','1'))))
    order=SORT.get(one('sort','occurred_at'),'occurred_at');direction='ASC' if one('direction')=='asc' else 'DESC'
    clauses=['occurred_at >= %s'];params=[start]
    if end: clauses.append('occurred_at < %s');params.append(end)
    for key in ('username','target','client_key','marketplace','channel','action'):
        val=clean(one(key),256)
        if val:
            if key in {'username','target'}:
                clauses.append(key+' ILIKE %s');params.append('%'+val.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
            else: clauses.append(key+' = %s');params.append(val)
    ip=one('ip_address')
    if ip: clauses.append('ip_address = %s::inet');params.append(str(ipaddress.ip_address(ip)))
    result=one('success')
    if result in {'true','false'}:clauses.append('success = %s');params.append(result=='true')
    where=' AND '.join(clauses)
    def select(connection):
        with connection.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='5000ms'")
            cur.execute('SELECT count(*) AS total FROM public.trend_activity_log WHERE '+where,params)
            total=cur.fetchone()['total']
            cur.execute('SELECT '+','.join(SELECT)+' FROM public.trend_activity_log WHERE '+where+f' ORDER BY {order} {direction} NULLS LAST,id {direction} LIMIT %s OFFSET %s',params+[limit,(page-1)*limit])
            rows=[dict(x) for x in cur.fetchall()]
            for row in rows:
                row['occurred_at']=row['occurred_at'].isoformat()
            cur.execute('SELECT min(occurred_at) AS since FROM public.trend_activity_log')
            since=cur.fetchone()['since']
            return {'ok':True,'rows':rows,'total':total,'page':page,'limit':limit,'logging_since':since.isoformat() if since else None}
    if conn is not None:return select(conn)
    with app.client_registry_connection() as connection:return select(connection)

def handle_read(app,handler):
    identity=handler.dashboard_access_identity()
    if not identity:
        handler.send_json({'ok':False,'error':'Требуется вход'},status=401);return
    if not can_read(identity):
        handler.send_json({'ok':False,'error':'Нет доступа к журналу действий'},status=403);return
    try: handler.send_json(read_events(app,parse_qs(urlparse(handler.path).query)))
    except (ValueError,TypeError):handler.send_json({'ok':False,'error':'Проверь фильтры и период'},status=400)
    except Exception as exc:
        LOG.error('activity_log_read_failed:%s',type(exc).__name__)
        handler.send_json({'ok':False,'error':'Журнал временно недоступен'},status=503)

def handle_page(app,handler):
    identity=handler.dashboard_access_identity()
    if not identity:
        handler.send_json({'ok':False,'error':'Требуется вход'},status=401);return
    origin=handler.headers.get('Origin')
    if origin and urlparse(origin).netloc != handler.headers.get('Host'):
        handler.send_json({'ok':False,'error':'Недопустимый источник запроса'},status=403);return
    try:
        if int(handler.headers.get('Content-Length','0') or '0')>2048:
            handler.send_json({'ok':False,'error':'Слишком большой запрос'},status=413);return
        payload=handler.read_json_body()
        if not isinstance(payload,dict): raise ValueError('object required')
        import re
        for key in ('dashboard','admin_section','marketplace','sales_channel','client'):
            if key in payload and not re.fullmatch(r'[A-Za-z0-9_-]{0,80}',str(payload[key])): raise ValueError('invalid code')
    except (ValueError,TypeError):
        handler.send_json({'ok':False,'error':'Неверные параметры страницы'},status=400);return
    client=payload.get('client')
    if client and not identity.get('is_admin') and client not in (identity.get('clients') or []):
        handler.send_json({'ok':False,'error':'Нет доступа к аккаунту'},status=403);return
    handler.send_json({'ok':True})
