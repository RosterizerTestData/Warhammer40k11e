"""Sync hub-owned weapons and statlines (Chaos, Imperium, … — assets a faction book inherits) to the Wahapedia exports.

gdc_codex_units.py skips inherited units, so their weapon profiles / statlines never got the 11e values. For every hub
asset this collects what Waha wants in each consuming book, writes the most common version onto the hub base, and gives
only the books that differ an override (removing overrides that now match the base). Fits the hub design: one shared
base, per-faction overrides.

  WAHA_DIR=… python3 waha_hub.py [--apply]
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from waha_lib import (RZ, UNITCLS, WA_ITEMS, datasheets_for, find_unit, gear, item, load_books, models, n, resolved, val,
                      weapon_trait, UNKNOWN)

APPLY = "--apply" in sys.argv
books = load_books()
CORE = "Warhammer_40k_11e"
by_name = {d["name"].strip(): s for s, d in books.items()}
deps = {s: [by_name[x["name"].strip()] for x in d.get("dependencies") or [] if x["name"].strip() in by_name] for s, d in books.items()}
HUBS = {h for s in deps for h in deps[s]} - {CORE}
log, review = [], []
STATS = ("M", "T", "Sv", "W", "Ld", "OC")


def render(v):
    """display value of a stat: "D3+1" is stored as format D3 / value 1; Melee and "-" are terms"""
    if not v:
        return None
    if v.get("format"):
        return v["format"] + (f"+{v['value']}" if v.get("value") else "")
    return str(v.get("value"))




def chain(stem):
    out = []
    for h in deps[stem]:
        out += [x for x in chain(h) if x not in out] + [h]
    return out


def base_book(stem, key):
    """first book in the dependency chain that defines key (where the base asset lives)"""
    return next((s for s in chain(stem) + [stem] if key in books[s]["rulebook"]["assetCatalog"]), None)


def kw_of(asset):
    return sorted(json.dumps(t, sort_keys=True) for t in (asset.get("assets") or {}).get("traits", []) if item(t) in WA_ITEMS)


def weapon_desire(g, kind):
    st = {}
    skill = "BS" if kind == "Ranged" else "WS"
    for s, col in (("Range", "range"), ("A", "A"), (skill, "BS_WS"), ("S", "S"), ("AP", "AP"), ("D", "D")):
        raw = g[col]
        if s == "Range" and raw == "Melee":
            st[s] = {"statType": "term", "value": "Melee"}; continue
        v = val(raw)
        if v is not None:
            st[s] = v
    kw = [t for t in (weapon_trait(k.strip()) for k in re.sub(r"<[^>]+>", "", g["description"] or "").split(",") if k.strip()) if t]
    return {"stats": st, "kw": sorted(json.dumps(t, sort_keys=True) for t in kw)}


def stat_desire(r):
    st = {}
    for s in STATS:
        raw = r[s].strip()
        if raw == "-":
            st[s] = {"statType": "term", "value": "-"} if s == "M" else {"value": 0}
            continue
        v = val(raw)
        if v is not None:
            st[s] = v
    return {"stats": st, "kw": None}


desire = defaultdict(dict)   # (hub, key) -> {book: desire}
for stem in sorted(books):
    if stem in HUBS and not datasheets_for(stem) or stem == CORE:
        continue
    res = resolved(books, stem)
    for d in datasheets_for(stem):
        uk = find_unit(res, d["name"])
        if not uk:
            continue
        blob = json.dumps(res[uk], ensure_ascii=False)
        mods = [m for m in dict.fromkeys(re.findall(r'"(Model§[^"]+)"', blob)) if m in res]
        found = set(re.findall(r'"((?:Ranged Weapon|Melee Weapon|Weapon)§[^"]+)"', blob + "".join(json.dumps(res[m], ensure_ascii=False) for m in mods)))
        todo = [k for k in found if k in res]
        while todo:
            k = todo.pop()
            for c in re.findall(r'"((?:Ranged Weapon|Melee Weapon|Weapon)§[^"]+)"', json.dumps(res[k].get("assets") or {}, ensure_ascii=False)):
                if c in res and c not in found:
                    found.add(c); todo.append(c)
        byname = {}
        for k in found:
            if k in res and (res[k].get("stats") or {}).get("A"):
                nm = k.split("§", 1)[1].split("—", 1)[-1]
                byname.setdefault((k.split("§")[0].split()[0], n(nm)), k)
        for g in gear[d["id"]]:
            if g["type"] not in ("Ranged", "Melee"):
                continue
            k = byname.get((g["type"], n(g["name"])))
            if not k:
                continue
            hb = base_book(stem, k)
            if hb and hb != stem and hb in HUBS:
                w_ = weapon_desire(g, g["type"])
                prev = desire[(hb, k)].get(stem)
                if prev and json.dumps(prev, sort_keys=True) != json.dumps(w_, sort_keys=True):
                    review.append(f"{stem}: {k} is shared by units wanting different values ({d['name']}) — first kept")
                    continue
                desire[(hb, k)][stem] = w_
        holders = [h for h in [uk] + mods if (res[h].get("stats") or {}).get("T")]
        rows = models[d["id"]]
        for r in rows:
            if len(rows) == 1:
                # one statline for the whole unit: models sharing the unit's common statline carry it; outliers
                # (jump-pack variants, a pilot model) and anything far from the row are left alone
                line = lambda h: tuple(render((res[h].get("stats") or {}).get(s)) for s in STATS)
                common = Counter(line(h) for h in holders).most_common(1)[0][0] if holders else None
                want = stat_desire(r)["stats"]
                near = lambda h: sum(render((res[h].get("stats") or {}).get(s)) != render(v) for s, v in want.items()) <= 2
                targets = [h for h in holders if line(h) == common and near(h)]
            else:
                hk = next((h for h in holders if n(h.split("§", 1)[1]) == n(r["name"]) or n((res[h].get("aspects") or {}).get("Label")) == n(r["name"])), None)
                targets = [hk] if hk else []
            for hk in targets:
                hb = base_book(stem, hk)
                if not (hb and hb != stem and hb in HUBS):
                    continue
                w_ = stat_desire(r)
                prev = desire[(hb, hk)].get(stem)
                if prev and json.dumps(prev, sort_keys=True) != json.dumps(w_, sort_keys=True):
                    review.append(f"{stem}: {hk} is shared by units wanting different values ({d['name']}) — first kept")
                    continue
                desire[(hb, hk)][stem] = w_

def canon(x):
    return json.dumps({"stats": {s: render(v) for s, v in x["stats"].items()}, "kw": x["kw"]}, sort_keys=True)
for (hub, k), per in sorted(desire.items()):
    votes = Counter(canon(v) for v in per.values())
    top_c = votes.most_common(1)[0][0]
    top = next(v for v in per.values() if canon(v) == top_c)
    hcat = books[hub]["rulebook"]["assetCatalog"]
    base = hcat[k]
    changed = []
    for s, v in top["stats"].items():
        if render((base.get("stats") or {}).get(s)) != render(v):
            changed.append(f"{s} {render((base.get('stats') or {}).get(s))}→{render(v)}")
            base.setdefault("stats", {})[s] = v
    if top["kw"] is not None and kw_of(base) != top["kw"]:
        keep = [t for t in (base.get("assets") or {}).get("traits", []) if item(t) not in WA_ITEMS]
        base.setdefault("assets", {})["traits"] = keep + [json.loads(t) for t in top["kw"]]
        changed.append("keywords")
    if changed:
        log.append(f"{hub}: {k}: {', '.join(changed)}  ({len(per)} books)")
    for stem, want in per.items():
        bcat = books[stem]["rulebook"]["assetCatalog"]
        if canon(want) == canon(top):
            ov = bcat.get(k)
            if ov:   # drop override fields the base now covers
                for s in list((ov.get("stats") or {})):
                    if s in top["stats"]:
                        ov["stats"].pop(s)
                if not ov.get("stats"):
                    ov.pop("stats", None)
                if top["kw"] is not None and ov.get("assets", {}).get("traits"):
                    ov["assets"]["traits"] = [t for t in ov["assets"]["traits"] if item(t) not in WA_ITEMS]
                    if not ov["assets"]["traits"]:
                        ov["assets"].pop("traits")
                    if not ov["assets"]:
                        ov.pop("assets")
                rem = ((ov.get("removed") or {}).get("assets") or {}).get("traits")
                if rem and top["kw"] is not None:
                    ov["removed"]["assets"]["traits"] = [x for x in rem if x not in WA_ITEMS]
                if not ov:
                    bcat.pop(k)
            continue
        ov = bcat.setdefault(k, {})
        diffs = []
        for s, v in want["stats"].items():
            if render(top["stats"].get(s)) != render(v):
                ov.setdefault("stats", {})[s] = v
                diffs.append(f"{s}→{render(v)}")
        if want["kw"] is not None and want["kw"] != top["kw"]:
            ov.setdefault("assets", {})["traits"] = [t for t in (ov.get("assets") or {}).get("traits", []) if item(t) not in WA_ITEMS] + [json.loads(t) for t in want["kw"]]
            drop = sorted({item(json.loads(t)) for t in top["kw"]} - {item(json.loads(t)) for t in want["kw"]})
            if drop:
                rm = ov.setdefault("removed", {}).setdefault("assets", {}).setdefault("traits", [])
                rm[:] = list(dict.fromkeys(rm + drop))
            diffs.append("keywords")
        if diffs:
            log.append(f"  {stem} override {k}: {', '.join(diffs)}")

print("\n".join(log))
if UNKNOWN:
    review.append(f"unknown weapon keywords: {sorted(UNKNOWN)}")
print("\n=== REVIEW ===\n" + "\n".join(review))
if APPLY:
    touched = {h for h, _ in desire} | {s for per in desire.values() for s in per}
    for s in sorted(touched):
        json.dump(books[s], open(RZ / f"{s}.rulebook", "w"), indent=2, ensure_ascii=False)
    print("written", sorted(touched))
