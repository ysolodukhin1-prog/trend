import sys,json
from urllib.parse import urlparse
sys.path.insert(0,'/workspace/ozon_category_dashboard');import pulse_vps_admin as v;v.configure_scope();a=v.app;t=a.CURRENT_CLIENT.set('toptop')
try:print(json.dumps(a.handle_planfact_daily(urlparse('/api/planfact/daily?client=toptop&marketplace=wb&date_from=2026-10-30&date_to=2026-10-30')),default=str))
finally:a.CURRENT_CLIENT.reset(t)