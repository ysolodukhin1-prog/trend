"""Read-only Health Check route/context regression sweep on a specified server."""
import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8068)
args = parser.parse_args()
base = f"http://127.0.0.1:{args.port}"
cases = [(client, market, metric) for client in ("lera_nena", "toptop")
         for market in ("wb", "ozon") for metric in ("ordered_revenue_rub", "oos_sku_share_pct", "ad_ctr_pct")]
results = []
start = time.monotonic()
print(f"PLAN: {len(cases)} diagnostic requests, sequential, read-only; no model provider requested", flush=True)
for index, (client, market, metric) in enumerate(cases, 1):
    params = urlencode(dict(client=client, marketplace=market, metric=metric, month="2026-09"))
    with urlopen(f"{base}/api/health-check-hypothesis-analysis?{params}", timeout=50) as response:
        assert response.status == 200 and "application/json" in response.headers["Content-Type"]
        data = json.load(response)
    assert data["context"]["client"] == client
    assert data["context"]["marketplace"] == market
    assert data["metric"]["id"] == metric
    assert isinstance(data["evidence_tree"], dict)
    results.append(dict(client=client, market=market, metric=metric, scenario=data["scenario"]["code"],
                        branches=len(data["evidence_tree"].get("hypotheses", [])), engine=data["engine"]["mode"]))
    elapsed = time.monotonic() - start
    print(f"PROGRESS: {index}/{len(cases)} ({index/len(cases):.0%}) | {client}/{market}/{metric} | errors=0 | ETA={elapsed/index*(len(cases)-index):.1f}s", flush=True)
for metric in ("", "../invalid"):
    try:
        urlopen(f"{base}/api/health-check-hypothesis-analysis?" + urlencode(dict(client="lera_nena", marketplace="wb", metric=metric)), timeout=10)
        raise AssertionError("Invalid metric accepted")
    except HTTPError as error:
        assert error.code == 400 and json.load(error)["error"] == "invalid_request"
scoped = dict(client="lera_nena", marketplace="wb", metric="ordered_revenue_rub", month="2026-09", category="Сумки")
with urlopen(f"{base}/api/health-check-hypothesis-analysis?{urlencode(scoped)}", timeout=50) as response:
    data = json.load(response)
assert data["context"]["scope"]["active"]
assert data["context"]["scope"]["filters"]["category"] == "Сумки"
with urlopen(f"{base}/glory/api/health-check-hypothesis-analysis?" + urlencode(dict(client="lera_nena", marketplace="wb", metric="ordered_revenue_rub")), timeout=50) as response:
    assert response.status == 200 and json.load(response)["context"]["client"] == "lera_nena"
output = Path(__file__).resolve().parents[1] / "reports" / f"health_check_api_{args.port}_20260913.json"
output.write_text(json.dumps(dict(cases=results, invalid_requests=2, scoped_category="Сумки", gateway_alias=True,
                                  elapsed_seconds=round(time.monotonic()-start, 2)), ensure_ascii=False, indent=2), encoding="utf-8")
print(f"COMPLETE: {len(cases)+4} checks passed; errors=0; output={output}", flush=True)
