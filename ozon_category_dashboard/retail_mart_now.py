import pulse_vps_admin as rt
rt.configure_scope();app=rt.app
import psycopg2,json
from psycopg2.extras import RealDictCursor
with psycopg2.connect(**app.read_db_config('toptop'),cursor_factory=RealDictCursor) as conn:
    with conn.cursor() as c:
        c.execute('SET TRANSACTION READ ONLY');c.execute("SET LOCAL statement_timeout='8s'")
        c.execute("SELECT channel,MAX(period) latest,COUNT(*) lines FROM retail_1c.sales GROUP BY channel");print('MART',json.dumps(c.fetchall(),default=str,ensure_ascii=False))
        c.execute('SELECT loaded_at,row_count,checked_receipts FROM retail_1c.import_runs ORDER BY loaded_at DESC LIMIT 1');print('IMPORT',json.dumps(c.fetchone(),default=str,ensure_ascii=False))
        c.execute("SELECT store_id,MAX(period) latest,COUNT(*) lines FROM retail_1c.sales WHERE channel='retail' GROUP BY store_id ORDER BY latest DESC");print('STORES',json.dumps(c.fetchall(),default=str,ensure_ascii=False))
from pathlib import Path
for line in Path('/workspace/ozon_category_dashboard/trend_retail.py').read_text().splitlines():
    if line.startswith(('SOURCE_KEY','SOURCE =')):print('ADAPTER',line)
