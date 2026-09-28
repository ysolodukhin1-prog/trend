"""Create one useful, unpublished real-review draft. No marketplace write calls."""
import sys,json
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ozon_category_dashboard'))
import app
import review_replies as replies
from km_trade_finance import connect_km
cfg=app.read_db_config();cfg['database']='toptop'
print('ПЛАН: 1 отзыв TOPTOP → 1 черновик Codex → сохранение в PULSE. Публикаций: 0.',flush=True)
with connect_km(cfg) as conn,conn.cursor() as cur:
    cur.execute("SELECT review_key FROM marketplace_reviews WHERE marketplace='wb' AND answered IS FALSE AND review_text<>'' ORDER BY rating,review_date DESC LIMIT 10")
    candidates=[r['review_key'] for r in cur.fetchall()]
key=next((key for key in candidates if replies.load(cfg,key)['draft'] is None),None)
if key is None: raise RuntimeError('Нет свободного от черновика отзыва для проверки')
print('ПРОГРЕСС: 0/1 | Codex готовит ответ; ожидаем до 150 секунд',flush=True)
result=replies.generate(cfg,dict(review_key=key,revision=0),'TOPTOP')
assert result['draft']['status']=='draft'
assert replies.load(cfg,key)['draft']['draft_text']==result['draft']['draft_text']
receipt=dict(client='toptop',review_key=key,model=result['draft']['model'],revision=result['draft']['revision'],
             status='draft',reply_length=len(result['draft']['draft_text']),publications=0)
(ROOT/'reports/review_reply_draft_20260913.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print('ПРОГРЕСС: 1/1 (100%) | черновик сохранён и перечитан; публикаций: 0',flush=True)
print(json.dumps(receipt),flush=True)
