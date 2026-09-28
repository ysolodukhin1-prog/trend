from pathlib import Path


path = Path(__file__).resolve().parent / "build_sportmaster_full_quality_v16.py"
text = path.read_text(encoding="utf-8")
anchor = "\n\ndef main() -> None:\n"
helper = '''\n\ndef explicit_title_audience(title: Any) -> str:\n    value = norm(title)\n    if re.search(r"(?:для\\s+)?мальчик|мальчиков|подросток.{0,12}мальч", value):\n        return "boys"\n    if re.search(r"(?:для\\s+)?девоч|девушк.{0,8}подрост", value):\n        return "girls"\n    if re.search(r"\\bмужск|\\bмужчин", value):\n        return "men"\n    if re.search(r"\\bженск|\\bженщин", value):\n        return "women"\n    return ""\n'''
if "def explicit_title_audience" not in text:
    if anchor not in text:
        raise RuntimeError("main anchor not found")
    text = text.replace(anchor, helper + anchor, 1)

needle = '''                if legacy_status:\n                    product["_v16LegacyVisual"] = legacy_status\n                kept.append(product)'''
replacement = '''                if legacy_status:\n                    product["_v16LegacyVisual"] = legacy_status\n                title_audience = explicit_title_audience(product.get("title"))\n                if title_audience and clean(product.get("audienceBucket")) != title_audience:\n                    product["_v16AudienceBeforeTitleGuard"] = clean(product.get("audienceBucket"))\n                    product["audienceBucket"] = title_audience\n                    product["audienceLabel"] = {"boys": "Дети · мальчики", "girls": "Дети · девочки", "men": "Взрослые · мужчины", "women": "Взрослые · женщины"}[title_audience]\n                kept.append(product)'''
if "_v16AudienceBeforeTitleGuard" not in text:
    if needle not in text:
        raise RuntimeError("legacy guard anchor not found")
    text = text.replace(needle, replacement, 1)
path.write_text(text, encoding="utf-8")
print("patched title-audience guard")
