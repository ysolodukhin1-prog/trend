import os,sys,json,pathlib,importlib.util
os.environ['DASHBOARD_CLIENT']='toptop';os.environ['DASHBOARD_DB_NAME']='toptop';sys.path.insert(0,'/workspace/scripts');sys.path.insert(0,'/workspace/ozon_category_dashboard')
import sync_km_wb_extended_api as m
o={'output_dir':str(m.OUTPUT_DIR),'token_in_runtime_environment':bool(os.environ.get(m.TOKEN_ENV)),'archives':[]}
if m.OUTPUT_DIR.exists():o['archives']=[{'name':p.name,'size':p.stat().st_size} for p in m.OUTPUT_DIR.glob('wb_stock_history*')]
print(json.dumps(o))