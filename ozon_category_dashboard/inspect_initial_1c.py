import pulse_vps_admin as rt
rt.configure_scope()
import one_c_import as imp,json
p=imp.status(rt.app)
print(json.dumps({'jobs':p['jobs'][:2],'snapshots':p['snapshots'],'price_types':p['price_types'],'selected_price_type':p['retail_price_type']},ensure_ascii=False))
