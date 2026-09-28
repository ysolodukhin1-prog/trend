"""Extract public Ozon rate workbooks. Originals and SHA256 retained for audit."""
from pathlib import Path
import hashlib
import json
from urllib.request import urlopen
from urllib.parse import quote
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/unit_tariff_refinement_20260913'
BASE = 'https://cdn.ozone.ru/s3/ozon-disk-api/Seller-edu/files/commissions-tariffs/cross-dock/'
FILES = {
    '2026-08-03': {
        'sc': 'tariffs-cross-dock-sc-03082026-1_1785492271.xlsx',
        'pvz': 'tariffs-cross-dock-pvz-ppz_с_03.08.26_1784813939.xlsx',
        'courier': 'tariffs-cross-dock-PickUp_с_03.08.26_1784813938.xlsx',
    },
    '2026-09-16': {
        'sc': 'tariffs-cross-dock-sc-16.09.26_1788260781.xlsx',
        'pvz': 'tariffs-cross-dock-pvz-ppz_с_16.09.26_1788260775.xlsx',
        'courier': 'tariffs-cross-dock-PickUp_с_16.09.26_1788260781.xlsx',
    },
}


def build():
    OUT.mkdir(exist_ok=True)
    versions, sources = {}, []
    print('ПЛАН: 6 официальных XLSX | последовательно | извлечение тарифов, оригиналы сохраняются', flush=True)
    count = 0
    for start, files in FILES.items():
        modes = {}
        for kind, filename in files.items():
            count += 1
            path = OUT / filename
            if not path.exists():
                with urlopen(BASE + quote(filename), timeout=25) as response:
                    path.write_bytes(response.read())
            sources.append(dict(date=start, kind=kind, url=BASE+filename, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            for sheet in wb:
                if sheet.title.startswith('Список'):
                    continue
                mode = ('ppz' if 'ППЗ' in sheet.title else 'pvz') if kind == 'pvz' else kind
                routes = modes.setdefault(mode, {})
                for row in sheet.iter_rows(values_only=True):
                    if len(row)<(7 if kind=='pvz' else 9) or not isinstance(row[5], (int, float)):
                        continue
                    origin = row[2] if kind == 'pvz' else row[1]
                    destination = row[2] if kind == 'courier' else row[3]
                    if not origin or not destination:
                        continue
                    if kind == 'pvz':
                        values = [row[5], row[6], None, None]
                    else:
                        values = [row[5], row[7], row[6], row[8]]
                    values = [None if v in ('-', '—') else v for v in values]
                    assert all(v is None or isinstance(v, (int, float)) and v >= 0 for v in values), row
                    # Different final warehouses in one cluster may quote different
                    # prices. Retain distinct offers; runtime explicitly budgets max.
                    key = origin.strip()+'|'+destination.strip()
                    offers = routes.setdefault(key, [])
                    if values not in offers:
                        offers.append(values)
            print(f'ПРОГРЕСС: {count}/6 ({count/6:.0%}) | {start} {kind} | маршрутов {sum(map(len,modes.values()))}', flush=True)
            wb.close()
        versions[start] = modes
    result = dict(checked_at='2026-09-13', source_page='https://seller-edu.ozon.ru/libra/commissions-tariffs/commissions-tariffs-ozon/rashody-na-dop-uslugi', sources=sources, versions=versions)
    target = ROOT/'frontend/src/dashboards/ozonCrossdockRates.json'
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'ИТОГ: 6/6 файлов | {target.name} | {target.stat().st_size} байт | ошибки 0', flush=True)


if __name__ == '__main__':
    build()
