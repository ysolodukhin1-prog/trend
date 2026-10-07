import pulse_vps_admin as rt
rt.configure_scope();app=rt.app
from one_c_endpoint import sql_endpoint
import pymssql,json,re,datetime
def clean(value):
    if isinstance(value,(datetime.datetime,datetime.date)):
        if value.year>=4000:value=value.replace(year=value.year-2000)
        return value.isoformat(sep=' ') if isinstance(value,datetime.datetime) else value.isoformat()
    return value
def emit(kind,value):print(json.dumps({'kind':kind,'value':value},ensure_ascii=False,default=clean),flush=True)
with app.client_registry_connection() as conn:
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%' ORDER BY service_key")
        keys=[r['service_key'] for r in cur.fetchall()]
for key in keys:
    database=app.service_credential(key,'database')
    emit('registered',{'source':key,'database':database})
    if 'retail' not in database.lower():continue
    values={k:app.service_credential(key,k) for k in ('host','port','username','password')}
    host,port=sql_endpoint(values['host'],values['port'])
    emit('route',{'source':key,'new_server':port==11434})
    db=pymssql.connect(server=host,port=port,database=database,user=values['username'],password=values['password'],login_timeout=5,timeout=25,autocommit=False,appname='TREND retail freshness read-only')
    try:
        with db.cursor(as_dict=True) as c:
            c.execute('SET LOCK_TIMEOUT 1500')
            c.execute('SELECT DB_NAME() actual_database, GETDATE() server_time');emit('identity',c.fetchone())
            c.execute("SELECT t.name table_name,c.name column_name FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id WHERE t.name IN ('_Document949','_Reference558') AND c.name IN ('_IDRRef','_Posted','_Marked','_Date_Time','_Fld30590RRef','_Description')")
            schema=c.fetchall();emit('schema',schema)
            expected={'_Document949':{'_IDRRef','_Posted','_Marked','_Date_Time','_Fld30590RRef'},'_Reference558':{'_IDRRef','_Description'}}
            assert all(cols <= {r['column_name'] for r in schema if r['table_name']==table} for table,cols in expected.items()),'Retail schema changed'
            c.execute('SELECT COUNT_BIG(*) receipts,MIN(_Date_Time) first_receipt,MAX(_Date_Time) last_receipt FROM dbo._Document949 WHERE _Posted=0x01 AND _Marked=0x00');emit('global',c.fetchone())
            c.execute('SELECT TOP(10) CAST(_Date_Time AS date) day,COUNT_BIG(*) receipts FROM dbo._Document949 WHERE _Posted=0x01 AND _Marked=0x00 GROUP BY CAST(_Date_Time AS date) ORDER BY day DESC');emit('latest_days',c.fetchall())
            c.execute("SELECT s._Description place,COUNT_BIG(*) receipts,MAX(d._Date_Time) last_receipt FROM dbo._Document949 d LEFT JOIN dbo._Reference558 s ON s._IDRRef=d._Fld30590RRef WHERE d._Posted=0x01 AND d._Marked=0x00 GROUP BY d._Fld30590RRef,s._Description ORDER BY last_receipt DESC");emit('stores',c.fetchall())
            c.execute("SELECT s._Description place,COUNT(d._IDRRef) receipts,MAX(d._Date_Time) last_receipt FROM dbo._Reference558 s LEFT JOIN dbo._Document949 d ON d._Fld30590RRef=s._IDRRef AND d._Posted=0x01 AND d._Marked=0x00 WHERE s._Description LIKE N'%Европ%' GROUP BY s._IDRRef,s._Description");emit('european',c.fetchall())
    finally:db.rollback();db.close()
