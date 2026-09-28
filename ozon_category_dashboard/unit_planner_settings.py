"""Explicit, versioned saving of unit planner inputs in the client database."""
import json
from km_trade_finance import connect_km
from unit_economics_workspace import checked_config

def validate(settings):
    if not isinstance(settings, dict) or len(json.dumps(settings)) > 1000000:
        raise ValueError('Некорректные вводные')
    allowed={'global','overrides','warehouse','logistics','market','method','wms','target'}
    if set(settings)-allowed: raise ValueError('Неизвестные параметры')
    def values(obj):
        if not isinstance(obj,dict): raise ValueError('Некорректные параметры')
        for k,v in obj.items():
            if not isinstance(k,str) or len(k)>200: raise ValueError('Некорректное поле')
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not __import__('math').isfinite(v)):
                raise ValueError('Параметры должны быть числами')
    values(settings.get('global',{}))
    from pl_financial_model import tax_profile
    tax_profile(settings.get('global',{}))
    for key in ('overrides','warehouse'):
        if not isinstance(settings.get(key,{}),dict): raise ValueError('Некорректные условия')
        for v in settings.get(key,{}).values(): values(v)
    for k in ('market','method','wms','target'):
        if k in settings and (not isinstance(settings[k],str) or len(settings[k])>100): raise ValueError('Некорректные условия')
    if not isinstance(settings.get('logistics',{}),dict): raise ValueError('Некорректная логистика')
    return settings

def load(config,client):
    checked_config(config,client)
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('unit_planner_input_versions') present")
        if not cur.fetchone()['present']: return {'settings':None,'revision':0}
        cur.execute('SELECT revision,settings,saved_at::text FROM unit_planner_input_versions ORDER BY revision DESC LIMIT 1')
        return dict(cur.fetchone() or {'settings':None,'revision':0})

def save(config,client,payload):
    checked_config(config,client); settings=validate(payload.get('settings'))
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('unit_planner_inputs'))")
        cur.execute('CREATE TABLE IF NOT EXISTS unit_planner_input_versions (revision bigserial PRIMARY KEY, settings jsonb NOT NULL, saved_at timestamptz NOT NULL DEFAULT now())')
        cur.execute('SELECT revision,settings,saved_at::text FROM unit_planner_input_versions ORDER BY revision DESC LIMIT 1')
        previous=cur.fetchone()
        if previous and previous['settings']==settings: return dict(previous)
        if int(payload.get('revision',0)) != (previous['revision'] if previous else 0):
            raise ValueError('Вводные изменены в другой вкладке. Обнови страницу перед сохранением.')
        cur.execute('INSERT INTO unit_planner_input_versions(settings) VALUES (%s::jsonb) RETURNING revision,settings,saved_at::text',(json.dumps(settings,allow_nan=False),))
        return dict(cur.fetchone())
