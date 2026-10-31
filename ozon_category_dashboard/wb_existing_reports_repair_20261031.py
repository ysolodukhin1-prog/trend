import os,sys,json,pathlib,datetime
os.environ['DASHBOARD_CLIENT']='toptop';os.environ['DASHBOARD_DB_NAME']='toptop';sys.path.insert(0,'/workspace/scripts');sys.path.insert(0,'/workspace/ozon_category_dashboard')
import sync_km_wb_extended_api as m
m.MAX_RETRIES=1
import pulse_vps_admin
pulse_vps_admin.configure_scope()
m.app.hydrate_registered_clients()
t=m.app.registered_client_credential('toptop','wb_api_token')
if not t:raise RuntimeError('Runtime service credential unavailable')
o={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'operation':'GET existing report metadata only; no creation'}
try:
 payload,status,headers=m.api_request('https://seller-analytics-api.wildberries.ru','/api/v2/nm-report/downloads',t,request_label='existing history reports')
 o['http_status']=status;o['payload']=payload
except Exception as e:o['error']=str(e)
p=pathlib.Path('/var/lib/pulse/wb_existing_reports_repair_20261031.json');p.write_text(json.dumps(o,ensure_ascii=False,indent=2));print(json.dumps(o,ensure_ascii=False))