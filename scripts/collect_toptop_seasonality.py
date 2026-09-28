"""Collect reviewed category mappings only; preserve raw MPStats evidence. No seller writes."""
import hashlib,json,sys,time,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]/'ozon_category_dashboard'
sys.path.insert(0,str(ROOT))
from seo_projects import _load_mpstats_token
BASE=ROOT.parent/'work/toptop-coefficients'
def main():
 mapping=json.loads((BASE/'mapping-reviewed.json').read_text(encoding='utf-8'))
 jobs=[r for r in mapping if not r['reuse'] and r['status']!='mapping_required']
 raw=BASE/'raw';raw.mkdir(exist_ok=True)
 token=_load_mpstats_token('wb');start=time.monotonic();errors=0
 gate=threading.Lock();next_request=[0.];local=threading.local()
 print(f'PLAN: {len(jobs)} categories, monthly sales 2023-2025; 3 in flight maximum, one request start/second; resumable cache; Retry-After respected.',flush=True)
 def collect(r):
  key=hashlib.sha256(r['market_path'].encode()).hexdigest()[:20];dest=raw/(key+'.json')
  if dest.exists():return r,'cached',False
  try:
   if not hasattr(local,'session'):
    local.session=requests.Session();local.session.headers['X-Mpstats-TOKEN']=token
   for attempt in range(3):
    with gate:
     delay=max(0,next_request[0]-time.monotonic())
     if delay:time.sleep(delay)
     next_request[0]=time.monotonic()+1
    response=local.session.get('https://mpstats.io/api/wb/get/category/trends',params={'path':r['market_path'],'d1':'2023-01-01','d2':'2025-12-31','trends_by':'month','fbs':0},timeout=45)
    if response.status_code!=429:break
    delay=max(1,int(response.headers.get('Retry-After','30')))
    with gate:next_request[0]=max(next_request[0],time.monotonic()+delay)
    print(f'RATE LIMIT: wait {delay}s | {r["category"]}',flush=True)
   if response.status_code!=200:raise RuntimeError(f'HTTP {response.status_code}')
   rows=response.json()
   if not isinstance(rows,list):raise ValueError('Unexpected response shape')
   rows=[x for x in rows if str(x.get('date',''))[:4] in {'2023','2024','2025'}]
   dest.write_text(json.dumps({'market_path':r['market_path'],'endpoint':'https://mpstats.io/api/wb/get/category/trends','fbs':0,'collected_at':datetime.now(timezone.utc).isoformat(),'rows':rows},ensure_ascii=False),encoding='utf-8')
   return r,f'{len(rows)} months',False
  except Exception as e:return r,type(e).__name__+(' '+str(e) if isinstance(e,(RuntimeError,ValueError)) else ''),True
 with ThreadPoolExecutor(max_workers=3) as pool:
  for i,future in enumerate(as_completed([pool.submit(collect,r) for r in jobs]),1):
   r,status,failed=future.result();errors+=int(failed);elapsed=time.monotonic()-start;eta=elapsed/i*(len(jobs)-i)
   print(f'PROGRESS: {i}/{len(jobs)} ({i/len(jobs):.0%}) | {r["category"]} | {status} | errors {errors} | elapsed {elapsed:.0f}s | ETA {eta:.0f}s',flush=True)
 print(f'FINAL: {len(jobs)} categories; errors {errors}; raw={raw}; elapsed {time.monotonic()-start:.0f}s',flush=True)
if __name__=='__main__':main()
