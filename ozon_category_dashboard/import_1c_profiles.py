"""Read schema metadata only; emit a verified physical mapping for 1C adapters."""
import pulse_vps_admin as rt
rt.configure_scope();app=rt.app
import pymssql,re,zlib,json
from pathlib import Path
from one_c_endpoint import sql_endpoint
profiles={}
with app.client_registry_connection() as pg:
 with pg.cursor() as c:
  c.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%'");keys=[r['service_key'] for r in c.fetchall()]
for key in keys:
 v={k:app.service_credential(key,k) for k in ('host','port','database','username','password')}
 if v['database'] not in {'1c_retail_prod','1c_ut_prod','1c_erp_prod'}:continue
 host,port=sql_endpoint(v['host'],v['port'])
 db=pymssql.connect(server=host,port=port,database=v['database'],user=v['username'],password=v['password'],timeout=20,login_timeout=5)
 objects={}
 try:
  with db.cursor(as_dict=True) as c:
   c.execute("SELECT BinaryData FROM Params WHERE FileName='DBNames' AND PartNo=0");names=zlib.decompress(c.fetchone()['BinaryData'],-15).decode('utf-8-sig')
   entries=re.findall(r'\{([0-9a-f-]{36}),"(Reference|InfoRg)",(\d+)\}',names)
   for i in range(0,len(entries),100):
    batch=entries[i:i+100];mapping={uid:(typ,num) for uid,typ,num in batch}
    c.execute('SELECT FileName,BinaryData FROM Config WHERE FileName IN ('+','.join(['%s']*len(mapping))+')',tuple(mapping))
    for row in c.fetchall():
     try:t=zlib.decompress(row['BinaryData'],-15).decode('utf-8-sig',errors='ignore')
     except zlib.error:continue
     labels=re.findall(r'([0-9a-f-]{36})\},"([А-Яа-яA-Za-z][А-Яа-яA-Za-z0-9_]*)",\s*\{1,"ru","([^"]+)"\}',t)
     if not labels:continue
     root=labels[0][1];typ,num=mapping[row['FileName']]
     required={'Номенклатура':'Reference','ХарактеристикиНоменклатуры':'Reference','ВидыЦен':'Reference','Валюты':'Reference','ЦеныНоменклатуры':'InfoRg','ШтрихкодыНоменклатуры':'InfoRg'}
     if required.get(root)!=typ:continue
     table='_'+typ+num
     c.execute('SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(%s)',('dbo.'+table,));columns=[r['name'] for r in c.fetchall()]
     fields={}
     for uid,name,label in labels:
      found=re.findall(r'\{'+re.escape(uid)+r',"Fld",(\d+)\}',names)
      for n in found:
       for col in columns:
        if col=='_Fld'+n or col=='_Fld'+n+'RRef':fields[name]=col
     objects[root]={'table':table,'fields':fields,'columns':columns}
   profiles[v['database']]=objects
   print(v['database'],json.dumps({n:{'table':o['table'],'fields':{k:v for k,v in o['fields'].items() if k in ['Артикул','Номенклатура','Характеристика','Цена','ВидЦены','ВидЦен','Валюта','ВалютаЦены','Штрихкод','Актуальность','ВключаяХарактеристики']}} for n,o in objects.items()},ensure_ascii=False),flush=True)
 finally:db.rollback();db.close()
Path('/var/lib/pulse/one_c_profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2),encoding='utf-8')

