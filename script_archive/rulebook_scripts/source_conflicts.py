"""Flag MFM vs game-datacards disagreements on detachments and enhancements.

MFM is the official source, so anything it lists stays in the books; anything only one
source has gets a "WARNING: source conflict" paragraph prepended to its text so a human
can check it. Idempotent: stale conflict paragraphs are removed when the sources agree.

  MFM_DIR=… GDC_DIR=… PYTHONPATH=<pyyaml> python3 source_conflicts.py <factions.txt> [--apply]

factions.txt lines: "<BookStem> <gdc.json> <mfm-slug>".
"""
import difflib, json, os, re, sys
from pathlib import Path
import yaml

RZ = Path(__file__).resolve().parents[2]
MFM = Path(os.environ["MFM_DIR"]) / "data"
GDC = Path(os.environ["GDC_DIR"])
APPLY = "--apply" in sys.argv
TAG = "WARNING: source conflict"

n = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\([^)]*\)", "", s.lower().replace("’", "'")))
en = lambda x: x.get("en", "") if isinstance(x, dict) else (x or "")


def typo(a, b):  # same item spelled differently (e.g. Introneural/Intraneural) is not a conflict
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def only(xs, ys):
    return [x for x in xs if x not in ys and not any(typo(x, y) for y in ys if y not in xs)]


def strip(t):
    return re.sub(rf"^{TAG}[^\n]*\n\n", "", t or "")


for line in open(sys.argv[1]):
    book, gf, slug = line.split()
    m = yaml.safe_load(open(MFM / f"{slug}.yaml"))
    g = json.load(open(GDC / gf))
    p = RZ / f"{book}.rulebook"
    d = json.load(open(p))
    cat = d["rulebook"]["assetCatalog"]

    md = {n(x["name"]) for x in m.get("detachments") or []}
    gd = {n(en(x["name"])) for x in g.get("detachments") or []}
    me = {(n(e["name"]), n(x["name"])) for x in m.get("detachments") or [] for e in x.get("enhancements") or []}
    ge = {(n(en(e["name"])), n(e.get("detachment") or "")) for e in g.get("enhancements") or []}
    shared = md & gd
    flags = {}
    for k in only(md, gd):
        flags[("Detachment", k, None)] = "listed in the MFM but not in the codex datasheets (game-datacards); check it is still legal."
    for k in only(gd, md):
        flags[("Detachment", k, None)] = "in the codex datasheets (game-datacards) but not in the MFM; DP and points unverified."
    mes = {e for e in me if e[1] in shared}
    ges = {e for e in ge if e[1] in shared}
    for e, det in mes - ges:
        if not any(typo(e, x) for x, dx in ges - mes if dx == det):
            flags[("Enhancement", e, det)] = "listed in the MFM for this detachment but not in the codex datasheets (game-datacards)."
    for e, det in ges - mes:
        if not any(typo(e, x) for x, dx in mes - ges if dx == det):
            flags[("Enhancement", e, det)] = "in the codex datasheets (game-datacards) for this detachment but not in the MFM; points unverified."

    changed = []
    for key, a in cat.items():
        cls, _, name = key.partition("§")
        if cls not in ("Detachment", "Enhancement"):
            continue
        dets = [n(x) for x in (a.get("keywords") or {}).get("Keywords") or []]
        msg = flags.get((cls, n(name), None)) if cls == "Detachment" else \
            next((flags[(cls, n(name), dt)] for dt in dets if (cls, n(name), dt) in flags), None)
        old = a.get("text") or ""
        new = (f"{TAG}: {msg}\n\n" if msg else "") + strip(old)
        if new != old:
            a["text"] = new
            changed.append(("+ " if msg else "- ") + key)
    if changed:
        print(book, *changed, sep="\n  ")
        if APPLY:
            json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
