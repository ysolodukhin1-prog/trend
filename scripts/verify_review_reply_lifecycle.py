"""Real DB rollback tests. Marketplace network is replaced with a local stub."""
import sys,json
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from urllib.error import URLError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ozon_category_dashboard'))
import app,review_replies as r
from km_trade_finance import connect_km
print('ПЛАН: 2 аккаунта; сохранение, конфликт версии, отправка и таймаут на mock API; все записи откатываются.',flush=True)
results=[]
for index,client in enumerate(('toptop','lera_nena'),1):
    cfg=app.read_db_config();cfg['database']=client
    with connect_km(cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(r.SCHEMA)
                cur.execute("SELECT review_key,source_review_id FROM marketplace_reviews r WHERE marketplace='wb' AND answered IS FALSE AND NOT EXISTS(SELECT 1 FROM marketplace_review_reply_drafts d WHERE d.review_key=r.review_key) LIMIT 1")
                row=cur.fetchone();assert row
            key=row['review_key'];rid=row['source_review_id'];calls=[]
            @contextmanager
            def transaction(_):yield conn
            def request(token,method,path,body=None):
                calls.append(method)
                return {'data':{'id':rid,'answer':None}} if method=='GET' else {}
            def timeout(token,method,path,body=None):
                if method=='POST':raise URLError('simulated timeout')
                return {'data':{'id':rid,'answer':None}}
            with patch.object(r,'connect_km',transaction):
                draft=r.save(cfg,dict(review_key=key,revision=0,text='Тестовый черновик. Только rollback.'))['draft']
                assert draft['revision']==1 and r.load(cfg,key)['draft']['draft_text']==draft['draft_text']
                try:r.save(cfg,dict(review_key=key,revision=0,text='stale'))
                except ValueError:pass
                else:raise AssertionError('stale revision accepted')
                payload=dict(review_key=key,revision=1,text=draft['draft_text'],confirmed=True)
                assert r.publish(cfg,payload,'TEST',request)['draft']['status']=='sent'
                try:r.publish(cfg,payload,'TEST',request)
                except ValueError:pass
                else:raise AssertionError('duplicate accepted')
                assert calls.count('POST')==1
                with conn.cursor() as cur:cur.execute("UPDATE marketplace_review_reply_drafts SET status='draft' WHERE review_key=%s",(key,))
                assert r.publish(cfg,payload,'TEST',timeout)['draft']['status']=='uncertain'
                try:r.save(cfg,dict(review_key=key,revision=1,text='overwrite uncertain'))
                except ValueError:pass
                else:raise AssertionError('uncertain status overwritten')
                try:r.load(cfg,'wb:QA-NO-SUCH-REVIEW')
                except ValueError:pass
                else:raise AssertionError('foreign review accepted')
            results.append(dict(client=client,checks=7,network_publications=0,rollback=True))
        finally:conn.rollback()
    print(f'ПРОГРЕСС: {index}/2 ({index*50}%) | {client} | 7 проверок пройдены; изменения откачены',flush=True)
(ROOT/'reports/review_reply_lifecycle_20260913.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('ИТОГ: 14 проверок пройдены; публикаций в площадках 0.',flush=True)
