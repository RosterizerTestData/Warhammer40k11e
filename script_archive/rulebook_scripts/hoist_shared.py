"""Homogenise units several consumer books define separately into their hub (shared base + per-book overrides).

  python3 hoist_shared.py <Hub> "<Unit name>" ["<Unit name>" ...] [--apply]

Per unit: each consumer book's copy (any class/case spelling) is re-keyed to one hub key (specific class preferred over
Unit§), together with the unit's local models and weapons ("<Unit>—…"). The hub gets the common core of all copies
minus keywords.Faction (so the base is available nowhere by itself — allowance comes from each book's Faction); each
book keeps only an override. Verified: merge(hub base, override) must equal the book's previous asset.
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rzmerge import merge_entry, make_overlay, common_core, canon_unordered_traits

RZ = Path(__file__).resolve().parents[2]
args = [a for a in sys.argv[1:] if a != "--apply"]
APPLY = "--apply" in sys.argv
hub, names = args[0], args[1:]
books = {p.stem: json.load(open(p)) for p in RZ.glob("*.rulebook")}
hub_name = books[hub]["name"].strip()
consumers = [s for s, b in books.items() if any(d["name"].strip() == hub_name for d in b.get("dependencies") or [])]
UNITCLS = ("Character", "Vehicle", "Infantry/Mounted", "Unit")   # preference order
n = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("’", "'"))
log, review, touched = [], [], set()


def cat(s):
    return books[s]["rulebook"]["assetCatalog"]


for U in names:
    copies = {s: k for s in consumers for k in cat(s) if k.split("§")[0] in UNITCLS and n(k.split("§", 1)[1]) == n(U)}
    if len(copies) < 2:
        review.append(f"{U}: only in {list(copies)} — nothing to hoist"); continue
    cls = next(c for c in UNITCLS if any(k.split("§")[0] == c for k in copies.values()))
    HK = f"{cls}§{U}"
    if HK in cat(hub):
        review.append(f"{U}: {HK} already in {hub}"); continue
    # 1. canonical keys in every book (unit key + "<old name>—" weapon prefixes)
    for s, k in copies.items():
        old = k.split("§", 1)[1]
        txt = json.dumps(books[s], ensure_ascii=False).replace(f'"{k}"', f'"{HK}"')
        if old != U:
            txt = txt.replace(f"§{old}—", f"§{U}—")
        books[s] = json.loads(txt)
    # 2. the group: unit, its local models, its "<U>—" weapons (+ their profiles)
    group = {HK}
    for s in copies:
        c = cat(s)
        blob = json.dumps(c[HK], ensure_ascii=False)
        group |= {m for m in re.findall(r'"(Model§[^"]+)"', blob) if m in c}
        group |= {k for k in c if "Weapon§" in k and k.split("§", 1)[1].startswith(f"{U}—")}
    ok = True
    plan = {}
    for key in sorted(group):
        have = {s: cat(s)[key] for s in copies if key in cat(s)}
        if key in cat(hub):
            continue   # model/weapon already shared through the hub
        if key.startswith("Model§") and any(key in cat(s) and s not in copies for s in consumers):
            review.append(f"{U}: {key} is also used outside these books — left local"); continue
        base = common_core(list(have.values())) if len(have) > 1 else json.loads(json.dumps(next(iter(have.values()))))
        if isinstance(base.get("keywords"), dict):
            base["keywords"].pop("Faction", None)
        ovs = {}
        for s, a in have.items():
            ov = make_overlay(base, a)
            if canon_unordered_traits(merge_entry(base, ov)) != canon_unordered_traits(a):
                review.append(f"{U}: {key} in {s} does not round-trip — unit skipped"); ok = False
            ovs[s] = ov
        plan[key] = (base, ovs)
    if not ok:
        continue
    for key, (base, ovs) in plan.items():
        cat(hub)[key] = base
        for s, ov in ovs.items():
            if ov:
                cat(s)[key] = ov
            else:
                cat(s).pop(key)
            touched.add(s)
    touched.add(hub)
    log.append(f"{U}: {HK} hubbed from {sorted(copies)} ({len(plan)} assets: {sum(k.startswith('Model§') for k in plan)} models, {sum('Weapon§' in k for k in plan)} weapons)")

print("\n".join(log))
print("\n=== REVIEW ===\n" + "\n".join(review))
if APPLY:
    for s in sorted(touched):
        json.dump(books[s], open(RZ / f"{s}.rulebook", "w"), indent=2, ensure_ascii=False)
    print("written", sorted(touched))
