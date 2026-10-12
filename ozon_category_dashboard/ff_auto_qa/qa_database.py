"""Isolated PostgreSQL integration check; uses a disposable schema, no account token."""
import json
import os
import sys
import types
import uuid
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/workspace/ozon_category_dashboard')
import pulse_vps_admin as rt
rt.configure_scope()
schema = 'ff_qa_7ec090b4'
module = types.ModuleType('ff_qa')
module.__file__ = str(Path(__file__).with_name('ff_platform.py'))
source = Path(module.__file__).read_text().replace('ff_platform.', schema + '.')
source = source.replace('SCHEMA IF NOT EXISTS ff_platform', 'SCHEMA IF NOT EXISTS ' + schema)
source = source.replace('SCHEMA ff_platform', 'SCHEMA ' + schema)
source = source.replace('public.bi_service_credentials', schema + '.credentials')
exec(compile(source, module.__file__, 'exec'), module.__dict__)
ff = module
conn_factory = rt.app.client_registry_connection

def ensure_credentials(conn):
    with conn.cursor() as cur:
        cur.execute('CREATE TABLE IF NOT EXISTS ' + schema + '.credentials(service_key text,credential_key text,encrypted_value bytea,fingerprint text,updated_at timestamptz default now(),primary key(service_key,credential_key))')
    conn.commit()

app = SimpleNamespace(client_registry_connection=conn_factory,ensure_service_credentials_schema=ensure_credentials,
                      ADMIN_CLIENTS={'toptop': {}, 'lera_nena': {}},
                      client_credentials_master_key=lambda **kw: 'isolated-fixture-key')

def fixture_token(service, field):
    with conn_factory() as db:
        with db.cursor() as cur:
            cur.execute('SELECT pgp_sym_decrypt(encrypted_value,%s) value FROM ' + schema + '.credentials WHERE service_key=%s AND credential_key=%s', ('isolated-fixture-key', service, field))
            row = cur.fetchone()
            return row['value'] if row else ''
app.service_credential = fixture_token

def stock(warehouse, qty):
    return {'nomenclatureId': {'value': 12}, 'skladId': {'value': int(warehouse)},
            'clientArticul': '0012', 'name': 'Fixture', 'numAvailable': qty,
            'numDefect': 1, 'numAtWork': 2, 'typeNomenclature': 'g'}

def run_job(client, config):
    job = str(uuid.uuid4())
    with conn_factory() as db:
        with db.cursor() as cur:
            cur.execute('INSERT INTO ' + schema + '.jobs(id,client_key,status,request) VALUES(%s,%s,\'queued\',%s)', (job, client, json.dumps(config)))
    ff.worker(app, job)
    return job

try:
    ff.ensure(app)
    ff.save(app, 'toptop', {'token': 'fixture-token', 'warehouse_ids': [3, 4], 'nomenclature_types': ['g']})
    with patch.object(ff, 'request_json', return_value=[{'id': 3, 'name': 'A'}, {'id': 4, 'name': 'B'}]):
        assert ff.check(app, 'toptop')[0] == 'connected'
    ff.save(app, 'toptop', {'warehouse_ids': [3, 4], 'nomenclature_types': ['g']})
    assert ff.status_payload(app, 'toptop')['connection']['check_status'] == 'connected'
    assert len(ff.status_payload(app, 'toptop')['connection']['settings']['warehouses']) == 2
    config = {'warehouse_ids': ['3', '4'], 'nomenclature_types': ['g']}
    def source(token, path, params=None):
        if path == '/sklads':
            return [{'id': 3, 'name': 'A'}, {'id': 4, 'name': 'B'}]
        if path == '/changed_stocks':
            return [{'skladId': int(params['sklad_id']), 'nomenclature': {'prodType': 'g'}}]
        return [stock(params['skladId'], 7)] if params['typeNomenclature']=='g' else []
    with patch.object(ff, 'request_json', side_effect=source):
        first = run_job('toptop', config)
    status = ff.status_payload(app, 'toptop')
    assert status['summary']['rows'] == 2 and status['summary']['available'] == 14
    assert status['jobs'][0]['status'] == 'completed'
    assert ff.status_payload(app, 'lera_nena')['summary']['rows'] == 0
    assert 'fixture-token' not in json.dumps(status, default=str)
    assert ff.preview(app, 'toptop', {})['rows'][0]['client_article'] == '0012'
    # Second warehouse fails: first scope must not partially replace old stock.
    def failed_source(token, path, params=None):
        if params and params.get('skladId')=='4':
            return {'error': 'fixture-token'}
        return source(token, path, params)
    with patch.object(ff, 'request_json', side_effect=failed_source):
        run_job('toptop', config)
    status = ff.status_payload(app, 'toptop')
    assert status['jobs'][0]['status'] == 'failed' and status['summary']['available'] == 14
    assert 'fixture-token' not in json.dumps(status, default=str)
    # Unique active jobs and no configuration replacement during import.
    with conn_factory() as db:
        with db.cursor() as cur:
            pending = str(uuid.uuid4())
            cur.execute('INSERT INTO ' + schema + '.jobs(id,client_key,status,request) VALUES(%s,\'toptop\',\'queued\',%s)', (pending, json.dumps(config)))
    try:
        ff.save(app, 'toptop', {'token': 'replacement', 'warehouse_ids': [3], 'nomenclature_types': ['g']})
        raise AssertionError('active import must lock settings')
    except ff.ImportErrorSafe:
        pass
    try:
        ff.start(app, 'toptop', {})
        raise AssertionError('active import must reject duplicate start')
    except ff.ImportErrorSafe:
        pass
    with conn_factory() as db:
        with db.cursor() as cur:
            cur.execute('UPDATE ' + schema + '.jobs SET status=\'failed\' WHERE id=%s', (pending,))
    ff.disconnect(app, 'toptop')
    status = ff.status_payload(app, 'toptop')
    assert not status['saved'] and status['summary']['rows'] == 2
    print(json.dumps({'ok': True, 'checks': ['encrypted fixture token/save/check', 'atomic multi-scope publish', 'failure preserves previous stock', 'client isolation', 'active job concurrency guard', 'zero stock and article formatting', 'disconnect retains snapshots', 'no token in responses'], 'production_credentials_read': False}))
finally:
    with conn_factory() as db:
        with db.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE')
