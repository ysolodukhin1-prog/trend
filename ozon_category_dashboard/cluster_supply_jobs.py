"""Bounded in-process calculation jobs with tenant-scoped progress polling."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,threading,time,uuid
from cluster_supply_store import calculate

_lock=threading.Lock()
_jobs={}
_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='cluster-supply')


def submit(config,client,payload):
    signature=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    with _lock:
        for key,value in list(_jobs.items()):
            if value['status'] in {'completed','failed'} and time.monotonic()-value['updated']>1800:del _jobs[key]
        active=[j for j in _jobs.values() if j['status'] in {'queued','running'}]
        same=next((j for j in active if j['client']==client and j['signature']==signature),None)
        if same:return {'job_id':same['job_id'],'status':same['status']}
        if len(active)>=4:raise ValueError('Очередь расчётов заполнена; дождитесь текущего расчёта')
        # Keep only the most recent completed responses; input snapshots may be sizable.
        old=sorted((j for j in _jobs.values() if j['status'] in {'completed','failed'}),key=lambda j:j['updated'])
        for j in old[:-16]:_jobs.pop(j['job_id'],None)
        identifier=str(uuid.uuid4())
        _jobs[identifier]={'job_id':identifier,'client':client,'signature':signature,'status':'queued',
            'progress':deque(maxlen=150),'updated':time.monotonic(),'response':None}
    def report(line):
        with _lock:
            _jobs[identifier]['progress'].append(str(line));_jobs[identifier]['updated']=time.monotonic()
    def work():
        with _lock:_jobs[identifier]['status']='running'
        try:
            response=calculate(config,client,payload,report)
            with _lock:_jobs[identifier].update(status='completed',response=response,updated=time.monotonic())
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            with _lock:_jobs[identifier].update(status='failed',error=str(exc),updated=time.monotonic())
        except Exception:
            with _lock:_jobs[identifier].update(status='failed',error='Расчёт недоступен. Проверьте источники и повторите запрос.',updated=time.monotonic())
    _pool.submit(work)
    return {'job_id':identifier,'status':'queued'}


def read(client,identifier):
    with _lock:
        value=_jobs.get(identifier)
        if not value or value['client']!=client:return None
        return {k:(list(v) if k=='progress' else v) for k,v in value.items() if k in {'job_id','status','progress','response','error'}}
