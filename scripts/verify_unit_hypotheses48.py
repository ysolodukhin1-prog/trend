"""Exercise both real client databases inside explicit rolled-back transactions."""
import copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'ozon_category_dashboard'),str(ROOT/'tests')]
import app,psycopg2
from psycopg2.extras import RealDictCursor
from unit_hypotheses import save
from test_unit_hypotheses import fixture
from unit_economics_workspace import workspace

def main():
    results=[]
    print('PLAN: 2 client databases; hypothesis lifecycle in rollback transactions; read-only workspace probes',flush=True)
    for i,client in enumerate(['toptop','lera_nena'],1):
        cfg=app.read_db_config();cfg['database']=client
        conn=psycopg2.connect(**cfg,cursor_factory=RealDictCursor)
        p=fixture(client);settings={'global':{},'markets':{},'products':{}}
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) n FROM unit_hypothesis_versions');before=cur.fetchone()['n']
                first=save(cur,client,p,settings);assert first['revision']==1
                try:save(cur,client,p,settings);raise AssertionError('stale update accepted')
                except ValueError:pass
                p['hypothesis'].update(expected_revision=1,status='running')
                launched=save(cur,client,p,settings);assert launched['revision']==2
                p['hypothesis'].update(expected_revision=2,status='completed');p['hypothesis']['plan'][0]['profit']=999
                completed=save(cur,client,p,{'global':{'tax':99}})
                assert completed['plan']==launched['plan'] and completed['settings']==settings
                other=fixture('lera_nena' if client=='toptop' else 'toptop')
                try:save(cur,client,other,settings);raise AssertionError('foreign cabinet accepted')
                except ValueError:pass
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) n FROM unit_hypothesis_versions');assert cur.fetchone()['n']==before
        finally:conn.rollback();conn.close()
        data=workspace(cfg,client,'2026-08-01','2026-08-10')
        assert data['client']==client and 'hypotheses' in data and 'order_activity' in data
        results.append({'client':client,'lifecycle':'PASS','rollback_verified':True,'source_rows':len(data['rows']),'order_activity':len(data['order_activity'])})
        print(f'PROGRESS: {i}/2 ({i*50}%) | {client} | persistence, lock, isolation, rollback, workspace PASS',flush=True)
    out=ROOT/'reports/unit_economics_20260912/author_v4_8_projects.json';out.write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(f'COMPLETE: 2/2, errors 0, permanent QA records 0; {out}',flush=True)
if __name__=='__main__':main()
