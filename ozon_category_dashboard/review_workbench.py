"""Client-local review operations. Durable PostgreSQL state, never implicit publication."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode

import review_replies as replies
from km_trade_finance import connect_km
from marketplace_reviews_dashboard import _filter_context, _where, _json_safe, _table_available

SCHEMA = """
CREATE TABLE IF NOT EXISTS public.review_workbench_settings (
 key text PRIMARY KEY, value jsonb NOT NULL, revision integer NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS public.review_reply_rules (
 id text PRIMARY KEY, name text NOT NULL, spec jsonb NOT NULL, revision integer NOT NULL,
 enabled boolean NOT NULL DEFAULT false, test jsonb, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS public.review_reply_events (
 id bigserial PRIMARY KEY, review_key text, action text NOT NULL, actor text NOT NULL,
 details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS public.review_reply_jobs (
 id text PRIMARY KEY, review_key text NOT NULL, kind text NOT NULL,
 payload jsonb NOT NULL, actor text NOT NULL, status text NOT NULL DEFAULT 'queued',
 message text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS review_reply_jobs_active
 ON public.review_reply_jobs(review_key) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS review_reply_events_key ON public.review_reply_events(review_key,id DESC);
"""
DEFAULT_STYLE = {'tone': 'Вежливо и кратко, 2–4 предложения, обращение на вы.',
                 'signature': '', 'facts': '', 'forbidden': 'Не обещать компенсацию, не просить изменить оценку, не выдумывать свойства.'}
VERSION = '20260913-reviews-presets-v1'
THEMES = {
 'packaging': ('Упаковка', r'упаков|короб|помят|протек|разбит|разлил'),
 'quality': ('Качество', r'качеств|брак|осып|слом|дефект|не работа'),
 'expectation': ('Соответствие ожиданиям', r'не соответств|отличается|ожидал|цвет|оттенок|размер'),
 'delivery': ('Доставка', r'достав|задерж|пункт выдачи|привез'),
 'usage': ('Использование', r'нанос|использ|смыва|держится|стойк'),
}
RISK = re.compile(r'аллерг|ожог|травм|отрав|здоров|суд|претензи|компенсац|вернуть|возврат|жалоб|плохо|ужас|брак|осып|не понрав|не работа', re.I)
SENSITIVE = re.compile(r'аллерг|ожог|травм|отрав|здоров|суд|претензи|компенсац', re.I)
_init_lock = threading.Lock()
_initialized = set()
_workers = {}
_worker_lock = threading.Lock()


def db_key(config):
    return (config.get('host'), config.get('port'), config.get('database'))


def ensure_schema(config):
    key = db_key(config)
    with _init_lock:
        if key in _initialized:
            return
        with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('review-workbench-schema'))")
            cur.execute(replies.SCHEMA)
            cur.execute(SCHEMA)
        _initialized.add(key)


def event(cur, action, actor, key=None, **details):
    cur.execute('INSERT INTO review_reply_events(review_key,action,actor,details) VALUES(%s,%s,%s,%s::jsonb)',
                (key, action, str(actor)[:160], json.dumps(details, ensure_ascii=False)))


def body_text(row):
    return ' '.join(str(row.get(k) or '').strip() for k in ('review_text', 'pros', 'cons')).strip()


def classify(row):
    text = body_text(row)
    return [key for key, (_, pattern) in THEMES.items() if re.search(pattern, text, re.I)]


def safe_row(row):
    fields = ('review_key','marketplace','product_id','seller_article','product_name','review_date','rating',
              'review_text','pros','cons','answer_text','answer_available','answered','source_synced_at')
    result = {key: row.get(key) for key in fields}
    result['themes'] = classify(row)
    result['risk'] = bool(RISK.search(body_text(row))) or (row.get('rating') is not None and row['rating'] <= 3)
    result['draft_status'] = row.get('draft_status')
    result['revision'] = row.get('revision')
    result['job_status'] = row.get('job_status')
    return result


def settings(cur):
    cur.execute("SELECT value,revision FROM review_workbench_settings WHERE key='brand'")
    row = cur.fetchone()
    return {'value': {**DEFAULT_STYLE, **(row['value'] if row else {})}, 'revision': row['revision'] if row else 0}


def queue_payload(config, parsed):
    ensure_schema(config)
    filters = _filter_context(parsed)
    params = parse_qs(parsed.query)
    status = params.get('status', ['unanswered'])[0]
    theme = params.get('theme', [''])[0]
    where, values = _where(filters)
    if theme in THEMES:
        where += " AND concat_ws(' ',review_text,pros,cons) ~* %s"
        values.append(THEMES[theme][1])
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        if not _table_available(conn):
            return dict(ok=True, rows=[], total=0, page=1, total_pages=1, counts={}, jobs=[], rules=[], brand=settings(cur), message='Отзывы ещё не загружены.')
        # Filter predicates execute inside the review-only subquery to avoid ambiguous columns.
        base = f'''SELECT r.*,d.status draft_status,d.revision,j.status job_status
          FROM (SELECT * FROM marketplace_reviews WHERE {where}) r
          LEFT JOIN marketplace_review_reply_drafts d USING(review_key)
          LEFT JOIN LATERAL (SELECT status FROM review_reply_jobs WHERE review_key=r.review_key
            ORDER BY created_at DESC LIMIT 1) j ON true'''
        status_where = {
          'all':'TRUE', 'unanswered':"answer_available AND answered IS FALSE AND COALESCE(draft_status,'') NOT IN ('sent','sending','uncertain','confirmed','external')",
          'unknown':'NOT answer_available OR answered IS NULL',
          'attention':"(rating<=3 OR concat_ws(' ',review_text,pros,cons) ~* %s) AND answered IS NOT TRUE",
          'draft':"draft_status='draft'", 'sent':"draft_status='sent'",
          'confirmed':"draft_status IN ('confirmed','external')", 'uncertain':"draft_status IN ('uncertain','sending')",
          'error':"job_status='error'"}.get(status, 'FALSE')
        status_where = '(' + status_where + ')'
        status_values = [RISK.pattern] if status == 'attention' else []
        allowed_columns = {'product_name','rating','review_text','draft_status','review_date'}
        try: column_filters = json.loads(params.get('column_filters',['{}'])[0])
        except (ValueError,TypeError): raise ValueError('Некорректные фильтры колонок.')
        if not isinstance(column_filters,dict) or len(column_filters)>6: raise ValueError('Некорректные фильтры колонок.')
        for column, condition in column_filters.items():
            if column not in allowed_columns or not isinstance(condition,dict): continue
            value=str(condition.get('value',''))[:200]
            if not value: continue
            op=condition.get('op','contains')
            if column=='rating':
                operators={'eq':'=','neq':'<>','gt':'>','gte':'>=','lt':'<','lte':'<='}
                if op not in operators: continue
                status_where+=f' AND {column} {operators[op]} %s'
                status_values.append(float(value.replace(',','.')))
            else:
                operators={'contains':'ILIKE','not_contains':'NOT ILIKE','eq':'=','neq':'<>'}
                if op not in operators: continue
                status_where+=f" AND COALESCE({column}::text,'') {operators[op]} %s"
                status_values.append('%'+value+'%' if 'contains' in op else value)
        cur.execute(f'''SELECT COUNT(*) total,
          COUNT(*) FILTER(WHERE answer_available AND answered IS FALSE AND COALESCE(draft_status,'') NOT IN ('sent','sending','uncertain','confirmed','external')) unanswered,
          COUNT(*) FILTER(WHERE NOT answer_available OR answered IS NULL) unknown,
          COUNT(*) FILTER(WHERE draft_status='draft') drafts,
          COUNT(*) FILTER(WHERE draft_status IN ('sending','uncertain')) uncertain,
          COUNT(*) FILTER(WHERE draft_status='sent') sent,
          COUNT(*) FILTER(WHERE draft_status IN ('confirmed','external')) confirmed
          FROM ({base}) q''', values)
        counts = dict(cur.fetchone())
        cur.execute(f'SELECT COUNT(*) total FROM ({base}) q WHERE {status_where}', [*values, *status_values])
        total = cur.fetchone()['total']
        pages = max(1, (total + filters['page_size'] - 1)//filters['page_size'])
        page = min(filters['page'], pages)
        order = {'newest':'review_date DESC NULLS LAST,review_key', 'oldest':'review_date ASC NULLS LAST,review_key',
                 'rating':'rating ASC NULLS LAST,review_date,review_key',
                 'priority':"CASE WHEN concat_ws(' ',review_text,pros,cons) ~* %s THEN 0 WHEN rating<=3 THEN 1 ELSE 2 END,review_date ASC NULLS LAST,review_key"}
        sort = params.get('sort',['priority'])[0]
        ordering = order.get(sort,order['newest'])
        sort_values = [RISK.pattern] if sort=='priority' else []
        column=params.get('sort_col',[''])[0]
        if column in allowed_columns:
            direction='DESC' if params.get('sort_dir',['asc'])[0]=='desc' else 'ASC'
            ordering=f'{column} {direction} NULLS LAST,review_key'
            sort_values=[]
        cur.execute(f'SELECT * FROM ({base}) q WHERE {status_where} ORDER BY {ordering} LIMIT %s OFFSET %s',
                    [*values,*status_values,*sort_values,filters['page_size'],(page-1)*filters['page_size']])
        rows = [safe_row(row) for row in cur.fetchall()]
        selected = params.get('review_key',[''])[0]
        if selected:
            cur.execute(f'SELECT * FROM ({base}) q WHERE review_key=%s', [*values,selected])
            found = cur.fetchone()
            selected_row = safe_row(found) if found else None
        else:
            selected_row = None
        cur.execute('SELECT * FROM review_reply_rules ORDER BY updated_at DESC')
        rules = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id,review_key,kind,status,message,created_at FROM review_reply_jobs ORDER BY created_at DESC LIMIT 30")
        jobs = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT status,COUNT(*) n FROM review_reply_jobs GROUP BY status")
        job_summary={row['status']:row['n'] for row in cur.fetchall()}
        cur.execute('SELECT * FROM review_reply_events ORDER BY id DESC LIMIT 100')
        events = [dict(r) for r in cur.fetchall()]
        brand = settings(cur)
    return _json_safe(dict(ok=True, version=VERSION, rows=rows, selected=selected_row, total=total, page=page, total_pages=pages, counts=counts,
                           jobs=jobs, job_summary=job_summary, rules=rules, brand=brand, events=events))


def validate_rule(payload):
    name = str(payload.get('name') or '').strip()[:120]
    spec = payload.get('spec') or {}
    if not name or not isinstance(spec,dict):
        raise ValueError('Укажите название и условия правила.')
    market = spec.get('marketplace','wb')
    mode = spec.get('mode','review')
    if market not in ('wb','ozon') or mode not in ('draft','review','auto'):
        raise ValueError('Некорректная площадка или режим.')
    rating = int(spec.get('rating',5))
    if rating not in range(1,6):
        raise ValueError('Оценка должна быть от 1 до 5.')
    text_mode = spec.get('text_mode','empty')
    if text_mode not in ('empty','any','text'):
        raise ValueError('Некорректное условие текста.')
    if mode=='auto' and (market!='wb' or rating!=5 or text_mode!='empty'):
        raise ValueError('Автоотправка доступна только для WB: 5 звёзд без текста. Остальные случаи — после проверки.')
    template = replies.reply_text(spec.get('template'))
    products = spec.get('products',[])
    if not isinstance(products,list) or len(products)>100 or any(not isinstance(p,str) or len(p)>150 for p in products):
        raise ValueError('Укажите не более 100 SKU.')
    limit = int(spec.get('daily_limit',10))
    start, end = int(spec.get('start_hour',9)), int(spec.get('end_hour',21))
    if not 1<=limit<=100 or not 0<=start<end<=24:
        raise ValueError('Проверьте дневной лимит (1–100) и часы работы (МСК).')
    negative_drafts = spec.get('allow_negative_drafts',False)
    if not isinstance(negative_drafts,bool):
        raise ValueError('Некорректный режим черновиков для низких оценок.')
    if negative_drafts and (mode=='auto' or rating>3):
        raise ValueError('Черновики для низких оценок доступны только для оценок 1–3 без автоотправки.')
    return name, dict(marketplace=market,mode=mode,rating=rating,text_mode=text_mode,
                     template=template,products=products,daily_limit=limit,start_hour=start,end_hour=end,
                     **({'allow_negative_drafts':True} if negative_drafts else {}))


def rule_match(row, spec, *, history=False):
    if row.get('marketplace')!=spec['marketplace']: return False,'Другая площадка'
    if not row.get('answer_available') or row.get('answered') is None: return False,'Статус ответа неизвестен'
    if not history and (row.get('answered') or row.get('answer_text')): return False,'Уже есть ответ'
    if row.get('rating')!=spec['rating']: return False,'Другая оценка'
    if spec['products'] and row.get('product_id') not in spec['products']: return False,'Другой товар'
    text = body_text(row)
    if RISK.search(text) or (row.get('rating') is not None and row['rating']<=3):
        negative_draft = (spec.get('allow_negative_drafts') is True and spec.get('mode') in ('draft','review')
                          and row.get('rating') in (1,2,3) and not SENSITIVE.search(text))
        if not negative_draft: return False,'Жалоба или риск: проверка человеком'
    if spec['text_mode']=='empty' and text: return False,'Есть текст'
    if spec['text_mode']=='text' and not text: return False,'Нет текста'
    return True,'Условия выполнены'


def rule_hash(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def save_rule(config,payload,actor):
    name,spec=validate_rule(payload)
    rule_id=str(payload.get('id') or uuid.uuid4())
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('review-rule:'||%s))",(rule_id,))
        cur.execute('SELECT revision FROM review_reply_rules WHERE id=%s',(rule_id,)); old=cur.fetchone()
        revision=old['revision'] if old else 0
        if int(payload.get('revision',0))!=revision: raise ValueError('Правило изменено. Перезагрузите список.')
        cur.execute('''INSERT INTO review_reply_rules(id,name,spec,revision) VALUES(%s,%s,%s::jsonb,%s)
          ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,spec=EXCLUDED.spec,revision=EXCLUDED.revision,
          enabled=false,test=NULL,updated_at=now()''',(rule_id,name,json.dumps(spec,ensure_ascii=False),revision+1))
        event(cur,'rule_saved',actor,rule_id=rule_id,revision=revision+1)
    return dict(ok=True,id=rule_id,message='Правило сохранено и выключено. Проверьте новую версию на истории.')


def test_rule(config,payload,actor):
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM review_reply_rules WHERE id=%s FOR UPDATE',(payload.get('id'),)); rule=cur.fetchone()
        if not rule: raise ValueError('Правило не найдено.')
        # Stratification includes each rating instead of selecting only latest positives.
        cur.execute('''SELECT * FROM (SELECT *,row_number() OVER(PARTITION BY rating ORDER BY review_date DESC,review_key) n
          FROM marketplace_reviews WHERE marketplace=%s) r WHERE n<=20 ORDER BY rating,review_key''',(rule['spec']['marketplace'],))
        rows=cur.fetchall(); matched=[]; reasons={}
        examples=[]
        for row in rows:
            accepted,reason=rule_match(row,rule['spec'],history=True)
            reasons[reason]=reasons.get(reason,0)+1
            if accepted: matched.append(row)
            if len(examples)<10 or (accepted and not any(e['matched'] for e in examples)):
                examples.append(dict(review_key=row['review_key'],rating=row['rating'],text=body_text(row),matched=accepted,reason=reason,
                                     reply=rule['spec']['template'] if accepted else None))
        result=_json_safe(dict(total=len(rows),matched=len(matched),excluded=len(rows)-len(matched),reasons=reasons,
              examples=examples,hash=rule_hash(rule['spec']),revision=rule['revision'],tested_at=datetime.now(timezone.utc),
              note='Проверены условия правила на истории; текст шаблона требует проверки человеком. Ничего не отправлено.'))
        cur.execute('UPDATE review_reply_rules SET test=%s::jsonb,enabled=false WHERE id=%s',(json.dumps(result,ensure_ascii=False),rule['id']))
        event(cur,'rule_tested',actor,rule_id=rule['id'],total=len(rows),matched=len(matched))
    return dict(ok=True,test=result)


def toggle_rule(config,payload,actor):
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM review_reply_rules WHERE id=%s FOR UPDATE',(payload.get('id'),)); rule=cur.fetchone()
        if not rule: raise ValueError('Правило не найдено.')
        enabled=payload.get('enabled') is True
        if enabled:
            test=rule.get('test') or {}
            if test.get('hash')!=rule_hash(rule['spec']) or not test.get('matched'):
                raise ValueError('Сначала проверьте текущую версию на истории: нужен хотя бы один подходящий пример.')
            if payload.get('confirmed') is not True or payload.get('revision')!=rule['revision']:
                raise ValueError('Подтвердите условия, текст и режим текущей версии правила.')
            # No new automatic messages can be authorized by changing a pre-enabled rule.
        cur.execute('UPDATE review_reply_rules SET enabled=%s,updated_at=now() WHERE id=%s',(enabled,rule['id']))
        event(cur,'rule_enabled' if enabled else 'rule_paused',actor,rule_id=rule['id'],revision=rule['revision'],mode=rule['spec']['mode'])
    return dict(ok=True,message='Правило включено.' if enabled else 'Правило на паузе. Уже отправленные запросы не отзываются.')


def save_brand(config,payload,actor):
    value=payload.get('value') or {}
    if not isinstance(value,dict): raise ValueError('Некорректный профиль бренда.')
    clean={k:str(value.get(k,'')).strip()[:2000] for k in DEFAULT_STYLE}
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('review-brand'))")
        old=settings(cur)
        if payload.get('revision')!=old['revision']: raise ValueError('Профиль изменён в другой вкладке. Обновите его.')
        cur.execute("INSERT INTO review_workbench_settings(key,value,revision) VALUES('brand',%s::jsonb,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,revision=EXCLUDED.revision",(json.dumps(clean,ensure_ascii=False),old['revision']+1))
        event(cur,'brand_saved',actor,revision=old['revision']+1)
    return dict(ok=True,message='Стиль бренда сохранён. Уже созданные черновики не изменены.')


def enqueue(config,payload,actor):
    kind=payload.get('kind','generate')
    if kind not in ('generate','reconcile'): raise ValueError('Неизвестный тип задания.')
    keys=payload.get('review_keys',[])
    if not isinstance(keys,list) or not 1<=len(keys)<=25: raise ValueError('Выберите от 1 до 25 отзывов на текущей странице.')
    keys=list(dict.fromkeys(keys))
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        # Validate complete batch before writing a single job.
        rows=[replies.source(cur,key) for key in keys]
        queued=0
        for row in rows:
            draft=replies.read_draft(cur,row['review_key'])
            if kind=='generate' and (draft or row.get('answered') or row.get('answer_text')): continue
            if kind=='reconcile' and row['marketplace']!='wb': continue
            job_id=str(uuid.uuid4())
            cur.execute("INSERT INTO review_reply_jobs(id,review_key,kind,payload,actor) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING id",
                        (job_id,row['review_key'],kind,json.dumps({'revision':0}),actor))
            if cur.fetchone(): queued+=1
        event(cur,'batch_queued',actor,kind=kind,requested=len(keys),queued=queued)
    return dict(ok=True,queued=queued,message=f'В очереди: {queued} из {len(keys)}. Существующие черновики не перезаписываются.')


def reconcile(config,key,token,actor,request=None):
    request=request or replies.wb_request
    if not token: raise ValueError('Доступ к отзывам WB не подключён.')
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        # Same lock as send; hold it across readback to serialize with a concurrent publisher.
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('review-reply:'+key,))
        row=replies.source(cur,key); draft=replies.read_draft(cur,key)
        if row['marketplace']!='wb': raise ValueError('Сверка публикации пока доступна только для WB.')
        live=request(token,'GET','/api/v1/feedback?'+urlencode({'id':row['source_review_id']})).get('data')
        if not isinstance(live,dict) or str(live.get('id'))!=row['source_review_id']: raise ValueError('WB не подтвердил исходный ID отзыва.')
        answer=live.get('answer')
        text=answer.get('text','') if isinstance(answer,dict) else ''
        state=answer.get('state','') if isinstance(answer,dict) else ''
        if text:
            # WB state=none is a pending moderation answer; never claim public confirmation.
            status='sent' if state!='wbRu' else ('confirmed' if draft and draft['draft_text'].strip()==text.strip() else 'external')
            if draft:
                cur.execute('UPDATE marketplace_review_reply_drafts SET status=%s,message=%s,updated_at=now() WHERE review_key=%s',
                            (status,'Ответ найден при сверке WB.' if status!='sent' else 'Ответ ожидает модерации WB.',key))
            cur.execute('UPDATE marketplace_reviews SET answer_available=true,answered=true,answer_text=%s WHERE review_key=%s',(text,key))
            message='Ответ найден в WB.' if status!='sent' else 'Ответ ожидает модерации WB.'
        else:
            status='uncertain' if draft and draft['status'] in ('sending','sent','uncertain') else 'unanswered'
            if status=='uncertain':
                cur.execute("UPDATE marketplace_review_reply_drafts SET status='uncertain',message=%s,updated_at=now() WHERE review_key=%s",
                            ('Ответ пока не найден. Автоматический повтор заблокирован.',key))
            message='Ответ не найден. Повтор ранее начатой отправки заблокирован.' if status=='uncertain' else 'WB подтвердил отсутствие ответа.'
        event(cur,'reconciled',actor,key,status=status,message=message)
    return dict(ok=True,message=message,status=status)


def dispatch(config,payload,actor):
    ensure_schema(config)
    action=payload.get('action')
    if action=='save_rule': return save_rule(config,payload,actor)
    if action=='test_rule': return test_rule(config,payload,actor)
    if action=='toggle_rule': return toggle_rule(config,payload,actor)
    if action=='save_brand': return save_brand(config,payload,actor)
    if action=='enqueue': return enqueue(config,payload,actor)
    if action=='pause_all':
        with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
            cur.execute('UPDATE review_reply_rules SET enabled=false WHERE enabled')
            cur.execute("UPDATE review_reply_jobs SET status='cancelled',message='Отменено пользователем',updated_at=now() WHERE status='queued'")
            event(cur,'paused_all',actor)
        return dict(ok=True,message='Правила выключены, ожидающие задания отменены. Текущую отправку нельзя отозвать.')
    raise ValueError('Неизвестное действие.')


def worker_rule(config,rule_id,revision):
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM review_reply_rules WHERE id=%s',(rule_id,)); rule=cur.fetchone()
        if not rule or not rule['enabled'] or rule['revision']!=revision:
            raise ValueError('Правило выключено или изменилось. Задание остановлено.')
        return rule


def schedule_rules(config):
    """Schedule only enabled rules; overlap routes to a human instead of arbitrary precedence."""
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        if not _table_available(conn): return
        cur.execute('SELECT * FROM review_reply_rules WHERE enabled ORDER BY id'); rules=cur.fetchall()
        if not rules: return
        cur.execute('''SELECT r.* FROM marketplace_reviews r LEFT JOIN marketplace_review_reply_drafts d USING(review_key)
          WHERE answer_available AND answered IS FALSE AND d.review_key IS NULL
          AND source_synced_at>=now()-interval '24 hours'
          AND NOT EXISTS(SELECT 1 FROM review_reply_jobs j WHERE j.review_key=r.review_key)
          ORDER BY review_date DESC,review_key LIMIT 500''')
        for row in cur.fetchall():
            matches=[rule for rule in rules if rule_match(row,rule['spec'])[0]]
            if len(matches)!=1: continue
            rule=matches[0]
            cur.execute("INSERT INTO review_reply_jobs(id,review_key,kind,payload,actor) VALUES(%s,%s,'template',%s::jsonb,'rule') ON CONFLICT DO NOTHING",
              (str(uuid.uuid4()),row['review_key'],json.dumps({'rule_id':rule['id'],'rule_revision':rule['revision']})))


def auto_allowed(cur, rule):
    spec=rule['spec']; now=datetime.now(timezone(timedelta(hours=3)))
    if not spec['start_hour']<=now.hour<spec['end_hour']: return False
    cur.execute("SELECT COUNT(*) n FROM review_reply_events WHERE action='auto_reserved' AND created_at >= date_trunc('day',now() AT TIME ZONE 'Europe/Moscow') AT TIME ZONE 'Europe/Moscow'")
    return cur.fetchone()['n']<spec['daily_limit']


def limited_request(config,token,method,path,body=None,rule=None,cooldown=780):
    """Conservative background limit, persisted across restarts (base token: at most 5/h).

    Manual UI sends use the existing publisher; background calls allow 13 minutes per request.
    Waiting occurs in this dedicated worker, never in the HTTP handler.
    """
    last_progress=0
    while True:
        if rule: worker_rule(config,rule['id'],rule['revision'])
        with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('review-background-rate'))")
            cur.execute("SELECT value FROM review_workbench_settings WHERE key='next_request'"); row=cur.fetchone()
            remaining=(float(row['value'])-time.time()) if row else 0
            if remaining<=0:
                cur.execute("INSERT INTO review_workbench_settings(key,value) VALUES('next_request',%s::jsonb) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(json.dumps(time.time()+cooldown),))
                break
        if time.monotonic()-last_progress>60:
            print(f'ПРОГРЕСС: очередь WB | ожидание лимита {round(remaining)}с | ETA запроса {round(remaining/60,1)} мин',flush=True)
            last_progress=time.monotonic()
        time.sleep(min(5,remaining))
    if rule: worker_rule(config,rule['id'],rule['revision'])
    return replies.wb_request(token,method,path,body)


def automatic_request(config,rule):
    def request(token,method,path,body=None):
        worker_rule(config,rule['id'],rule['revision'])
        if method=='GET':
            result=limited_request(config,token,method,path,rule=rule,cooldown=1560)
            try: remaining=int(result.get('_rate_remaining',0))
            except (ValueError,TypeError): remaining=0
            if remaining<1:
                raise ValueError('WB не подтвердил запас лимита для безопасной пары проверка/отправка. Черновик сохранён; используйте ручной режим.')
            return result
        # Never sleep for minutes between preflight and POST: another operator may answer meanwhile.
        time.sleep(.4)
        current=worker_rule(config,rule['id'],rule['revision'])
        hour=datetime.now(timezone(timedelta(hours=3))).hour
        if not current['spec']['start_hour']<=hour<current['spec']['end_hour']:
            raise ValueError('Рабочее время правила закончилось. Отправка остановлена.')
        return replies.wb_request(token,method,path,body)
    return request


def process_job(config,job,brand,token_provider):
    key=job['review_key']; kind=job['kind']; payload=job['payload']
    if kind=='generate':
        with closing(connect_km(config)) as conn, conn, conn.cursor() as cur: style=settings(cur)['value']
        return replies.generate(config,dict(review_key=key,revision=payload.get('revision',0)),brand,style=style)
    if kind=='reconcile':
        return reconcile(config,key,token_provider(),job['actor'],request=lambda token,method,path:limited_request(config,token,method,path))
    rule=worker_rule(config,payload['rule_id'],payload['rule_revision'])
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        row=replies.source(cur,key); old=replies.read_draft(cur,key)
        matches,reason=rule_match(row,rule['spec'])
        if not matches: raise ValueError(reason)
        synced=row.get('source_synced_at')
        if not synced or datetime.now(timezone.utc)-synced>timedelta(hours=24): raise ValueError('Данные отзыва старше 24 часов. Обновите источник.')
        if old: raise ValueError('Уже есть черновик. Автоматическая замена запрещена.')
        if rule['spec']['mode']=='auto':
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('review-auto-limit'))")
            if not auto_allowed(cur,rule): return {'deferred':True}
            event(cur,'auto_reserved','rule',key,rule_id=rule['id'],revision=rule['revision'])
    result=replies.store_draft(config,key,rule['spec']['template'],0,replies.fingerprint(row),'approved-template')
    if rule['spec']['mode']!='auto': return result
    worker_rule(config,rule['id'],rule['revision'])
    draft=result['draft']
    # The current saved rule is the owner's explicit authorization for this exact template/scope.
    result=replies.publish(config,dict(review_key=key,revision=draft['revision'],text=draft['draft_text'],confirmed=True),
                           token_provider(),request=automatic_request(config,rule))
    return result


def worker_loop(config,brand,token_provider):
    # Session lock prevents multiple app processes from executing one client's jobs.
    with closing(connect_km(config)) as owner:
        with owner.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('review-workbench-worker')) locked")
            if not cur.fetchone()['locked']: return
        owner.commit()
        print('ПЛАН: очередь отзывов из PostgreSQL | по одному заданию | генерация до 150с | фоновые запросы WB через 13 минут | прогресс и ошибки в интерфейсе',flush=True)
        with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
            cur.execute("UPDATE review_reply_jobs SET status='error',message='Обработка прервана перезапуском. Проверьте черновик или сверку.',updated_at=now() WHERE status='running'")
        while True:
            try:
                schedule_rules(config)
                with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
                    # Reconcile durable ambiguous/accepted sends, but never resend them.
                    if _table_available(conn):
                        cur.execute("""INSERT INTO review_reply_jobs(id,review_key,kind,payload,actor)
                          SELECT md5(d.review_key||date_trunc('hour',now())::text),d.review_key,'reconcile','{}','reconciler'
                          FROM marketplace_review_reply_drafts d JOIN marketplace_reviews r USING(review_key)
                          WHERE r.marketplace='wb' AND d.status IN ('sent','sending','uncertain')
                          AND d.updated_at<now()-interval '15 minutes'
                          AND NOT EXISTS(SELECT 1 FROM review_reply_jobs j WHERE j.review_key=d.review_key AND j.updated_at>now()-interval '1 hour')
                          LIMIT 1 ON CONFLICT DO NOTHING""")
                    cur.execute("SELECT * FROM review_reply_jobs WHERE status='queued' AND (message='' OR updated_at<now()-interval '1 minute') ORDER BY CASE WHEN kind='template' THEN 1 ELSE 0 END,created_at FOR UPDATE SKIP LOCKED LIMIT 1")
                    job=cur.fetchone()
                    if job:
                        cur.execute("UPDATE review_reply_jobs SET status='running',updated_at=now() WHERE id=%s",(job['id'],))
                if not job:
                    time.sleep(10); continue
                started=time.monotonic()
                try:
                    result=process_job(config,job,brand,token_provider)
                    status='queued' if result.get('deferred') else ('done' if result.get('ok',True) else 'error')
                    message='Ожидает расписания или дневного лимита.' if status=='queued' else result.get('message','Черновик готов.')
                except Exception as exc:
                    status='error'
                    message=str(exc) if isinstance(exc,ValueError) else 'Обработка не завершена. Проверьте подключение и журнал.'
                with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
                    cur.execute('UPDATE review_reply_jobs SET status=%s,message=%s,updated_at=now() WHERE id=%s',(status,message[:500],job['id']))
                    event(cur,'job_'+status,job['actor'],job['review_key'],kind=job['kind'],message=message[:500],elapsed_seconds=round(time.monotonic()-started))
                    cur.execute("SELECT COUNT(*) total,COUNT(*) FILTER(WHERE status IN ('done','error','cancelled')) completed,COUNT(*) FILTER(WHERE status='error') errors FROM review_reply_jobs")
                    progress=cur.fetchone()
                print(f"ПРОГРЕСС: {progress['completed']}/{progress['total']} | {job['kind']} | {status} | ошибок {progress['errors']} | {round(time.monotonic()-started)}с | ETA зависит от типа задания и лимита WB",flush=True)
                time.sleep(10 if status=='queued' else 1)
            except Exception:
                # Keep the worker alive; never print connection details or private response payloads.
                print('Отзывы: временная ошибка обработчика; повтор чтения через 30с.',flush=True)
                time.sleep(30)


def start_worker(config,brand,token_provider):
    ensure_schema(config)
    key=db_key(config)
    with _worker_lock:
        if key in _workers and _workers[key].is_alive(): return
        thread=threading.Thread(target=worker_loop,args=(dict(config),brand,token_provider),daemon=True,name='review-workbench')
        _workers[key]=thread; thread.start()


def insights(config,parsed):
    filters=_filter_context(parsed); where,values=_where(filters)
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        if not _table_available(conn): return dict(ok=True,themes=[],text_count=0,coverage=0,sources=[],comparison=None)
        cur.execute(f"SELECT COUNT(*) n FROM marketplace_reviews WHERE {where} AND NULLIF(trim(concat_ws(' ',review_text,pros,cons)),'') IS NOT NULL",values)
        text_count=cur.fetchone()['n']
        themes=[]
        for key,(label,pattern) in THEMES.items():
            cur.execute(f"SELECT COUNT(*) n FROM marketplace_reviews WHERE {where} AND concat_ws(' ',review_text,pros,cons) ~* %s",[*values,pattern])
            n=cur.fetchone()['n']
            themes.append(dict(key=key,label=label,count=n,share=round(n/text_count*100,1) if text_count else None))
        cur.execute("""SELECT marketplace,COUNT(*) rows,MIN(review_date) date_from,MAX(review_date) date_to,
          MAX(source_synced_at) synced_at,COUNT(*) FILTER(WHERE answer_available AND answered IS NOT NULL) answer_observed
          FROM marketplace_reviews WHERE (%s='total' OR marketplace=%s) GROUP BY marketplace""",(filters['marketplace'],filters['marketplace']))
        sources=[dict(r) for r in cur.fetchall()]
        from marketplace_reviews_dashboard import _last_run
        for s in sources:
            run=_last_run(conn,s['marketplace'])
            s['last_status']=run.get('status') if run else None
        comparison=None
        if filters['date_from'] and filters['date_to']:
            width=(filters['date_to']-filters['date_from']).days+1
            if width>0:
                previous={**filters,'date_to':filters['date_from']-timedelta(days=1),'date_from':filters['date_from']-timedelta(days=width)}
                pw,pv=_where(previous)
                cur.execute(f'SELECT COUNT(*) total,AVG(rating) average_rating,COUNT(*) FILTER(WHERE rating<=3) negative FROM marketplace_reviews WHERE {pw}',pv)
                comparison=dict(cur.fetchone()); comparison.update(date_from=previous['date_from'],date_to=previous['date_to'],
                    comparable=bool(sources) and all(s['date_from'] and s['date_from']<=previous['date_from'] and s['date_to']>=filters['date_to'] for s in sources),
                    note='По загруженным отзывам; полнота всего кабинета не подтверждена.')
    return _json_safe(dict(ok=True,themes=themes,text_count=text_count,coverage=text_count,sources=sources,comparison=comparison,
                           methodology='Словарные упоминания, не ИИ-диагноз. Один отзыв может иметь несколько тем. Темы не доказывают причину проблемы.'))
