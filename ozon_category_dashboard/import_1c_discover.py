import pulse_vps_admin as rt
rt.configure_scope();app=rt.app
import json,zlib,re,pymssql,psycopg2
from one_c_endpoint import sql_endpoint
def out(k,v):print(k,json.dumps(v,ensure_ascii=False,default=str),flush=True)
with app.client_registry_connection() as pg:
 with pg.cursor() as c:
  c.execute("SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns WHERE table_schema IN ('retail_1c','assortment_master') ORDER BY table_schema,table_name,ordinal_position");out('PG_SCHEMA',c.fetchall())
  c.execute("SELECT tablename,indexdef FROM pg_indexes WHERE schemaname IN ('retail_1c','assortment_master')");out('PG_INDEXES',c.fetchall())
  c.execute("SELECT metadata FROM retail_1c.import_runs ORDER BY loaded_at DESC LIMIT 1");out('IMPORT_METADATA',c.fetchone())
  c.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%'");keys=[r['service_key'] for r in c.fetchall()]
for key in keys:
 v={k:app.service_credential(key,k) for k in ('host','port','database','username','password')}
 if v['database'] not in {'1c_retail_prod','1c_ut_prod'}:continue
 host,port=sql_endpoint(v['host'],v['port']);out('DATABASE',v['database'])
 db=pymssql.connect(server=host,port=port,database=v['database'],user=v['username'],password=v['password'],timeout=20,login_timeout=5,autocommit=False)
 try:
  with db.cursor(as_dict=True) as c:
   c.execute("SELECT BinaryData FROM dbo.Params WHERE FileName='DBNames' AND PartNo=0");names=zlib.decompress(c.fetchone()['BinaryData'],wbits=-15).decode('utf-8-sig')
   entries=re.findall(r'\{([0-9a-f-]{36}),"(InfoRg|Reference|Document|AccumRg)",([0-9]+)\}',names)
   candidates=[(uid,typ,num) for uid,typ,num in entries if typ in {'InfoRg','Reference'} or (typ,num) in [('AccumRg','44751'),('Document','949')]]
   for i in range(0,len(candidates),80):
    batch=candidates[i:i+80];byuid={a:(b,d) for a,b,d in batch}
    c.execute('SELECT FileName,BinaryData FROM dbo.Config WHERE FileName IN ('+','.join(['%s']*len(batch))+')',tuple(byuid))
    for row in c.fetchall():
     try:text=zlib.decompress(row['BinaryData'],wbits=-15).decode('utf-8-sig',errors='ignore')
     except zlib.error:continue
     labels=re.findall(r'([0-9a-f-]{36})\},"([А-Яа-яA-Za-z][А-Яа-яA-Za-z0-9_]*)",\s*\{1,"ru","([^"]+)"\}',text)
     if not labels:continue
     uid,root,label=labels[0]
     if root not in {'Номенклатура','ХарактеристикиНоменклатуры','ЦеныНоменклатуры','ВидыЦен','ТипыЦенНоменклатуры','ШтрихкодыНоменклатуры','Продажи','ЧекККМ','Магазины','СтруктурныеЕдиницы'}:continue
     typ,num=byuid[row['FileName']];table='_'+typ+num
     fields=[]
     for uid,n,l in labels:
      physical=re.findall(r'\{'+re.escape(uid)+r',"Fld",(\d+)\}',names)
      fields.append((n,l,physical))
     out('MAP',{'table':table,'name':root,'fields':fields})
     c.execute("SELECT col.name,ty.name datatype FROM sys.columns col JOIN sys.types ty ON ty.user_type_id=col.user_type_id WHERE col.object_id=OBJECT_ID(%s) ORDER BY col.column_id",('dbo.'+table,));out('COLUMNS',{'table':table,'columns':c.fetchall()})
     c.execute('SELECT COUNT_BIG(*) n FROM dbo.['+table+']');out('COUNT',{'table':table,**c.fetchone()})
 finally:db.rollback();db.close()
