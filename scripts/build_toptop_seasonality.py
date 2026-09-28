"""Build audited TOPTOP seasonality artifacts from cached MPStats monthly responses."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'ozon_category_dashboard';sys.path.insert(0,str(ROOT))
from sales_planning_seasonality import seasonal_indices
BASE=ROOT.parent/'work/toptop-coefficients'
def main():
 mapping=json.loads((BASE/'mapping-reviewed.json').read_text(encoding='utf-8'))
 catalog=json.loads((BASE/'catalog-live.json').read_text(encoding='utf-8'))
 assert {r['category'] for r in mapping} == {r['category_name'] for r in catalog}, 'Mapping must match live hydrated TOPTOP catalog'
 lera=json.loads((ROOT/'data/lera_nena_seasonality.json').read_text(encoding='utf-8'))
 result=[];coverage=[]
 print(f'PLAN: validate {len(mapping)} mappings, require all 36 months; retain LERA coefficients for exact overlaps.',flush=True)
 for i,r in enumerate(mapping,1):
  status=r['status'];rows=[];meta={'category':r['category'],'market_path':r['market_path'],'mapping_status':status,'mapping_note':r.get('mapping_note',''),'training_period':'2023–2025'}
  if r['reuse']:
   rows=[dict(x) for x in lera if x['category']==r['category']];meta.update(reused_from='lera_nena',reused_source_sha256=hashlib.sha256((ROOT/'data/lera_nena_seasonality.json').read_bytes()).hexdigest(),method='Existing LERA market category seasonal profile');status='research_estimate'
  elif status!='mapping_required':
   source=BASE/'raw'/(hashlib.sha256(r['market_path'].encode()).hexdigest()[:20]+'.json')
   if source.exists():
    body=json.loads(source.read_text(encoding='utf-8'));rows,status=seasonal_indices(body['rows'])
    meta.update(source_file=str(source.relative_to(ROOT.parent)),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),collected_at=body['collected_at'],source=body['endpoint'],method='Mean of month sales / annual monthly average across complete 2023–2025 years; transition = index / previous month index')
   else:status='source_unavailable'
  meta['status']=status
  if not rows:rows=[{'month':m,'seasonality_index':None,'transition_coefficient':None,'training_observations':0} for m in range(1,13)]
  result.extend([{**row,**meta} for row in rows]);coverage.append({'category':r['category'],'sku_count':r['sku_count'],'status':status,'reused':r['reuse'],'market_path':r['market_path']})
  if i%50==0:print(f'PROGRESS: {i}/{len(mapping)}',flush=True)
 target=ROOT/'data/toptop_seasonality.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 (BASE/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding='utf-8')
 from collections import Counter
 print('FINAL',dict(Counter(r['status'] for r in coverage)),str(target),flush=True)
if __name__=='__main__':main()
