from pathlib import Path


path = Path(r"D:\Codex\New project\scripts\prepare_sportmaster_full_candidates_v16.py")
text = path.read_text(encoding="utf-8")
old_1 = '''    shortlist_by_profile = {
        row["profile"]["profile_id"]: row for row in shortlist["profiles"]
    }
    candidates_by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
'''
new_1 = '''    shortlist_by_profile = {
        row["profile"]["profile_id"]: row for row in shortlist["profiles"]
    }
    global_by_category_market: dict[
        tuple[str, str], dict[str, dict[str, Any]]
    ] = defaultdict(dict)
    for source_profile in shortlist["profiles"]:
        source_category = source_profile["profile"]["category"]
        source_profile_id = source_profile["profile"]["profile_id"]
        for source_market, source_block in source_profile.get("platforms", {}).items():
            target = global_by_category_market[(source_category, source_market)]
            for source_product in source_block.get("shortlist", []):
                source_sku = str(
                    source_product.get("id") or source_product.get("itemid") or ""
                ).strip()
                if not source_sku:
                    continue
                current = target.get(source_sku)
                candidate = {
                    **source_product,
                    "source_profile_id": source_profile_id,
                }
                if current is None or float(candidate.get("revenue") or 0) > float(
                    current.get("revenue") or 0
                ):
                    target[source_sku] = candidate
    candidates_by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
'''
old_2 = '''        source = shortlist_by_profile.get(profile_id, {}).get("platforms", {}).get(market, {})
        for product in source.get("shortlist", []):
'''
new_2 = '''        profile_rows = (
            shortlist_by_profile.get(profile_id, {})
            .get("platforms", {})
            .get(market, {})
            .get("shortlist", [])
        )
        source_rows: dict[str, dict[str, Any]] = {
            str(product.get("id") or product.get("itemid") or ""): {
                **product,
                "source_profile_id": profile_id,
            }
            for product in profile_rows
            if str(product.get("id") or product.get("itemid") or "")
        }
        source_rows.update(global_by_category_market.get((category, market), {}))
        for product in source_rows.values():
'''
old_3 = '''                    "niche": str(product.get("niche") or product.get("subject") or "").strip(),
                    "guardReason": guard_reason,
'''
new_3 = '''                    "niche": str(product.get("niche") or product.get("subject") or "").strip(),
                    "sourceProfileId": str(product.get("source_profile_id") or profile_id),
                    "guardReason": guard_reason,
'''
for old, new in ((old_1, new_1), (old_2, new_2), (old_3, new_3)):
    if old not in text:
        raise SystemExit("expected block not found")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print(path)
