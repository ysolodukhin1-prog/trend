"""Launch the existing PULSE command with its environment preserved in memory.

No environment values are logged or written to disk. An alternate port leaves
the source server running; --port 8052 replaces only the verified source PID.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".runtime/health-check-tools"))
import psutil

parser = argparse.ArgumentParser()
parser.add_argument("--source-pid", type=int, required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
source = psutil.Process(args.source_pid)
command = source.cmdline()
cwd = source.cwd()
assert Path(cwd).resolve() == ROOT.resolve()
assert any(arg.replace("\\", "/").endswith("ozon_category_dashboard/app.py") for arg in command)
environment = source.environ()
environment["DASHBOARD_PORT"] = str(args.port)
environment["DASHBOARD_HOST"] = "127.0.0.1"
if args.port != 8052:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", args.port))
else:
    assert any(c.status == psutil.CONN_LISTEN and c.laddr.port == 8052 for c in source.net_connections())
    source.terminate()
    source.wait(timeout=20)
log = ROOT / ".runtime" / f"health-check-{args.port}"
with log.with_suffix(".out.log").open("ab") as out, log.with_suffix(".err.log").open("ab") as err:
    child = subprocess.Popen(command, cwd=cwd, env=environment, stdout=out, stderr=err,
                             creationflags=subprocess.CREATE_NO_WINDOW)
print(json.dumps({"port": args.port, "pid": child.pid, "environment": "preserved, not persisted"}), flush=True)
deadline = time.monotonic() + 45
while time.monotonic() < deadline:
    if child.poll() is not None:
        raise RuntimeError(f"PULSE exited: {child.returncode}; inspect bounded startup log")
    try:
        with urlopen(f"http://127.0.0.1:{args.port}/api/health?client=lera_nena&marketplace=wb", timeout=3) as response:
            data = json.load(response)
            assert data["ok"] and data["client"] == "lera_nena"
        print("PULSE health and client context verified", flush=True)
        break
    except (OSError, ValueError):
        time.sleep(1)
else:
    raise RuntimeError("PULSE startup health timed out")
