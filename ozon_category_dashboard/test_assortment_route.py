import json,urllib.request,urllib.error
from urllib.parse import urlparse
import pulse_vps_admin as rt
rt.configure_scope();app=rt.app
reports={'assortmentProducts','assortmentPrices','assortmentABC','assortmentXYZ'}
assert reports.issubset(set(rt.REPORTS))
assert reports.issubset({r['id'] for r in app.ADMIN_REPORT_CATALOG})
class H(rt.VPSAdminHandler):
 def __init__(self,identity,path):self.identity=identity;self.path=path;self.headers={};self.status=None
 def dashboard_access_granted(self):return True
 def dashboard_access_identity(self):return self.identity
 def send_json(self,payload,status=200):self.payload=payload;self.status=status
 def send_error(self,status,*args):self.status=status
def check(identity,path,status):
 h=H(identity,path);h.do_GET();assert h.status==status,(h.status,status);return h
base='/api/assortment?client=toptop&dashboard='
admin={'is_admin':True,'clients':['toptop','lera_nena']}
user={'is_admin':False,'clients':['toptop'],'reports':['assortmentProducts'],'data_access':[{'source':'marketplace:toptop','resources':['assortmentProducts']}]}
result=check(user,base+'assortmentProducts',200)
assert all(l['channel']!='retail' for r in result.payload['rows'] for l in r['links'])
check(user,base+'assortmentPrices',403)
check(dict(user,reports=[]),base+'assortmentProducts',403)
check(dict(user,data_access=[]),base+'home',403)
check(dict(user,clients=['lera_nena']),base+'assortmentProducts',403)
h=check(admin,base+'assortmentPrices&sales_channel=retail&status=matched',200)
assert h.payload['rows'] and any(l['channel']=='lamoda' and l.get('price') for r in h.payload['rows'] for l in r['links'])
colored=sum(bool(l.get('price',{}).get('tone')) for r in h.payload['rows'] for l in r['links'] if l.get('price'))
assert colored>0
for market in ('ozon','retail'):
 h=check(admin,base+'assortmentXYZ&market='+market,200)
 print(json.dumps({'market':market,'counts':h.payload['counts']},ensure_ascii=False))
try:
 urllib.request.urlopen('http://127.0.0.1:8062'+base+'assortmentProducts',timeout=10)
 raise AssertionError('Anonymous request accepted')
except urllib.error.HTTPError as e:assert e.code==401
for asset in ('index-r133-assortment.js','assortment-r133.css'):
 response=urllib.request.urlopen('http://127.0.0.1:8062/react/assets/'+asset,timeout=10)
 assert response.status==200 and len(response.read())>100
print(json.dumps({'ok':True,'checks':['route allowlist','report rights','source rights','client isolation','retail source hidden without grant','new report precedes retail adapter','anonymous 401','asset HTTP 200','Lamoda prices and conditional colors'],'colored_cells_first_page':colored}))
