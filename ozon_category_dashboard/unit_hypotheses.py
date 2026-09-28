"""Append-only hypothesis revisions. Seller data is never modified."""
from datetime import date
from uuid import UUID, uuid4
import json
import math
from psycopg2.extras import Json

SCHEMA = '''CREATE TABLE IF NOT EXISTS unit_hypothesis_versions (
 id uuid PRIMARY KEY, project_id uuid NOT NULL, revision integer NOT NULL,
 name text NOT NULL, kind text NOT NULL, status text NOT NULL,
 settings jsonb NOT NULL, source_period jsonb NOT NULL, tracking_period jsonb NOT NULL,
 products jsonb NOT NULL, plan jsonb NOT NULL, model_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(project_id,revision));'''

def clean_period(period):
    if not isinstance(period,dict): raise ValueError('Укажите период гипотезы')
    begin=date.fromisoformat(str(period.get('from')));end=date.fromisoformat(str(period.get('to')))
    if begin>end or (end-begin).days>366: raise ValueError('Период гипотезы: от 1 до 367 дней')
    return {'from':str(begin),'to':str(end)}

def validate_project(project):
    if not isinstance(project,dict): raise ValueError('Нужны параметры гипотезы')
    if project.get('kind') not in ('scenario','promotions'): raise ValueError('Неизвестный вид гипотезы')
    if project.get('status') not in ('draft','running','completed'): raise ValueError('Неизвестный статус гипотезы')
    project_id=str(UUID(str(project.get('project_id') or uuid4())))
    revision=project.get('expected_revision',0)
    if isinstance(revision,bool) or not isinstance(revision,int) or revision<0: raise ValueError('Некорректная версия гипотезы')
    tracking=clean_period(project.get('tracking_period'))
    products=project.get('products');plan=project.get('plan')
    if not isinstance(products,list) or not 1<=len(products)<=10000 or any(not isinstance(k,str) or len(k)>300 for k in products) or len(set(products))!=len(products): raise ValueError('Выберите от 1 до 10000 уникальных товаров')
    if not isinstance(plan,list) or len(plan)!=len(products) or {p.get('key') for p in plan if isinstance(p,dict)}!=set(products): raise ValueError('План должен содержать каждый выбранный товар ровно один раз')
    numeric=('cogs','advertising','units','price','revenue','profit','comparable','baseline_revenue','baseline_comparable')
    allowed={'key','article','marketplace','cabinet','basis',*numeric}
    for row in plan:
        if not set(row)<=allowed: raise ValueError('Неизвестные поля плана')
        for key in numeric:
            v=row.get(key)
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1e16): raise ValueError('План содержит некорректную сумму')
        if row.get('units') is None or row['units']<0 or row.get('price') is None or row['price']<=0 or row.get('revenue') is None or row.get('profit') is None: raise ValueError('Сначала рассчитайте все выбранные товары')
        if abs(row['revenue']-row['units']*row['price'])>0.02: raise ValueError('Выручка плана не соответствует объёму и цене')
        if row['marketplace'] not in ('wb','ozon','yandex') or not row['key'].startswith(row['marketplace']+'|'+str(row['cabinet'])+'|'): raise ValueError('Неверная принадлежность товара')
    if len(json.dumps(project,allow_nan=False))>4000000: raise ValueError('Слишком большой проект')
    return project_id,revision,tracking,products,plan

def save(cur,client,payload,settings):
    project=payload['hypothesis']
    project_id,expected,tracking,products,plan=validate_project(project)
    cur.execute('SELECT campaign_id FROM yandex_dim_store WHERE client_key=%s',(client,))
    cabinets={str(r['campaign_id']) for r in cur.fetchall()}
    for row in plan:
        if row['marketplace']=='yandex' and str(row['cabinet']) not in cabinets or row['marketplace']!='yandex' and row['cabinet']!=client: raise ValueError('Кабинет не принадлежит аккаунту')
    cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(project_id,))
    cur.execute('SELECT * FROM unit_hypothesis_versions WHERE project_id=%s ORDER BY revision DESC LIMIT 1',(project_id,))
    previous=cur.fetchone()
    revision=previous['revision'] if previous else 0
    if revision!=expected: raise ValueError('Гипотеза изменена в другой вкладке. Откройте актуальную версию')
    # After launch only lifecycle status can change; preserve the original plan.
    if previous and previous['status'] in ('running','completed'):
        if project['status']=='draft' or previous['status']=='completed' and project['status']!='completed': raise ValueError('Запущенный план зафиксирован. Создайте новую гипотезу для изменений')
        settings=previous['settings'];plan=previous['plan'];products=previous['products'];tracking=previous['tracking_period']
        payload={**payload,'name':previous['name'],'source_period':previous['source_period'],'model_version':previous['model_version']}
    cur.execute('''INSERT INTO unit_hypothesis_versions(id,project_id,revision,name,kind,status,settings,source_period,tracking_period,products,plan,model_version)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
      (str(uuid4()),project_id,revision+1,payload['name'],previous['kind'] if previous else project['kind'],project['status'],Json(settings),Json(clean_period(payload['source_period'])),Json(tracking),Json(products),Json(plan),payload['model_version']))
    return serialize(cur.fetchone())

def serialize(row):
    return {**dict(row),'ok':True,'id':str(row['id']),'project_id':str(row['project_id']),'created_at':str(row['created_at'])}

def list_projects(cur):
    cur.execute('SELECT DISTINCT ON(project_id) * FROM unit_hypothesis_versions ORDER BY project_id,revision DESC')
    return sorted((serialize(r) for r in cur.fetchall()),key=lambda r:r['created_at'],reverse=True)
