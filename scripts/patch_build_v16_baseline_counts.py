from pathlib import Path


path = Path(__file__).resolve().parent / "build_sportmaster_full_quality_v16.py"
text = path.read_text(encoding="utf-8")
old = '"beforeCounts": coverage.get("counts"),'
new = '"beforeCounts": {"sufficient": 36, "limited": 33, "insufficient": 98, "missing": 180},'
if old not in text:
    raise RuntimeError("beforeCounts anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched immutable v15 baseline counts")
