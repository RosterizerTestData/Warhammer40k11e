"""Read-only audit of the 11e books against the Wahapedia CSV exports.

Compares each book's *resolved* catalog (dependency chain merged, as a roster sees it) with Waha:
datasheet presence, per-model statlines + invulnerable saves, weapon profiles + weapon keywords,
unit keywords, abilities, Leader attach lists, detachments / enhancements / stratagems.
Points and DP are MFM's job (mfm_points.py) and are not compared.

  WAHA_DIR=<dir with the CSVs> python3 waha_audit.py [Book ...] > report.txt
"""
import csv, json, os, re, sys
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rzmerge import merge_entry

RZ = Path(__file__).resolve().parents[2]
W = Path(os.environ["WAHA_DIR"])
UNITCLS = ("Unit", "Character", "Vehicle", "Infantry/Mounted")


def rd(name):
    return list(csv.DictReader(open(W / f"{name}.csv", encoding="utf-8-sig"), delimiter="|"))


def n(s):
    s = (s or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


# ------------------------------------------------------------------ Waha
fac = {r["id"]: r["name"] for r in rd("Factions")}
src = {r["id"]: r for r in rd("Source")}
ds = {r["id"]: r for r in rd("Datasheets")}
models = defaultdict(list); [models[r["datasheet_id"]].append(r) for r in rd("Datasheets_models")]
gear = defaultdict(list); [gear[r["datasheet_id"]].append(r) for r in rd("Datasheets_wargear")]
kws = defaultdict(list); [kws[r["datasheet_id"]].append(r) for r in rd("Datasheets_keywords")]
abil_lib = {r["id"]: r for r in rd("Abilities")}
abils = defaultdict(list); [abils[r["datasheet_id"]].append(r) for r in rd("Datasheets_abilities")]
leads = defaultdict(set); [leads[r["leader_id"]].add(r["attached_id"]) for r in rd("Datasheets_leader")]
dets = [r for r in rd("Detachments") if r["type"] != "Boarding Actions"]
det_by_id = {r["id"]: r for r in dets}
enh = [r for r in rd("Enhancements") if r["detachment_id"] in det_by_id]
strat = [r for r in rd("Stratagems") if r["detachment_id"] in det_by_id]

CHAPTERS = {"Black Templars": "SM_Black_Templars", "Blood Angels": "SM_Blood_Angels", "Dark Angels": "SM_Dark_Angels",
            "Space Wolves": "SM_Space_Wolves", "Deathwatch": "SM_Deathwatch"}
FACTION_BOOK = {"Imperial Agents": "Imperial_Agents", "Astra Militarum": "Astra_Militarum", "Genestealer Cults": "Genestealer_Cults",
                "Necrons": "Necrons", "Aeldari": "Aeldari", "Adeptus Titanicus": "Adeptus_Titanicus", "Orks": "Orks",
                "Grey Knights": "Grey_Knights", "T’au Empire": "T'au_Empire", "Leagues of Votann": "Leagues_of_Votann",
                "Adeptus Mechanicus": "Adeptus_Mechanicus", "Thousand Sons": "Thousand_Sons", "Death Guard": "Death_Guard",
                "Emperor’s Children": "Emperor's_Children", "World Eaters": "World_Eaters", "Chaos Knights": "Chaos_Knights",
                "Chaos Daemons": "Chaos_Daemons", "Imperial Knights": "Imperial_Knights", "Space Marines": "Space_Marines",
                "Tyranids": "Tyranids", "Adeptus Custodes": "Adeptus_Custodes", "Adepta Sororitas": "Adepta_Sororitas",
                "Chaos Space Marines": "Chaos_Space_Marines", "Drukhari": "Drukhari"}


def book_of_datasheet(d):
    f = fac.get(d["faction_id"], "")
    if f == "Space Marines":
        for kw in kws[d["id"]]:
            if kw["keyword"] in CHAPTERS:
                return CHAPTERS[kw["keyword"]]
        # chapter-only datasheets carry the chapter in the link
        for ch, b in CHAPTERS.items():
            if n(ch) in n(d.get("link", "")):
                return b
    return FACTION_BOOK.get(f)


def book_of_detachment(r):
    f = fac.get(r["faction_id"], "")
    if f == "Space Marines" and r.get("type") in CHAPTERS:
        return CHAPTERS[r["type"]]
    return FACTION_BOOK.get(f)


# ------------------------------------------------------------------ Rz (resolved)
raw = {}
for p in RZ.glob("*.rulebook"):
    d = json.load(open(p))
    raw[p.stem] = d
by_name = {d["name"].strip(): stem for stem, d in raw.items()}


def chain(stem, seen=None):
    out = []
    for dep in raw[stem].get("dependencies") or []:
        s = by_name.get(dep["name"].strip())
        if s and s not in out:
            out += [x for x in chain(s) if x not in out] + [s]
    return out


_res = {}
def resolved(stem):
    if stem not in _res:
        cat = {}
        for s in chain(stem) + [stem]:
            for k, v in raw[s]["rulebook"]["assetCatalog"].items():
                cat[k] = merge_entry(cat[k], v) if k in cat else v
        _res[stem] = cat
    return _res[stem]


def find_unit(cat, name, local=None):
    """local: the book's own catalog — its suffixed variant ("Venerable Dreadnought (SW)") beats an inherited exact match"""
    k = next((x for x in cat if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]) == n(name)), None)
    if k and local is not None and k not in local:
        own = [x for x in local if x.split("§")[0] in UNITCLS and "(" in x and n(re.sub(r"\s*\(.*?\)\s*$", "", x.split("§", 1)[1])) == n(name)]
        if len(own) == 1:
            return own[0]
    if k:
        return k
    cands = [x for x in cat if x.split("§")[0] in UNITCLS and n(re.sub(r"\s*\(.*?\)\s*$", "", x.split("§", 1)[1])) == n(name)]
    if len(cands) == 1:
        return cands[0]
    plural = [x for x in cat if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]).rstrip("s") == n(name).rstrip("s")]
    return plural[0] if len(plural) == 1 else None


def item(t):
    return t.get("item") if isinstance(t, dict) else t


def refs(cat, key, pattern):
    """keys matching pattern referenced anywhere in asset `key` (and its Model§ children)"""
    blob = json.dumps(cat.get(key, {}), ensure_ascii=False)
    mods = [m for m in dict.fromkeys(re.findall(r'"(Model§[^"]+)"', blob)) if m in cat]
    blob += "".join(json.dumps(cat[m], ensure_ascii=False) for m in mods)
    found = set(k for k in re.findall(r'"(' + pattern + r'[^"]+)"', blob) if k in cat)
    todo = list(found)
    while todo:   # multi-profile parents reference their profiles
        k = todo.pop()
        for c in re.findall(r'"(' + pattern + r'[^"]+)"', json.dumps(cat[k].get("assets") or {}, ensure_ascii=False)):
            if c in cat and c not in found:
                found.add(c); todo.append(c)
    return mods, sorted(found)


def sval(a, s):
    v = ((a.get("stats") or {}).get(s) or {})
    if v.get("format"):   # dice: "D3+1" is stored as format "D3", value 1
        return v["format"] + (f"+{v['value']}" if v.get("value") else "")
    return v.get("value")


def num(x):
    m = re.search(r"-?\d+", str(x or ""))
    return int(m.group()) if m else None


def inv_of(a):
    for t in (a.get("assets") or {}).get("traits") or []:
        if item(t) == "Ability§Invulnerable":
            return num((((t.get("stats") or {}).get("x") or {}).get("value")) if isinstance(t, dict) else None)
    return None


def wk_tokens(desc):
    out = set()
    for tok in re.split(r",\s*", html(desc)):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"anti-(.+?)\s+(\d\+?)$", tok, re.I)
        if m:
            for part in m.group(1).split("/"):
                out.add(n(f"anti{part}{m.group(2)}"))
            continue
        out.add(n(re.sub(r":.*$", "", tok)) if ":" in tok else n(tok))
    return out


def rz_wk_tokens(a):
    out = set()
    for t in (a.get("assets") or {}).get("traits") or []:
        it = item(t) or ""
        if not it.startswith("Ability§"):
            continue
        base = it.split("§", 1)[1]
        st = (t.get("stats") or {}) if isinstance(t, dict) else {}
        x = ((st.get("x") or {}).get("value")) or ""
        kw = ((st.get("keyword") or {}).get("value")) or ""
        if base.rstrip("-") == "Anti":
            for part in str(kw).split("/"):
                out.add(n(f"anti{part}{x}"))
        elif x and not str(x).startswith("("):
            out.add(n(f"{base}{re.sub(r'\s*\(.*\)$', '', str(x))}"))
        else:
            out.add(n(base))
    return out


# ------------------------------------------------------------------ audit
only = set(sys.argv[1:])
R = defaultdict(lambda: defaultdict(list))   # book -> category -> lines

for did, d in ds.items():
    if d.get("virtual") == "true":
        continue
    stem = book_of_datasheet(d)
    if not stem or stem not in raw or (only and stem not in only):
        continue
    legends = "Legends" in src.get(d["source_id"], {}).get("name", "")
    cat = resolved(stem)
    uk = find_unit(cat, d["name"], raw[stem]["rulebook"]["assetCatalog"])
    if not uk:
        if any(find_unit(raw[s]["rulebook"]["assetCatalog"], d["name"]) for s in raw if s != stem):
            continue   # another faction's unit fielded as an ally (e.g. renegades for Knights/Daemons)
        R[stem]["unit missing" + (" (Legends)" if legends else "")].append(d["name"])
        continue
    a = cat[uk]
    mods, weps = refs(cat, uk, r"(?:Ranged Weapon|Melee Weapon|Weapon)§")
    holders = {uk: a, **{m: cat[m] for m in mods}}
    # statlines + invul
    rows = models[did]
    for r in rows:
        statful = [h for h in holders if sval(holders[h], "T") is not None]
        if len(rows) == 1 and len(statful) == 1:
            hk = statful[0]
        else:
            hk = next((h for h in statful if n(h.split("§", 1)[1]) == n(r["name"]) or n((holders[h].get("aspects") or {}).get("Label")) == n(r["name"])), None)
            if not hk:
                hk = next((h for h in statful if n(r["name"]) in n(h.split("§", 1)[1]) or n(h.split("§", 1)[1]) in n(r["name"])), None)
            if not hk and len(rows) == 1 and statful:
                hk = statful[0]
        if not hk:
            R[stem]["model row unmatched"].append(f"{d['name']}: {r['name']}")
            continue
        h = holders[hk]
        diffs = []
        for s, wcol in (("M", "M"), ("T", "T"), ("Sv", "Sv"), ("W", "W"), ("Ld", "Ld"), ("OC", "OC")):
            wv, rv = num(r[wcol]), num(sval(h, s))
            if r[wcol].strip() == "-":
                wv = 0
            if wv is not None and wv != (rv or 0):
                diffs.append(f"{s} {rv}→{wv}")
        if diffs:
            R[stem]["statline"].append(f"{d['name']} / {r['name']}: {', '.join(diffs)}  [{hk}]")
        winv = num(r["inv_sv"]) if r["inv_sv"] not in ("", "-") else None
        rinv = inv_of(h) or (inv_of(a) if hk != uk else None)
        if winv != rinv:
            R[stem]["invulnerable"].append(f"{d['name']} / {r['name']}: {rinv}→{winv}{' (' + html(r['inv_sv_descr']) + ')' if r['inv_sv_descr'] else ''}")
    # weapons
    rz_w = {}
    for k in weps:
        nm = k.split("—", 1)[1] if "—" in k else k.split("§", 1)[1]
        rz_w.setdefault((k.split("§")[0].split()[0], n(nm)), k)
    for g in gear[did]:
        if g["type"] not in ("Ranged", "Melee"):
            continue
        wn = g["name"]
        key = (g["type"], n(wn))
        k = rz_w.get(key)
        if not k:
            k = next((v for (t, nm), v in rz_w.items() if t == g["type"] and nm.replace("standard", "") == n(wn).replace("standard", "") and sval(cat[v], "A") is not None), None)
        if not k:
            pre = f"{g['type']} Weapon§"
            loose = [x for x in cat if x.startswith(pre) and "—" in x and n(x.split("—", 1)[1]) == n(wn)
                     and n(x.split("§", 1)[1].split("—")[0]) == n(uk.split("§", 1)[1])]
            R[stem]["weapon unwired" if loose else "weapon absent"].append(f"{d['name']}: {wn}")
            continue
        if sval(cat[k], "A") is None and f"{k} – standard" in cat:   # parent: the base profile is its "– standard" sub-weapon
            k = f"{k} – standard"
        wa = cat[k]
        diffs = []
        for s, col in (("Range", "range"), ("A", "A"), ("S", "S"), ("AP", "AP"), ("D", "D")):
            wv = g[col]
            if s == "Range" and wv == "Melee":
                continue
            rv = sval(wa, s)
            if (num(wv) if re.fullmatch(r"-?\d+", wv or "") else n(wv)) != (rv if isinstance(rv, int) else (num(rv) if re.fullmatch(r"-?\d+", str(rv)) else n(str(rv)))):
                diffs.append(f"{s} {rv}→{wv}")
        sk = "BS" if g["type"] == "Ranged" else "WS"
        wv = num(g["BS_WS"]) if g["BS_WS"] not in ("-", "") else None
        rv = num(sval(wa, sk))
        if wv != rv and not (wv is None and rv in (None, 0)):
            diffs.append(f"{sk} {rv}→{wv}")
        if diffs:
            R[stem]["weapon profile"].append(f"{d['name']}: {wn}: {', '.join(diffs)}  [{k}]")
        wt, rt = wk_tokens(g.get("description")), rz_wk_tokens(wa)
        if wt - rt:
            R[stem]["weapon keywords"].append(f"{d['name']}: {wn}: missing {sorted(wt - rt)}" + (f", extra {sorted(rt - wt)}" if rt - wt else "") + f"  [{k}]")
    # keywords
    wkw = {n(k["keyword"]) for k in kws[did] if k["is_faction_keyword"] != "true" and not k["model"]}
    rkw = {n(x) for x in ((a.get("keywords") or {}).get("Keywords") or [])}
    if wkw - rkw:
        R[stem]["unit keywords"].append(f"{d['name']}: missing {sorted(wkw - rkw)}")
    # abilities (names only)
    GENERIC = {"leader", "support", "invulnerable", "damaged", "transport", "objectivesecured"}
    rz_ab = {n(re.sub(r"\s*\(.*?\)$", "", item(t).split("§", 1)[1])) for t in (a.get("assets") or {}).get("traits") or [] if item(t) and item(t).startswith("Ability§")}
    w_ab = {n(re.sub(r"\s*\(.*?\)$", "", html(ab["name"] or abil_lib.get(ab["ability_id"], {}).get("name", "")))) for ab in abils[did]}
    legends_unit = "Legends" in ((a.get("keywords") or {}).get("Source") or [])
    for x in sorted(rz_ab - w_ab) if not legends_unit else []:   # Legends units keep their abilities
        if not any(x.startswith(g_) for g_ in GENERIC) and not any(x.startswith(y) or y.startswith(x) for y in w_ab if len(y) > 4):
            R[stem]["ability stale"].append(f"{d['name']}: {x}")
    for ab in abils[did]:
        name = ab["name"] or abil_lib.get(ab["ability_id"], {}).get("name", "")
        if ab["type"] not in ("Datasheet", "Core", "Faction", "Psychic", "Primarch") or not name:
            continue
        if n(html(name)) in ("leader", "support"):
            continue
        base = n(re.sub(r"\s*\(.*?\)$", "", html(name)))
        if base and base not in rz_ab and not any(base.startswith(x) or x.startswith(base) for x in rz_ab if len(x) > 4):
            R[stem]["ability missing"].append(f"{d['name']}: {html(name)} [{ab['type']}]")
    # leader
    if leads.get(did):
        lt = next((t for t in (a.get("assets") or {}).get("traits") or [] if isinstance(t, dict) and item(t) in ("Ability§Leader", "Ability§Support")), None)
        rz_targets = {n(x) for x in re.findall(r"\*\s*([^\n]+)", (lt or {}).get("text") or "")}
        w_targets = {n(ds[x]["name"]) for x in leads[did] if x in ds}
        if w_targets - rz_targets:
            R[stem]["leader attach"].append(f"{d['name']}: missing {sorted(ds[x]['name'] for x in leads[did] if x in ds and n(ds[x]['name']) not in rz_targets)}")

# detachments / enhancements / stratagems
SM_FAMILY = ["Space_Marines"] + list(CHAPTERS.values())
def home(r, pre):
    """book holding this detachment-scoped asset; SM-family detachments live in whichever chapter book has them"""
    stem = book_of_detachment(r)
    if stem == "Space_Marines":
        for b in SM_FAMILY[1:]:
            if any(k.startswith("Detachment§") and n(k[11:]) == n(r["name"]) for k in raw[b]["rulebook"]["assetCatalog"]):
                return b
    return stem
for r in dets:
    stem = home(r, "Detachment§")
    if not stem or stem not in raw or (only and stem not in only):
        continue
    cat = resolved(stem)
    if not any(k.startswith("Detachment§") and n(k[11:]) == n(r["name"]) for k in cat):
        R[stem]["detachment missing"].append(r["name"])
for kind, rows in (("enhancement", enh), ("stratagem", strat)):
    pre = "Enhancement§" if kind == "enhancement" else "Stratagem§"
    for r in rows:
        dr = det_by_id[r["detachment_id"]]
        stem = home(dr, pre)
        if not stem or stem not in raw or (only and stem not in only):
            continue
        cat = resolved(stem)
        nm = n(re.sub(r"\s*\(.*?\)", "", html(r["name"])))
        if not any(k.startswith(pre) and n(re.sub(r"\s*\(.*?\)", "", k[len(pre):])) == nm for k in cat):
            R[stem][f"{kind} missing"].append(f"{dr['name']}: {html(r['name'])}")

# ------------------------------------------------------------------ report
tot = Counter()
for stem in sorted(R):
    for c_, lines in R[stem].items():
        tot[c_] += len(lines)
print("TOTALS:", dict(sorted(tot.items(), key=lambda x: -x[1])))
print()
cats = sorted(tot)
print("book".ljust(22) + "".join(c_[:12].rjust(13) for c_ in cats))
for stem in sorted(R):
    print(stem[:21].ljust(22) + "".join(str(len(R[stem].get(c_, []))).rjust(13) for c_ in cats))
for stem in sorted(R):
    print(f"\n######## {stem}")
    for c_, lines in sorted(R[stem].items()):
        print(f"== {c_} ({len(lines)})")
        for l in lines:
            print("  " + l)
