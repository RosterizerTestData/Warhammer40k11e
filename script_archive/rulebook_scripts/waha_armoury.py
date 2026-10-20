"""Armoury / generic weapons never vary — so a unit whose Wahapedia profile differs from the shared weapon it uses isn't
using that weapon: it gets its own "<Kind> Weapon§<Unit>—<Weapon>" (multi-profile weapons: parent + profiles copied),
and the unit's references are repointed in the book that defines the unit. Run waha_hub.py afterwards so hub copies
pick up per-faction overrides.

  WAHA_DIR=… python3 waha_armoury.py [--apply]
"""
import copy, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from waha_lib import (RZ, WA_ITEMS, datasheets_for, find_unit, gear, item, load_books, n, resolved, val, weapon_trait)

APPLY = "--apply" in sys.argv
books = load_books()
CORE = "Warhammer_40k_11e"
WEAPON = r'"((?:Ranged Weapon|Melee Weapon|Weapon)§[^"]+)"'
log, review, touched = [], [], set()


def shared(k):
    nm = k.split("§", 1)[1]
    return "—" not in nm or "Armoury" in nm.split("—", 1)[0]


def wname(k):
    nm = k.split("§", 1)[1]
    return nm.split("—", 1)[1] if "—" in nm else nm


def render(v):
    if not v:
        return None
    if v.get("format"):
        return v["format"] + (f"+{v['value']}" if v.get("value") else "")
    return str(v.get("value"))


def desire(g):
    kind = g["type"]
    st = {}
    for s, col in (("Range", "range"), ("A", "A"), ("BS" if kind == "Ranged" else "WS", "BS_WS"), ("S", "S"), ("AP", "AP"), ("D", "D")):
        if s == "Range" and g[col] == "Melee":
            continue
        v = val(g[col])
        if v is not None:
            st[s] = v
    kw = [t for t in (weapon_trait(k.strip()) for k in re.sub(r"<[^>]+>", "", g["description"] or "").split(",") if k.strip()) if t]
    return st, kw


def same(asset, st, kw):
    a_st = asset.get("stats") or {}
    if any(render(a_st.get(s)) != render(v) for s, v in st.items()):
        return False
    a_kw = sorted(json.dumps(t, sort_keys=True) for t in (asset.get("assets") or {}).get("traits", []) if item(t) in WA_ITEMS)
    return a_kw == sorted(json.dumps(t, sort_keys=True) for t in kw)


def base_book(stem, key, chain_):
    return next((s for s in chain_ + [stem] if key in books[s]["rulebook"]["assetCatalog"]), None)


def chain(stem):
    by_name = {d["name"].strip(): s for s, d in books.items()}
    out = []
    for dep in books[stem].get("dependencies") or []:
        s = by_name.get(dep["name"].strip())
        if s and s not in out:
            out += [x for x in chain(s) if x not in out] + [s]
    return out


def copy_into(home, key, U, profiles):
    """copy shared weapon `key` into book `home` as U's own; profiles: {child key or key: (st, kw)}"""
    cat = books[home]["rulebook"]["assetCatalog"]
    src = res_home[key]
    new = f"{key.split('§')[0]}§{U}—{wname(key)}"
    if new in cat:
        return new
    a = copy.deepcopy(src)
    a.setdefault("aspects", {}).setdefault("Label", wname(key))
    kids = [item(t) for t in (a.get("assets") or {}).get("traits", []) if item(t) and item(t).split("§")[0] in ("Ranged Weapon", "Melee Weapon", "Weapon")]
    if kids:
        tr = []
        for t in a["assets"]["traits"]:
            it = item(t)
            if it in kids:
                nk = copy_into(home, it, U, profiles)
                tr.append(nk if isinstance(t, str) else {**t, "item": nk})
            else:
                tr.append(t)
        a["assets"]["traits"] = tr
    if key in profiles:
        st, kw = profiles[key]
        a.setdefault("stats", {}).update(st)
        keep = [t for t in (a.get("assets") or {}).get("traits", []) if item(t) not in WA_ITEMS]
        a.setdefault("assets", {})["traits"] = keep + kw
        if not a["assets"]["traits"]:
            a["assets"].pop("traits")
    cat[new] = a
    log.append(f"  + {home}: {new}")
    return new


for stem in sorted(books):
    if stem == CORE or not datasheets_for(stem):
        continue
    ch = chain(stem)
    res = resolved(books, stem)
    for d in datasheets_for(stem):
        uk = find_unit(res, d["name"])
        if not uk:
            continue
        U = uk.split("§", 1)[1]
        blob = json.dumps(res[uk], ensure_ascii=False)
        holders = [uk] + [m for m in dict.fromkeys(re.findall(r'"(Model§[^"]+)"', blob)) if m in res]
        direct = {}   # shared weapon referenced directly by a holder -> holders
        for h in holders:
            for w in set(re.findall(WEAPON, json.dumps(res[h], ensure_ascii=False))):
                if w in res and shared(w):
                    direct.setdefault(w, set()).add(h)
        for top_key, hs in direct.items():
            kids = [item(t) for t in (res[top_key].get("assets") or {}).get("traits", []) if item(t) in res and shared(item(t))]
            cands = kids or [top_key]
            profiles, differs = {}, False
            for k in cands:
                g = next((g for g in gear[d["id"]] if g["type"] in ("Ranged", "Melee") and n(g["name"]) == n(wname(k)) and
                          (g["type"] + " Weapon") == k.split("§")[0]), None)
                if not g:
                    continue
                st, kw = desire(g)
                profiles[k] = (st, kw)
                if not same(res[k], st, kw):
                    differs = True
            if not differs:
                continue
            for h in hs:
                home = base_book(stem, h, ch)
                others = [s for s, b in books.items() if s != home and f'"{top_key}"' in json.dumps(b["rulebook"]["assetCatalog"].get(h, {}), ensure_ascii=False)]
                if others:
                    review.append(f"{U}: {h} keeps {top_key} — overridden with it in {others}")
                    continue
                res_home = resolved(books, home)
                new = copy_into(home, top_key, U, profiles)
                hc = books[home]["rulebook"]["assetCatalog"]
                hc[h] = json.loads(json.dumps(hc[h], ensure_ascii=False).replace(f'"{top_key}"', f'"{new}"'))
                touched.add(home)
                log.append(f"{home}: {h}: {top_key} → {new}  (Waha profile differs for {d['name']} in {stem})")
                res = resolved(books, stem)

print("\n".join(log))
print("\n=== REVIEW ===\n" + "\n".join(dict.fromkeys(review)))
if APPLY:
    for s in sorted(touched):
        json.dump(books[s], open(RZ / f"{s}.rulebook", "w"), indent=2, ensure_ascii=False)
    print("written", sorted(touched))
