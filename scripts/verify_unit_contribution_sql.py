"""Exercise actual compensation SQL with inline CTE fixtures; no source writes."""
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ozon_category_dashboard'))
import app
from km_trade_finance import connect_km
from unit_economics_workspace import _yandex


class CaptureCursor:
    def __init__(self):
        self.queries = []
    def execute(self, query, params=None):
        self.queries.append((query, params))
    def fetchall(self):
        return []


def main():
    print('PLAN: one read-only production-SQL fixture; known, conflicting and missing compensation events; no source writes.', flush=True)
    cursor = CaptureCursor()
    _yandex(cursor, date(2026, 9, 7), date(2026, 9, 7), 'toptop')
    query, params = next(q for q in cursor.queries if 'WITH raw AS' in q[0])
    fixtures = []
    for order, amount in [('known', 20), ('conflict', 10), ('conflict', 30), ('missing', None)]:
        payload = json.dumps(dict(orderId=order, yourSku='check', compensationDate='2026-09-07', compensationAmount=amount))
        fixtures.append(('toptop', 'test-business', payload, 'realization', 'lost_items'))
    # Values remain parameterized. CTEs shadow the two named source views only
    # inside this SELECT; permanent rows, settings and schemas are untouched.
    prefix = '''WITH yandex_report_rows(client_key,business_id,payload,source_key,sheet) AS (
      VALUES ''' + ','.join(['(%s,%s,%s::jsonb,%s,%s)'] * len(fixtures)) + '''),
      yandex_fact_realization(client_key,business_id,order_id,offer_id,campaign_id) AS (
      VALUES ('toptop','test-business','known','check','1'),
             ('toptop','test-business','conflict','check','1'),
             ('toptop','test-business','missing','check','1')),
      raw AS'''
    sql = query.replace('WITH raw AS', prefix, 1)
    config = app.read_db_config(); config['database'] = 'toptop'
    with connect_km(config) as connection, connection.cursor() as db:
        db.execute(sql, tuple(x for row in fixtures for x in row) + tuple(params))
        rows = db.fetchall()
    assert len(rows) == 1
    result = dict(rows[0])
    assert result['net'] == 20 and result['source_rows'] == 3
    assert result['conflicts'] == 1 and result['missing_amount'] == 2
    result['scope'] = 'actual production SQL, read-only inline fixture; not observed marketplace operations'
    target = ROOT / 'reports/unit_economics_20260912/contribution_gap_sql_verification.json'
    target.write_text(json.dumps(result, default=str, indent=2), encoding='utf-8')
    print('DONE: known net20, events3, conflict1, missing2; contribution_gap_sql_verification.json', flush=True)


if __name__ == '__main__':
    main()
