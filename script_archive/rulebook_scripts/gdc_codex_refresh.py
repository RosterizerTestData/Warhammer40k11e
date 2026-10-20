"""Refresh one faction book's detachments, enhancements and stratagems from game-datacards 11e (codex) data.

  GDC_DIR=<dir of gdc json> MFM_DIR=<wh40k-11e-mfm clone> python3 gdc_codex_refresh.py <book_stem> <gdc_file> <mfm_slug> [--apply]

- Detachments: text = "**RULE NAME**" + body for each GDC detachment rule; DP/Role from MFM; assignStratagems
  rebuilt from GDC; roster Detachment rank added if missing. Other (hand-authored) rules on existing
  detachments are kept and listed for review.
- Enhancements: text, detachment keyword (exact roster rank name), points (MFM, else GDC cost), Upgrade tagging;
  eligibility rule generated from the description's "X unit/model only (excluding Y)." sentence when the asset
  has no eligibility rule yet.
- Stratagems: WHEN/TARGET/EFFECT text, Keywords [faction, detachment], Turn, Phase, Command Cost (if not 1).
- Stale: detachments no longer in GDC are removed from the roster ranks and deleted, with enhancements and
  stratagems that belonged only to them, and stratagems no longer wired by any detachment.
"""
import os, re, sys, json, copy
from pathlib import Path
from collections import defaultdict
import yaml

RZ = Path(__file__).resolve().parents[2]
GDC = Path(os.environ.get("GDC_DIR", "/tmp/gdc-11e-cache"))
MFM = Path(os.environ.get("MFM_DIR", "/tmp/wh40k-11e-mfm")) / "data"
book, gdc_file, slug = sys.argv[1:4]
APPLY = "--apply" in sys.argv

en = lambda x: x.get("en", "") if isinstance(x, dict) else (x or "")
key = lambda s: re.sub(r"\s+", " ", s.lower().replace("’", "'").replace("‘", "'")).strip()
strip_up = lambda s: re.sub(r"\s*\(Upgrade\)\s*$", "", s, flags=re.I).strip()

def md(text):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"<li>", "\n- ", t); t = re.sub(r"</li>", "", t); t = re.sub(r"</?ul>", "", t)
    t = re.sub(r"<k>(.*?)</k>", lambda m: "**" + m.group(1).upper() + "**", t, flags=re.S)
    t = re.sub(r"<b>(.*?)</b>", r"**\1**", t, flags=re.S)
    t = re.sub(r"<i>(.*?)</i>", r"_\1_", t, flags=re.S)
    t = re.sub(r"<u>(.*?)</u>", r"__\1__", t, flags=re.S)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()

def rules_text(blocks):
    parts = []
    for r in sorted(blocks or [], key=lambda x: x.get("order", 0)):
        if r.get("type") == "text":
            parts.append(md(en(r.get("text"))))
        elif r.get("type") in ("header", "accordion", "quote"):
            parts.append(md(en(r.get("title") or r.get("text"))))
    return "\n\n".join(p for p in parts if p)

d = json.load(open(RZ / f"{book}.rulebook"))
cat = d["rulebook"]["assetCatalog"]
g = json.load(open(GDC / gdc_file))
m = yaml.safe_load(open(MFM / f"{slug}.yaml"))
faction = (cat.get("Roster§Roster", {}).get("stats", {}).get("Faction", {}) or {}).get("value") or g.get("name")
faction = en(faction)
ranks = cat["Roster§Roster"]["stats"]["Detachment"].setdefault("ranks", {})
log, review = [], []

import difflib
def find_key(prefix, name):
    k = key(strip_up(name))
    hits = [x for x in cat if x.startswith(prefix) and key(strip_up(x[len(prefix):])) == k]
    exact = [x for x in hits if x[len(prefix):] == name]
    if exact or hits:
        return (exact or hits)[0]
    norm_ = lambda s: re.sub(r"[^a-z0-9]", "", key(strip_up(s)))
    scored = sorted(((difflib.SequenceMatcher(None, norm_(name), norm_(x[len(prefix):])).ratio(), x) for x in cat if x.startswith(prefix)), reverse=True)
    if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.2):
        review.append(f"fuzzy name match GDC '{name}' -> {scored[0][1]}")
        return scored[0][1]
    return None

# ---------------------------------------------------------------- detachments
gdc_dets = {en(x["name"]): x for x in g["detachments"]}
det_rules = {x["detachment"]: x["rules"] for x in g["rules"].get("detachment", [])}
mfm_dets = {key(x["name"]): x for x in m.get("detachments") or []}
strats_by_det = defaultdict(list)
for s in g.get("stratagems") or []:
    strats_by_det[s.get("detachment")].append(s)
rank_name = {}
for name, gd in gdc_dets.items():
    rk = next((r for r in ranks if key(r) == key(name)), None)
    dk = find_key("Detachment§", name) or f"Detachment§{name}"
    if rk is None:
        rk = dk.split("§", 1)[1]
        ranks[rk] = {"order": max([v.get("order", 0) for v in ranks.values()] + [0]) + 1, "traits": [{"trait": dk}]}
        log.append(f"+ rank {rk}")
    rank_name[key(name)] = rk
    a = cat.setdefault(dk, {})
    new = dk not in cat or not a
    blocks = det_rules.get(name) or det_rules.get(next((n for n in det_rules if key(n) == key(name)), ""), [])
    text = "\n\n".join(f"**{en(b['name']).upper()}**\n\n{rules_text(b.get('rules'))}" for b in blocks)
    old_text = a.get("text", "")
    a["text"] = text
    md_ = mfm_dets.get(key(name), {})
    a.setdefault("stats", {})["Detachment Points"] = {"value": md_.get("dp", gd.get("detachmentPoints"))}
    roles = [o.title() for o in md_.get("objectives") or []] or [en(f["name"]).title() for f in gd.get("forceDispositions") or []]
    a.setdefault("keywords", {})["Role"] = roles
    strat_keys = []
    for s in strats_by_det.get(name, []):
        strat_keys.append(find_key("Stratagem§", en(s["name"])) or f"Stratagem§{en(s['name'])}")
    a.setdefault("rules", {})["assignStratagems"] = {
        "actions": [{"actionType": "add", "iterations": 1, "paths": [["{roster}", "traits", "item", "Stratagems§Stratagems", "traits"]], "value": sk} for sk in strat_keys],
        "evals": [], "evaluate": "AND", "failState": "pass"}
    for r in [r for r in a["rules"] if r != "assignStratagems" and a["rules"][r].get("actions")
              and all(str(x.get("value", "")).startswith("Stratagem§") for x in a["rules"][r]["actions"])]:
        del a["rules"][r]; log.append(f"  dropped old stratagem wiring {dk}/{r}")
    other = [r for r in a["rules"] if r not in ("assignStratagems", "removeSameTagDetachments")]
    if other:
        review.append(f"{dk}: kept hand-authored rules {other} — check against new text")
    if re.search(r"BATTLELINE|has the \*\*[A-Z]|gains? the \*\*[A-Z]", text) and not other:
        review.append(f"{dk}: text grants keywords/Battleline — needs a functional rule")
    log.append(f"{'+' if not old_text else '~'} {dk} ({len(strat_keys)} strats){'' if old_text != text else ' (text unchanged)'}")

# ---------------------------------------------------------------- stratagems
TURN = {"your": ["Yours"], "opponents": ["Opponent’s"], "either": ["Yours", "Opponent’s"]}
for s in g.get("stratagems") or []:
    name = en(s["name"])
    sk = find_key("Stratagem§", name) or f"Stratagem§{name}"
    a = cat.setdefault(sk, {})
    rk = rank_name.get(key(s.get("detachment") or ""), s.get("detachment"))
    parts = [f"**WHEN:** {md(en(s.get('when')))}", f"**TARGET:** {md(en(s.get('target')))}", f"**EFFECT:** {md(en(s.get('effect')))}"]
    if en(s.get("restrictions")):
        parts.append(f"**RESTRICTIONS:** {md(en(s.get('restrictions')))}")
    a["text"] = "\n\n".join(parts)
    kw = a.setdefault("keywords", {})
    kw["Keywords"] = [faction, rk] if rk else [faction]
    kw["Turn"] = TURN.get(s.get("turn"), ["Yours"])
    kw["Phase"] = [p.title() for p in s.get("phase") or []]
    if s.get("type"):
        kw["Category"] = [s["type"].title()]
    cost = s.get("cost")
    if cost is not None and cost != 1:
        a.setdefault("stats", {})["Command Cost"] = {"value": cost}
    elif "stats" in a:
        a["stats"].pop("Command Cost", None)
        if not a["stats"]:
            del a["stats"]

# ---------------------------------------------------------------- enhancements
vocab = set()
for a in cat.values():
    for c in ("Keywords", "Faction"):
        vocab |= set((a.get("keywords") or {}).get(c) or [])
factions = {faction} | {v for a in cat.values() for v in ((a.get("keywords") or {}).get("Faction") or [])}

def split_kw(phrase):
    """'Orks Psyker' -> ['Orks','Psyker'] using known keywords (longest match first)."""
    words, out, i = phrase.split(), [], 0
    while i < len(words):
        for j in range(len(words), i, -1):
            cand = " ".join(words[i:j])
            hit = next((v for v in vocab if v.lower() == cand.lower()), None)
            if hit:
                out.append(hit); i = j; break
        else:
            out.append(words[i].title()); i += 1
    return out

def eligibility(desc):
    first = re.split(r"(?<=only\.)|(?<=\)\.)", desc, 1)[0]
    mm = re.match(r"^(.*?)\s+(?:model|unit)s?\s+only(?:\s*\(excluding (.*?)\))?\.", first)
    if not mm:
        return None, None
    def chunks(txt):
        txt = re.sub(r"</k>\s*/\s*<k>", "/", txt or "")
        return [[split_kw(alt.strip()) for alt in c.split("/") if alt.strip()] for c in re.findall(r"<k>(.*?)</k>", txt)]
    req, excl = chunks(mm.group(1)), chunks(mm.group(2))
    if not req:
        return None, md(first)
    path = lambda k: [["{parent}", "keywords", "Faction" if k in factions else "Keywords"]]
    has = lambda k, act, neg=False: {"actionable": act, "contains": True, "not": neg, "operator": "AND", "paths": path(k), "value": k}
    evals = []
    for alts in req:
        if len(alts) == 1:
            evals += [has(k, True) for k in alts[0]]
        elif all(len(a_) == 1 for a_ in alts):
            base = len(evals)
            evals += [has(a_[0], False) for a_ in alts]
            evals.append({"actionable": True, "not": False, "operator": "OR", "value": True,
                          "paths": [["{this}", "evals", str(base + i), "result", "truthy"] for i in range(len(alts))]})
        else:
            return None, md(first)
    for alts in excl:
        for a_ in alts:
            evals += [has(k, True, True) for k in a_]
    return {re.sub(r"\*\*", "", md(first)): {"evals": evals, "evaluate": "AND", "failState": "error"}}, None

mfm_enh = {}
for det in m.get("detachments") or []:
    for e in det.get("enhancements") or []:
        mfm_enh.setdefault(key(strip_up(e["name"])), {})[key(det["name"])] = e["points"]
seen_enh = {}
for e in g.get("enhancements") or []:
    name = en(e["name"])
    ek = find_key("Enhancement§", name) or f"Enhancement§{name}"
    a = cat.setdefault(ek, {})
    is_new = not a
    rk = rank_name.get(key(e.get("detachment") or ""), e.get("detachment"))
    desc = en(e.get("description"))
    a["text"] = md(desc)
    kws = a.setdefault("keywords", {}).setdefault("Keywords", [])
    if ek not in seen_enh:
        kws[:] = []                         # first time this run: rebuild detachment list from GDC
        seen_enh[ek] = True
    if rk and rk not in kws:
        kws.append(rk)
    pts = (mfm_enh.get(key(strip_up(name))) or {}).get(key(e.get("detachment") or ""))
    if pts is None:
        pts = int(e.get("cost") or 0)
    st = a.setdefault("stats", {})
    if not st.get("Detachment Trigger"):
        st["Points"] = {**(st.get("Points") or {}), "value": pts}
    if e.get("equipableByNonCharacter") or re.search(r"\(Upgrade\)", name):
        tags = a["keywords"].setdefault("Tags", [])
        if "Upgrade" not in tags:
            tags.append("Upgrade")
        a.setdefault("removed", {}).setdefault("keywords", {})["Tags"] = ["Character Only"]
        a.setdefault("aspects", {})["Unique"] = False
    has_elig = any((r.get("failState") == "error") for r in (a.get("rules") or {}).values())
    if not has_elig:
        rule, manual = eligibility(desc)
        if rule:
            a.setdefault("rules", {}).update(rule)
        elif manual:
            review.append(f"{ek}: eligibility needs hand authoring: {manual}")
    log.append(f"{'+' if is_new else '~'} {ek} [{', '.join(kws)}] {pts}pts")

# ---------------------------------------------------------------- stale content
live_dets = set(rank_name.values())
book_dets = set(ranks)
_names = {json.load(open(p_))["name"].strip(): p_ for p_ in RZ.glob("*.rulebook")}
def _dep_ranks(doc):
    out = set()
    for dep in doc.get("dependencies", []):
        dd = json.load(open(_names[dep["name"].strip()]))
        out |= _dep_ranks(dd)
        out |= set((dd["rulebook"]["assetCatalog"].get("Roster§Roster", {}).get("stats", {}).get("Detachment", {}).get("ranks") or {}))
    return out
inherited_dets = _dep_ranks(d)
retired = []
for r in [r for r in ranks if r not in live_dets and r not in inherited_dets]:
    asset = next((t["trait"] for t in ranks[r].get("traits", []) if isinstance(t.get("trait"), str)), None)
    if asset and asset in cat:
        cat[asset].setdefault("keywords", {})["Source"] = ["Legends"]
    retired.append(r)
    log.append(f"~ retired detachment {r} -> Legends")
if retired or any(f"hideLegendsDetachments ({book})" == x for x in cat["Roster§Roster"].get("rules", {})):
    prev = [a["value"] for a in cat["Roster§Roster"].get("rules", {}).get(f"hideLegendsDetachments ({book})", {}).get("actions", [])]
    names = sorted(set(prev + retired) & set(ranks))
    cat["Roster§Roster"].setdefault("rules", {})[f"hideLegendsDetachments ({book})"] = {
        "evals": [{"actionable": True, "not": False, "operator": "AND", "paths": [["{self}", "stats", "Legends", "value"]], "value": "-"}],
        "actions": [{"actionType": "remove", "iterations": 1, "paths": [["{self}", "stats", "Detachment", "ranks"]], "value": nm} for nm in names],
        "evaluate": "AND", "failState": "pass", "order": 9}
legends_dets = {r for r in ranks if (cat.get(next((t["trait"] for t in ranks[r].get("traits", []) if isinstance(t.get("trait"), str)), ""), {}).get("keywords") or {}).get("Source") == ["Legends"]}
gdc_enh = {key(strip_up(en(e["name"]))) for e in g.get("enhancements") or []}
for k_ in [k_ for k_ in cat if k_.startswith("Enhancement§") and key(strip_up(k_[12:])) not in gdc_enh]:
    dets = (cat[k_].get("keywords") or {}).get("Keywords") or []
    if not dets:
        del cat[k_]; log.append(f"- orphan enhancement {k_} (no detachment)")
wired = {act["value"] for a in cat.values() if isinstance(a, dict) for r in (a.get("rules") or {}).values()
         for act in r.get("actions", []) if isinstance(act.get("value"), str) and act["value"].startswith("Stratagem§")}
for k_ in [k_ for k_ in cat if k_.startswith("Stratagem§") and k_ not in wired
           and set((cat[k_].get("keywords") or {}).get("Keywords") or []) & {key(x) for x in book_dets} | set()
           or (k_.startswith("Stratagem§") and k_ not in wired and {key(x) for x in (cat[k_].get("keywords") or {}).get("Keywords") or []} & {key(x) for x in book_dets})]:
    del cat[k_]; log.append(f"- stale stratagem {k_}")

missing = [n for n in gdc_dets if not strats_by_det.get(n)] + [f"{n} (only {len(v)})" for n, v in strats_by_det.items() if 0 < len(v) < 3]
if missing:
    review.append(f"GDC has fewer than 3 stratagems for: {missing}")
print("\n".join(log))
print("\n=== REVIEW ===\n" + "\n".join(review))
if APPLY:
    open(RZ / f"{book}.rulebook", "w").write(json.dumps(d, indent=2, ensure_ascii=False))
    print("written")
