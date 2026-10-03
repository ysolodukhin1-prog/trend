"""Per-user, deny-by-default data grants; no credentials in permission metadata."""
import re


def normalize_grants(value, catalog):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError('Некорректный список доступов к данным')
    known = {item['key']: item for item in catalog}
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Некорректное разрешение')
        source = item.get('source')
        resources = item.get('resources')
        if source not in known or source in seen or not isinstance(resources, list) or len(resources) > 300:
            raise ValueError('Неизвестный или повторный источник данных')
        seen.add(source)
        clean = sorted(set(str(x).strip() for x in resources if str(x).strip()))
        if not clean:
            continue
        for resource in clean:
            if len(resource) > 240:
                raise ValueError('Слишком длинное имя ресурса')
            if resource == '*':
                continue
            if source == 'mpstats':
                if not re.fullmatch(r'GET /api/[A-Za-z0-9_./{}-]+', resource) or '..' in resource:
                    raise ValueError('Метод MPStats: GET /api/путь, без параметров и ключей')
            elif source.startswith('1c'):
                if not re.fullmatch(r'[\w]+\.[\w]+', resource, re.UNICODE):
                    raise ValueError('Таблица 1С: схема.таблица, например dbo._Reference123')
            elif resource not in {x['id'] for x in known[source].get('resources', [])}:
                raise ValueError('Неизвестный вид данных аккаунта')
        result.append({'source': source, 'resources': clean})
    return result


def permits(grants, source, resource):
    if not source or not resource:
        return False
    return any(isinstance(g, dict) and g.get('source') == source and
               (resource in g.get('resources', []) or '*' in g.get('resources', [])) for g in grants or [])


def require_user_grant(connection, user_id, source, resource):
    from galactica_entitlement import SourceDenied
    with connection.cursor() as cur:
        cur.execute('SELECT data_access FROM public.bi_users WHERE user_id=%s AND is_active', (user_id,))
        row = cur.fetchone()
    grants = row.get('data_access') if isinstance(row, dict) else row[0] if row else []
    if not permits(grants, source, resource):
        raise SourceDenied()


def catalog(app):
    sources = [{'key': 'mpstats', 'label': 'MPStats', 'kind': 'methods',
                'hint': 'GET /api/путь — каждый метод с новой строки. * — все методы чтения.'}]
    for s in app.admin_connections_payload()['services']:
        if s['key'].startswith('1c') and s['saved']:
            sources.append({'key': s['key'], 'label': s['label'], 'kind': 'tables',
                            'hint': 'схема.таблица — каждая с новой строки. * — все таблицы базы.'})
    for key, config in app.ADMIN_CLIENTS.items():
        if config.get('show_in_dashboard'):
            reports = set(app.effective_client_reports(key))
            sources.append({'key': 'marketplace:'+key, 'label': 'Маркетплейсы · '+config['label'],
                            'kind': 'reports', 'resources': [x for x in app.ADMIN_REPORT_CATALOG if x['id'] in reports],
                            'hint': 'Данные отчётов аккаунта. Дополнительно нужны права на аккаунт и отчёт.'})
    if 'toptop' in app.ADMIN_CLIENTS and app.ADMIN_CLIENTS['toptop'].get('show_in_dashboard'):
        sources.append({'key': 'galactica:toptop', 'label': 'GALACTICA TOPTOP', 'kind': 'reports',
                        'resources': [{'id': 'context', 'label': 'Контекст и знания'},
                                      {'id': 'tasks', 'label': 'Задачи и итоги'},
                                      {'id': 'markdown', 'label': 'Изменение Markdown'},
                                      {'id': 'manage', 'label': 'Администратор GALACTICA'}],
                        'hint': 'Единая учётная запись TREND. Для записи нужны также задачи и контекст.'})
    return sources


def report_permitted(identity, client, report):
    return bool(identity and (identity.get('is_admin') or permits(identity.get('data_access'), 'marketplace:'+client, report)))


def handle_entitlement(app, handler, parsed):
    from urllib.parse import parse_qs
    from galactica_entitlement import configured_subject, require_current_session, SourceDenied
    try:
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
        if set(params) != {'source','resource'} or any(len(v)!=1 for v in params.values()):
            raise SourceDenied()
        source, resource = params['source'][0], params['resource'][0]
        original_resource=resource
        if source=='galactica:toptop' and resource in {'identity','users'}:
            if 'toptop' not in app.ADMIN_CLIENTS or (app.dashboard_locked_client() and app.dashboard_locked_client()!='toptop'):
                raise SourceDenied()
            token=app.dashboard_access_session_from_cookie(handler.headers.get('Cookie'))
            managed=app.verify_managed_access_token(token)
            admin_token=app.admin_session_from_cookie(handler.headers.get('Cookie'))
            if (resource=='identity' and managed and not managed.get('is_admin')
                and ('toptop' not in managed.get('clients',[]) or not permits(managed.get('data_access'),'galactica:toptop','context'))):
                handler.send_json({'allowed':False,'authenticated':True,'reason_code':'GALACTICA_ACCESS_MISSING',
                                   'username':managed.get('username'),'source_subject_key':str(managed.get('user_id'))},
                                  status=403,headers={'Cache-Control':'no-store'})
                return
            if ((managed and managed.get('is_admin') is True)
                or app.verify_admin_session_token(admin_token)
                or app.verify_dashboard_access_session_token(token)):
                if resource=='identity':
                    handler.send_json({'allowed':True,'source':source,'resource':resource,'source_subject_key':'admin',
                                       'username':'Администратор TREND','role':'owner','acl_admin':True},headers={'Cache-Control':'no-store'})
                else:
                    with app.client_registry_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SET TRANSACTION READ ONLY")
                            cur.execute("SELECT user_id,username,display_name,is_active FROM public.bi_users u WHERE EXISTS(SELECT 1 FROM public.bi_user_clients c WHERE c.user_id=u.user_id AND c.client_key='toptop') ORDER BY username LIMIT 500")
                            users=[dict(r) for r in cur.fetchall()]
                    handler.send_json({'users':users},headers={'Cache-Control':'no-store'})
                return
            resource='context' if resource=='identity' else 'manage'
        token=app.dashboard_access_session_from_cookie(handler.headers.get('Cookie'))
        subject=configured_subject(app,token)
        with app.client_registry_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout='2s'")
            require_current_session(conn, subject)
            require_user_grant(conn,subject[0],source,resource)
            result={'allowed':True,'source':source,'resource':resource,'action':'read'}
            if source == 'galactica:toptop':
                if resource not in {'context','tasks','markdown','manage'} or 'toptop' not in app.ADMIN_CLIENTS:
                    raise SourceDenied()
                locked=app.dashboard_locked_client()
                if (locked and locked != 'toptop') or 'toptop' in app.dashboard_excluded_clients():
                    raise SourceDenied()
                with conn.cursor() as cur:
                    cur.execute('SELECT 1 FROM public.bi_user_clients WHERE user_id=%s AND client_key=%s', (subject[0], 'toptop'))
                    if not cur.fetchone(): raise SourceDenied()
                    cur.execute('SELECT data_access FROM public.bi_users WHERE user_id=%s AND is_active', (subject[0],))
                    row=cur.fetchone()
                grants=row['data_access'] if isinstance(row,dict) else row[0]
                resources=[r for r in ('context','tasks','markdown','manage') if permits(grants,source,r)]
                if 'context' not in resources: raise SourceDenied()
                role='manager' if 'tasks' in resources and 'markdown' in resources else 'contributor' if 'tasks' in resources else 'reader'
                result.update(source_subject_key=str(subject[0]),username=subject[1],
                              session_expires_at=subject[2],source_user_revision=subject[3],
                              client_key='toptop',role=role,acl_admin='manage' in resources)
                result['resource']=original_resource
                if original_resource=='users':
                    if 'manage' not in resources: raise SourceDenied()
                    with conn.cursor() as cur:
                        cur.execute("SELECT user_id,username,display_name,is_active FROM public.bi_users u WHERE EXISTS(SELECT 1 FROM public.bi_user_clients c WHERE c.user_id=u.user_id AND c.client_key='toptop') ORDER BY username LIMIT 500")
                        result={'users':[dict(r) for r in cur.fetchall()]}
            elif source.startswith('marketplace:'):
                from galactica_entitlement import read_entitlement
                read_entitlement(conn,subject,source.split(':',1)[1],resource)
            elif source.startswith('1c'):
                with conn.cursor() as cur:
                    cur.execute('SELECT 1 FROM public.bi_service_credentials WHERE service_key=%s LIMIT 1',(source,))
                    if not cur.fetchone(): raise SourceDenied()
        handler.send_json(result, headers={'Cache-Control':'no-store'})
    except (SourceDenied,ValueError):
        handler.send_json({'allowed':False,'reason_code':'SOURCE_ACCESS_DENIED'},status=403)
    except Exception:
        handler.send_json({'allowed':False,'reason_code':'SOURCE_AUTHORITY_UNAVAILABLE'},status=503)
