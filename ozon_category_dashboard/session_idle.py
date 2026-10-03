"""Activity-driven TREND sessions. Polls and identity checks never touch activity."""
import hashlib,hmac,json,time,logging,traceback
from pathlib import Path
from galactica_entitlement import SourceDenied,require_access_origin,verified_subject,require_current_session,transaction_limits

IDLE_SECONDS=3*60*60

def personal_status(app,token,*,touch=False):
    # Only browser-signed credentials can be renewed here, never delegated keys.
    subject=verified_subject(token,app._managed_access_signing_key())
    user_id,username,expiry,revision,session_hash=subject
    with app.client_registry_connection() as conn:
        transaction_limits(conn)
        require_current_session(conn,subject)
        with conn.cursor() as cur:
            if touch:
                cur.execute("SELECT updated_at FROM public.bi_users WHERE user_id=%s AND is_active FOR SHARE",(user_id,))
                from galactica_entitlement import session_revision
                row=cur.fetchone()
                if not row or session_revision(row['updated_at'])!=revision:raise SourceDenied()
                now=int(time.time());expiry=now+IDLE_SECONDS
                cur.execute("""UPDATE public.bi_personal_sessions SET last_activity_at=clock_timestamp(),
                    expires_at=GREATEST(expires_at,to_timestamp(%s))
                    WHERE session_hash=%s AND user_id=%s AND user_revision=%s::timestamptz
                    AND parent_session_hash IS NULL AND revoked_at IS NULL AND expires_at>clock_timestamp()
                    AND last_activity_at>clock_timestamp()-interval '3 hours'
                    RETURNING last_activity_at""",(expiry,session_hash,user_id,revision))
                if cur.fetchone() is None:raise SourceDenied()
                encoded=token.split('.')[0]
                payload=json.loads(app._admin_b64decode(encoded))
                payload.update(issued_at=now,expires_at=expiry)
                encoded=app._admin_b64encode(json.dumps(payload,separators=(',',':')).encode())
                signature=hmac.new(app._managed_access_signing_key(),encoded.encode('ascii'),hashlib.sha256).digest()
                renewed=encoded+'.'+app._admin_b64encode(signature)
            else:
                cur.execute("SELECT extract(epoch FROM last_activity_at)+%s AS deadline FROM public.bi_personal_sessions WHERE session_hash=%s",(IDLE_SECONDS,session_hash))
                expiry=min(expiry,int(cur.fetchone()['deadline']));renewed=None
    return renewed,expiry

def handle(app,handler,*,touch=False):
    if touch:
        try:require_access_origin(handler.headers)
        except (SourceDenied,ValueError):
            # A foreign origin must not be able to clear a legitimate cookie.
            handler.send_json({'ok':False,'reason_code':'SOURCE_ACCESS_DENIED'},status=403,headers={'Cache-Control':'no-store'})
            return
    try:
        if touch:
            length=int(handler.headers.get('Content-Length','0') or '0')
            if not 0<=length<=256:raise ValueError('Invalid activity request')
            if handler.read_json_body()!={}:raise ValueError('Invalid activity body')
        token=app.dashboard_access_session_from_cookie(handler.headers.get('Cookie'))
        identity=app.verify_managed_access_token(token)
        cookie_header=handler.dashboard_access_cookie_header
        if identity and identity.get('is_admin'):
            # Legacy admin tokens have a sliding signed deadline too. No personal
            # or connector grant is minted from this branch.
            payload=json.loads(app._admin_b64decode(token.split('.')[0]))
            expiry=min(int(payload['expires_at']),int(payload['issued_at'])+IDLE_SECONDS)
            if expiry<=time.time():raise SourceDenied()
            renewed=app.create_managed_access_token(identity) if touch else None
            if touch:expiry=int(time.time())+IDLE_SECONDS
        elif identity:
            renewed,expiry=personal_status(app,token,touch=touch)
        else:
            admin=app.admin_session_from_cookie(handler.headers.get('Cookie'))
            if app.verify_admin_session_token(admin):
                token=admin;factory=app.create_admin_session_token;cookie_header=handler.admin_cookie_header
            elif app.verify_dashboard_access_session_token(token):
                factory=app.create_dashboard_access_session_token
            else:raise SourceDenied()
            payload=json.loads(app._admin_b64decode(token.split('.')[0]))
            expiry=min(int(payload['expires_at']),int(payload['issued_at'])+IDLE_SECONDS)
            if expiry<=time.time():raise SourceDenied()
            renewed=factory() if touch else None
            if touch:expiry=int(time.time())+IDLE_SECONDS
        headers={'Cache-Control':'no-store'}
        if renewed:headers['Set-Cookie']=cookie_header(renewed)
        handler.send_json({'ok':True,'idle_seconds':IDLE_SECONDS,'expires_at':expiry},headers=headers)
    except (SourceDenied,ValueError,TypeError,KeyError):
        handler.send_json({'ok':False,'reason_code':'SESSION_EXPIRED'},status=401,
          headers={'Cache-Control':'no-store','Set-Cookie':handler.dashboard_access_cookie_header(clear=True)})
    except Exception as exc:
        # Record location/type only: exception messages can contain SQL or credentials.
        frames=[f'{Path(f.filename).name}:{f.name}:{f.lineno}' for f in traceback.extract_tb(exc.__traceback__)]
        logging.getLogger(__name__).error('SESSION_AUTHORITY_FAILURE operation=%s type=%s sqlstate=%s frames=%s',
            'activity' if touch else 'status',type(exc).__name__,getattr(exc,'pgcode',None),','.join(frames))
        handler.send_json({'ok':False,'reason_code':'SESSION_AUTHORITY_UNAVAILABLE'},status=503,headers={'Cache-Control':'no-store'})

def script(handler):
    body=Path(__file__).with_name('session_activity.js').read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type','text/javascript; charset=utf-8')
    handler.send_header('Cache-Control','no-cache')
    handler.send_header('Content-Length',str(len(body)))
    handler.end_headers();handler.wfile.write(body)
