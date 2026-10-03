"""Opt-in bootstrap, inherited only by TREND import subprocesses."""
import os
if os.environ.get('TREND_IMPORT_WORKER') == '1':
    try:
        import pulse_vps_admin as v
        v.configure_scope()
        v._USE_WRITER_CONFIG.set(True)
        client = v._strict_client(os.environ.get('DASHBOARD_CLIENT'))
        v.app.CURRENT_CLIENT.set(client)
        os.environ["DASHBOARD_DB_NAME"] = client
        original_config = v.app.read_db_config
        def worker_config(value=None):
            config = original_config(value)
            config["options"] = "-c statement_timeout=0 -c lock_timeout=120000"
            return config
        v.app.read_db_config = worker_config
    except Exception as exc:
        # Python normally ignores sitecustomize errors; fail closed instead.
        import sys
        print('TREND bootstrap failed: ' + type(exc).__name__, file=sys.stderr, flush=True)
        os._exit(78)
