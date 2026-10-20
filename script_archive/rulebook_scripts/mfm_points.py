"""Hub-aware MFM points importer for the Rz 40k 11e books.

For every MFM faction file: unit costs (tiers + first-N discount), enhancement points, detachment DP + Role
(objectives), and per-item wargear points. Each change is written into the faction's own book — directly
on its own asset, or as an override of an asset inherited from a dependency (Chaos / Imperium / Harlequins /
Space_Marines). Every override is verified with the merge simulator. Dry-run unless --apply.
Also adds a tracked Points stat to the core Wargear class (same shape as Weapon's).
"""
import os, json, re, sys, copy, unicodedata
from pathlib import Path
from collections import defaultdict, Counter
SCR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCR)); sys.path.insert(0, str(SCR / "pylib"))
import yaml
from rzmerge import merge_entry, make_overlay, canon_unordered_traits

RZ = Path("/Users/kduda/websites/RosterizerTestData/Warhammer40k11e")
MFM = Path(os.environ.get("MFM_DIR", "/tmp/wh40k-11e-mfm")) / "data"
APPLY = "--apply" in sys.argv
SLUG_TO_FILE = {
    "orks": "Orks", "space-marines": "Space_Marines", "aeldari": "Aeldari", "astra-militarum": "Astra_Militarum",
    "adeptus-mechanicus": "Adeptus_Mechanicus", "adeptus-custodes": "Adeptus_Custodes",
    "adepta-sororitas": "Adepta_Sororitas", "chaos-space-marines": "Chaos_Space_Marines",
    "chaos-daemons": "Chaos_Daemons", "chaos-knights": "Chaos_Knights", "death-guard": "Death_Guard",
    "drukhari": "Drukhari", "emperors-children": "Emperor's_Children", "genestealer-cults": "Genestealer_Cults",
    "grey-knights": "Grey_Knights", "imperial-agents": "Imperial_Agents", "imperial-knights": "Imperial_Knights",
    "leagues-of-votann": "Leagues_of_Votann", "necrons": "Necrons", "tau-empire": "T'au_Empire",
    "thousand-sons": "Thousand_Sons", "tyranids": "Tyranids", "world-eaters": "World_Eaters",
    "black-templars": "SM_Black_Templars", "blood-angels": "SM_Blood_Angels", "dark-angels": "SM_Dark_Angels",
    "deathwatch": "SM_Deathwatch", "space-wolves": "SM_Space_Wolves", "titan-legions": "Adeptus_Titanicus",
    "chaos-titan-legions": "Titanicus_Traitoris",
}
UP = ("Unit", "Character", "Vehicle", "Infantry/Mounted", "Titan")
ORD = ["1st", "2nd", "3rd", "4th", "5th", "6th"]

def norm(s):
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[\x27`]", "", s).replace("&", "and")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()
strip_paren = lambda s: re.sub(r"\s*\(.*?\)\s*$", "", s).strip()

# ---------------------------------------------------------------- books + dependency chains
docs, cat, deps, byname = {}, {}, {}, {}
for p in sorted(RZ.glob("*.rulebook")):
    if p.stem == "Combat_Patrol":
        continue
    d = json.load(open(p)); docs[p.stem] = d; cat[p.stem] = d["rulebook"]["assetCatalog"]
    byname[d["name"].strip()] = p.stem
for b, d in docs.items():
    deps[b] = [byname[x["name"].strip()] for x in d.get("dependencies", [])]
def chain(b):
    out = []
    for a in deps[b]:
        for x in chain(a) + [a]:
            if x not in out:
                out.append(x)
    return out
def resolve(layers, key):
    res = None
    for L in layers:
        if key in cat[L]:
            res = copy.deepcopy(cat[L][key]) if res is None else merge_entry(res, cat[L][key])
    return res
def view_keys(b):
    ks = set()
    for L in chain(b) + [b]:
        ks |= set(cat[L])
    return ks

log, problems, conflicts = [], [], []
stats = Counter()
same = lambda a, b: canon_unordered_traits(a) == canon_unordered_traits(b)

def set_target(b, key, mutate, what):
    """Apply mutate() to the asset as book b sees it; persist the minimal change in book b."""
    layers = chain(b)
    cur = resolve(layers + [b], key)
    desired = mutate(copy.deepcopy(cur))
    if desired == cur:
        stats[what + "_same"] += 1
        return False
    dep = resolve(layers, key)
    if dep is None:                        # b owns the asset outright: edit in place
        cat[b][key] = mutate(cat[b][key])
        if not same(cat[b][key], desired):
            problems.append(f"in-place mismatch {b} {key}")
    else:                                  # inherited: (re)build b's override
        own = cat[b].get(key)
        patched = mutate(copy.deepcopy(own)) if own is not None else None
        if patched is not None and same(merge_entry(dep, patched), desired):
            cat[b][key] = patched
        else:
            ov = make_overlay(dep, desired)
            if not same(merge_entry(dep, ov), desired):
                problems.append(f"VERIFY {b} {key}")
                return False
            cat[b][key] = ov
    stats[what + "_changed"] += 1
    return True

# ---------------------------------------------------------------- core: Wargear Points
core_tx = docs["Warhammer_40k_11e"]["rulebook"]["assetTaxonomy"]
wg = core_tx.setdefault("Wargear", {}).setdefault("stats", {})
if "Points" not in wg:
    wg["Points"] = copy.deepcopy(core_tx["Weapon"]["stats"]["Points"])
    log.append("core: added tracked Points stat to Wargear class " + json.dumps(wg["Points"]))

# ---------------------------------------------------------------- cost writers
def cost_mutator(mu, report):
    pricing = mu["pricing"]
    pricier = pricing[-1]
    cheaper = pricing[0] if len(pricing) == 2 else None
    costs = [(c["models"], c["points"]) for c in pricier["costs"] if not c.get("addon")]
    discount = threshold = 0
    if cheaper:
        a = {c["models"]: c["points"] for c in cheaper["costs"] if not c.get("addon")}
        ds = {p - a[m] for m, p in costs if m in a}
        discount = ds.pop()
        threshold = int(re.match(r"\[1,(\d+)\]", cheaper["range"]).group(1)) + 1
    def mutate(asset):
        st = asset.setdefault("stats", {})
        def setv(k, v):
            if isinstance(st.get(k), dict):
                st[k] = {**st[k], "value": v}
            else:
                st[k] = {"value": v}
        if isinstance(st.get("model1stTally"), dict) and st["model1stTally"].get("value") is not None:
            setv("model1stTally", costs[0][0]); setv("Points", costs[0][1])
            for i, (m, p) in enumerate(costs[1:], 2):
                setv(f"model{ORD[i-1]}Tally", m); setv(f"model{ORD[i-1]}Cost", p)
            for i in range(len(costs) + 1, len(ORD) + 1):
                st.pop(f"model{ORD[i-1]}Tally", None); st.pop(f"model{ORD[i-1]}Cost", None)
        else:
            if len(costs) > 1:
                report.append(f"flat cost in Rz but MFM has sizes {costs}")
            setv("Points", costs[0][1])
        if discount or "unitDiscount" in st:
            setv("unitDiscount", discount)
        if threshold or "unitDiscountThreshold" in st:
            setv("unitDiscountThreshold", threshold)
        return asset
    return mutate

FACTION_PREFIX = {"Thousand_Sons": "Thousand Sons", "Death_Guard": "Death Guard", "World_Eaters": "World Eaters",
                  "Emperor's_Children": "Emperor's Children"}
def sing(n):
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in n.split())

def find_unit_fallback(b, name, keys):
    n = norm(name)
    units = [k for k in keys if k.partition("§")[0] in UP]
    for k in units:
        nm = norm(k.partition("§")[2])
        if nm in (n + " agents", norm(FACTION_PREFIX.get(b, "zz") + " " + name)):
            return k
    suffixed = [k for k in cat[b] if k.partition("§")[0] in UP and "(" in k and norm(strip_paren(k.partition("§")[2])) == n]
    if len(suffixed) == 1:
        return suffixed[0]
    own = [k for k in cat[b] if k.partition("§")[0] in UP and norm(k.partition("§")[2]).startswith(n + " ")]
    return own[0] if len(own) == 1 else None

def find_unit(b, name, keys):
    cands = [f"{cl}§{name}" for cl in UP]
    for k in cands:
        if k in keys:
            return k
    n = norm(name)
    hits = [k for k in keys if k.partition("§")[0] in UP and norm(k.partition("§")[2]) == n]
    if b == "Titanicus_Traitoris" and not hits and name.startswith("Chaos "):
        return find_unit(b, name[6:], keys)
    return hits[0] if hits else find_unit_fallback(b, name, keys)

absent = defaultdict(list)
for slug, b in SLUG_TO_FILE.items():
    yml = MFM / f"{slug}.yaml"
    if not yml.exists() or b not in cat:
        continue
    m = yaml.safe_load(open(yml))
    keys = sorted(view_keys(b))
    # units: one entry per name — prefer the faction's own (ungrouped) entry; skip true conflicts
    by = defaultdict(list)
    for mu in m.get("units") or []:
        by[norm(mu["name"])].append(mu)
    chosen = []
    for n_, entries in by.items():
        own = [e for e in entries if not e.get("groupTitle")] or entries
        if len({json.dumps(e["pricing"], sort_keys=True) for e in own}) > 1:
            conflicts.append(f"{b}: unit '{entries[0]['name']}' listed {len(own)}x with different pricing: {[e['pricing'][-1]['costs'] for e in own]}")
            continue
        if len(entries) > 1 and len({json.dumps(e["pricing"], sort_keys=True) for e in entries}) > 1:
            conflicts.append(f"{b}: unit '{entries[0]['name']}' — used ungrouped price; group '{[e.get('groupTitle') for e in entries if e.get('groupTitle')][0]}' price ignored")
        chosen.append(own[0])
    # allied prices (Imperial Agents taken by another Imperium army) live on the shared hub asset
    if slug == "imperial-agents":
        hub_keys = sorted(view_keys("Imperium"))
        for mu in m.get("units") or []:
            if mu.get("groupTitle") != "Every Model Has The Imperium Keyword":
                continue
            hk = find_unit("Imperium", mu["name"], hub_keys)
            if not hk or hk not in cat["Imperium"]:
                absent["allied"].append(f"Imperium: {mu['name']}")
                continue
            rep = []
            if set_target("Imperium", hk, cost_mutator(mu, rep), "allied"):
                log.append(f"Imperium {hk}: allied cost -> {[(c['models'], c['points']) for c in mu['pricing'][-1]['costs']]}")
    for mu in chosen:
        k = find_unit(b, mu["name"], keys)
        if not k:
            absent["unit"].append(f"{b}: {mu['name']}" + (" [legends]" if mu.get("legends") else ""))
            continue
        rows = {}
        for t in mu["pricing"]:
            seen = {}
            for c in list(t["costs"]):
                if c.get("addon"):
                    continue
                if c["models"] in seen and seen[c["models"]] == c["points"]:
                    t["costs"].remove(c)
                seen.setdefault(c["models"], c["points"])
        dup = [c["models"] for c in mu["pricing"][-1]["costs"] if not c.get("addon")]
        if len(dup) != len(set(dup)):
            problems.append(f"{b} {k}: composite rows with duplicate model counts — skipped: {mu['pricing'][-1]['costs']}")
            continue
        rep = []
        if set_target(b, k, cost_mutator(mu, rep), "unit"):
            log.append(f"{b} {k}: cost -> {[ (c['models'], c['points']) for c in mu['pricing'][-1]['costs']]}" + (f" (discount {len(mu['pricing'])==2})" if len(mu['pricing']) == 2 else ""))
        for r in rep:
            log.append(f"  NOTE {b} {k}: {r}")
            stats["flat_vs_sizes"] += 1
        # wargear costs
        uname = k.partition("§")[2]
        for w in mu.get("wargear") or []:
            want = sing(norm(w["item"]))
            wk = [x for x in keys if re.match(r"^(Ranged Weapon|Melee Weapon|Weapon|Wargear)§", x)
                  and x.partition("§")[2].startswith(uname + "—") and sing(norm(x.partition("—")[2])) == want]
            if len(wk) > 1:   # parent multi-profile weapon vs its profiles: keep what the unit's loadout references
                ujson = json.dumps(resolve(chain(b) + [b], k), ensure_ascii=False)
                models = set(re.findall(r'"(Model§[^"]+)"', ujson))
                ujson += "".join(json.dumps(resolve(chain(b) + [b], mk) or {}, ensure_ascii=False) for mk in models)
                wk = [x for x in wk if f'"{x}"' in ujson] or wk
            if not wk:        # shared wargear asset used only by this unit
                shared = [x for x in keys if x.startswith("Wargear§") and sing(norm(strip_paren(x[8:]))) == sing(norm(strip_paren(w["item"])))]
                users = lambda x: [u for L in chain(b) + [b] for u, a in cat[L].items() if u != x and f'"{x}"' in json.dumps(a, ensure_ascii=False)]
                wk = [x for x in shared if all(u.endswith("§" + uname) or ("§" + uname + "—") in u for u in users(x))]
            if len(wk) != 1:
                absent["wargear"].append(f"{b}: {uname} — {w['item']} ({w['points']} pts) " + (f"ambiguous {wk}" if wk else "no matching asset"))
                continue
            def mut(a, pts=w["points"]):
                st = a.setdefault("stats", {})
                st["Points"] = {**(st.get("Points") or {}), "value": pts}
                return a
            if set_target(b, wk[0], mut, "wargear"):
                log.append(f"{b} {wk[0]}: Points {w['points']}")
    # detachments + enhancements
    enh_prices = defaultdict(set)
    for det in m.get("detachments") or []:
        for e in det.get("enhancements") or []:
            enh_prices[norm(strip_paren(e["name"]))].add(e["points"])
    for n_, ps in enh_prices.items():
        if len(ps) > 1:
            conflicts.append(f"{b}: enhancement '{n_}' has different points in different detachments {sorted(ps)} — one Rz asset, left as is")
    for det in m.get("detachments") or []:
        dks = [x for x in keys if x.startswith("Detachment§") and norm(x[11:]) == norm(det["name"])]
        if len(dks) > 1:
            conflicts.append(f"{b}: duplicate detachment assets {dks} — both updated")
        if not dks:
            absent["detachment"].append(f"{b}: {det['name']}")
        for dk in dks:
            roles = [o.title() for o in det.get("objectives") or []]
            def mut(a, dp=det.get("dp"), roles=roles):
                if dp is not None:
                    a.setdefault("stats", {})["Detachment Points"] = {**(a.get("stats", {}).get("Detachment Points") or {}), "value": dp}
                if roles:
                    a.setdefault("keywords", {})["Role"] = roles
                return a
            if set_target(b, dk, mut, "detachment"):
                log.append(f"{b} {dk}: DP {det.get('dp')} Role {roles}")
        for e in det.get("enhancements") or []:
            if len(enh_prices[norm(strip_paren(e["name"]))]) > 1:
                continue
            ek = next((x for x in keys if x.startswith("Enhancement§") and norm(x[12:]) == norm(e["name"])), None) \
                or next((x for x in keys if x.startswith("Enhancement§") and norm(strip_paren(x[12:])) == norm(strip_paren(e["name"]))), None)
            if not ek:
                absent["enhancement"].append(f"{b}: {e['name']} ({det['name']})")
                continue
            def mut(a, pts=e["points"]):
                a.setdefault("stats", {})["Points"] = {**(a.get("stats", {}).get("Points") or {}), "value": pts}
                return a
            if set_target(b, ek, mut, "enhancement"):
                log.append(f"{b} {ek}: Points {e['points']}")

out = Path(os.environ.get("RZ_REPORT_DIR", "/tmp")) / "mfm_points_report.txt"
with open(out, "w") as f:
    f.write("\n".join(log))
    f.write(f"\n\n=== CONFLICTS / DECISIONS ({len(conflicts)})\n" + "\n".join(conflicts))
    for kind, items in absent.items():
        f.write(f"\n\n=== ABSENT {kind} ({len(items)})\n" + "\n".join(items))
print(dict(stats))
print({k: len(v) for k, v in absent.items()})
print("report:", out)
print("\nPROBLEMS:" if problems else "\nno problems", *problems[:30], sep="\n")
if APPLY and not problems:
    for b, d in docs.items():
        new = json.dumps(d, indent=2, ensure_ascii=False)
        if new != open(RZ / f"{b}.rulebook").read():
            open(RZ / f"{b}.rulebook", "w").write(new)
            print("wrote", b)
