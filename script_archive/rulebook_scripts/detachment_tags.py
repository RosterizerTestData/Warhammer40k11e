"""Generate MFM `unique:` tag rules: each tagged detachment removes its same-tag siblings from the roster
Detachment dropdown. Rule lives on the Detachment asset (it only exists in the roster while selected).
Re-runnable: rebuilds the rule each time from the current MFM + books. Dry-run unless --apply."""
import os, json, sys, glob, copy
from collections import defaultdict
from pathlib import Path
SCR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCR / "pylib"))
import yaml
RZ = Path("/Users/kduda/websites/RosterizerTestData/Warhammer40k11e")
APPLY = "--apply" in sys.argv
RULE = "removeSameTagDetachments"
SLUG = {"orks": "Orks", "space-marines": "Space_Marines", "aeldari": "Aeldari", "astra-militarum": "Astra_Militarum",
        "adeptus-mechanicus": "Adeptus_Mechanicus", "adeptus-custodes": "Adeptus_Custodes", "adepta-sororitas": "Adepta_Sororitas",
        "chaos-space-marines": "Chaos_Space_Marines", "chaos-daemons": "Chaos_Daemons", "chaos-knights": "Chaos_Knights",
        "death-guard": "Death_Guard", "drukhari": "Drukhari", "emperors-children": "Emperor's_Children",
        "genestealer-cults": "Genestealer_Cults", "grey-knights": "Grey_Knights", "imperial-agents": "Imperial_Agents",
        "imperial-knights": "Imperial_Knights", "leagues-of-votann": "Leagues_of_Votann", "necrons": "Necrons",
        "tau-empire": "T'au_Empire", "thousand-sons": "Thousand_Sons", "tyranids": "Tyranids", "world-eaters": "World_Eaters",
        "black-templars": "SM_Black_Templars", "blood-angels": "SM_Blood_Angels", "dark-angels": "SM_Dark_Angels",
        "deathwatch": "SM_Deathwatch", "space-wolves": "SM_Space_Wolves"}
docs = {p.stem: json.load(open(p)) for p in RZ.glob("*.rulebook")}
cat = {b: d["rulebook"]["assetCatalog"] for b, d in docs.items()}
byname = {d["name"].strip(): b for b, d in docs.items()}
def chain(b):
    out = []
    for x in docs[b].get("dependencies", []):
        a = byname[x["name"].strip()]
        for y in chain(a) + [a]:
            if y not in out:
                out.append(y)
    return out
key = lambda s: s.lower().replace("’", "'")

log, touched = [], set()
for slug, b in SLUG.items():
    m = yaml.safe_load(open(Path(os.environ.get("MFM_DIR", "/tmp/wh40k-11e-mfm")) / "data" / f"{slug}.yaml"))
    ranks = {}
    for L in chain(b) + [b]:
        for r, rv in (cat[L].get("Roster§Roster", {}).get("stats", {}).get("Detachment", {}).get("ranks") or {}).items():
            asset = next((t["trait"] for t in rv.get("traits", []) if isinstance(t.get("trait"), str) and t["trait"].startswith("Detachment§")), None)
            ranks[r] = asset
    fams = defaultdict(list)
    for det in m["detachments"]:
        if det.get("unique"):
            fams[det["unique"]] += [r for r in ranks if key(r) == key(det["name"])]
    for tag, members in fams.items():
        members = sorted(set(members))
        for r in members:
            siblings = [s for s in members if key(s) != key(r)]
            asset = ranks[r]
            if not siblings or not asset:
                continue
            rule = {"actions": [{"actionType": "remove", "iterations": 1, "paths": [["{roster}", "stats", "Detachment", "ranks"]], "value": s} for s in siblings],
                    "evals": [], "evaluate": "AND", "failState": "pass", "order": 10}
            target = cat[b].setdefault(asset, {})          # own asset, or an override in this faction's book
            if (target.get("rules") or {}).get(RULE) != rule:
                target.setdefault("rules", {})[RULE] = rule
                touched.add(b)
                log.append(f"{b} {asset} [{tag}]: removes {siblings}")
print("\n".join(log)); print(len(log), "rules written/updated in", sorted(touched))
if APPLY:
    for b in touched:
        open(RZ / f"{b}.rulebook", "w").write(json.dumps(docs[b], indent=2, ensure_ascii=False))
    print("written")
