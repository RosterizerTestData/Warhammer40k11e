"""Bring a faction book in line with the Wahapedia 11e exports (rules content). Run per book, in order:

  1. split   — a weapon named for *another* unit gets copied as "<Kind> Weapon§<Unit>—<Weapon>" and the unit's references
               repointed. Armoury and generic (ownerless) weapons stay shared — they never vary.
  2. export  — writes <out>/<Book>.json in game-datacards shape, then run
               `GDC_DIR=<out> gdc_codex_units.py <Book> <Book>.json --apply` for statlines, weapon profiles/keywords,
               datasheet-level invul/damaged and new units. Missing units are exported only if MFM lists them.
  3. sync    — what the builder doesn't do: per-model invulnerable saves, unit keywords (add), abilities (add missing;
               unlink stale ones from live units only — ability assets are never deleted, Legends units are untouched),
               Leader/Support attach lists.

  WAHA_DIR=… MFM_DIR=… PYTHONPATH=<pyyaml> python3 waha_sync.py <split|export|sync> <Book> [--out DIR] [--apply]

Trust order: MFM decides inclusion/points (mfm_points.py), Waha decides rules content. Prints a log + REVIEW list.
"""
import copy, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from waha_lib import (RZ, UNITCLS, abil_lib, abils, book_of_datasheet, chain, comp, datasheets_for, ds, fac, find_unit,
                      gear, html, is_legends, item, kws, leads, load_books, md, mfm_units, models, n, opts, resolved)

mode, stem = sys.argv[1], sys.argv[2]
APPLY = "--apply" in sys.argv
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
FACTIONS_TXT = sys.argv[sys.argv.index("--factions") + 1] if "--factions" in sys.argv else None

books = load_books()
book = books[stem]
cat = book["rulebook"]["assetCatalog"]
CORE = books["Warhammer_40k_11e"]["rulebook"]["assetCatalog"]
CORE_TOUCHED = []
res = resolved(books, stem)
log, review = [], []
WCLS = ("Ranged Weapon", "Melee Weapon", "Weapon")


def local(k):
    return k in cat


def unit_tree(uk):
    """the unit + its Model§ children (resolved), and the direct weapon references in them"""
    blob = json.dumps(res[uk], ensure_ascii=False)
    mods = [m for m in dict.fromkeys(re.findall(r'"(Model§[^"]+)"', blob)) if m in res]
    return mods


def weapon_refs(asset):
    return sorted(set(k for k in re.findall(r'"((?:Ranged Weapon|Melee Weapon|Weapon)§[^"]+)"', json.dumps(asset, ensure_ascii=False))))


def owner(k):
    nm = k.split("§", 1)[1]
    return nm.split("—", 1)[0] if "—" in nm else None


def wname(k):
    nm = k.split("§", 1)[1]
    return nm.split("—", 1)[1] if "—" in nm else nm


# ================================================================== 1. split
def split():
    units = [k for k in cat if k.split("§")[0] in UNITCLS]
    users = {}   # model key -> units using it (within this resolved book)
    for uk in [k for k in res if k.split("§")[0] in UNITCLS]:
        for m in unit_tree(uk):
            users.setdefault(m, set()).add(uk)
    for uk in units:
        U = uk.split("§", 1)[1]
        holders = [uk] + [m for m in unit_tree(uk) if local(m)]
        for hk in holders:
            if hk != uk and len(users.get(hk, ())) > 1:
                review.append(f"{U}: {hk} is shared by {len(users[hk])} units — its weapons were not split")
                continue
            for wk in weapon_refs(cat[hk]):
                if wk not in res or n(owner(wk) or "") == n(U):
                    continue
                if owner(wk) is None or "Armoury" in (owner(wk) or ""):
                    continue   # armoury / generic weapons never vary: the shared key is the indicator (user rule)
                if any(n(owner(wk) or "") == n(m.split("§", 1)[1]) for m in holders[1:]):
                    continue   # named for one of this unit's own models
                others = [s_ for s_, b_ in books.items() if s_ != stem and f'"{wk}"' in json.dumps(b_["rulebook"]["assetCatalog"].get(hk, {}), ensure_ascii=False)]
                if others:   # another book's override of this holder still names the shared weapon: repointing here would duplicate it
                    review.append(f"{U}: {hk} keeps {wk} — overridden with it in {others}")
                    continue
                new = copy_weapon(wk, U)
                s = json.dumps(cat[hk], ensure_ascii=False).replace(f'"{wk}"', f'"{new}"')
                cat[hk] = json.loads(s)
                log.append(f"{hk}: {wk} → {new}")


def copy_weapon(wk, U):
    cls = wk.split("§")[0]
    new = f"{cls}§{U}—{wname(wk)}"
    if new in res:
        return new
    a = copy.deepcopy(res[wk])
    a.setdefault("aspects", {}).setdefault("Label", wname(wk).split(" – ")[0] if " – " not in wname(wk) else wname(wk))
    # multi-profile parent: copy its profiles too
    if a.get("assets", {}).get("traits"):
        tr = []
        for t in a["assets"]["traits"]:
            it = item(t)
            if it and it.split("§")[0] in WCLS and it in res:
                nk = copy_weapon(it, U)
                tr.append(nk if isinstance(t, str) else {**t, "item": nk})
            else:
                tr.append(t)
        a["assets"]["traits"] = tr
    cat[new] = a
    res[new] = a
    log.append(f"  + {new} (copy of {wk})")
    return new


# ================================================================== 2. export
def export():
    OUT.mkdir(parents=True, exist_ok=True)
    have_mfm = mfm_units(stem, FACTIONS_TXT)
    out, legends = [], []
    for d in datasheets_for(stem):
        uk = find_unit(res, d["name"])
        if not uk:
            elsewhere = [s_ for s_, b_ in books.items() if s_ != stem and find_unit(b_["rulebook"]["assetCatalog"], d["name"])]
            if elsewhere:   # another faction's unit Waha lists here (allies, e.g. renegades for Knights/Daemons): not ours to build
                review.append(f"not built — exists in {elsewhere}: {d['name']}")
                continue
        if not uk and (is_legends(d) or n(d["name"]) not in have_mfm):
            legends.append(d["name"])   # exists but isn't current: built as Legends (user rule)
        out.append(to_gdc(d))
    if legends:
        json.dump(legends, open(OUT / f"{stem}.legends.json", "w"), ensure_ascii=False)
        log.append(f"to build as Legends: {legends}")
    json.dump({"name": fac.get(datasheets_for(stem)[0]["faction_id"]) if datasheets_for(stem) else stem,
               "datasheets": out, "enhancements": [], "stratagems": [], "detachments": [], "rules": {}},
              open(OUT / f"{stem}.json", "w"), ensure_ascii=False, indent=1)
    log.append(f"exported {len(out)} datasheets → {OUT / (stem + '.json')}")


E = lambda s: {"en": s}


def to_gdc(d):
    did = d["id"]
    rows = models[did]
    weapons = {"Ranged": [], "Melee": []}
    cur = None
    for g in sorted(gear[did], key=lambda r: int(r["line"] or 0)):
        if g["type"] not in weapons:
            continue
        pr = {"name": E(g["name"].replace(" - ", " – ")), "range": "Melee" if g["range"] == "Melee" else g["range"] + '"',
              "attacks": g["A"], "skill": (g["BS_WS"] + "+") if g["BS_WS"] not in ("", "-", "N/A") else "N/A",
              "strength": g["S"], "ap": g["AP"], "damage": g["D"],
              "keywords": [k.strip() for k in html(g["description"]).split(",") if k.strip()]}
        if g["line_in_wargear"] in ("", "1") or cur is None or cur[0] != g["type"]:
            cur = (g["type"], {"profiles": [pr]})
            weapons[g["type"]].append(cur[1])
        else:
            cur[1]["profiles"].append(pr)
    ab = {"core": [], "faction": [], "other": []}
    for a in abils[did]:
        lib = abil_lib.get(a["ability_id"], {})
        name = html(a["name"] or lib.get("name", ""))
        if not name:
            continue
        if a["type"] == "Core" and n(name) == "damaged" and a["parameter"]:   # 11e: Damaged is a Core ability, parameter = top of the range
            ab["damaged"] = {"range": E(f"1-{a['parameter']} WOUNDS REMAINING"), "description": E("")}
        elif a["type"] == "Core":
            ab["core"].append({"name": E(f"{name} {a['parameter']}".strip())})
        elif a["type"] == "Faction":
            ab["faction"].append({"name": E(name)})
        elif a["type"] in ("Datasheet", "Psychic", "Primarch"):
            ab["other"].append({"name": E(name), "description": E(a["description"] or lib.get("description", ""))})
    invs = {(r["inv_sv"], html(r["inv_sv_descr"])) for r in rows if r["inv_sv"] not in ("", "-")}
    if len(invs) == 1 and len([r for r in rows if r["inv_sv"] not in ("", "-")]) == len(rows):
        v, info = next(iter(invs))
        ab["invul"] = {"value": v.rstrip("+") + "+", **({"info": E(info)} if info else {})}
    if d.get("damaged_w") and "damaged" not in ab:
        ab["damaged"] = {"range": E(f"{d['damaged_w']} WOUNDS REMAINING"), "description": E(html(d.get("damaged_description")))}
    comp_txt = "\n".join(html(c["description"]) for c in comp[did])
    loadout = html(d.get("loadout"))
    attach = [{"type": "support" if d.get("is_support") == "true" else "leader", "target": ds[x]["name"]} for x in sorted(leads.get(did, ())) if x in ds]
    return {"name": E(d["name"]), "factions": [fac.get(d["faction_id"], "")],
            "keywords": [E(k) for k in dict.fromkeys(k["keyword"] for k in kws[did] if k["is_faction_keyword"] != "true" and not k["model"])],
            "stats": [{"name": E(r["name"]), "m": r["M"], "t": r["T"], "sv": r["Sv"], "w": r["W"], "ld": r["Ld"], "oc": r["OC"]} for r in rows],
            "rangedWeapons": weapons["Ranged"], "meleeWeapons": weapons["Melee"], "abilities": ab,
            "composition": [E(comp_txt + "\r" + loadout)], "attachesTo": attach,
            "wargearOptions": [{"instruction": E(html(o["description"]))} for o in opts[did]]}


# ================================================================== 3. sync
GENERIC = ("leader", "support", "invulnerable", "damaged", "transport")
generic = lambda b: b in GENERIC or b.startswith(("damaged", "invulnerable"))


_rule_blob = None
def rule_refs(key):
    """True when any rule (catalog or taxonomy, any book) mentions this ability — those stay linked"""
    global _rule_blob
    if _rule_blob is None:
        parts = []
        for b in books.values():
            for where in ("assetCatalog", "assetTaxonomy"):
                for a in b["rulebook"].get(where, {}).values():
                    if a.get("rules"):
                        parts.append(json.dumps(a["rules"], ensure_ascii=False))
        _rule_blob = "\n".join(parts)
    nm = key.split("§", 1)[1]
    return f'"{key}"' in _rule_blob or f'"{nm}"' in _rule_blob


def target(uk):
    """the asset to write for uk: the local one, or a new local override of an inherited one"""
    if uk not in cat:
        cat[uk] = {}
        log.append(f"  (override created for inherited {uk})")
    return cat[uk]


def ability_key(name, desc, U):
    m = re.match(r"(.+?)\s+(\d+\+?\"?|\d*D\d+(?:\+\d+)?)$", name)
    base, x = (m.group(1), m.group(2)) if m else (name, None)
    q = re.search(r"\s*\(([^)]*)\)\s*$", base)
    qual = None
    if q and re.search(r"once per|per army|per battle|per turn|aura|psychic|first battle round", q.group(1), re.I):
        base, qual = base[:q.start()].strip(), q.group(1)
    text = md(desc) if desc else None
    if text and qual:
        text = f"_{qual}_\n\n{text}"
    key = f"Ability§{base}"
    if key not in res and text is not None:
        cat[key] = res[key] = {"text": text}
        log.append(f"  + {key}")
    elif key in res and text and n(md(res[key].get("text", ""))) not in (n(text), "") and not x:
        key = f"Ability§{base} ({U})"
        if key not in res:
            cat[key] = res[key] = {"text": text}
            log.append(f"  + {key} (11e text differs from the shared ability)")
    return {"item": key, "stats": {"x": {"value": x}}} if x else key


SHARED_PARENTS = {"Chaos", "Imperium", "Space_Marines", "Warhammer_40k_11e"}   # hubs + the SM parent of chapter books


def ally_unit(uk):
    """unit defined only in a dependency that is another faction's own book (Tyranids for GSC, Harlequins for Drukhari…)"""
    if uk in cat:
        return False
    owners = [s for s in chain(books, stem) if uk in books[s]["rulebook"]["assetCatalog"]]
    return bool(owners) and not any(o in SHARED_PARENTS for o in owners)


def sync():
    for d in datasheets_for(stem):
        uk = find_unit(res, d["name"])
        if not uk:
            continue
        if ally_unit(uk):
            review.append(f"{uk}: inherited from another faction's book (ally) — synced there, not overridden here")
            continue
        U = uk.split("§", 1)[1]
        did = d["id"]
        a_res = res[uk]
        legends = "Legends" in ((a_res.get("keywords") or {}).get("Source") or [])
        # --- unit keywords (add)
        want = [k["keyword"] for k in kws[did] if k["is_faction_keyword"] != "true" and not k["model"]]
        have = {n(x) for x in ((a_res.get("keywords") or {}).get("Keywords") or [])}
        add = [k for k in want if n(k) not in have]
        if add:
            t = target(uk)
            t.setdefault("keywords", {})["Keywords"] = list(dict.fromkeys(((t.get("keywords") or {}).get("Keywords") or []) + add))
            log.append(f"{U}: + keywords {add}")
        # --- abilities
        traits = (a_res.get("assets") or {}).get("traits") or []
        rz_ab = {}
        for tr in traits:
            it = item(tr)
            if it and it.startswith("Ability§"):
                rz_ab[n(re.sub(r"\s*\(.*?\)$", "", it.split("§", 1)[1]))] = tr
        w_names = set()
        adds = []
        for ab in abils[did]:
            lib = abil_lib.get(ab["ability_id"], {})
            name = html(ab["name"] or lib.get("name", ""))
            if ab["type"] not in ("Datasheet", "Core", "Faction", "Psychic", "Primarch") or not name:
                continue
            full = f"{name} {ab['parameter']}".strip() if ab["type"] == "Core" else name
            base = n(re.sub(r"\s*\(.*?\)$", "", name))
            w_names.add(base)
            if generic(base):
                continue
            if base in rz_ab or any(base.startswith(x) or x.startswith(base) for x in rz_ab if len(x) > 4):
                cur = rz_ab.get(base)
                if ab["type"] == "Core" and ab["parameter"] and cur is not None:
                    curx = ((cur.get("stats") or {}).get("x") or {}).get("value") if isinstance(cur, dict) else None
                    if str(curx) != ab["parameter"]:
                        t = target(uk)
                        tl = t.setdefault("assets", {}).setdefault("traits", [])
                        it = item(cur)
                        tl[:] = [y for y in tl if item(y) != it]
                        tl.append({**(cur if isinstance(cur, dict) else {"item": it}), "stats": {**((cur.get("stats") if isinstance(cur, dict) else None) or {}), "x": {"value": ab["parameter"]}}})
                        log.append(f"{U}: {it.split('§', 1)[1]} {curx}→{ab['parameter']}")
                continue
            if ab["type"] in ("Core", "Faction"):
                bk = "Ability§" + re.sub(r"\s+(\d+\+?\"?|\d*D\d+(?:\+\d+)?)$", "", name)
                if bk not in res:   # a core / faction rule nobody has defined yet: create it from Waha's ability library
                    home = CORE if ab["type"] == "Core" else cat
                    home[bk] = res[bk] = {"keywords": {"Keywords": ["Model Ability"], "Tags": ["Core"]}, "text": md(lib.get("description", ""))} \
                        if ab["type"] == "Core" else {"text": md(lib.get("description", ""))}
                    CORE_TOUCHED.append(bk) if ab["type"] == "Core" else None
                    log.append(f"  + {bk}{' (core book)' if ab['type'] == 'Core' else ''}")
                px = ab["parameter"] if ab["type"] == "Core" else None
                adds.append({"item": bk, "stats": {"x": {"value": px}}} if px else bk)   # core/faction rules keep their own key
                continue
            desc = ab["description"] or lib.get("description", "")
            adds.append(ability_key(full, desc, U))
        if adds:
            t = target(uk)
            t.setdefault("assets", {}).setdefault("traits", [])
            t["assets"]["traits"] += adds
            log.append(f"{U}: + abilities {[item(x).split('§', 1)[1] for x in adds]}")
        if not legends:
            stale = [tr for b_, tr in rz_ab.items()
                     if b_ not in w_names and not generic(b_)
                     and not any(b_.startswith(y) or y.startswith(b_) for y in w_names if len(y) > 4)]
            kept = [item(x) for x in stale if rule_refs(item(x))]
            for k_ in kept:
                review.append(f"{U}: stale ability {k_.split('§', 1)[1]} kept — a rule references it")
            stale = [x for x in stale if item(x) not in kept]
            if stale:
                names = [item(x) for x in stale]
                t = target(uk)
                if uk in books[stem]["rulebook"]["assetCatalog"] and (t.get("assets") or {}).get("traits") and all(
                        any(item(y) == nm for y in t["assets"]["traits"]) for nm in names):
                    t["assets"]["traits"] = [y for y in t["assets"]["traits"] if item(y) not in names]
                else:
                    t.setdefault("removed", {}).setdefault("assets", {}).setdefault("traits", [])
                    t["removed"]["assets"]["traits"] = list(dict.fromkeys(t["removed"]["assets"]["traits"] + names))
                log.append(f"{U}: − stale abilities {[x.split('§', 1)[1] for x in names]}")
        # --- Leader / Support
        if leads.get(did):
            role = "Support" if d.get("is_support") == "true" else "Leader"
            targets = sorted(ds[x]["name"] for x in leads[did] if x in ds)
            want_t = {"item": f"Ability§{role}", "text": "This model can be attached to the following units:\n\n" + "\n\n".join(f"* {x}" for x in targets)}
            cur = next((tr for tr in traits if item(tr) in ("Ability§Leader", "Ability§Support")), None)
            if cur != want_t:
                was_local = uk in json.load(open(RZ / f"{stem}.rulebook"))["rulebook"]["assetCatalog"]
                t = target(uk)
                tl = t.setdefault("assets", {}).setdefault("traits", [])
                tl[:] = [y for y in tl if item(y) not in ("Ability§Leader", "Ability§Support")]
                tl.insert(0, want_t)
                if cur and item(cur) != want_t["item"] and not was_local:   # inherited Leader → Support: strip the inherited one
                    rm = t.setdefault("removed", {}).setdefault("assets", {}).setdefault("traits", [])
                    rm.append(item(cur))
                log.append(f"{U}: {role} → {targets}" + (f" (was {item(cur).split('§')[1]})" if cur and item(cur) != want_t['item'] else ""))
                for x in targets:
                    tk = find_unit(res, x)
                    if not tk:
                        review.append(f"{U}: attach target {x} not in book"); continue
                    allowed = (res[tk].get("allowed") or {}).get("items") or []
                    if uk not in allowed:
                        tt = target(tk)
                        tt.setdefault("allowed", {}).setdefault("items", [])
                        if uk not in tt["allowed"]["items"]:
                            tt["allowed"]["items"].append(uk)
        # --- "-" statlines (e.g. 11e aircraft M): the builder skips "-", Rz writes a term
        for r in models[did]:
            if r["M"].strip() == "-":
                hs = [uk] + unit_tree(uk)
                hk = next((h for h in hs if "M" in (res[h].get("stats") or {}) and (res[h]["stats"]["M"].get("value") != "-")), None)
                if hk:
                    target(hk).setdefault("stats", {})["M"] = {"statType": "term", "value": "-"}
                    log.append(f"{U} / {hk}: M → -")
        # --- per-model invulnerable saves
        rows = models[did]
        if len(rows) > 1 or (rows and unit_tree(uk)):
            holders = {m: res[m] for m in unit_tree(uk)}
            for r in rows:
                hk = next((h for h in holders if n(h.split("§", 1)[1]) == n(r["name"]) or n((holders[h].get("aspects") or {}).get("Label")) == n(r["name"])), None)
                if not hk:
                    continue
                inv = r["inv_sv"].rstrip("+") + "+" if r["inv_sv"] not in ("", "-") else None
                trs = (holders[hk].get("assets") or {}).get("traits") or []
                cur = next((t for t in trs if item(t) == "Ability§Invulnerable"), None)
                curv = (((cur.get("stats") or {}).get("x") or {}).get("value")) if isinstance(cur, dict) else None
                if inv and str(curv).rstrip("*") != inv:
                    t = target(hk)
                    tl = t.setdefault("assets", {}).setdefault("traits", [])
                    tl[:] = [y for y in tl if item(y) != "Ability§Invulnerable"]
                    new = {"item": "Ability§Invulnerable", "stats": {"x": {"value": inv}}}
                    if html(r["inv_sv_descr"]):
                        new["text"] = html(r["inv_sv_descr"]).strip("* ")
                    tl.append(new)
                    log.append(f"{U} / {hk}: invulnerable {curv}→{inv}")


def mark_legends():
    """after the builder: units built from the export's Legends list get Source Legends"""
    f = OUT / f"{stem}.legends.json"
    if not f.exists():
        return
    for name in json.load(open(f)):
        uk = find_unit(cat, name)
        if uk and "Legends" not in ((cat[uk].get("keywords") or {}).get("Source") or []):
            cat[uk].setdefault("keywords", {})["Source"] = ["Legends"]
            log.append(f"{uk}: Source → Legends")


def sync_attached_to():
    """reverse direction: units in this book accept every Waha leader/support that can join them (incl. leaders from the parent book)"""
    attachers = {}
    for lid, targets in leads.items():
        for t in targets:
            attachers.setdefault(t, set()).add(lid)
    for d in datasheets_for(stem):
        uk = find_unit(res, d["name"])
        if not uk:
            continue
        for lid in sorted(attachers.get(d["id"], ())):
            if lid not in ds:
                continue
            lk = find_unit(res, ds[lid]["name"])
            if not lk:
                continue
            if lk not in ((res[uk].get("allowed") or {}).get("items") or []):
                t = target(uk)
                items = t.setdefault("allowed", {}).setdefault("items", [])
                if lk not in items:
                    items.append(lk)
                    res[uk].setdefault("allowed", {}).setdefault("items", []).append(lk)
                    log.append(f"{uk.split('§', 1)[1]}: accepts {lk}")


{"split": split, "export": export, "legends": mark_legends, "sync": lambda: (sync(), sync_attached_to())}[mode]()
print("\n".join(log))
print("\n=== REVIEW ===\n" + "\n".join(dict.fromkeys(review)))
if APPLY and mode != "export":
    json.dump(book, open(RZ / f"{stem}.rulebook", "w"), indent=2, ensure_ascii=False)
    if CORE_TOUCHED:
        json.dump(books["Warhammer_40k_11e"], open(RZ / "Warhammer_40k_11e.rulebook", "w"), indent=2, ensure_ascii=False)
    print("written")
