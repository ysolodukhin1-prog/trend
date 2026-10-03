import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import export_sources as export
import sync

class SafetyTests(unittest.TestCase):
    def test_exclusions(self):
        for p in ['.env','config/credentials.json','ozon_category_dashboard/data/accounts.py','scripts/clients/gloria/x.py','scripts/import_gloria.py','ozon_category_dashboard/__pycache__/x.py']:
            self.assertFalse(export.allowed(p),p)
        self.assertTrue(export.allowed('ozon_category_dashboard/pulse_vps_admin.py'))

    def test_secret_and_syntax(self):
        with self.assertRaises(ValueError):
            export.validate('x.py',b'API_KEY = "' + b'a'*40 + b'"')
        with self.assertRaises(SyntaxError):
            export.validate('x.py',b'def broken(')

    def test_archive_traversal(self):
        out=io.BytesIO()
        data=b'pass\n'
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('../x.py',data)
            z.writestr('LIVE_SOURCE_MANIFEST.json',json.dumps({'../x.py':hashlib.sha256(data).hexdigest()}))
        with self.assertRaises(ValueError):
            sync.unpack(out.getvalue())

    def test_real_git_stability_changes_and_remote_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            origin=base/'origin.git'
            seed=base/'seed'
            def cmd(*args,cwd=None):
                return subprocess.check_output(args,cwd=cwd,stderr=subprocess.DEVNULL).decode().strip()
            cmd('git','init','--bare','--initial-branch=main',str(origin))
            cmd('git','clone',str(origin),str(seed))
            cmd('git','config','user.name','Test',cwd=seed)
            cmd('git','config','user.email','test@example.invalid',cwd=seed)
            (seed/'README.md').write_text('Initial\n')
            cmd('git','add','README.md',cwd=seed)
            cmd('git','commit','-m','Initial',cwd=seed)
            cmd('git','push','origin','main',cwd=seed)
            for name in ('README.md','SOURCE_STATUS.md','sync.py','export_sources.py','test_sync.py','trend-source-sync.service','trend-source-sync.timer'):
                (base/name).write_text('Prepared\n')
            files={'ozon_category_dashboard/pulse_vps_admin.py':b'print(1)\n'}
            def snapshot():
                return files,{p:hashlib.sha256(v).hexdigest() for p,v in files.items()},'test-image'
            with patch.object(sync,'BASE',base),patch.object(sync,'REPO',base/'repo'),patch.object(sync,'REMOTE',str(origin)),patch.object(sync,'snapshot',snapshot),patch('sys.argv',['sync.py']):
                sync.main()
                self.assertFalse((base/'repo').exists())
                sync.main()
                first=sync.git('rev-parse','HEAD')
                sync.main()
                self.assertEqual(first,sync.git('rev-parse','HEAD'))
                files['ozon_category_dashboard/pulse_vps_admin.py']=b'print(2)\n'
                sync.main()
                self.assertEqual(first,sync.git('rev-parse','HEAD'))
                sync.main()
                self.assertNotEqual(first,sync.git('rev-parse','HEAD'))
                cmd('git','pull','--ff-only',cwd=seed)
                (seed/'ozon_category_dashboard/pulse_vps_admin.py').write_text('print(999)\n')
                cmd('git','add','.',cwd=seed)
                cmd('git','commit','-m','Concurrent edit',cwd=seed)
                cmd('git','push','origin','main',cwd=seed)
                with self.assertRaisesRegex(RuntimeError,'reconciliation'):
                    sync.main()
                self.assertEqual(cmd('git','rev-parse','HEAD',cwd=seed),cmd('git','rev-parse','main',cwd=origin))

if __name__=='__main__':
    unittest.main()
