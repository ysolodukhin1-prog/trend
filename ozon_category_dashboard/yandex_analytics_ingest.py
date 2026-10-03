"""Durable, rate-safe read-only Yandex analytical report ingestion.

Report generation creates download artifacts only; no marketplace settings change.
Raw rows are published atomically and retain report/sheet/row provenance.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError, URLError
from email.utils import parsedate_to_datetime
import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values
from yandex_market_history import sanitize

REPORTS = {
    'sales_funnel': ('shows-sales',600),
    'boost_sales': ('boost-consolidated',120),
    'boost_shows': ('shows-boost',120),
    'banners': ('banners-statistics',120),
    'shelves': ('shelf-statistics',120),
    'payments': ('united-netting',120),
    'services': ('united-marketplace-services',120),
    'realization': ('goods-realization',120),
    'movement': ('goods-movement',120),
    'turnover': ('goods-turnover',120),
    'stocks': ('stocks-on-warehouses',120),
    'prices_report': ('goods-prices',120),
}
DIRECT = {'catalog': ('offer-mappings','offerMappings'), 'prices': ('offer-prices','offers')}
SUBSCRIPTION_REPORTS=('sales_funnel','boost_sales','boost_shows','banners','shelves','movement','turnover','prices_report')
MAX_FILE = 256 * 1024 * 1024

DDL = """
CREATE TABLE IF NOT EXISTS yandex_analytics_jobs (
 job_key text PRIMARY KEY, client_key text NOT NULL, business_id text NOT NULL,
 campaign_id text NOT NULL, source_key text NOT NULL, date_from date NOT NULL,
 date_to date NOT NULL, request_body jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
 report_id text, attempts integer NOT NULL DEFAULT 0, rows_count bigint NOT NULL DEFAULT 0,
 next_at timestamptz NOT NULL DEFAULT now(), error text, started_at timestamptz,
 finished_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS yandex_analytics_raw (
 job_key text NOT NULL REFERENCES yandex_analytics_jobs(job_key), sheet text NOT NULL,
 row_no bigint NOT NULL, payload jsonb NOT NULL, PRIMARY KEY(job_key,sheet,row_no));
CREATE TABLE IF NOT EXISTS yandex_analytics_rate (
 business_id text NOT NULL, resource text NOT NULL, next_at timestamptz NOT NULL,
 PRIMARY KEY(business_id,resource));
CREATE INDEX IF NOT EXISTS yandex_analytics_jobs_scope ON yandex_analytics_jobs(source_key,business_id,campaign_id,date_from,date_to);
ALTER TABLE yandex_analytics_jobs ADD COLUMN IF NOT EXISTS requested_date_from date;
ALTER TABLE yandex_analytics_jobs ADD COLUMN IF NOT EXISTS requested_date_to date;
ALTER TABLE yandex_analytics_jobs ADD COLUMN IF NOT EXISTS next_page_token text;
ALTER TABLE yandex_analytics_jobs ADD COLUMN IF NOT EXISTS direct_page integer NOT NULL DEFAULT 0;
ALTER TABLE yandex_analytics_jobs ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0;
UPDATE yandex_analytics_jobs SET requested_date_from=date_from,requested_date_to=date_to WHERE requested_date_from IS NULL;
"""

def months(start,end):
    while start<=end:
        nxt=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        yield start,min(end,nxt-timedelta(days=1))
        start=nxt

def plan(client,start,end):
    """Business reports remain business grain; never copied into every store."""
    result=[]
    def add(source,business,campaign,a,b,body):
        identity=[client['key'],business,campaign,source,a.isoformat(),b.isoformat(),body]
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        result.append((key,client['key'],str(business),str(campaign),source,a,b,Json(body)))
    for account in client.get('marketplace_accounts',{}).get('yandex_market',[]):
        stores=[s for s in account.get('stores',[]) if s.get('is_accessible') and s.get('import_enabled')]
        if not stores or not account.get('is_accessible'):continue
        business=int(account['business_id']);ids=[int(s['campaign_id']) for s in stores]
        for source in DIRECT:
            for archived in (False,True):
                add(source,business,'',date.today(),date.today(),{'archived':archived})
        for source in ['boost_sales','boost_shows','banners','shelves']:
            body={'businessId':business,'dateFrom':start.isoformat(),'dateTo':end.isoformat()}
            if source in ('boost_shows','shelves'):body['attributionType']='CLICKS'
            add(source,business,'',start,end,body)
        for a,b in months(start,end):
            for source in ['payments','services']:
                add(source,business,'',a,b,{'businessId':business,'campaignIds':ids,'dateFrom':a.isoformat(),'dateTo':b.isoformat()})
        add('prices_report',business,'',date.today(),date.today(),{'businessId':business})
        for store in stores:
            campaign=int(store['campaign_id'])
            add('sales_funnel',business,campaign,start,end,{'campaignId':campaign,'dateFrom':start.isoformat(),'dateTo':end.isoformat(),'grouping':'OFFERS'})
            if store.get('placement_type')=='FBY':
                # API returns the day PRECEDING reportDate.
                add('stocks',business,campaign,end,end,{'campaignId':campaign,'reportDate':(end+timedelta(days=1)).isoformat()})
                add('turnover',business,campaign,end,end,{'campaignId':campaign,'date':end.isoformat()})
                add('movement',business,campaign,start,end,{'campaignId':campaign,'dateFrom':start.isoformat(),'dateTo':end.isoformat()})
            for a,b in months(start,end):
                # Financial realization is a calendar-month document, not a daily event.
                add('realization',business,campaign,a,b,{'campaignId':campaign,'year':a.year,'month':a.month})
        partner=[int(s['campaign_id']) for s in stores if s.get('placement_type')!='FBY']
        for campaign in partner:
            add('stocks',business,campaign,date.today(),date.today(),{'businessId':business,'campaignIds':[campaign]})
    return result

def check_scope_overlap(conn,jobs):
    """Allow identical resume and full-window extension; reject ambiguous overlap."""
    snapshots={'stocks','turnover','prices','prices_report','catalog'}
    with conn.cursor() as c:
        for key,client,business,campaign,source,a,b,_ in jobs:
            if source in snapshots:continue
            c.execute("SELECT date_from,date_to FROM yandex_analytics_jobs WHERE client_key=%s AND business_id=%s AND campaign_id=%s AND source_key=%s AND job_key<>%s AND state IN ('completed','polling','queued','retry','submitting','uncertain') AND date_from<=%s AND date_to>=%s",(client,business,campaign,source,key,b,a))
            for old_a,old_b in c.fetchall():
                if not (a<=old_a and b>=old_b):
                    raise ValueError(f'Overlapping {source} report periods: use an identical or fully enclosing window')

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        check_download_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def check_download_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or not any(p.hostname==d or p.hostname.endswith('.'+d) for d in ['yandex.ru','yandex.net','yandexcloud.net']):
        raise ValueError('Report download host is not an approved Yandex host')

class ApiError(Exception):
    def __init__(self,status,message,retry=0):
        self.status,self.retry=status,retry
        super().__init__(message)

def header_wait(headers,request_cost=1,now=None):
    """Server quota deadline is RFC822; missing headers do not invent a wait."""
    now=now or datetime.now(timezone.utc)
    try:
        remaining=float(headers.get('X-RateLimit-Resource-Remaining','inf'))
        if remaining>=request_cost:return 0
        until=parsedate_to_datetime(headers.get('X-RateLimit-Resource-Until',''))
        if until.tzinfo is None:until=until.replace(tzinfo=timezone.utc)
        return max(0,(until-now).total_seconds()+2)
    except (ValueError,TypeError,OverflowError):return 0

def report_lane(source):
    return 'subscription' if source in SUBSCRIPTION_REPORTS else ('stocks' if source=='stocks' else 'financial')

def can_progress(jobs):
    blocked={(b,report_lane(s)) for b,s,state in jobs if state in ('submitting','uncertain')}
    return any(state=='polling' or (state in ('queued','retry') and (s in DIRECT or (b,report_lane(s)) not in blocked)) for b,s,state in jobs)

def decode_archive(data):
    """No extraction; preserve equal financial lines by ordinal, reject malformed reports."""
    out=[]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members=[i for i in archive.infolist() if not i.is_dir()]
        if sum(i.file_size for i in members)>MAX_FILE*4:raise ValueError('Report expanded size exceeds limit')
        for member in members:
            if not member.filename.lower().endswith('.json'):continue
            sheet=Path(member.filename).stem
            payload=json.loads(archive.read(member))
            if isinstance(payload,dict):
                candidates=[v for v in payload.values() if isinstance(v,list)]
                if len(candidates)!=1:raise ValueError('Unknown report JSON wrapper')
                payload=candidates[0]
            if not isinstance(payload,list) or any(not isinstance(row,dict) for row in payload):
                raise ValueError('Unknown report JSON rows')
            out.extend((sheet,i,sanitize(row)) for i,row in enumerate(payload))
        if not any(i.filename.lower().endswith('.json') for i in members):raise ValueError('Report contains no JSON sheets')
    return out

class Collector:
    def __init__(self,client,conn,key,log=print,job_keys=None):
        self.client,self.conn,self.key,self.log=client,conn,key,log
        self.job_keys = None if job_keys is None else list(job_keys)
        self.requests=0;self.last_request=0
        with conn.cursor() as cur:cur.execute(DDL)
        conn.commit()

    def request(self,method,path,query=None,body=None):
        allowed=(method=='POST' and (path in ['/v2/reports/'+p+'/generate' for p,_ in REPORTS.values()] or re.fullmatch(r'/v2/businesses/[1-9]\d*/(offer-mappings|offer-prices)',path))) or (method=='GET' and re.fullmatch(r'/v2/reports/info/[A-Za-z0-9_-]+',path))
        if not allowed:raise ValueError('Read operation not allowed')
        resource='api:'+path.split('/info/')[0]
        with self.conn.cursor() as c:
            c.execute("SELECT next_at FROM yandex_analytics_rate WHERE business_id='' AND resource=%s",(resource,))
            row=c.fetchone()
        self.conn.commit()
        if row:
            while (wait:=(row[0]-datetime.now(timezone.utc)).total_seconds())>0:
                self.log(f"WAIT | {self.client['key']} {resource} quota resumes in {wait:.0f}s")
                time.sleep(min(20,wait))
        # Prices spend points per returned row: 100 rows / 1.5s = 4000/min,
        # below the documented 5000/min even when quota headers are absent.
        spacing=1.5 if path.endswith('offer-prices') else 1
        time.sleep(max(0,spacing-(time.monotonic()-self.last_request)))
        self.requests+=1;self.last_request=time.monotonic()
        self.log(f"API | {self.client['key']} request={self.requests} | {method} {path.split('/info/')[0]}")
        req=Request('https://api.partner.market.yandex.ru'+path+('?' + urlencode(query) if query else ''),data=json.dumps(body).encode() if body is not None else None,headers={'Api-Key':self.key,'Content-Type':'application/json'},method=method)
        try:
            with build_opener().open(req,timeout=60) as response:
                self.save_headers(resource,response.headers,100 if path.endswith(('offer-mappings','offer-prices')) else 1)
                result=json.load(response)
                if result.get('status')=='ERROR':raise ApiError(502,'API returned ERROR')
                return result
        except HTTPError as exc:
            quota_wait=self.save_headers(resource,exc.headers)
            # Only sanitized, bounded diagnostic text; never store the response itself.
            try:
                errors=json.loads(exc.read(12000)).get('errors',[])
                message='; '.join(str(e.get('code',''))+': '+str(e.get('message','')) for e in errors)
            except Exception:message='HTTP error'
            message=message.replace(self.key,'[redacted]')
            message=re.sub(r'https?://\S+','[url]',message)[:500]
            retry=exc.headers.get('Retry-After','')
            try:retry_seconds=int(retry) if retry.isdigit() else max(0,int((parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()))
            except (TypeError,ValueError):retry_seconds=0
            raise ApiError(exc.code,message,max(retry_seconds,int(quota_wait))) from None

    def save_headers(self,resource,headers,request_cost=1):
        wait=header_wait(headers,request_cost)
        if wait:
            with self.conn.cursor() as c:
                c.execute("INSERT INTO yandex_analytics_rate VALUES('',%s,%s) ON CONFLICT(business_id,resource) DO UPDATE SET next_at=GREATEST(yandex_analytics_rate.next_at,EXCLUDED.next_at)",(resource,datetime.now(timezone.utc)+timedelta(seconds=wait)))
            self.conn.commit()
        return wait

    def update(self,key,state,**fields):
        allowed={'report_id','rows_count','next_at','error','finished_at','started_at','request_body','date_from','date_to','retry_count'}
        if set(fields)-allowed:raise ValueError('Invalid job update')
        setters=['state=%s','updated_at=now()']+[f'{k}=%s' for k in fields]
        with self.conn.cursor() as c:c.execute('UPDATE yandex_analytics_jobs SET '+','.join(setters)+' WHERE job_key=%s',[state,*fields.values(),key])
        self.conn.commit()

    def publish(self,job,rows):
        with self.conn.cursor() as c:
            c.execute('DELETE FROM yandex_analytics_raw WHERE job_key=%s',(job['job_key'],))
            if rows:execute_values(c,'INSERT INTO yandex_analytics_raw(job_key,sheet,row_no,payload) VALUES %s',[(job['job_key'],s,n,Json(r)) for s,n,r in rows],page_size=1000)
            c.execute("UPDATE yandex_analytics_jobs SET state='completed',rows_count=%s,error=CASE WHEN error LIKE 'HTTP %%' OR error LIKE 'network%%' THEN NULL ELSE error END,finished_at=now(),updated_at=now() WHERE job_key=%s",(len(rows),job['job_key']))
        self.conn.commit()
        self.log(f"DONE | {self.client['key']} {job['source_key']} store={job['campaign_id'] or 'business'} {job['date_from']}..{job['date_to']} rows={len(rows)}")

    def direct(self,job):
        self.update(job['job_key'],job['state'],started_at=job['started_at'] or datetime.now(timezone.utc))
        with self.conn.cursor() as c:
            c.execute('UPDATE yandex_analytics_jobs SET attempts=attempts+1 WHERE job_key=%s',(job['job_key'],))
        self.conn.commit()
        suffix,field=DIRECT[job['source_key']];token=job.get('next_page_token') or '';seen=set()
        offset=int(job['rows_count']);page_no=int(job.get('direct_page') or 0)
        while True:
            query={'limit':100}
            if token:query['pageToken']=token
            p=self.request('POST',f"/v2/businesses/{job['business_id']}/{suffix}",query,job['request_body']).get('result',{})
            page=p.get(field)
            if not isinstance(page,list):raise ValueError('Missing direct response rows')
            next_token=(p.get('paging') or {}).get('nextPageToken') or ''
            if next_token and (next_token==token or next_token in seen):raise ValueError('Repeated page token')
            # Atomic page checkpoint. Partial raw rows stay invisible to analytical
            # views until the final page marks the job completed.
            with self.conn.cursor() as c:
                if page:execute_values(c,'INSERT INTO yandex_analytics_raw(job_key,sheet,row_no,payload) VALUES %s',[(job['job_key'],job['source_key'],offset+i,Json(sanitize(row))) for i,row in enumerate(page)])
                offset+=len(page);page_no+=1
                c.execute("UPDATE yandex_analytics_jobs SET rows_count=%s,direct_page=%s,next_page_token=%s,state=%s,error=CASE WHEN %s THEN NULL ELSE error END,finished_at=CASE WHEN %s THEN now() ELSE NULL END,updated_at=now() WHERE job_key=%s",(offset,page_no,next_token,'queued' if next_token else 'completed',not next_token,not next_token,job['job_key']))
            self.conn.commit()
            self.log(f"PROGRESS | {self.client['key']} {job['source_key']} page={page_no} batch={len(page)} rows={offset} errors=0 ETA=unknown (API has no total)")
            if not next_token:break
            seen.add(next_token);token=next_token
        self.log(f"DONE | {self.client['key']} {job['source_key']} rows={offset} pages={page_no}")

    def tick(self):
        # End read transactions so PostgreSQL now() advances during quota waits.
        self.conn.commit()
        with self.conn.cursor(cursor_factory=RealDictCursor) as c:
            scope = " AND job_key=ANY(%s)" if self.job_keys is not None else ""
            c.execute("SELECT * FROM yandex_analytics_jobs WHERE state IN ('polling','queued','retry') AND next_at<=now()" + scope + " ORDER BY CASE WHEN state='polling' THEN 0 ELSE 1 END, CASE WHEN source_key='sales_funnel' THEN 0 ELSE 1 END,job_key", (self.job_keys,) if self.job_keys is not None else None)
            jobs=c.fetchall()
        for job in jobs:
            business,source=job['business_id'],job['source_key']
            with self.conn.cursor() as c:
                if job['state']!='polling':
                    # Official limits: the shared subscription concurrency cap applies
                    # only to reports explicitly carrying that cap. Keep one of those
                    # plus at most one financial/operational report. HTTP calls are serial.
                    lane=report_lane(source)
                    c.execute("SELECT EXISTS(SELECT 1 FROM yandex_analytics_jobs WHERE business_id=%s AND state IN ('polling','submitting','uncertain') AND CASE WHEN source_key=ANY(%s) THEN 'subscription' WHEN source_key='stocks' THEN 'stocks' ELSE 'financial' END=%s)",(business,list(SUBSCRIPTION_REPORTS),lane))
                    if c.fetchone()[0] and source not in DIRECT:continue
                    c.execute('SELECT next_at>now() FROM yandex_analytics_rate WHERE business_id=%s AND resource=%s',(business,source))
                    row=c.fetchone()
                    if row and row[0]:continue
            try:
                if source in DIRECT:
                    self.direct(job);return True
                if job['state']!='polling':
                    slug,cooldown=REPORTS[source]
                    # Persist reservation before request; restart cannot bypass a quota wait.
                    with self.conn.cursor() as c:
                        c.execute("INSERT INTO yandex_analytics_rate VALUES(%s,%s,now()+(%s*interval '1 second')) ON CONFLICT(business_id,resource) DO UPDATE SET next_at=EXCLUDED.next_at",(business,source,cooldown+5))
                        c.execute("UPDATE yandex_analytics_jobs SET attempts=attempts+1,started_at=COALESCE(started_at,now()),state='submitting' WHERE job_key=%s",(job['job_key'],))
                    self.conn.commit()
                    p=self.request('POST','/v2/reports/'+slug+'/generate',{'format':'JSON'},job['request_body'])['result']
                    report_id=p['reportId']
                    if not re.fullmatch(r'[A-Za-z0-9_-]+',str(report_id)):raise ValueError('Invalid report id')
                    delay=max(15,min(60,int(p.get('estimatedGenerationTime',15000))/1000))
                    self.update(job['job_key'],'polling',report_id=str(report_id),next_at=datetime.now(timezone.utc)+timedelta(seconds=delay))
                    self.log(f"REPORT | {self.client['key']} {source} submitted; polling after {delay:.0f}s")
                else:
                    p=self.request('GET','/v2/reports/info/'+job['report_id'])['result']
                    if p['status']=='DONE':
                        if p.get('subStatus')=='NO_DATA':
                            self.publish(job,[])
                            return True
                        check_download_url(p['file'])
                        # API credentials are never sent to the report host.
                        with build_opener(SafeRedirect()).open(p['file'],timeout=120) as f:data=f.read(MAX_FILE+1)
                        if len(data)>MAX_FILE:raise ValueError('Report exceeds download size limit')
                        self.publish(job,decode_archive(data))
                    elif p['status'] in ('FAILED','CANCELLED'):
                        self.update(job['job_key'],'limited',error='report '+str(p.get('subStatus') or p['status']),finished_at=datetime.now(timezone.utc))
                    else:self.update(job['job_key'],'polling',next_at=datetime.now(timezone.utc)+timedelta(seconds=20))
                return True
            except ApiError as e:
                self.conn.rollback()
                state='failed';extra={'error':f'HTTP {e.status}: {e}','retry_count':job.get('retry_count',0)+1}
                if e.status in (401,403):state='limited'
                elif e.status in (420,429,500,502,503,504) and job.get('retry_count',0)<5:
                    state='polling' if job['report_id'] else 'retry'
                    extra['next_at']=datetime.now(timezone.utc)+timedelta(seconds=max(e.retry,REPORTS.get(source,('',60))[1]+5))
                # An explicitly rejected old period can be retried once for the documented 90-day window.
                elif e.status==400 and source in SUBSCRIPTION_REPORTS and 'dateFrom' in job['request_body'] and job['date_from']<date.today()-timedelta(days=89) and '90 days' in str(e):
                    a=date.today()-timedelta(days=89);body=dict(job['request_body']);body['dateFrom']=a.isoformat()
                    extra.update(request_body=Json(body),date_from=a,next_at=datetime.now(timezone.utc)+timedelta(seconds=REPORTS[source][1]+5),error='History before '+a.isoformat()+' unavailable: '+str(e))
                    state='retry'
                self.update(job['job_key'],state,**extra)
                self.log(f"{state.upper()} | {self.client['key']} {source} | {extra['error']}")
                return True
            except (URLError,TimeoutError):
                self.conn.rollback()
                # Do not blindly re-generate after an ambiguous POST timeout.
                retries=job.get('retry_count',0)+1
                state=('polling' if retries<=5 else 'uncertain') if job['report_id'] else ('uncertain' if source not in DIRECT else ('retry' if retries<=5 else 'failed'))
                self.update(job['job_key'],state,retry_count=retries,error='network timeout; report generation outcome unknown' if not job['report_id'] and source not in DIRECT else 'download/poll timeout',next_at=datetime.now(timezone.utc)+timedelta(seconds=120))
                return True
            except Exception as e:
                self.conn.rollback()
                self.update(job['job_key'],'failed',error=type(e).__name__+': '+str(e)[:200] if isinstance(e,ValueError) else type(e).__name__)
                self.log(f"FAILED | {self.client['key']} {source} | {type(e).__name__}")
                return True
        return False

def main():
    import app,client_registry
    parser=argparse.ArgumentParser()
    parser.add_argument('--clients',nargs='+',required=True)
    parser.add_argument('--date-from',type=date.fromisoformat,required=True)
    parser.add_argument('--date-to',type=date.fromisoformat,required=True)
    args=parser.parse_args()
    if args.date_from>args.date_to or args.date_to>=date.today():raise ValueError('History ends no later than yesterday')
    log=lambda s:print(s,flush=True)
    registry=app.client_registry_connection()
    with registry.cursor() as c:
        c.execute("SELECT pg_try_advisory_lock(hashtext('yandex_analytics_collector')) AS acquired")
        if not c.fetchone()['acquired']:raise RuntimeError('Collector already running')
    registry.commit()
    with app.client_registry_connection() as c:clients=client_registry.list_clients(c)
    collectors=[]
    try:
        for key in args.clients:
            client=next(c for c in clients if c['key']==key and c['status']=='active' and 'yandex_market' in c['marketplaces'])
            cfg=dict(app.read_db_config());cfg['database']=client['db_name']
            conn=psycopg2.connect(**cfg)
            credential=app.registered_client_credential(key,'yandex_market_api_key')
            if not credential:raise ValueError(f'No saved Yandex API key for {key}')
            collector=Collector(client,conn,credential,log)
            jobs=plan(client,args.date_from,args.date_to)
            check_scope_overlap(conn,jobs)
            with conn.cursor() as c:
                execute_values(c,'INSERT INTO yandex_analytics_jobs(job_key,client_key,business_id,campaign_id,source_key,date_from,date_to,request_body) VALUES %s ON CONFLICT(job_key) DO NOTHING',jobs)
            conn.commit();collectors.append(collector)
            with conn.cursor() as c:
                c.execute('UPDATE yandex_analytics_jobs SET requested_date_from=date_from,requested_date_to=date_to WHERE requested_date_from IS NULL')
            conn.commit()
            log(f'PLAN | {key} jobs={len(jobs)}; at most one subscription, one financial and one stock report per business; HTTP serial; generation cooldown=125s or 605s; resume=yes')
        started=time.monotonic();last=0;initial_pending=None
        while True:
            progressed=False
            for collector in collectors:progressed=collector.tick() or progressed
            totals={};pending=0
            for collector in collectors:
                with collector.conn.cursor() as c:
                    c.execute('SELECT state,count(*),sum(rows_count) FROM yandex_analytics_jobs GROUP BY state')
                    counts=c.fetchall();totals[collector.client['key']]=counts
                    pending+=sum(n for state,n,_ in counts if state in ('queued','retry','polling'))
            if time.monotonic()-last>20 or not pending:
                if initial_pending is None:initial_pending=pending
                total=sum(n for counts in totals.values() for _,n,_ in counts)
                done=sum(n for counts in totals.values() for state,n,_ in counts if state=='completed')
                rows=sum(int(rows or 0) for counts in totals.values() for _,_,rows in counts)
                elapsed=time.monotonic()-started;advanced=initial_pending-pending
                eta=f'~{elapsed*pending/advanced:.0f}s' if advanced>0 else 'unknown (report generation)'
                log(f'PROGRESS | {done}/{total} ({100*done/total if total else 0:.1f}%) rows={rows} elapsed={elapsed:.0f}s pending={pending} ETA={eta} | '+str(totals));last=time.monotonic()
            if not pending:break
            if not progressed:
                eventual=[]
                for collector in collectors:
                    with collector.conn.cursor() as c:
                        c.execute('SELECT business_id,source_key,state FROM yandex_analytics_jobs')
                        eventual.append(can_progress(c.fetchall()))
                if not any(eventual):
                    log('BLOCKED | unresolved report generation outcome; preserved report IDs and queued work for operator recovery')
                    break
            if not progressed:time.sleep(5)
        from yandex_analytics import install
        from yandex_market_history import ensure_schema
        gaps=0
        for collector in collectors:
            log(f"NORMALIZE | {collector.client['key']} installing typed facts and refreshing analytical aggregates")
            ensure_schema(collector.conn);install(collector.conn,collector.client)
            with collector.conn.cursor() as c:
                c.execute("SELECT count(*) FROM yandex_data_coverage WHERE coverage_status NOT IN ('completed','empty')")
                gaps+=c.fetchone()[0]
        bad=sum(n for counts in totals.values() for state,n,_ in counts if state not in ('completed',))
        log(f'FINAL | status={"partial" if bad or gaps else "completed"} errors_or_limits={bad} coverage_gaps={gaps} elapsed={time.monotonic()-started:.0f}s | output=client DB yandex_analytics_raw and typed analytical marts')
        return 3 if bad or gaps else 0
    finally:
        for collector in collectors:collector.conn.close()
        registry.close()

if __name__=='__main__':
    raise SystemExit(main())
