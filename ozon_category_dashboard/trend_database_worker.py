"""One isolated SQL read. No secrets or query values in diagnostics."""
import json,sys,re
import pulse_vps_admin as runtime
import trend_database as gateway

def main():
    try:
        request=json.loads(sys.stdin.read(30000))
        runtime.configure_scope()
        runtime._USE_WRITER_CONFIG.set(True)
        result=gateway.execute(runtime.app,tuple(request['subject']),request['args'])
        body=json.dumps({'result':result},default=str)
        if len(body.encode())>800000:raise ValueError('Result too large')
        print(body)
    except gateway.SourceDenied:
        print(json.dumps({'error':'denied'}))
    except gateway.DatabaseUnavailable as error:
        print(json.dumps({'error':'unavailable','reason_code':error.reason}))
    except (ValueError,TypeError,KeyError):
        print(json.dumps({'error':'invalid'}))
    except Exception as error:
        codes=sorted(set(re.findall(r'\b(?:18456|18452|4060|916|20009|20002)\b',str(error.args))))
        print(json.dumps({'error':'unavailable','error_class':type(error).__name__,'codes':codes}))

if __name__=='__main__':main()
