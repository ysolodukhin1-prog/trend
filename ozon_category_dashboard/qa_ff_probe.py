"""Read-only capability probe, output only shapes/counts and product type codes."""
import pulse_vps_admin as rt
import ff_platform as ff
import json
rt.configure_scope()
rt._USE_WRITER_CONFIG.set(True)
for client in ('toptop', 'lera_nena'):
    status = ff.status_payload(rt.app, client)
    print(json.dumps({'client':client,'saved':status['saved']}))
    if not status['saved']:
        continue
    token = ff.token_value(rt.app, client)
    warehouses = ff.parse_warehouses(ff.request_json(token, '/sklads'))
    print(json.dumps({'warehouses':len(warehouses)}))
    for params in ({'skladId':warehouses[0]['id'],'isGetZeroStock':'true','typeNomenclature':''},
                   {'skladId':warehouses[0]['id'],'isGetZeroStock':'true','typeNomenclature':'all'}):
        try:
            payload = ff.request_json(token, '/stocks', params)
            rows = ff.collection(payload, ('data','items','stocks','result'))
            print(json.dumps({'filter':params['typeNomenclature'],'rows':len(rows),
                              'types':sorted(set(str(r.get('typeNomenclature')) for r in rows)),
                              'keys':sorted(rows[0]) if rows else []}))
        except ff.ImportErrorSafe as exc:
            print(json.dumps({'filter':params['typeNomenclature'],'error':str(exc)}))
