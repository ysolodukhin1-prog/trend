import json,time
from urllib.parse import urlparse
import pulse_vps_admin as rt
import assortment_api as api
rt.configure_scope(); app=rt.app
identity={'is_admin':True,'clients':['toptop','lera_nena']}
app.CURRENT_ACCESS_USER.set(identity)
for client in ['toptop','lera_nena']:
 app.CURRENT_CLIENT.set(client)
 for report in sorted(api.REPORTS):
  t=time.monotonic()
  result=api.build(app,urlparse('/api/assortment?client='+client+'&dashboard='+report),identity)
  assert result['rows'] and result['client']==client
  if report=='assortmentProducts':assert all(len({l['channel'] for l in r['links']})==len(r['links']) for r in result['rows'])
  if report=='assortmentPrices':assert any(l.get('price') for r in result['rows'] for l in r['links'])
  print(json.dumps({'client':client,'report':report,'counts':result['counts'],'total':result['total'],'seconds':round(time.monotonic()-t,2)},ensure_ascii=False),flush=True)
class Handler:
 def send_json(self,payload,status=200):self.status=status;self.payload=payload
h=Handler();app.CURRENT_CLIENT.set('toptop');app.CURRENT_ACCESS_USER.set({'is_admin':False,'clients':['lera_nena']})
assert api.handle(app,h,urlparse('/api/assortment')) and h.status==403
assert api.report_id(urlparse('/api/assortment?dashboard=home'))=='assortmentProducts'
print('authorization guards passed',flush=True)
