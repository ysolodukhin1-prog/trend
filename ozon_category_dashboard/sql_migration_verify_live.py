import pulse_vps_admin as rt
rt.configure_scope()
app=rt.app
import json
import trend_database
with app.client_registry_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%'")
        keys=[r['service_key'] for r in cur.fetchall()]
for key in keys:
    if app.service_credential(key,'database')!='1c_erp_prod':continue
    # Authorized migration changes only the supplied endpoint, retaining secrets.
    app.save_admin_connection({'service':key,'action':'save','credentials':{'host':'192.168.50.242','port':'1433'}})
    status,message,code=app.probe_1c_connection(key)
    print(json.dumps({'source':key,'probe':status,'message':message},ensure_ascii=False))
    assert status=='connected'
    with trend_database.sql_connection(app,key) as db:
        with db.cursor() as cur:
            cur.execute('SELECT DB_NAME()');name=cur.fetchone()[0]
            assert name=='1c_erp_prod'
            cur.execute("SELECT name FROM sys.databases WHERE HAS_DBACCESS(name)=1 AND name IN ('1c_erp_prod','1c_retail_prod','1c_ut_prod') ORDER BY name")
            names=[r[0] for r in cur.fetchall()]
            print(json.dumps({'gateway':'connected','database':name,'available_1c_databases':names},ensure_ascii=False))
    app.record_admin_connection_event(key,'check',status,message,code)
