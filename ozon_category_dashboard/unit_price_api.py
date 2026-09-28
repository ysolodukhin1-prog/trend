"""Bounded read-only scenario API executing the same price engine as the UI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import hashlib
import time
from collections import OrderedDict
from unit_economics_workspace import checked_config

_slots=threading.BoundedSemaphore(2)
_cache=OrderedDict()
_cache_lock=threading.Lock()

def model_fingerprint():
    root=Path(__file__).parent
    files=[root/'unit_price_worker.mjs',*sorted((root/'frontend/src/dashboards').glob('*.ts'))]
    return hashlib.sha256('|'.join(str(p)+':'+str(p.stat().st_mtime_ns) for p in files).encode()).hexdigest()

def evaluate(config,client,payload):
    checked_config(config,client)
    items=payload.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=1000:raise ValueError('Нужно от 1 до 1000 товарных сценариев')
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('row'),dict):raise ValueError('Некорректная строка сценария')
        row=item['row']
        if row.get('marketplace') not in ('wb','ozon','yandex'):raise ValueError('Неизвестная площадка')
        if not isinstance(row.get('condition'),dict):raise ValueError('Нет условия цены')
    encoded=json.dumps({'items':items,'snapshot':payload.get('snapshot')},ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf8'))>8_000_000:raise ValueError('Слишком большой пакет; разбейте сценарии на части')
    # Exact input+model cache. Time-sensitive Yandex quotes are always revalidated.
    cacheable=all(item['row']['marketplace']!='yandex' for item in items)
    fingerprint=model_fingerprint()
    key=hashlib.sha256((client+encoded+fingerprint+time.strftime('%Y-%m-%d')).encode()).hexdigest()
    if cacheable:
        with _cache_lock:
            entry=_cache.get(key)
            if entry and time.monotonic()-entry[0]<300:
                _cache.move_to_end(key)
                return dict(json.loads(entry[1]),client=client,cache=dict(hit=True,model_fingerprint=fingerprint))
    candidates=[os.environ.get('PULSE_NODE_BINARY'),shutil.which('node'),r'D:\Codex\Кодекс\tools\node\node.exe']
    node=next((p for p in candidates if p and Path(p).is_file()),None)
    if not node:raise ValueError('Не найден Node.js для общего расчётного ядра')
    if not _slots.acquire(blocking=False):raise ValueError('Расчёт занят; повторите запрос')
    try:
        result=subprocess.run([node,str(Path(__file__).with_name('unit_price_worker.mjs'))],input=encoded,text=True,encoding='utf8',
          capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('Не удалось рассчитать сценарий; проверьте вводные')
        if cacheable and len(result.stdout.encode('utf8'))<=1_000_000:
            with _cache_lock:
                _cache[key]=(time.monotonic(),result.stdout)
                while len(_cache)>32:_cache.popitem(last=False)
        return dict(json.loads(result.stdout),client=client,cache=dict(hit=False,model_fingerprint=fingerprint))
    except subprocess.TimeoutExpired:raise ValueError('Превышено время расчёта; уменьшите пакет') from None
    finally:_slots.release()
