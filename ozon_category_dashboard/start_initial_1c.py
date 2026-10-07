import pulse_vps_admin as rt
rt.configure_scope();rt._USE_WRITER_CONFIG.set(True)
import one_c_import as imp,json
imp.ensure(rt.app)
sources=imp.sources(rt.app)
retail=[s['key'] for s in sources if s['database']=='1c_retail_prod']
assert len(retail)==1
print(json.dumps(imp.start(rt.app,{'sources':retail,'datasets':['sales','catalog','prices'],'date_from':'2026-08-01','date_to':'2026-10-07'})))
