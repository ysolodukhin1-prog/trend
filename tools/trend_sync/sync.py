"""Dedicated VPS mirror. No production writes, force pushes, resets or broad git add."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from export_sources import allowed, validate

BASE = Path(__file__).resolve().parent
REPO = BASE / 'repo'
CONTAINER = 'toptop_data_stage-pulse_reader-1'
REMOTE = 'git@github.com:ysolodukhin1-prog/trend.git'
os.environ['GIT_TERMINAL_PROMPT'] = '0'
os.environ['GIT_SSH_COMMAND'] = f'ssh -i {BASE}/deploy_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={BASE}/known_hosts'

def run(args, cwd=None, data=None):
    p = subprocess.run(args, cwd=cwd, input=data, capture_output=True, timeout=120)
    if p.returncode:
        # Avoid propagating remote output containing accidental secrets into logs.
        raise RuntimeError('Command failed: ' + args[0] + ' ' + args[1] + ' (exit ' + str(p.returncode) + ')')
    return p.stdout

def git(*args):
    return run(['git', *args], cwd=REPO).decode().strip()

def unpack(raw):
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        expected = json.loads(z.read('LIVE_SOURCE_MANIFEST.json'))
        if len(z.namelist()) != len(set(z.namelist())) or set(z.namelist()) != set(expected) | {'LIVE_SOURCE_MANIFEST.json'}:
            raise ValueError('Archive entry mismatch')
        for name, digest in expected.items():
            if not allowed(name) or '..' in Path(name).parts or Path(name).is_absolute():
                raise ValueError('Unexpected archive path')
            data = z.read(name)
            validate(name, data)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Source checksum mismatch')
            result[name] = data
    return result, expected

def snapshot():
    before = json.loads(run(['docker', 'inspect', '--format', '{{json .Id}}', CONTAINER]))
    image = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Image}}', CONTAINER]))
    raw = run(['docker', 'exec', '-i', CONTAINER, 'python', '-', '/workspace'], data=(BASE/'export_sources.py').read_bytes())
    after = json.loads(run(['docker', 'inspect', '--format', '{{json .Id}}', CONTAINER]))
    if before != after:
        raise RuntimeError('Deployment changed during snapshot; retry next cycle')
    files, manifest = unpack(raw)
    (BASE/'pending.zip').write_bytes(raw)
    return files, manifest, image

def main():
    with (BASE/'sync.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        files, manifest, image = snapshot()
        fingerprint = hashlib.sha256(json.dumps([manifest,image],sort_keys=True).encode()).hexdigest()
        if '--prepare' in sys.argv:
            print(json.dumps({'files':len(files),'image':image,'fingerprint':fingerprint,'push':False}))
            return
        # Require two consecutive matching snapshots before committing.
        candidate = BASE/'candidate.sha256'
        previous = candidate.read_text() if candidate.exists() else ''
        candidate.write_text(fingerprint)
        if previous != fingerprint:
            print('Waiting for stable source snapshot')
            return
        if not REPO.exists():
            run(['git','clone','--branch','main','--single-branch',REMOTE,str(REPO)])
            git('config','user.name','TREND source sync')
            git('config','user.email','trend-sync@users.noreply.github.com')
        if git('status','--porcelain'):
            raise RuntimeError('Mirror has uncommitted changes; manual review required')
        git('fetch','origin','main')
        head = git('rev-parse','HEAD')
        remote = git('rev-parse','origin/main')
        if head != remote:
            # Pending local commit from a failed push: retry only when remote is ancestor.
            ancestors = subprocess.run(['git','merge-base','--is-ancestor','origin/main','HEAD'],cwd=REPO).returncode
            if ancestors == 0:
                git('push','origin','HEAD:main')
            else:
                # Do not overwrite externally edited managed files.
                changed = git('diff','--name-only','HEAD','origin/main').splitlines()
                if any(allowed(p) or p in {'LIVE_SOURCE_MANIFEST.json','README.md','SOURCE_STATUS.md'} or p.startswith('tools/trend_sync/') for p in changed):
                    raise RuntimeError('Remote source edits need reconciliation; no overwrite')
                git('merge','--ff-only','origin/main')
        metadata = {'image':image,'sha256':manifest,'scope':'Live TREND code; excludes credentials, runtime data, unrelated client scripts. Frontend is compiled runtime.'}
        oldpath = REPO/'LIVE_SOURCE_MANIFEST.json'
        old = json.loads(oldpath.read_text()).get('sha256',{}) if oldpath.exists() else {}
        changed = []
        for name,data in files.items():
            target = REPO/name
            if target.is_symlink() or REPO.resolve() not in target.resolve().parents:
                raise RuntimeError('Symlink in mirror')
            if not target.exists() or target.read_bytes() != data:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
                changed.append(name)
        for name in set(old)-set(files):
            if not allowed(name) or '..' in Path(name).parts:
                raise RuntimeError('Invalid old manifest')
            (REPO/name).unlink(missing_ok=True)
            changed.append(name)
        additions = {'LIVE_SOURCE_MANIFEST.json':(json.dumps(metadata,indent=2,sort_keys=True)+'\n').encode()}
        for name in ('README.md','SOURCE_STATUS.md'):
            additions[name] = (BASE/name).read_bytes()
        for name in ('sync.py','export_sources.py','test_sync.py','trend-source-sync.service','trend-source-sync.timer'):
            additions['tools/trend_sync/'+name] = (BASE/name).read_bytes()
        for name,data in additions.items():
            target=REPO/name
            if target.is_symlink() or REPO.resolve() not in target.resolve().parents:
                raise RuntimeError('Symlink in managed metadata')
            if not target.exists() or target.read_bytes()!=data:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(data)
                changed.append(name)
        if changed:
            git('add','--',*changed)
            git('commit','-m','Sync live TREND sources: '+image)
        git('push','origin','HEAD:main')
        if git('ls-remote','origin','refs/heads/main').split()[0] != git('rev-parse','HEAD'):
            raise RuntimeError('Remote commit verification failed')
        print(json.dumps({'status':'synced','files':len(files),'commit':git('rev-parse','HEAD'),'changed':len(changed)}))

if __name__=='__main__':
    main()
