"""Isolated read process; never writes raw exception messages or query results to logs."""
import json,sys,contextlib,io
import pulse_vps_admin as runtime
import trend_reports as g

def main():
 try:
  request=json.loads(sys.stdin.read(20000))
  with contextlib.redirect_stdout(io.StringIO()):
   runtime.configure_scope();runtime._USE_WRITER_CONFIG.set(False)
   original=runtime.app.read_db_config
   def read_config(client=None):
    if client not in (None,'toptop'):raise g.SourceDenied()
    return g.toptop_report_config(original('toptop'))
   runtime.app.read_db_config=read_config
   with runtime.app.client_registry_connection() as db:
    with db.cursor() as c:
     c.execute("SELECT reports,status,marketplaces FROM public.bi_client_registry WHERE client_key='toptop'")
     row=c.fetchone()
   if row:runtime.app.ADMIN_CLIENTS['toptop'].update(row)
   # Preserve safe, precise failure categories even when the UI handler catches SQL errors.
   old_error=runtime.app.api_error_payload
   def error_payload(e):
    payload=old_error(e);code=getattr(e,'pgcode',None)
    payload['reason_code']={'42P01':'report_relation_missing','42501':'storage_role_permission_denied','57014':'sql_statement_timeout','25006':'report_attempted_storage_write'}.get(code,'source_'+type(e).__name__)
    return payload
   runtime.app.api_error_payload=error_payload
   result=g.execute(runtime.app,tuple(request['subject']),request['args'])
  response={'result':result}
 except g.SourceDenied:response={'error':'denied'}
 except (ValueError,TypeError,KeyError):response={'error':'invalid','reason_code':'invalid_request'}
 except g.Unavailable as e:response={'error':'unavailable','reason_code':e.code,'upstream_http_status':e.http_status}
 except Exception as e:
  code=getattr(e,'pgcode',None)
  response={'error':'unavailable','reason_code':{'42P01':'report_relation_missing','42501':'storage_role_permission_denied','57014':'sql_statement_timeout','25006':'report_attempted_storage_write'}.get(code,'source_'+type(e).__name__), 'sqlstate':code}
 print(json.dumps(response,default=str,ensure_ascii=False))

if __name__=='__main__':main()
