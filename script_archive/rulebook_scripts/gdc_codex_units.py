"""Mechanical codex pass over a faction book's units, from game-datacards 11e data.

  GDC_DIR=… python3 gdc_codex_units.py <BookStem> <gdc.json> [--rename "Old=New" ...] [--apply]

- statlines (M/T/Sv/W/Ld/OC) on the unit or its Model§ children, matched to GDC stat rows by name/label
- existing weapon profiles: stats + weapon-keyword traits (custom, non-keyword traits are kept)
- new GDC weapon profiles: created as "<Kind> Weapon§<Unit>—<Name>"; multi-profile weapons get a parent
  ("Tags": Multi-weapon) whose traits are the profiles; an existing single-profile asset becomes that parent
- --rename: re-key a unit (and its weapons/references) to its codex name
- units in GDC but not in the book: built with stats, default weapons (composition text), abilities,
  keywords, Leader/Support attach lists. Wargear options are NOT wired (loadout pass).
Prints a REVIEW list. Points come from mfm_points.py afterwards.
"""
import os, re, sys, json, copy
from pathlib import Path

RZ = Path(__file__).resolve().parents[2]
GDC = Path(os.environ.get("GDC_DIR", "/tmp/gdc-11e-cache"))
args = sys.argv[1:]
APPLY = "--apply" in args
book, gfile = args[0], args[1]
renames = dict(a.split("=", 1) for i, a in enumerate(args) if i > 0 and args[i - 1] == "--rename")
HUB_FACTION = next((args[i + 1] for i, a in enumerate(args) if a == "--hub-faction"), None)  # shared-book mode

en = lambda x: (x.get("en", "") if isinstance(x, dict) else (x or "")).replace("\u2011", "-").replace("\u00a0", " ")
n = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("’", "'"))
UNITCLS = ("Unit", "Character", "Vehicle", "Infantry/Mounted")

d = json.load(open(RZ / f"{book}.rulebook"))
cat = d["rulebook"]["assetCatalog"]
g = json.load(open(GDC / gfile))
log, review = [], []
_books = {json.load(open(p))["name"].strip(): p for p in RZ.glob("*.rulebook")}
def _chain(doc):
    out = []
    for dep in doc.get("dependencies", []):
        dd = json.load(open(_books[dep["name"].strip()]))
        out += _chain(dd) + [dd["rulebook"]["assetCatalog"]]
    return out
DEP_CAT = {}
for c_ in _chain(d):
    DEP_CAT.update(c_)

# ---------------------------------------------------------------- renames
for old, new in renames.items():
    ok = next((f"{c}§{old}" for c in UNITCLS if f"{c}§{old}" in cat), None)
    if not ok:
        review.append(f"rename: {old} not found"); continue
    s = json.dumps(d, ensure_ascii=False)
    s = s.replace(f'"{ok}"', f'"{ok.split("§")[0]}§{new}"').replace(f"§{old}—", f"§{new}—").replace(f'"{old}"', f'"{new}"')
    d = json.loads(s); cat = d["rulebook"]["assetCatalog"]
    nk = f'{ok.split("§")[0]}§{new}'
    if f"Model§{old}" in cat and new in (cat[nk].get("stats") or {}):   # model-count stat shares the unit name: keep the model's name
        cat[nk]["stats"][old] = cat[nk]["stats"].pop(new)
    log.append(f"rename {ok} -> {new}")

# ---------------------------------------------------------------- value helpers
def val(s, kind=None):
    s = str(s).strip().replace('"', "").replace("″", "").replace("–", "-")
    if s.lower() == "melee":
        return {"statType": "term", "value": "Melee"}
    s = s.rstrip("+")
    if s in ("-", "N/A", ""):
        return None
    if re.fullmatch(r"-?\d+", s):
        return {"value": int(s)}
    m = re.fullmatch(r"(\d*D\d+)(?:\+(\d+))?", s.upper())
    if m:
        return {"format": m.group(1), "value": int(m.group(2) or 0)}
    return {"statType": "term", "value": s}

CORE_WA = {"assault", "blast", "devastating wounds", "extra attacks", "hazardous", "heavy", "ignores cover", "indirect fire",
           "lance", "lethal hits", "one shot", "pistol", "precision", "psychic", "torrent", "twin-linked", "close-quarters",
           "conversion"}
PARAM_WA = {"rapid fire": "Rapid Fire", "sustained hits": "Sustained Hits", "melta": "Melta", "cleave": "Cleave", "blast": "Blast"}
WA_ITEMS = {"Ability§" + x.title().replace("-Linked", "-linked").replace("-Quarters", "-quarters") for x in CORE_WA} | \
           {"Ability§" + v for v in PARAM_WA.values()} | {"Ability§Anti-"}

def weapon_trait(k):
    k = str(k).strip()
    cond = None
    if ":" in k:
        k, cond = [x.strip() for x in k.split(":", 1)]
    low = k.lower()
    m = re.match(r"anti-(.+?)\s+(\d\+)$", low)
    if m:
        return {"item": "Ability§Anti-", "stats": {"keyword": {"value": m.group(1)}, "x": {"value": m.group(2) + (f" ({cond})" if cond else "")}}}
    for p, item in PARAM_WA.items():
        m = re.match(rf"{p}\s+(\S+)$", low)
        if m:
            return {"item": f"Ability§{item}", "stats": {"x": {"value": m.group(1).upper() + (f" ({cond})" if cond else "")}}}
    if low in CORE_WA:
        item = "Ability§" + low.title().replace("-Linked", "-linked").replace("-Quarters", "-quarters")
        return {"item": item, "stats": {"x": {"value": f"({cond})"}}} if cond else item
    review.append(f"unknown weapon keyword '{k}'"); return None

def weapon_asset(kind, pr, base=None):
    a = copy.deepcopy(base) if base else {}
    a.setdefault("aspects", {})["Label"] = (base or {}).get("aspects", {}).get("Label") or en(pr["name"])
    st = a.setdefault("stats", {})
    skill = "BS" if kind == "Ranged" else "WS"
    for stat, gk in (("Range", "range"), ("A", "attacks"), (skill, "skill"), ("S", "strength"), ("AP", "ap"), ("D", "damage")):
        v = val(pr.get(gk))
        if v is not None:
            st[stat] = v
    if kind == "Melee":
        st["Range"] = {"statType": "term", "value": "Melee"}
    old = (a.get("assets") or {}).get("traits", [])
    traits = [t for t in old if (t if isinstance(t, str) else t.get("item")) not in WA_ITEMS]
    new_kw = [t for t in (weapon_trait(k) for k in pr.get("keywords") or []) if t]
    old_kw = [t for t in old if (t if isinstance(t, str) else t.get("item")) in WA_ITEMS]
    same = sorted(json.dumps(t, sort_keys=True) for t in new_kw) == sorted(json.dumps(t, sort_keys=True) for t in old_kw)
    traits = old if same else traits + new_kw   # identical keyword set: keep the book's order (no churn)
    if traits:
        a.setdefault("assets", {})["traits"] = traits
    elif "assets" in a:
        a["assets"].pop("traits", None)
    return a

def find(prefix, name):
    k = n(name)
    return next((x for x in cat if x.startswith(prefix) and n(x[len(prefix):]) == k), None)

def unit_key(name):
    k = next((x for x in cat if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]) == n(name)), None)
    if k:
        return k
    suff = [x for x in cat if x.split("§")[0] in UNITCLS and "(" in x and n(re.sub(r"\s*\(.*?\)\s*$", "", x.split("§", 1)[1])) == n(name)]
    return suff[0] if len(suff) == 1 else None

def inherited(name):
    k = next((x for x in DEP_CAT if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]) == n(name)), None)
    if k:
        return k
    suff = [x for x in DEP_CAT if x.split("§")[0] in UNITCLS and "(" in x and n(re.sub(r"\s*\(.*?\)\s*$", "", x.split("§", 1)[1])) == n(name)]
    return suff[0] if len(suff) == 1 else None

STATS = (("M", "m"), ("T", "t"), ("Sv", "sv"), ("W", "w"), ("Ld", "ld"), ("OC", "oc"))
def set_statline(asset, row):
    ch = []
    for s, gk in STATS:
        v = val(row.get(gk))
        if v is None:
            continue
        old = (asset.get("stats") or {}).get(s)
        if not old or old.get("value") != v.get("value") or old.get("format") != v.get("format"):
            asset.setdefault("stats", {})[s] = {**{k_: x for k_, x in (old or {}).items() if k_ not in ("value", "format", "statType")}, **v}
            ch.append(f"{s} {(old or {}).get('value')}→{v.get('value')}")
    return ch

SHARED_SEEN = {}
def referenced(U):
    """weapon keys referenced by the unit (and its models), by normalised weapon name"""
    uk = unit_key(U) or next((x for x in cat if x.split("§")[0] in UNITCLS and x.split("§", 1)[1] == U), None)
    if not uk:
        return {}
    blob = json.dumps(cat[uk], ensure_ascii=False)
    blob += "".join(json.dumps(cat.get(m, {}), ensure_ascii=False) for m in set(re.findall(r'"(Model§[^"]+)"', blob)))
    out = {}
    for k in set(re.findall(r'"((?:Ranged Weapon|Melee Weapon|Weapon)§[^"]+)"', blob)):
        if "—" in k:
            out.setdefault((k.split("§")[0], n(k.split("—", 1)[1])), k)
    return out

def sync_weapons(U, ds, created_ok=True, inherited_unit=False):
    out = []
    refs = referenced(U)
    def find_w(pre, name):
        kind = pre.split("§")[0]
        k = refs.get((kind, n(name))) or find(pre, name)   # the weapon the unit actually uses wins over a same-named stray
        if not k and inherited_unit:
            dk = next((x for x in DEP_CAT if x.startswith(pre) and n(x[len(pre):]) == n(name)), None)
            if dk:
                return "__inherited__"
        if k and not k.startswith(pre):          # shared weapon (e.g. an armoury): note conflicting codex profiles
            SHARED_SEEN.setdefault(k, set())
        return k
    for kind, key in (("Ranged", "rangedWeapons"), ("Melee", "meleeWeapons")):
        pre = f"{kind} Weapon§{U}—"
        for w in ds.get(key) or []:
            profs = w.get("profiles") or []
            if len(profs) > 1:
                base_name = en(profs[0]["name"]).split(" – ")[0].split(" - ")[0].strip()
                pk = find_w(pre, base_name) or find(f"Weapon§{U}—", base_name) or pre + base_name
                parent = cat.get(pk, {})
                prof_keys = []
                if inherited_unit and find_w(pre, base_name) == "__inherited__":
                    continue
                for pr in profs:
                    k = find_w(pre, en(pr["name"]))
                    if k == "__inherited__":   # profile lives in a parent book: never write the sentinel
                        continue
                    if not k:
                        mode = en(pr["name"]).split(" – ", 1)[-1].lower()
                        k = f"{pre}{pk.split('—', 1)[1]} – {mode}"
                        out.append(f"+ {k}")
                    cat[k] = weapon_asset(kind, pr, cat.get(k))
                    prof_keys.append(k)
                if not prof_keys:   # all profiles inherited: leave the parent alone
                    continue
                combat = {"Range", "A", "BS", "WS", "S", "AP", "D"}
                if combat & set(parent.get("stats") or {}):
                    out.append(f"~ {pk} became multi-profile parent")
                    parent["stats"] = {k_: v for k_, v in parent["stats"].items() if k_ not in combat}
                    if not parent["stats"]:
                        del parent["stats"]
                parent.setdefault("aspects", {}).setdefault("Label", pk.split("—", 1)[1])
                parent["assets"] = {"traits": prof_keys}
                parent.setdefault("keywords", {})["Tags"] = ["Multi-weapon"]
                if pk not in cat:
                    out.append(f"+ {pk} (parent)")
                cat[pk] = parent
            elif profs:
                pr = profs[0]
                k = find_w(pre, en(pr["name"]))
                if k == "__inherited__":
                    continue
                if not k:
                    k = pre + en(pr["name"])
                    out.append(f"+ {k}")
                before = json.dumps(cat.get(k), sort_keys=True)
                cat[k] = weapon_asset(kind, pr, cat.get(k))
                if k in SHARED_SEEN:
                    SHARED_SEEN[k].add(json.dumps({x: pr.get(x) for x in ("range", "attacks", "skill", "strength", "ap", "damage")}, sort_keys=True) + str(sorted(map(str, pr.get("keywords") or []))))
                if before != json.dumps(cat[k], sort_keys=True) and "+ " + k not in out:
                    out.append(f"~ {k}")
    return out

# ---------------------------------------------------------------- existing units
faction = en(g.get("name"))
missing = []
SYNC_LATER = []
for ds in g["datasheets"]:
    name = en(ds["name"]); uk = unit_key(name)
    if HUB_FACTION:
        if not uk or HUB_FACTION not in ((cat[uk].get("keywords") or {}).get("Faction") or []):
            continue
        ik = None
    else:
        ik = inherited(name)
    if ik and (not uk or uk == ik):
        for line in sync_weapons(ik.split("§", 1)[1], ds, inherited_unit=True):
            log.append(f"  {line} (option for inherited {ik})")
        review.append(f"{ik}: chapter datasheet variant of an inherited unit — only new weapon options created; wire in loadout pass")
        continue
    if not uk:
        missing.append(ds); continue
    U = uk.split("§", 1)[1]; a = cat[uk]
    rows = {n(en(r.get("name"))): r for r in ds.get("stats") or []}
    kids = re.findall(r'"(Model§[^"]+)"', json.dumps(a, ensure_ascii=False))
    holders = [(x, cat[x]) for x in dict.fromkeys(kids) if x in cat] or [(uk, a)]
    pairs, used = {}, set()
    for hk, h in holders:
        label = (h.get("aspects") or {}).get("Label") or hk.split("§", 1)[1]
        rn = next((r for r in (n(label), n(hk.split("§", 1)[1]), n(name) if hk == uk else None) if r in rows), None)
        if rn:
            pairs[hk] = rn; used.add(rn)
    stem = lambda x: re.sub(r"(ies|y|z|s)$", "", x)
    for hk, h in holders:
        if hk in pairs:
            continue
        lab = n((h.get("aspects") or {}).get("Label") or hk.split("§", 1)[1])
        fz = [r for r in rows if r not in used and (stem(r) == stem(lab) or (len(r) >= 5 and (r in lab or lab in r)))]
        if len(fz) == 1:
            pairs[hk] = fz[0]; used.add(fz[0])
    left_h = [hk for hk, _ in holders if hk not in pairs]
    left_r = [r for r in rows if r not in used]
    def close(hk, rn):   # fallback only for variants of the same model (<=2 stats differ)
        h, r = cat[hk], rows[rn]
        diff = sum(1 for s_, gk in STATS if val(r.get(gk)) and (h.get("stats") or {}).get(s_)
                   and str((h["stats"][s_]).get("value")) != str(val(r.get(gk)).get("value")))
        return diff <= 2
    if len(rows) == 1:
        for hk in left_h:
            if close(hk, next(iter(rows))):
                pairs[hk] = next(iter(rows))
    elif len(left_h) == len(left_r) == 1 and close(left_h[0], left_r[0]):
        pairs[left_h[0]] = left_r[0]
    for hk, h in holders:
        if hk not in pairs:
            review.append(f"{U}: no GDC stat row for {hk} (rows {list(rows)})"); continue
        row = rows[pairs[hk]]
        ch = set_statline(h, row)
        if ch:
            log.append(f"~ {hk}: {', '.join(ch)}")
    for line in sync_weapons(U, ds):
        log.append(f"  {line}")
    SYNC_LATER.append((uk, ds))

# ---------------------------------------------------------------- invul / damaged / primarch
DAMAGED_STD = "While this model has {r}, each time this model makes an attack, subtract 1 from the Hit roll."
def sync_profile(uk, ds):
    """invulnerable save, damaged profile and primarch abilities on the unit asset: add or update, never remove"""
    a = cat[uk]; tr = a.setdefault("assets", {}).setdefault("traits", [])
    ab = ds.get("abilities") or {}
    item = lambda t: t.get("item") if isinstance(t, dict) else t
    kids = [cat[m] for m in dict.fromkeys(re.findall(r'"(Model§[^"]+)"', json.dumps(a, ensure_ascii=False))) if m in cat]
    def on_models(key):
        return [t for m in kids for t in (m.get("assets") or {}).get("traits") or [] if item(t) == key]
    inv = (ab.get("invul") or {}).get("value")
    have = [t for t in tr if item(t) == "Ability§Invulnerable"]
    if inv:
        inv = inv.strip()
        info = md(en((ab.get("invul") or {}).get("info"))).strip("*_ ")
        if have:
            t = have[0]
            if isinstance(t, str):
                tr[tr.index(t)] = t = {"item": t, "stats": {}}
            old = ((t.get("stats") or {}).get("x") or {}).get("value")
            if str(old).rstrip("*") != inv:   # "5+*" = footnoted 5+, same save
                t.setdefault("stats", {})["x"] = {"value": inv}
                log.append(f"~ {uk}: invulnerable {old}→{inv}")
            if info and not t.get("text"):
                t["text"] = info
                log.append(f"~ {uk}: invulnerable note '{info}'")
        elif on_models("Ability§Invulnerable"):
            olds = {((t.get("stats") or {}).get("x") or {}).get("value") for t in on_models("Ability§Invulnerable") if isinstance(t, dict)}
            if inv not in olds:   # GDC carries one value per datasheet; per-model saves (e.g. Wardens of Ultramar) legitimately differ
                review.append(f"{uk}: model-level invulnerable {sorted(map(str, olds))} vs GDC {inv}{' (' + info + ')' if info else ''}")
        else:
            t = {"item": "Ability§Invulnerable", "stats": {"x": {"value": inv}}}
            if info:
                t["text"] = info
            tr.append(t)
            log.append(f"~ {uk}: + invulnerable {inv}{' (' + info + ')' if info else ''}")
    elif have or on_models("Ability§Invulnerable"):
        review.append(f"{uk}: has an invulnerable save but GDC lists none")
    dm = ab.get("damaged") or {}
    dash = lambda x: re.sub(r"[\u2010\u2011\u2012\u2013]", "-", x)
    desc = dash(md(en(dm.get("description"))))
    rng = dash(en(dm.get("range"))).strip().lower()
    m_ = re.search(r"has (\d+-\d+ wounds remaining)", desc)
    if m_:
        rng = m_.group(1)   # description wins over a malformed range field (e.g. "1:-4")
    desc = desc.replace("each time it makes", "each time this model makes")
    if rng:
        bespoke = [item(t) for t in tr if item(t) and item(t).startswith("Ability§Damaged") and item(t) != "Ability§Damaged:"]
        t = next((t for t in tr if item(t) == "Ability§Damaged:"), None)
        if bespoke and t is None:   # e.g. "Damaged: Baneblade Variant" — a book-specific damaged ability already covers it
            rng = ""
    if rng:
        want = {"item": "Ability§Damaged:", "stats": {"x": {"value": rng}}}
        if desc and desc != DAMAGED_STD.format(r=rng):
            want["text"] = desc
        elif t is not None and isinstance(t, dict) and t.get("text"):
            want["text"] = t["text"]   # source gives only the range: keep the book's custom damaged text (e.g. Knights' OC loss)
        if t is None:
            tr.append(want); log.append(f"~ {uk}: + damaged {rng}")
        elif t != want:
            tr[tr.index(t)] = want; log.append(f"~ {uk}: damaged → {rng}{' (custom text)' if 'text' in want else ''}")
    for grp in ab.get("primarch") or []:
        gname = en(grp.get("name"))
        body = "\n\n".join(f"**{en(x.get('name'))}:** {md(en(x.get('description')))}" for x in grp.get("abilities") or [])
        base = re.sub(r"\s*\(.*\)\s*$", "", gname)
        if not any(item(t) and item(t).split("§", 1)[-1].startswith(base) for t in tr):
            tr.append(ability_trait(gname, body, uk.split("§", 1)[1]))
            log.append(f"~ {uk}: + primarch ability {gname}")

# ---------------------------------------------------------------- new units
def ability_trait(name, desc=None, unit=None):
    m = re.match(r"(.+?)\s+(\d+\+?\"?|\d*D\d+(?:\+\d+)?)$", name)
    base, x = (m.group(1), m.group(2)) if m else (name, None)
    if base.lower() == "damaged":
        return {"item": "Ability§Damaged:", "stats": {"x": {"value": f"1-{x} wounds remaining"}}}
    q = re.search(r"\s*\((Once per [^)]*|[^)]*per army[^)]*)\)\s*$", base)
    if q:
        base = base[:q.start()].strip()
        desc = f"_{q.group(1)}_\n\n{desc}" if desc else desc
    key = f"Ability§{base}"
    if key in DEP_CAT and key not in cat:
        return {"item": key, "stats": {"x": {"value": x}}} if x else key
    if key not in cat and desc is not None:
        cat[key] = {"text": desc}
        log.append(f"+ {key}")
    elif desc is not None and md(cat[key].get("text", "")) != md(desc) and unit:
        key = f"Ability§{base} ({unit})"
        cat[key] = {"text": desc}
        log.append(f"+ {key} (name clash with existing ability text)")
    return {"item": key, "stats": {"x": {"value": x}}} if x else key

def md(t):
    t = (t or "").replace("\r", "\n")
    t = re.sub(r"<k>(.*?)</k>", lambda m: "**" + m.group(1).upper() + "**", t)
    t = re.sub(r"<b>(.*?)</b>", r"**\1**", t)
    t = re.sub(r"<li>", "\n- ", t)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "", t)).strip()

attach = []
if HUB_FACTION:
    missing = []
for ds in missing:
    name = en(ds["name"])
    kws = [en(k) for k in ds.get("keywords") or []]
    have = {x.split("§")[0] for x in cat}
    cls = ("Character" if "Character" in kws else
           "Vehicle" if "Vehicle" in kws and "Vehicle" in have else
           "Infantry/Mounted" if ({"Infantry", "Mounted", "Beast", "Swarm"} & set(kws)) and "Infantry/Mounted" in have else "Unit")
    uk = f"{cls}§{name}"
    a = {"keywords": {"Faction": [en(f) for f in ds.get("factions") or [faction]], "Source": ["11th Edition Codex"],
                      "Keywords": kws + ([name] if name not in kws else []),
                      "Category": ["Epic Hero"] if "Epic Hero" in kws else ["Character"] if cls == "Character" else
                      ["Vehicle"] if "Vehicle" in kws else ["Monster"] if "Monster" in kws else ["Mounted"] if "Mounted" in kws else ["Infantry"]},
         "stats": {}, "assets": {"traits": []}}
    if cls != "Character":
        a["aspects"] = {"Type": "game piece"}
    rows = ds.get("stats") or []
    if rows:
        set_statline(a, rows[0])
    if len(rows) > 1:
        review.append(f"{uk}: {len(rows)} stat rows — model split needs the loadout pass")
    ab = ds.get("abilities") or {}
    tr = a["assets"]["traits"]
    for c in ab.get("core") or []:
        cn = en(c.get("name"))
        if cn.lower() in ("leader", "support"):
            continue
        tr.append(ability_trait(cn))
    for f in ab.get("faction") or []:
        tr.append(f"Ability§{en(f.get('name'))}")
    for o in ab.get("other") or []:
        tr.append(ability_trait(en(o.get("name")), md(en(o.get("description"))), name))
    for sp in ab.get("special") or []:
        if en(sp.get("name")).upper() == "TRANSPORT":
            tr.append({"item": "Ability§Transport", "text": md(en(sp.get("description")))})
        else:
            tr.append(ability_trait(en(sp.get("name")).title(), md(en(sp.get("description"))), name))
    for at in ds.get("attachesTo") or []:
        role = "Leader" if at.get("type") == "leader" else "Support"
        attach.append((uk, role, at.get("target")))
    cat[uk] = a
    for line in sync_weapons(name, ds):
        log.append(f"  {line}")
    comp = en((ds.get("composition") or [{}])[0])
    eq = re.search(r"equipped with:\s*(.*?)(?:\.|$)", comp.replace("\r", " "))
    for item in (eq.group(1).split(";") if eq else []):
        wn = re.sub(r"^\s*\d+\s+", "", item).strip()
        k = find(f"Ranged Weapon§{name}—", wn) or find(f"Melee Weapon§{name}—", wn)
        if k:
            tr.append(k)
        else:
            review.append(f"{uk}: default wargear '{wn}' has no weapon asset")
    SYNC_LATER.append((uk, ds))
    if ds.get("wargearOptions"):
        review.append(f"{uk}: wargear options not wired ({len(ds['wargearOptions'])} groups)")
    log.append(f"+ {uk}")

by_role = {}
for uk, role, target in attach:
    by_role.setdefault((uk, role), []).append(target)
for (uk, role), targets in by_role.items():
    cat[uk]["assets"]["traits"] = [t for t in cat[uk]["assets"]["traits"] if t != f"Ability§{role}"]
    cat[uk]["assets"]["traits"].insert(0, {"item": f"Ability§{role}", "text": "This model can be attached to the following units:\n\n" + "\n\n".join(f"* {t}" for t in targets)})
    for t in targets:
        tk = unit_key(t)
        if not tk:
            review.append(f"{uk}: attach target {t} not in book"); continue
        items = cat[tk].setdefault("allowed", {}).setdefault("items", [])
        if uk not in items:
            items.append(uk)

for uk, ds in SYNC_LATER:
    sync_profile(uk, ds)

for k, profs in SHARED_SEEN.items():
    if len(profs) > 1:
        review.append(f"shared weapon {k}: codex datasheets disagree ({len(profs)} profiles) — last one written")
print("\n".join(log))
print("\n=== REVIEW ===\n" + "\n".join(dict.fromkeys(review)))
if APPLY:
    open(RZ / f"{book}.rulebook", "w").write(json.dumps(d, indent=2, ensure_ascii=False))
    print("written")
