"""Additive SHA-guarded deployment; preserve reader/import processes."""
from pathlib import Path
import hashlib
import subprocess
import json
import time
root = Path('/opt/toptop/deploy/ff-platform-real-20261012')
reader = 'toptop_data_stage-pulse_reader-1'
w = '/workspace/ozon_category_dashboard/'
compose = Path('/opt/toptop/infra/compose.pulse.yaml')
guards = {'ff_platform.py': '1d3b7a315242287a9ab7fbc691629924208470f2a8b1c5a164f2a16a64b13031',
          'static/react/index.html': 'da8012cd213bd754a4c3a7343b1fb2053ed2838cb3827d93c670d2fda32c882f',
          'static/react/assets/index-ff-platform-auto-20261012.js': '6eaee433e0953c348dce92930b9bb3dd43581ba6db60dd52ebe9f0dc7d611543'}
def run(*args): return subprocess.check_output(args, text=True).strip()
def guard():
    for path, sha in guards.items():
        assert run('docker', 'exec', reader, 'sha256sum', w+path).split()[0] == sha, path
guard()
old = compose.read_bytes()
base = b'toptop-trend:ff-platform-auto-20261012'
assert old.count(base) == 1
image = 'toptop-trend:ff-platform-real-20261012'
files = [('ff_platform.py', 'ff_platform.py'),
         ('index-ff-platform-real-20261012.js','static/react/assets/index-ff-platform-real-20261012.js'),
         ('index.real.html','static/react/index.html')]
(root/'Dockerfile').write_text('FROM '+base.decode()+'\n'+''.join('COPY '+local+' '+w+remote+'\n' for local,remote in files))
run('docker', 'build', '-t', image, str(root))
assert compose.read_bytes() == old
guard()
before = run('docker', 'inspect', reader, '--format', '{{.State.Pid}} {{.State.StartedAt}}')
for path in guards:
    run('docker', 'cp', reader+':'+w+path, str(root/(Path(path).name+'.before')))
for local, remote in files:
    run('docker', 'cp', str(root/local), reader+':'+w+remote)
    assert run('docker','exec',reader,'sha256sum',w+remote).split()[0] == hashlib.sha256((root/local).read_bytes()).hexdigest()
stop = "import os,signal; from pathlib import Path; ids=[int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit() and (p/'cmdline').exists() and (p/'cmdline').read_bytes().split(bytes([0]))[:-1] == [b'python',b'-u',b'ff_platform.py']]; assert len(ids)==1,ids; os.kill(ids[0],signal.SIGTERM)"
run('docker', 'exec', reader, 'python', '-c', stop)
time.sleep(1)
run('docker', 'exec', '-d', '-w', w, reader, 'python', '-u', 'ff_platform.py')
for _ in range(20):
    check = subprocess.run(['docker','exec',reader,'python','-c',"import urllib.request,urllib.error;\ntry: urllib.request.urlopen('http://127.0.0.1:8063/api/admin/ff-platform',timeout=2)\nexcept urllib.error.HTTPError as e: assert e.code==401"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode == 0: break
    time.sleep(1)
else: raise RuntimeError('FF handler not ready')
assert compose.read_bytes() == old
(root/'compose.before.yaml').write_bytes(old)
compose.write_bytes(old.replace(base,image.encode()))
assert before == run('docker','inspect',reader,'--format','{{.State.Pid}} {{.State.StartedAt}}')
print(json.dumps({'ok':True,'reader_unchanged':True,'image':image}))
