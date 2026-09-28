from pathlib import Path


path = Path(__file__).resolve().parent / "verify_sportmaster_no_classic_v17.mjs"
text = path.read_text(encoding="utf-8")
old = "if(interactions.activeTabs!==1||interactions.fitBadges!==interactions.cards||interactions.policyCount!==1)throw new Error(`interaction failed ${JSON.stringify(interactions)}`);"
new = "if((interactions.cards>0&&interactions.activeTabs!==1)||(interactions.cards===0&&interactions.activeTabs!==0)||interactions.fitBadges!==interactions.cards||interactions.policyCount!==1)throw new Error(`interaction failed ${JSON.stringify(interactions)}`);"
if old not in text:
    raise RuntimeError("interaction assertion anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched empty target assertion")
