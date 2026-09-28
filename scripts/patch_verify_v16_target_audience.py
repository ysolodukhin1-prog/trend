from pathlib import Path


path = Path(__file__).resolve().parent / "verify_sportmaster_full_quality_v16.mjs"
text = path.read_text(encoding="utf-8")
old = "state.mode='category';state.brand='Fila';state.category='Верхняя одежда';state.market='OZON';state.audience='girls';state.competitor='';render();"
new = "state.mode='category';state.brand='Fila';state.category='Верхняя одежда';state.market='OZON';state.audience='';state.competitor='';render();state.audience='girls';state.competitor='';renderCategory();"
count = text.count(old)
if count != 2:
    raise RuntimeError(f"expected 2 target anchors, got {count}")
path.write_text(text.replace(old, new), encoding="utf-8")
print("patched target audience rendering")
