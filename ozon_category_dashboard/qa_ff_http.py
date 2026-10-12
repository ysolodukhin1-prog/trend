"""On-server HTTP verification; ephemeral signed test session never printed."""
import json
import sys
import requests

sys.path.insert(0, '/workspace/ozon_category_dashboard')
import pulse_vps_admin as rt
rt.configure_scope()
app = rt.app
base = 'https://83.222.17.242'
session = requests.Session()
token = app.create_managed_access_token({'username':'ff-platform-deployment-check','is_admin':True})
session.cookies.set(app.DASHBOARD_ACCESS_COOKIE_NAME,token)
results = {}
endpoint = base+'/api/admin/ff-platform'
anonymous = requests.get(endpoint,timeout=15)
assert anonymous.status_code == 401
results['anonymous'] = 401
for client in ('toptop','lera_nena'):
    response = session.get(endpoint,params={'client':client},timeout=20)
    assert response.status_code == 200, response.status_code
    data = response.json()
    assert data['ok'] and data['client']==client
    assert not any('encrypted_value' in str(k) or k=='token' for k in data)
    results[client] = {'http':200,'configured':data['saved'],'rows':data['summary']['rows']}
    preview = session.get(endpoint,params={'client':client,'view':'preview'},timeout=15)
    assert preview.status_code == 200 and preview.json()['client']==client
bad_client = session.get(endpoint,params={'client':'foreign'},timeout=15)
assert bad_client.status_code == 400
assert session.post(endpoint,json={'client':'toptop','action':'save','token':'invalid\nheader'},timeout=15).status_code == 400
assert session.post(endpoint,json={'client':'toptop','action':'import'},headers={'Origin':'https://evil.invalid'},timeout=15).status_code == 403
results['invalid_client'] = 400
results['cross_origin_post'] = 403
old = session.get(base+'/api/admin/connections',timeout=25)
assert old.status_code==200 and old.json()['ok']
results['existing_connections'] = 200
html = session.get(base+'/react/',timeout=15)
assert html.status_code==200 and 'index-ff-platform-20261012.js' in html.text
asset = session.get(base+'/react/assets/index-ff-platform-20261012.js',timeout=20)
assert asset.status_code==200 and 'function TRFFPlatform' in asset.text
assert asset.text.count('e.jsx(TRFFPlatform,{})')==1
results['frontend'] = 200
results['business_or_credential_writes'] = False
print(json.dumps({'ok':True,'checks':results}))
