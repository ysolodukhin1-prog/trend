import pulse_vps_admin as rt
rt.configure_scope()
app=rt.app
import pymssql,re,json
with app.client_registry_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%'")
        keys=[r['service_key'] for r in cur.fetchall()]
for key in keys:
    database=app.service_credential(key,'database')
    if database!='1c_erp_prod': continue
    values={k:app.service_credential(key,k) for k in ('username','password')}
    try:
        conn=pymssql.connect(server='172.19.0.1',port=11434,database=database,user=values['username'],password=values['password'],login_timeout=6,timeout=6,autocommit=True,appname='TREND migration read-only verification')
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT DB_NAME()');name=cur.fetchone()[0]
                cur.execute("SELECT name FROM sys.databases WHERE HAS_DBACCESS(name)=1 AND name NOT IN ('master','model','msdb','tempdb') ORDER BY name")
                names=[r[0] for r in cur.fetchall()]
                print(json.dumps({'source':key,'status':'connected','database':name,'accessible_database_names':names},ensure_ascii=False))
        finally: conn.close()
    except Exception as exc:
        codes=sorted(set(re.findall(r'\b(?:18456|18452|4060|916|20002|20009)\b',str(exc.args))))
        print(json.dumps({'source':key,'status':'failed','error_class':type(exc).__name__,'codes':codes}))
