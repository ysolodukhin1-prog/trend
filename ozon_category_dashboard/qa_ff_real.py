"""Provider endpoint supplied by owner; no tokens or product records printed."""
import json
import requests
import pulse_vps_admin as rt
import ff_platform as ff
rt.configure_scope()
rt._USE_WRITER_CONFIG.set(True)
token = ff.token_value(rt.app, 'toptop')
base = 'https://ff-platform.ru/lk/4/client_api/v1'
for path,params in [('/sklads',None),('/stocks',{'skladId':4,'isGetZeroStock':'true','typeNomenclature':'Goods'}),
                    ('/changed_stocks',{'sklad_id':2,'timestamp':0}),
                    ('/stocks',{'skladId':2,'isGetZeroStock':'true','typeNomenclature':'Goods'}),
                    ('/stocks',{'skladId':2,'isGetZeroStock':'true','typeNomenclature':'p'})]:
    r = requests.get(base+path,params=params,headers={'token':token,'Accept':'application/json'},
                     timeout=(8,45),allow_redirects=False)
    summary={'path':path,'filter':params.get('typeNomenclature') if params else None,
             'status':r.status_code,'redirect':r.is_redirect}
    try:
        data=r.json()
        summary['shape']=type(data).__name__
        if isinstance(data,dict): summary['keys']=sorted(data)
        try:
            rows=ff.collection(data,('data','items','stocks','result','sklads'))
            summary['rows']=len(rows)
            if rows and isinstance(rows[0],dict):
                summary['row_keys']=sorted(rows[0])
                summary['field_shapes']={k:type(v).__name__ for k,v in rows[0].items()}
                summary['types']=sorted({str(row.get('typeNomenclature')) for row in rows})
                summary['warehouse_ids']=sorted({str(row.get('skladId')) for row in rows})
                if path=='/stocks':
                    summary['quantities']={k:sum(row.get(k,0) for row in rows) for k in ('numAvailable','numDefect','numAtWork')}
                    summary['nonzero_rows']=sum(any(row.get(k,0)!=0 for k in ('numAvailable','numDefect','numAtWork')) for row in rows)
                if path=='/sklads': summary['ids']=[row.get('id',row.get('skladId')) for row in rows]
                if path=='/changed_stocks':
                    summary['nested_keys']=sorted(rows[0].get('nomenclature',{}))
                    summary['prod_types']=sorted({str(row.get('nomenclature',{}).get('prodType')) for row in rows})
        except ff.ImportErrorSafe: pass
    except ValueError: summary['shape']='non_json'
    print(json.dumps(summary))
