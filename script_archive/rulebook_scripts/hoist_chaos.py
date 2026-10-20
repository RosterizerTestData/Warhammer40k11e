"""Homogenise shared Chaos vehicles/monsters into Chaos.rulebook with per-legion overrides.

Per unit H:
  * hub base (faction-neutral) lives in Chaos.rulebook under the CSM class key; its weapons move there too
  * CSM gets an override adding Faction + Dark Pacts
  * each legion book (DG/EC/TS/WE): its datasheet for H — an 11e-named full copy, or the 10e legion-prefixed
    asset ("Thousand Sons Rhino") — becomes an override under the hub key; its weapons are re-keyed to
    "<H>—<weapon>" and stored as overrides of the hub weapon (or as new assets when the hub has none);
    stubs and the old keys are deleted
Every converted asset is verified: merge(hub, override) must equal the legion's previous datasheet
(trait order aside). Dry-run unless --apply.
"""
import os, json
from pathlib import Path, re, sys, copy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rzmerge import merge_entry, make_overlay, canon_unordered_traits

RZ = "/Users/kduda/websites/RosterizerTestData/Warhammer40k11e/"
APPLY = "--apply" in sys.argv
HUB, CSM = "Chaos", "Chaos_Space_Marines"
LEGIONS = {"Death_Guard": "Death Guard", "Emperor's_Children": "Emperor's Children",
           "Thousand_Sons": "Thousand Sons", "World_Eaters": "World Eaters"}
FACTION_ABILITIES = {"Ability§Dark Pacts"}
UNITS = {  # hub name -> legion-prefixed 10e suffix (prefixed name = "<Legion> <suffix>")
    "Chaos Rhino": "Rhino", "Chaos Land Raider": "Land Raider",
    "Chaos Predator Annihilator": "Predator Annihilator", "Chaos Predator Destructor": "Predator Destructor",
    "Chaos Vindicator": "Vindicator", "Defiler": "Defiler", "Helbrute": "Helbrute", "Heldrake": "Heldrake",
    "Maulerfiend": "Maulerfiend", "Forgefiend": "Forgefiend", "Chaos Spawn": "Chaos Spawn",
}
UP = ("Unit", "Character", "Vehicle", "Infantry/Mounted")
WRE = re.compile(r"^(Ranged Weapon|Melee Weapon|Weapon)§(.+?)—(.+)$")

docs = {b: json.load(open(RZ + b + ".rulebook")) for b in [HUB, CSM, *LEGIONS]}
cat = {b: d["rulebook"]["assetCatalog"] for b, d in docs.items()}
log, problems = [], []
touched = {b: set() for b in LEGIONS}

def unit_key(book, name):
    for cl in UP:
        if f"{cl}§{name}" in cat[book]:
            return f"{cl}§{name}"
    return None

def is_stub(a):
    return "WARNING" in (a.get("text") or "")

def weapons_of(book, unit_name):
    return sorted(k for k in cat[book] if (m := WRE.match(k)) and m.group(2) == unit_name)

def rekey(obj, old, new):
    s = json.dumps(obj, ensure_ascii=False)
    s = s.replace(f"§{old}—", f"§{new}—")
    return json.loads(s)

def same(a, b):
    return canon_unordered_traits(a) == canon_unordered_traits(b)

for H, suffix in UNITS.items():
    log.append(f"\n##### {H}")
    # ---------------- hub base
    hk_hub, hk_csm = unit_key(HUB, H), unit_key(CSM, H)
    if hk_hub and hk_csm and hk_csm.split("§")[0] != hk_hub.split("§")[0]:
        problems.append(f"{H}: hub/CSM class mismatch {hk_hub} vs {hk_csm}")
        continue
    HK = hk_hub or hk_csm
    if not HK:
        problems.append(f"{H}: no hub or CSM source")
        continue
    if hk_hub:
        csm_view = merge_entry(cat[HUB][HK], cat[CSM][HK]) if hk_csm else cat[HUB][HK]
    else:
        csm_view = cat[CSM][HK]
    base = copy.deepcopy(csm_view)
    base.get("keywords", {}).pop("Faction", None)
    if "assets" in base:
        base["assets"]["traits"] = [t for t in base["assets"].get("traits", []) if (t if isinstance(t, str) else t.get("item")) not in FACTION_ABILITIES]
    cat[HUB][HK] = base
    if not hk_hub:
        log.append(f"hoist {HK}: CSM -> Chaos (neutral)")
    # weapons CSM -> hub
    for w in weapons_of(CSM, H):
        if w in cat[HUB]:
            if cat[HUB][w] != cat[CSM][w]:
                problems.append(f"{w}: differs between hub and CSM")
            del cat[CSM][w]
        else:
            cat[HUB][w] = cat[CSM].pop(w)
        log.append(f"weapon {w}: CSM -> Chaos")
    # unit-only abilities/models referenced by base that live only in CSM -> hub
    for r in set(re.findall(r'"((?:Ability|Model|Wargear)§[^"]+)"', json.dumps(base, ensure_ascii=False))):
        if r in cat[CSM] and r not in cat[HUB]:
            users = [k for k, a in cat[CSM].items() if k != r and r in json.dumps(a, ensure_ascii=False)]
            cat[HUB][r] = cat[CSM][r] if len(users) > 1 else cat[CSM].pop(r)
            log.append(f"{'copy' if len(users) > 1 else 'move'} {r}: CSM -> Chaos (CSM users: {len(users)})")
    csm_ov = make_overlay(base, csm_view)
    cat[CSM][HK] = csm_ov
    if not same(merge_entry(base, csm_ov), csm_view):
        problems.append(f"VERIFY CSM {H}")
    log.append(f"CSM override: {json.dumps(csm_ov, ensure_ascii=False)[:300]}")

    # ---------------- legions
    for B, legion in LEGIONS.items():
        c = cat[B]
        named = [f"{cl}§{H}" for cl in UP if f"{cl}§{H}" in c]
        prefixed = f"Unit§{legion} {suffix}"
        full = [k for k in named if not is_stub(c[k]) and (k != HK or len(json.dumps(c[k])) > 700)]
        stubs = [k for k in named if is_stub(c[k])]
        if HK in c and HK not in full and not stubs and prefixed not in c:
            log.append(f"  {B}: already an override, untouched")
            continue
        if full and HK not in full or (full and len(full) > 1):
            src_key = [k for k in full if k != HK][0] if any(k != HK for k in full) else HK
        elif full:
            src_key = full[0]
        elif prefixed in c:
            src_key = prefixed
        else:
            if stubs:
                problems.append(f"{B} {H}: stub with no source datasheet ({stubs})")
            continue
        src_name = src_key.split("§", 1)[1]
        src = rekey(c[src_key], src_name, H)
        stub_pts = None
        for s in stubs:
            stub_pts = (c[s].get("stats", {}).get("Points") or {}).get("value", stub_pts)

        # weapons: re-key + overlay vs hub
        for w in weapons_of(B, src_name):
            nw = rekey(w, src_name, H) if False else w.replace(f"§{src_name}—", f"§{H}—")
            wa = c.pop(w)
            touched[B].add(nw)
            if nw in cat[HUB]:
                ov = make_overlay(cat[HUB][nw], wa)
                if not same(merge_entry(cat[HUB][nw], ov), wa):
                    problems.append(f"VERIFY weapon {B} {nw}")
                if ov:
                    c[nw] = ov
                log.append(f"  {B} weapon {w} -> {nw} {'override ' + json.dumps(ov, ensure_ascii=False)[:160] if ov else '(identical to hub, dropped)'}")
            else:
                c[nw] = wa
                log.append(f"  {B} weapon {w} -> {nw} (new, legion-only)")
        # other assets in this book that borrow the re-keyed weapons
        if src_name != H:
            for k, a in list(c.items()):
                s = json.dumps(a, ensure_ascii=False)
                if f"§{src_name}—" in s and k != src_key:
                    c[k] = json.loads(s.replace(f"§{src_name}—", f"§{H}—"))
                    log.append(f"  {B}: re-keyed borrowed {src_name} weapons inside {k}")
        # unit override
        target = copy.deepcopy(src)
        if stub_pts is not None:
            target.setdefault("stats", {}).setdefault("Points", {})["value"] = stub_pts
        ov = make_overlay(base, target)
        if not same(merge_entry(base, ov), target):
            problems.append(f"VERIFY {B} {H}:\n got ={json.dumps(canon_unordered_traits(merge_entry(base, ov)), ensure_ascii=False)[:900]}\n want={json.dumps(canon_unordered_traits(target), ensure_ascii=False)[:900]}")
        for k in set(named + [src_key]):
            c.pop(k, None)
        c[HK] = ov
        touched[B].add(HK)
        log.append(f"  {B}: {src_key} -> override {HK}" + (f" (points {stub_pts} from MFM stub)" if stub_pts is not None else "")
                   + f"; removed {sorted(set(named + [src_key]) - {HK})}")
        # rewrite references to removed keys
        for old in set(named + [src_key]) - {HK}:
            for k, a in c.items():
                s = json.dumps(a, ensure_ascii=False)
                if f'"{old}"' in s:
                    c[k] = json.loads(s.replace(f'"{old}"', f'"{HK}"'))
                    log.append(f"  {B}: ref {old} -> {HK} in {k}")

# ---------------- assets newly present in hub that legion books also define in full
hub_orig = json.load(open(RZ + HUB + ".rulebook"))["rulebook"]["assetCatalog"]
for k in [k for k in cat[HUB] if k not in hub_orig]:
    for B in LEGIONS:
        if k in cat[B] and k not in touched[B]:
            la = cat[B][k]
            ov = make_overlay(cat[HUB][k], la)
            if not same(merge_entry(cat[HUB][k], ov), la):
                problems.append(f"VERIFY collision {B} {k}")
            if ov:
                cat[B][k] = ov
            else:
                del cat[B][k]
            log.append(f"collision {B} {k}: {'override ' + json.dumps(ov, ensure_ascii=False)[:200] if ov else 'identical, dropped'}")

# ---------------- dangling-reference check on every resolved view
core = json.load(open(RZ + "Warhammer_40k_11e.rulebook"))["rulebook"]["assetCatalog"]
for B in [CSM, *LEGIONS]:
    view = {**core, **cat[HUB], **cat[B]}
    for H in UNITS:
        HK = unit_key(HUB, H)
        if not HK or HK not in cat[B]:
            continue
        a = merge_entry(cat[HUB][HK], cat[B][HK])
        for r in set(re.findall(r'"((?:Ranged Weapon|Melee Weapon|Weapon|Model|Wargear|Ability)§[^"]+)"', json.dumps(a, ensure_ascii=False))):
            if r not in view:
                problems.append(f"dangling in {B} view of {H}: {r}")

print("\n".join(log))
print("\nPROBLEMS:" if problems else "\nno problems", *problems, sep="\n")
if APPLY and not problems:
    for b, d in docs.items():
        with open(RZ + b + ".rulebook", "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    print("written")
