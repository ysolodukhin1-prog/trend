"""Read-only production quotes for six stores at scenario/promotion prices."""
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'reports/unit_economics_20260912'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    began = time.monotonic()
    samples = json.loads((FOLDER / 'yandex_tariff_quote_probe.json').read_text(encoding='utf-8'))
    print('ПЛАН: 2 локальных источника, 12 запросов котировок для6магазинов; цены сценария и акции−20%; без изменения цен и источников.', flush=True)
    for client in ('toptop', 'lera_nena'):
        with urlopen(f'http://127.0.0.1:8052/api/km-trade/unit-workspace?client={client}&date_from=2026-08-01&date_to=2026-08-31', timeout=90) as response:
            source = json.load(response)
        assert source['ok'] and source['client'] == client
        (FOLDER / f'{client}_quote46_source.json').write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    out = []
    for sample in samples:
        price = sample['request']['offers'][0]['price']
        for candidate in (price, round(price * .8, 2)):
            payload = dict(client=sample['client'], cabinet=sample['cabinet'], sku=sample['sku'], price=candidate)
            req = Request('http://127.0.0.1:8052/api/km-trade/unit-yandex-tariffs', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(req, timeout=45) as response:
                result = json.load(response)
            assert result['ok']
            out.append(result)
            elapsed = time.monotonic() - began
            print(f'ПРОГРЕСС: {len(out)}/12 ({len(out)/12:.0%}) | {sample["client"]} {sample["cabinet"]}, цена{candidate} | услуг{len(result["services"])} | {elapsed:.1f}с | ETA{elapsed/len(out)*(12-len(out)):.1f}с', flush=True)
    target = FOLDER / 'yandex_quote46_live.json'
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'ИТОГ:12/12; ошибок0;{time.monotonic()-began:.1f}с;{target.name}', flush=True)


if __name__ == '__main__':
    main()
