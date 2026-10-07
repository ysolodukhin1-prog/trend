import pulse_vps_admin as rt
rt.configure_scope();rt._USE_WRITER_CONFIG.set(True)
import one_c_import as imp,json
sources=[s['key'] for s in imp.sources(rt.app) if s['database'] in {'1c_retail_prod','1c_ut_prod'}]
print(json.dumps(imp.start(rt.app,{'sources':sources,'datasets':['sales','prices'],'date_from':'2026-08-01','date_to':'2026-10-07'})))
