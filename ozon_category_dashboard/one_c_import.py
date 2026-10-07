"""Admin-triggered 1C snapshots. SQL Server is read only; publication is atomic."""
from __future__ import annotations
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from contextlib import contextmanager
import hashlib,json,os,re,subprocess,sys,uuid,threading,tempfile
from decimal import Decimal
import psycopg2,pymssql
from psycopg2.extras import Json,execute_values,RealDictCursor
from one_c_endpoint import sql_endpoint

PROFILES=json.loads(Path(__file__).with_name('one_c_profiles.json').read_text(encoding='utf-8-sig'))
LABELS={'sales':'Продажи по чекам ККМ','catalog':'Ассортимент и штрихкоды','prices':'Цены номенклатуры'}
DATABASES={'1c_retail_prod':'Розница','1c_ut_prod':'УТ','1c_erp_prod':'ERP'}
STORES={'817900155D321E0311EA04897619B7D8':'Грибоедова, 18','820900155D321E0311EB362502AF06E2':'Авиапарк','BAA00050569E751211EC1D3088CECBE6':'Афимолл'}
ZERO='0'*32
MAX_ROWS=600000
_READY=False
_SCHEMA_LOCK=threading.Lock()
_TYPE_CACHE={}

def clean(v):
 if isinstance(v,uuid.UUID):return str(v)
 if isinstance(v,datetime):return v.isoformat()
 if isinstance(v,date):return v.isoformat()
 if isinstance(v,(bytes,bytearray)):return v.hex().upper()
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [clean(x) for x in v]
 if hasattr(v,'as_tuple'):return str(v)
 return v
def normalized(d):return d.replace(year=d.year-2000) if d and d.year>=4000 else d
def ident(s):
 if not re.fullmatch(r'_[A-Za-z0-9_]+',s):raise ValueError('Неподдерживаемая схема 1С')
 return '['+s+']'
def hexid(v):return v.hex().upper() if isinstance(v,(bytes,bytearray)) else str(v or ZERO).upper()
def require_profile(database):
 if database not in DATABASES:raise ValueError('База не поддерживается')
 return PROFILES.get(database,{})
def capabilities(database):
 p=require_profile(database);out=[]
 if database=='1c_retail_prod':out.append('sales')
 if all(n in p for n in ['Номенклатура','ХарактеристикиНоменклатуры','ШтрихкодыНоменклатуры']):out.append('catalog')
 if all(n in p for n in ['ЦеныНоменклатуры','ВидыЦен','Валюты']):out.append('prices')
 return out

DDL='''CREATE TABLE IF NOT EXISTS one_c_import.jobs(id uuid PRIMARY KEY,created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,finished_at timestamptz,status text NOT NULL,request jsonb NOT NULL,result jsonb NOT NULL DEFAULT '[]',message text NOT NULL DEFAULT '',pid integer,user_id bigint);
ALTER TABLE one_c_import.jobs ADD COLUMN IF NOT EXISTS events jsonb NOT NULL DEFAULT '[]';
ALTER TABLE one_c_import.jobs ADD COLUMN IF NOT EXISTS current_task jsonb;
ALTER TABLE one_c_import.jobs ADD COLUMN IF NOT EXISTS stop_requested boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS one_c_single_active_job ON one_c_import.jobs((true)) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS one_c_import.snapshots(id uuid PRIMARY KEY,database_name text NOT NULL,dataset text NOT NULL,source_key text NOT NULL,loaded_at timestamptz NOT NULL DEFAULT now(),source_latest timestamp,row_count bigint NOT NULL,digest text NOT NULL,summary jsonb NOT NULL);
ALTER TABLE one_c_import.snapshots ADD COLUMN IF NOT EXISTS checked_at timestamptz NOT NULL DEFAULT now();
CREATE TABLE IF NOT EXISTS one_c_import.active(database_name text NOT NULL,dataset text NOT NULL,snapshot_id uuid NOT NULL REFERENCES one_c_import.snapshots(id),PRIMARY KEY(database_name,dataset));
CREATE TABLE IF NOT EXISTS one_c_import.catalog(snapshot_id uuid NOT NULL REFERENCES one_c_import.snapshots(id),product_id text NOT NULL,variant_id text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(snapshot_id,product_id,variant_id));
CREATE TABLE IF NOT EXISTS one_c_import.prices(snapshot_id uuid NOT NULL REFERENCES one_c_import.snapshots(id),product_id text NOT NULL,variant_id text NOT NULL,price_type_id text NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(snapshot_id,product_id,variant_id,price_type_id));
CREATE TABLE IF NOT EXISTS one_c_import.settings(key text PRIMARY KEY,value jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS one_c_import.sales_backups(job_id uuid NOT NULL,saved_at timestamptz NOT NULL DEFAULT now(),payload jsonb NOT NULL);
CREATE OR REPLACE VIEW one_c_import.current_catalog AS SELECT c.*,s.database_name,s.source_key,s.checked_at AS loaded_at FROM one_c_import.catalog c JOIN one_c_import.active a ON a.snapshot_id=c.snapshot_id JOIN one_c_import.snapshots s ON s.id=a.snapshot_id WHERE a.dataset='catalog';
CREATE OR REPLACE VIEW one_c_import.current_prices AS SELECT p.*,s.database_name,s.source_key,s.checked_at AS loaded_at FROM one_c_import.prices p JOIN one_c_import.active a ON a.snapshot_id=p.snapshot_id JOIN one_c_import.snapshots s ON s.id=a.snapshot_id WHERE a.dataset='prices';
GRANT USAGE ON SCHEMA one_c_import TO pulse_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA one_c_import TO pulse_reader;'''
def ensure(app):
 global _READY
 with _SCHEMA_LOCK:
  if _READY:return
  with app.client_registry_connection() as db:
   with db.cursor() as c:c.execute(DDL)
  _READY=True

def sources(app):
 with app.client_registry_connection() as db:
  with db.cursor() as c:
   c.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%' ORDER BY service_key")
   keys=[r['service_key'] for r in c.fetchall()]
 result=[]
 for key in keys:
  name=app.service_credential(key,'database')
  if name in DATABASES:result.append({'key':key,'database':name,'label':DATABASES[name]+' · '+name,'datasets':[{'key':x,'label':LABELS[x]} for x in capabilities(name)]})
 return result

@contextmanager
def sql(app,source,database):
 v={k:app.service_credential(source,k) for k in ('host','port','database','username','password')}
 if v['database']!=database:raise ValueError('Подключение изменилось. Обновите список и запустите импорт снова.')
 host,port=sql_endpoint(v['host'],v['port'])
 db=pymssql.connect(server=host,port=port,database=database,user=v['username'],password=v['password'],login_timeout=6,timeout=120,autocommit=False,appname='TREND 1C read-only import')
 try:
  with db.cursor() as c:c.execute('SET LOCK_TIMEOUT 3000')
  # No writes or hints that permit dirty reads on the source.
  yield db
 finally:
  try:db.rollback()
  finally:db.close()

def query(db,text,params=()):
 with db.cursor(as_dict=True) as c:
  c.execute(text,params);rows=c.fetchmany(MAX_ROWS+1)
  if len(rows)>MAX_ROWS:raise ValueError('Превышен предел снимка. Данные не опубликованы.')
  return rows
def table(p,name):return 'dbo.'+ident(p[name]['table'])
def field(p,name,label,alias=''):
 col=p[name]['fields'].get(label)
 if not col:raise ValueError('Схема 1С изменилась: не найдено поле '+label)
 return (alias+'.' if alias else '')+ident(col)
def check_schema(db,p):
 for obj in p.values():
  rows=query(db,'SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(%s)',('dbo.'+obj['table'],))
  if not set(obj['columns'])<= {r['name'] for r in rows}:raise ValueError('Схема 1С изменилась. Импорт остановлен до повторного сопоставления.')

def extract_catalog(db,p):
 products=query(db,'SELECT _IDRRef id,_Description name,'+field(p,'Номенклатура','Артикул')+' article FROM '+table(p,'Номенклатура')+' WHERE _Marked=0x00')
 if not products:raise ValueError('Справочник номенклатуры пуст. Последний успешный снимок сохранён.')
 variants=query(db,'SELECT _IDRRef id,_OwnerID_RRRef owner,_Description name FROM '+table(p,'ХарактеристикиНоменклатуры')+' WHERE _Marked=0x00')
 codes=query(db,'SELECT '+field(p,'ШтрихкодыНоменклатуры','Номенклатура')+' product,'+field(p,'ШтрихкодыНоменклатуры','Характеристика')+' variant,'+field(p,'ШтрихкодыНоменклатуры','Штрихкод')+' barcode FROM '+table(p,'ШтрихкодыНоменклатуры'))
 byproduct={};barcodes={}
 for r in variants:byproduct.setdefault(hexid(r['owner']),[]).append(r)
 for r in codes:barcodes.setdefault((hexid(r['product']),hexid(r['variant'])),set()).add(str(r['barcode'] or '').strip())
 rows=[]
 for r in products:
  pid=hexid(r['id'])
  # Keep an explicit product row for prices that have no characteristic.
  for variant in [None]+byproduct.get(pid,[]):
   vid=hexid(variant['id']) if variant else ZERO
   rows.append({'product_id':pid,'variant_id':vid,'name':r['name'] or '', 'article':r['article'] or '', 'variant':variant['name'] if variant else '', 'barcodes':sorted(x for x in barcodes.get((pid,vid),[]) if x)})
 return rows,{'products':len(products),'variants':len(variants),'barcode_rows':len(codes)},None

def extract_prices(db,p):
 name='ЦеныНоменклатуры';obj=p[name];f=obj['fields']
 product=field(p,name,'Номенклатура','p');variant=field(p,name,'Характеристика','p')
 typ=field(p,name,'ВидЦены' if 'ВидЦены' in f else 'ВидЦен','p')
 cutoff=datetime.combine(date.today()+timedelta(days=1),datetime.min.time());cutoff=cutoff.replace(year=cutoff.year+2000)
 predicate=' AND p._Active=0x01' if '_Active' in obj['columns'] else ''
 types={hexid(r['_IDRRef']):r for r in query(db,'SELECT _IDRRef,_Description,_Marked FROM '+table(p,'ВидыЦен'))}
 currencies={hexid(r['_IDRRef']):str(r['_Code']).strip() for r in query(db,'SELECT _IDRRef,_Code FROM '+table(p,'Валюты'))}
 fd,path=tempfile.mkstemp(prefix='one-c-prices-',suffix='.jsonl',dir='/var/lib/pulse')
 latest=None;count=0;active_count=0;previous_key=None;previous_item=None;digest=hashlib.sha256();digest.update(b'[');seen_types=set()
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as output,db.cursor(as_dict=True) as cursor:
   order=','.join(ident(f[k]) for k in ['Номенклатура','Характеристика','ВидЦены' if 'ВидЦены' in f else 'ВидЦен'])
   cursor.execute('WITH ranked AS (SELECT p.*,DENSE_RANK() OVER(PARTITION BY '+','.join([product,variant,typ])+' ORDER BY p._Period DESC) rk FROM '+table(p,name)+' p WHERE p._Period<%s'+predicate+') SELECT * FROM ranked WHERE rk=1 ORDER BY '+order,(cutoff,))
   while True:
    batch=cursor.fetchmany(1000)
    if not batch:break
    for r in batch:
     pid,vid,tid=(hexid(r[f[k]]) for k in ['Номенклатура','Характеристика','ВидЦены' if 'ВидЦены' in f else 'ВидЦен'])
     currency=currencies.get(hexid(r[f['Валюта' if 'Валюта' in f else 'ВалютаЦены']]),'')
     currency={'643':'RUB','810':'RUB','840':'USD','978':'EUR'}.get(currency,currency)
     active=(r.get(f.get('Актуальность'))!=b'\x00') and types.get(tid,{}).get('_Marked')!=b'\x01'
     period=normalized(r['_Period']);latest=max(latest,period) if latest else period
     item={'product_id':pid,'variant_id':vid,'price_type_id':tid,'type':types.get(tid,{}).get('_Description') or tid,'value':str(r[f['Цена']]),'currency':currency,'date':str(period),'active':active,'inherit':r.get(f.get('ВключаяХарактеристики'))==b'\x01'}
     key=(pid,vid,tid)
     if key==previous_key:
      if item!=previous_item:raise ValueError('Несколько разных цен на одну дату и вид цены. Снимок не опубликован.')
      continue
     previous_key=key;previous_item=item
     text=json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'))
     if count:digest.update(b',')
     digest.update(text.encode());output.write(text+'\n');count+=1;active_count+=active;seen_types.add(item['type'])
     if count>5000000:raise ValueError('Превышен предел снимка цен. Данные не опубликованы.')
  digest.update(b']')
  if not count:raise ValueError('Регистр цен пуст. Последний успешный снимок сохранён.')
  return SnapshotRows(path,count,digest.hexdigest()),{'price_types':sorted(seen_types),'price_types_details':[{'price_type_id':key,'label':value['_Description']} for key,value in types.items()],'active_prices':active_count},latest
 except Exception:
  Path(path).unlink(missing_ok=True);raise

class SnapshotRows:
 def __init__(self,path,count,digest):self.path=Path(path);self.count=count;self.digest=digest
 def __len__(self):return self.count
 def __iter__(self):
  with self.path.open(encoding='utf-8') as file:
   for line in file:yield json.loads(line)
 def close(self):self.path.unlink(missing_ok=True)

def extract_sales(db,start,finish):
 # Existing confirmed retail rule: only active movements of posted, unmarked KKM receipts.
 end=finish+timedelta(days=1)
 rows=query(db,'''SELECT r._Period period,r._RecorderRRef recorder_id,r._LineNo line_no,
 r._Fld44752RRef product_id,r._Fld44753RRef variant_id,p._Description product_name,p._Fld8132 article,
 r._Fld44761RRef store_id,s._Description store_name,r._Fld44758RRef organization_id,
 r._Fld44766 quantity,r._Fld44767 revenue,r._Fld44768 vat,r._Fld44769 revenue_before_discount,
 r._Fld52390 open_shift,d._Fld30572RRef shift_id
 FROM dbo._AccumRg44751 r JOIN dbo._Document949 d ON d._IDRRef=r._RecorderRRef
 LEFT JOIN dbo._Reference333 p ON p._IDRRef=r._Fld44752RRef
 LEFT JOIN dbo._Reference558 s ON s._IDRRef=r._Fld44761RRef
 WHERE r._Active=0x01 AND d._Posted=0x01 AND d._Marked=0x00 AND r._RecorderTRef=0x000003B5
 AND r._Period>=%s AND r._Period<%s ORDER BY r._Period,r._RecorderRRef,r._LineNo''',(start.replace(year=start.year+2000),end.replace(year=end.year+2000)))
 latest=None
 for r in rows:
  r['period']=normalized(r['period']);latest=max(latest,r['period']) if latest else r['period']
  for key in ('recorder_id','product_id','variant_id','store_id','organization_id','shift_id'):r[key]=hexid(r[key])
  r['line_no']=int(r['line_no']);r['open_shift']=1 if r['open_shift']==b'\x01' else 0
  r['product_name']=r['product_name'] or '';r['article']=r['article'] or '';r['store_name']=r['store_name'] or ''
  r['channel']='retail' if r['store_id'] in STORES else 'unclassified'
 keys={(r['period'],r['recorder_id'],r['line_no']) for r in rows}
 if len(keys)!=len(rows):raise ValueError('Дубли движений чеков в источнике. Витрина не обновлена.')
 if not rows:raise ValueError('За выбранный период нет проведённых чеков. Прежние данные сохранены.')
 # Reconcile aggregates with an independent source query before publication.
 expected=query(db,'''SELECT COUNT_BIG(*) lines,COUNT(DISTINCT r._RecorderRRef) receipts,SUM(r._Fld44766) quantity,SUM(r._Fld44767) revenue
 FROM dbo._AccumRg44751 r JOIN dbo._Document949 d ON d._IDRRef=r._RecorderRRef
 WHERE r._Active=0x01 AND d._Posted=0x01 AND d._Marked=0x00 AND r._RecorderTRef=0x000003B5 AND r._Period>=%s AND r._Period<%s''',
 (start.replace(year=start.year+2000),end.replace(year=end.year+2000)))[0]
 actual={'lines':len(rows),'receipts':len({r['recorder_id'] for r in rows}),'quantity':sum(r['quantity'] for r in rows),'revenue':sum(r['revenue'] for r in rows)}
 if actual!=expected:raise ValueError('Источник изменился во время чтения или сверка сумм не прошла. Повторите импорт.')
 return rows,{**clean(actual),'date_from':str(start),'checked_through':str(finish),'source_key':None},latest

def publish(app,job,source,database,dataset,rows,summary,latest,start,finish):
 if isinstance(rows,SnapshotRows):digest=rows.digest
 else:
  if dataset in {'catalog','prices'}:rows.sort(key=lambda r:(r['product_id'],r['variant_id'],r.get('price_type_id','')))
  digest=hashlib.sha256(json.dumps(clean(rows),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 snapshot=str(uuid.uuid4())
 summary={**summary,'database':database,'source_key':source,'date_from':str(start),'checked_through':str(finish)}
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute("SET LOCAL lock_timeout='5s'")
   c.execute('SELECT pg_advisory_xact_lock(715001)')
   c.execute('SELECT s.id,s.digest FROM one_c_import.active a JOIN one_c_import.snapshots s ON s.id=a.snapshot_id WHERE a.database_name=%s AND a.dataset=%s',(database,dataset));old=c.fetchone()
   unchanged=bool(old and old['digest']==digest)
   if dataset=='sales':
    # Back up affected rows and replace in one transaction, including corrected/unposted source records.
    c.execute("INSERT INTO one_c_import.sales_backups(job_id,payload) SELECT %s,to_jsonb(s) FROM retail_1c.movements s WHERE recorder_type='000003B5' AND period>=%s AND period<%s",(job,start,finish+timedelta(days=1)))
    c.execute("DELETE FROM retail_1c.movements WHERE recorder_type='000003B5' AND period>=%s AND period<%s",(start,finish+timedelta(days=1)))
    values=[]
    for r in rows:
     payload={**clean(r),'active':1,'check_posted':1,'check_marked':0,'check_shift_id':r['shift_id']}
     values.append((r['period'],'000003B5',r['recorder_id'],r['line_no'],Json(payload)))
    execute_values(c,'INSERT INTO retail_1c.movements(period,recorder_type,recorder_id,line_no,payload) VALUES %s',values,page_size=1000)
    c.execute('SELECT COUNT(*) lines,COUNT(DISTINCT recorder_id) receipts,SUM(quantity) quantity,SUM(revenue) revenue FROM retail_1c.sales WHERE period>=%s AND period<%s',(start,finish+timedelta(days=1)));check=c.fetchone()
    if any(check[k]!=(Decimal(str(summary[k])) if k in {'quantity','revenue'} else summary[k]) for k in ('lines','receipts','quantity','revenue')):raise ValueError('Сверка витрины не прошла; изменения отменены.')
    c.execute('INSERT INTO retail_1c.import_runs(export_sha,metadata,row_count,checked_receipts) VALUES(%s,%s,%s,%s) ON CONFLICT(export_sha) DO UPDATE SET metadata=EXCLUDED.metadata,loaded_at=now()', (digest,Json({**summary,'date_to_exclusive':str(finish+timedelta(days=1)),'table':'_AccumRg44751'}),len(rows),summary['receipts']))
   if not unchanged:
    c.execute('INSERT INTO one_c_import.snapshots(id,database_name,dataset,source_key,source_latest,row_count,digest,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(snapshot,database,dataset,source,latest,len(rows),digest,Json(summary)))
    if dataset in {'catalog','prices'}:
     cols=['snapshot_id','product_id','variant_id']+(['price_type_id'] if dataset=='prices' else [])+['payload']
     vals=([snapshot,r['product_id'],r['variant_id']]+([r['price_type_id']] if dataset=='prices' else [])+[Json(r)] for r in rows)
     execute_values(c,'INSERT INTO one_c_import.'+dataset+'('+','.join(cols)+') VALUES %s',vals,page_size=1000)
     c.execute('ANALYZE one_c_import.'+dataset)
    c.execute('INSERT INTO one_c_import.active(database_name,dataset,snapshot_id) VALUES(%s,%s,%s) ON CONFLICT(database_name,dataset) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id',(database,dataset,snapshot))
   else:
    c.execute('UPDATE one_c_import.snapshots SET checked_at=now(),summary=%s,source_key=%s WHERE id=%s',(Json(summary),source,str(old['id'])))
   c.execute('INSERT INTO one_c_import.settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()',('source:'+database+':'+dataset,Json(source)))
   if dataset=='prices' and database=='1c_retail_prod':
    candidates={r['price_type_id'] for r in summary.get('price_types_details',[]) if r['label'].strip().lower() in {'розничная','розничные','розничная цена'}}
    if len(candidates)==1:c.execute("INSERT INTO one_c_import.settings(key,value) VALUES('retail_price_type',%s) ON CONFLICT DO NOTHING",(Json(next(iter(candidates))),))
 return {'database':database,'source':source,'dataset':dataset,'label':LABELS[dataset],'status':'completed','row_count':len(rows),'source_latest':str(latest or ''),'unchanged':unchanged,'summary':summary}

def status(app):
 ensure(app)
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute("SELECT * FROM one_c_import.jobs WHERE status IN ('queued','running')")
   for row in c.fetchall():
    if (datetime.now(timezone.utc)-row['created_at']).total_seconds()>60:
     try:
      if not row['pid']:raise ProcessLookupError()
      os.kill(row['pid'],0)
     except (ProcessLookupError,PermissionError):c.execute("UPDATE one_c_import.jobs SET status='interrupted',finished_at=now(),message='Процесс импорта прерван. Опубликованные данные сохранены; повторите запуск.' WHERE id=%s",(str(row['id']),))
   c.execute('SELECT id,created_at,started_at,finished_at,status,result,message,request,events,current_task,stop_requested FROM one_c_import.jobs ORDER BY created_at DESC LIMIT 15');jobs=c.fetchall()
   c.execute('SELECT a.database_name,a.dataset,s.source_key,s.checked_at AS loaded_at,s.source_latest,s.row_count,s.summary,s.digest FROM one_c_import.active a JOIN one_c_import.snapshots s ON s.id=a.snapshot_id ORDER BY a.database_name,a.dataset');snapshots=c.fetchall()
   price_snapshot=next((s for s in snapshots if s['database_name']=='1c_retail_prod' and s['dataset']=='prices'),None)
   types=[]
   if price_snapshot:
    digest=price_snapshot['digest'];types=price_snapshot['summary'].get('price_types_details') or _TYPE_CACHE.get(digest)
    if types is None:
     c.execute("SELECT DISTINCT price_type_id,payload->>'type' label FROM one_c_import.current_prices WHERE database_name='1c_retail_prod' ORDER BY label");types=c.fetchall();_TYPE_CACHE[digest]=types
   c.execute("SELECT value FROM one_c_import.settings WHERE key='retail_price_type'");row=c.fetchone()
 return clean({'ok':True,'sources':sources(app),'jobs':jobs,'snapshots':snapshots,'price_types':types,'retail_price_type':row['value'] if row else '', 'default_from':'2026-08-01','default_to':str(date.today())})

def validate_request(app,payload):
 available={s['key']:s for s in sources(app)}
 selected=payload.get('sources');datasets=payload.get('datasets')
 if not isinstance(selected,list) or not 1<=len(selected)<=3 or len(set(selected))!=len(selected) or any(s not in available for s in selected):raise ValueError('Выберите текущее подключение 1С')
 if not isinstance(datasets,list) or not datasets or len(set(datasets))!=len(datasets) or any(d not in LABELS for d in datasets):raise ValueError('Выберите данные для импорта')
 start=date.fromisoformat(str(payload.get('date_from') or '2026-08-01'));finish=date.fromisoformat(str(payload.get('date_to') or date.today()))
 if start>finish or finish>date.today() or (finish-start).days>731:raise ValueError('Выберите период не больше двух лет, до сегодняшнего дня')
 tasks=[]
 for src in selected:
  entry=available[src]
  for dataset in datasets:
   if dataset in capabilities(entry['database']):tasks.append({'source':src,'database':entry['database'],'dataset':dataset})
 if payload.get('tasks') is not None:
  exact=payload['tasks']
  if not isinstance(exact,list) or not exact or len(exact)>9:raise ValueError('Выберите этапы 1С')
  pairs=[]
  for item in exact:
   if not isinstance(item,dict) or (item.get('source'),item.get('dataset')) not in {(t['source'],t['dataset']) for t in tasks}:raise ValueError('Выберите доступные этапы 1С')
   pairs.append((item['source'],item['dataset']))
  if len(set(pairs))!=len(pairs):raise ValueError('Этапы повторяются')
  tasks=[t for t in tasks if (t['source'],t['dataset']) in set(pairs)]
 if not tasks:raise ValueError('Для выбранных данных схема этой базы пока не сопоставлена')
 return {'tasks':tasks,'date_from':str(start),'date_to':str(finish)}

def start(app,payload,user_id=None):
 request=validate_request(app,payload);ensure(app);job=str(uuid.uuid4())
 try:
  with app.client_registry_connection() as pg:
   with pg.cursor() as c:c.execute("INSERT INTO one_c_import.jobs(id,status,request,user_id) VALUES(%s,'queued',%s,%s)",(job,Json(request),user_id))
 except psycopg2.errors.UniqueViolation:raise ValueError('Импорт из 1С уже выполняется. Дождитесь результата.')
 try:
  child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--job',job],cwd=str(Path(__file__).parent),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  with app.client_registry_connection() as pg:
   with pg.cursor() as c:c.execute('UPDATE one_c_import.jobs SET pid=%s WHERE id=%s',(child.pid,job))
 except Exception:
  with app.client_registry_connection() as pg:
   with pg.cursor() as c:c.execute("UPDATE one_c_import.jobs SET status='failed',finished_at=now(),message='Не удалось запустить процесс импорта' WHERE id=%s",(job,))
  raise ValueError('Не удалось запустить процесс импорта')
 return {'ok':True,'job_id':job,'status':'queued'}

def event(app,job,text,task=None):
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute("UPDATE one_c_import.jobs SET events=events||%s::jsonb,current_task=%s WHERE id=%s",(Json([{'time':datetime.now(timezone.utc).isoformat(),'text':text,'marketplace':'one_c'}]),Json(task) if task else None,job))

def stopped(app,job):
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute('SELECT stop_requested FROM one_c_import.jobs WHERE id=%s',(job,));return bool(c.fetchone()['stop_requested'])

def worker(app,job):
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute("UPDATE one_c_import.jobs SET status='running',started_at=now(),pid=%s WHERE id=%s AND status='queued' RETURNING request",(os.getpid(),job));row=c.fetchone()
 if not row:return
 request=row['request'];results=[];interrupted=False
 event(app,job,'1С · Запущено этапов: '+str(len(request['tasks'])))
 for task in request['tasks']:
  if stopped(app,job):interrupted=True;break
  source,database,dataset=task['source'],task['database'],task['dataset'];rows=None
  label=DATABASES[database]+' · '+LABELS[dataset]
  try:
   event(app,job,label+' · Чтение источника',task)
   with sql(app,source,database) as db:
    p=require_profile(database);check_schema(db,p)
    start_date=date.fromisoformat(request['date_from']);finish=date.fromisoformat(request['date_to'])
    if dataset=='sales':rows,summary,latest=extract_sales(db,start_date,finish)
    elif dataset=='catalog':rows,summary,latest=extract_catalog(db,p)
    else:rows,summary,latest=extract_prices(db,p)
   if stopped(app,job):interrupted=True;event(app,job,label+' · Остановлено до публикации');break
   event(app,job,label+' · Прочитано '+str(len(rows))+' строк, сверка и публикация',task)
   result=publish(app,job,source,database,dataset,rows,summary,latest,start_date,finish)
   event(app,job,label+' · Готово: '+str(result['row_count'])+' строк'+(' · без изменений' if result['unchanged'] else ''))
  except Exception as exc:
   message=str(exc) if isinstance(exc,ValueError) else 'Источник недоступен или импорт не прошёл сверку. Прежние данные сохранены.'
   result={**task,'label':LABELS[dataset],'status':'failed','message':message,'error_class':type(exc).__name__}
   event(app,job,label+' · Ошибка: '+message)
  finally:
   if isinstance(rows,SnapshotRows):rows.close()
  results.append(result)
  with app.client_registry_connection() as pg:
   with pg.cursor() as c:c.execute('UPDATE one_c_import.jobs SET result=%s,message=%s WHERE id=%s',(Json(results),'Завершено '+str(len(results))+' из '+str(len(request['tasks'])),job))
 failed=sum(r['status']=='failed' for r in results)
 final='interrupted' if interrupted else 'completed' if not failed else 'failed' if failed==len(results) else 'partial'
 if any(r['status']=='completed' and r['database']=='1c_retail_prod' for r in results):
  try:
   event(app,job,'1С · Обновление сопоставления ассортимента')
   import build_catalog
   build_catalog.run('toptop')
  except Exception:
   if not interrupted:final='partial'
   results.append({'dataset':'crosswalk','status':'failed','message':'Данные 1С загружены, но сопоставление ассортимента не обновилось. Повторите импорт.'})
 message='Импорт остановлен; опубликованные данные сохранены' if interrupted else 'Импорт завершён' if final=='completed' else 'Импорт завершён с ошибками; см. результаты'
 event(app,job,'1С · '+message)
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:c.execute('UPDATE one_c_import.jobs SET status=%s,finished_at=now(),result=%s,message=%s,current_task=NULL WHERE id=%s',(final,Json(results),message,job))

def source_key(app,dataset='sales'):
 # Resolve from the currently configured source, not an obsolete credential UUID.
 current=[s['key'] for s in sources(app) if s['database']=='1c_retail_prod']
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute('SELECT value FROM one_c_import.settings WHERE key=%s',('source:1c_retail_prod:'+dataset,));row=c.fetchone()
 if row:return row['value'] if row['value'] in current else ''
 return current[0] if len(current)==1 else ''
def report_tables(dataset='sales'):
 if dataset=='sales':return ('dbo._AccumRg44751','dbo._Document949','dbo._Reference333','dbo._Reference558')
 p=PROFILES['1c_retail_prod'];names=['Номенклатура','ХарактеристикиНоменклатуры','ШтрихкодыНоменклатуры']
 if dataset=='prices':names+=['ЦеныНоменклатуры','ВидыЦен','Валюты']
 return tuple('dbo.'+p[n]['table'] for n in names)
def access(app,identity,dataset='sales'):
 if identity.get('is_admin'):return True
 from data_access import permits
 key=source_key(app,dataset)
 return bool(key) and all(permits(identity.get('data_access'),key,t) for t in report_tables(dataset))

def handle(rt,h,method):
 from urllib.parse import urlparse,parse_qs
 app=rt.app;parsed=urlparse(h.path)
 if not h.dashboard_access_granted():h.send_dashboard_access_required(parsed);return
 identity=h.dashboard_access_identity() or {}
 origin=h.headers.get('Origin') if hasattr(h,'headers') else None
 if method=='POST' and (h.headers.get('Sec-Fetch-Site')=='cross-site' or origin and urlparse(origin).netloc!=h.headers.get('Host')):
  h.send_json({'ok':False,'error':'Недопустимый источник запроса'},status=403);return
 if not identity.get('is_admin') and (not {'clientOnboarding','allDaily'}.intersection(identity.get('admin_sections') or []) or 'toptop' not in identity.get('clients',[])):
  h.send_json({'ok':False,'error':'Нет доступа к импорту из 1С'},status=403);return
 if parse_qs(parsed.query).get('client',['toptop'])[0]!='toptop':h.send_json({'ok':False,'error':'Подключения 1С доступны в TOPTOP'},status=403);return
 try:
  if method=='GET':
   q=parse_qs(parsed.query)
   if q.get('view')==['preview']:
    h.send_json(preview(app,q),headers={'Cache-Control':'no-store'});return
   h.send_json(status(app),headers={'Cache-Control':'no-store'});return
  payload=h.read_json_body()
  if payload.get('action')=='stop':
   ensure(app)
   with app.client_registry_connection() as pg:
    with pg.cursor() as c:c.execute("UPDATE one_c_import.jobs SET stop_requested=true,message='Остановка запрошена; текущая операция завершается' WHERE status IN ('queued','running')")
   h.send_json(status(app));return
  if payload.get('action')=='settings':
   value=str(payload.get('retail_price_type') or '');ensure(app)
   with app.client_registry_connection() as pg:
    with pg.cursor() as c:
     c.execute("SELECT 1 FROM one_c_import.current_prices WHERE database_name='1c_retail_prod' AND price_type_id=%s LIMIT 1",(value,))
     if value and not c.fetchone():raise ValueError('Выберите загруженный вид цен Розницы')
     c.execute("INSERT INTO one_c_import.settings(key,value) VALUES('retail_price_type',%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()",(Json(value),))
   h.send_json(status(app));return
  h.send_json(start(app,payload,identity.get('user_id')),status=202)
 except (ValueError,TypeError):h.send_json({'ok':False,'error':'Проверьте параметры импорта' if not isinstance(sys.exception(),ValueError) else str(sys.exception())},status=400)
 except psycopg2.Error:h.send_json({'ok':False,'error':'Хранилище импорта недоступно'},status=503)

def preview(app,q):
 get=lambda k,d='':q.get(k,[d])[0]
 source=next((s for s in sources(app) if s['key']==get('source')),None)
 dataset=get('dataset')
 if not source or dataset not in {'catalog','prices'}:raise ValueError('Выберите подключение и вид данных')
 page=max(1,min(int(get('page','1')),100000));search=get('q')[:120]
 name='one_c_import.current_'+dataset;params=[source['database']]
 if dataset=='prices':
  join="LEFT JOIN one_c_import.current_catalog cat ON cat.database_name=d.database_name AND cat.product_id=d.product_id AND cat.variant_id=d.variant_id"
  item="d.payload || jsonb_build_object('name',cat.payload->>'name','article',cat.payload->>'article','variant',cat.payload->>'variant')"
  search_expr="coalesce(cat.payload::text,'')||d.payload::text"
 else:join='';item='d.payload';search_expr='d.payload::text'
 where='d.database_name=%s'
 if search:where+=' AND ('+search_expr+') ILIKE %s';params.append('%'+search+'%')
 with app.client_registry_connection() as pg:
  with pg.cursor() as c:
   c.execute("SET LOCAL statement_timeout='10s'")
   c.execute('SELECT COUNT(*) total FROM '+name+' d '+(join if search else '')+' WHERE '+where,params);total=c.fetchone()['total']
   if dataset=='prices' and not search:
    c.execute('WITH page AS MATERIALIZED (SELECT * FROM '+name+' d WHERE '+where+' ORDER BY d.product_id,d.variant_id,d.price_type_id LIMIT 50 OFFSET %s) SELECT '+item+' item FROM page d '+join+' ORDER BY d.product_id,d.variant_id,d.price_type_id',params+[(page-1)*50])
   else:c.execute('SELECT '+item+' item FROM '+name+' d '+join+' WHERE '+where+' ORDER BY d.product_id,d.variant_id'+(',d.price_type_id' if dataset=='prices' else '')+' LIMIT 50 OFFSET %s',params+[(page-1)*50])
   rows=[r['item'] for r in c.fetchall()]
 return {'ok':True,'rows':rows,'total':total,'page':page,'database':source['database'],'dataset':dataset}

if __name__=='__main__':
 import pulse_vps_admin as rt
 rt.configure_scope();rt._USE_WRITER_CONFIG.set(True)
 worker(rt.app,str(uuid.UUID(sys.argv[2])))
