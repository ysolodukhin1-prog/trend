from pathlib import Path


path = Path(__file__).resolve().parent / "verify_sportmaster_full_quality_v16.mjs"
text = path.read_text(encoding="utf-8")
old = "if(JSON.stringify(structural.coverageCounts)!==JSON.stringify({sufficient:45,missing:167,insufficient:96,limited:39}))throw new Error(`coverage counts changed: ${JSON.stringify(structural.coverageCounts)}`);"
new = "if(structural.coverageCounts?.sufficient!==45||structural.coverageCounts?.limited!==39||structural.coverageCounts?.insufficient!==96||structural.coverageCounts?.missing!==167)throw new Error(`coverage counts changed: ${JSON.stringify(structural.coverageCounts)}`);"
if old not in text:
    raise RuntimeError("coverage assertion anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched coverage assertion")
