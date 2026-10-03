"""Export only reviewed source types from the live TREND workspace; never read secrets."""
import ast
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT_FILES = {'Dockerfile', 'requirements.txt', 'build_product_abc_views.py'}
ROOT_DIRS = {'ozon_category_dashboard', 'scripts', 'migrations'}
EXTENSIONS = {'.py', '.js', '.css', '.html', '.sql', '.svg'}
DENY_PARTS = {'data', 'credentials', 'secrets', '__pycache__', 'node_modules', '.git', '.codex-memory', 'logs'}
OTHER_CLIENTS = re.compile(r'demix|gloria|sportmaster|fila|konstex|avito|detskiy|divan|delicatex|delikateks|ametist|tsvet|na100|ozon_bank|yasno', re.I)
SECRET = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-proj-[A-Za-z0-9_-]{30,}|(?:postgres(?:ql)?|mysql)://[^\s:/]+:[^\s@]+@')

def allowed(path):
    p = Path(path)
    if any(x.lower() in DENY_PARTS or x.startswith('.') for x in p.parts):
        return False
    if re.search(r'(?:credential|secret|password|token).*(?:\.json|\.txt|\.env|\.key|\.pem)$', p.name, re.I):
        return False
    if len(p.parts) == 1:
        return p.name in ROOT_FILES
    if p.parts[0] not in ROOT_DIRS or p.suffix not in EXTENSIONS:
        return False
    if '_tmp_' in p.name or '.bak' in p.name:
        return False
    if p.parts[0] == 'scripts':
        if OTHER_CLIENTS.search(str(p)):
            return False
        if len(p.parts) > 2 and p.parts[1] == 'clients' and p.parts[2] not in {'toptop', 'lera_nena'}:
            return False
    return True

def validate(path, content):
    if len(content) > 15_000_000:
        raise ValueError('Oversized source: ' + path)
    if SECRET.search(content):
        raise ValueError('Possible embedded secret: ' + path)
    if path.endswith('.py'):
        tree = ast.parse(content, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) >= 8:
                    for target in targets:
                        if isinstance(target, ast.Name) and re.fullmatch(r'(?:[A-Z_]*_)?(?:PASSWORD|API_KEY|ACCESS_TOKEN|AUTH_TOKEN|CLIENT_SECRET|SECRET_KEY)', target.id, re.I):
                            if value.value.lower() not in {'changeme', 'your_api_key', 'your_token', 'password'}:
                                raise ValueError('Review literal credential variable: ' + path + ':' + str(node.lineno))

def collect(root):
    result = {}
    for name in sorted(ROOT_FILES | ROOT_DIRS):
        start = root / name
        candidates = start.rglob('*') if start.is_dir() else [start]
        for p in candidates:
            rel = p.relative_to(root).as_posix()
            if not allowed(rel) or not p.is_file() or p.is_symlink():
                continue
            # Resolve prevents symlinked parent directories escaping the source tree.
            if root.resolve() not in p.resolve().parents:
                raise ValueError('Source outside root: ' + rel)
            data = p.read_bytes()
            validate(rel, data)
            result[rel] = data
    for required in ('ozon_category_dashboard/pulse_vps_admin.py', 'ozon_category_dashboard/static/react/index.html', 'requirements.txt'):
        if required not in result:
            raise ValueError('Missing essential source: ' + required)
    return result

def main():
    files = collect(Path(sys.argv[1]))
    manifest = {p: hashlib.sha256(data).hexdigest() for p, data in sorted(files.items())}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for p, data in sorted(files.items()):
            z.writestr(p, data)
        z.writestr('LIVE_SOURCE_MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    sys.stdout.buffer.write(output.getvalue())

if __name__ == '__main__':
    main()
