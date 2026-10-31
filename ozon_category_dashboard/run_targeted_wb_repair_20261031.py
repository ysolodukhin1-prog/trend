import sys,json,pathlib,time,datetime,os
sys.path.insert(0,'/workspace/ozon_category_dashboard');import pulse_vps_admin as v;v.configure_scope();a=v.app;a.hydrate_registered_clients()
p=pathlib.Path('/var/lib/pulse');out=p/'targeted_wb_repair_20261031.json';parent='260717e9-f205-4d3a-8fda-b9caf9023822'
if out.exists():raise RuntimeError('Repair already registered; inspect existing ledger instead of duplicate')
s=json.loads((p/'admin_all_clients_daily_state.json').read_text());old=json.loads((p/'daily_schedule.json').read_text());scope=set(s['scope_task_ids']);tasks=[t for t in s['tasks'] if t['id'] in scope];failed=[t['id'] for t in tasks if t['status'] in ['error','failed']]
assert s['run_id']==parent and s['status']=='error' and not s.get('active') and len(scope)==122 and old['run_id']==parent
assert failed==['toptop:monthly:wb_stock_history:2026-10'] and not any(t['status'] in ['running','queued','stopping'] for t in tasks)
(p/'admin_before_targeted_wb_repair_20261031.json').write_text(json.dumps(s,ensure_ascii=False));(p/'schedule_before_targeted_wb_repair_20261031.json').write_text(json.dumps(old,ensure_ascii=False))
r={'status':'starting','parent_run_id':parent,'parent_scope_task_ids':sorted(scope),'business_date':'2026-10-30','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requested_import_task_ids':failed,'action':'only failed TOPTOP WB history29/30 plus six dependent views; no other import'}
def save():out.write_text(json.dumps(r,ensure_ascii=False,indent=2))
save();runner=a.admin_all_clients_daily_runner();started=runner.start(resume=True,only_task_ids=failed);r['run_id']=started['run_id'];r['scope_task_ids']=started['scope_task_ids'];assert len(r['scope_task_ids'])==7;save();print(json.dumps({'started':r['run_id'],'selected':len(r['scope_task_ids'])}),flush=True)
runner.thread.join();s=runner.snapshot();r['status']=s['status'];r['finished_at']=s['finished_at'];r['scoped_tasks']=[{'id':t['id'],'status':t['status'],'detail':t.get('detail')} for t in s['tasks'] if t['id'] in set(r['scope_task_ids'])];r['original_scope_status_counts']={st:sum(t['status']==st for t in s['tasks'] if t['id'] in scope) for st in ['ok','limited','error','queued','running']};save()
assert s['status'] in ['completed','idle'] and not s.get('active') and all(t['status'] in ['ok','limited','skipped'] for t in r['scoped_tasks']), 'Target repair failed; preserve evidence'
assert not any(t['status'] in ['error','failed','queued','running','stopping'] for t in s['tasks'] if t['id'] in scope),'Originalscope notterminal'
# Journal the real targeted run, preserving the original 122-task ledger above.
new={**old,'run_id':s['run_id'],'origin':'owner_targeted_wb_history_repair','parent_run_id':parent,'parent_selected_tasks':122,'task_ids':s['scope_task_ids'],'selected_tasks':len(s['scope_task_ids']),'started_at':s['started_at'],'finished_at':s['finished_at'],'state_status':s['status'],'repair_ledger':out.name}
for filename in ['daily_schedule.json','daily_schedule_2026-10-30.json']:
 dest=p/filename;tmp=dest.with_suffix('.repair.tmp');tmp.write_text(json.dumps(new,ensure_ascii=False,indent=2));os.replace(tmp,dest)
r['schedule_reconciled']=True;save();print(json.dumps(r,ensure_ascii=False),flush=True)