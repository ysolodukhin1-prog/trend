import sys,json
sys.path.insert(0,'/workspace/ozon_category_dashboard');import pulse_vps_admin as v;v.configure_scope();a=v.app;a.hydrate_registered_clients();p=a.build_admin_all_clients_daily_plan()
from trend_marketplace_rows import with_views
ids=with_views(p['tasks'],{'toptop:monthly:wb_stock_history:2026-10'})
print(json.dumps({'task_ids':sorted(ids),'tasks':[{k:t.get(k) for k in ['id','client','stage','key','command']} for t in p['tasks'] if t['id'] in ids]},ensure_ascii=False))