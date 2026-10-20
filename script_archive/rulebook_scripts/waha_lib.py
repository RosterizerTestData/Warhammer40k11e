"""Shared helpers for the Wahapedia-export scripts (waha_sync.py; waha_audit.py has its own copy).

WAHA_DIR = directory holding the 11e export CSVs (pipe-delimited, UTF-8 BOM).
"""
import csv, json, os, re
from collections import defaultdict
from pathlib import Path

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


def md(s):
    """Waha HTML → the books' markdown-ish text"""
    t = s or ""
    t = re.sub(r'<div class="abNameWrap">.*?</div>\s*<div class="abIcon">.*?</div>\s*</div>', "", t, flags=re.S)   # rule heading
    t = re.sub(r'<p class="ShowFluff[^"]*">.*?</p>', "", t, flags=re.S)   # flavour text
    t = re.sub(r"<(b|strong)>\s*<\1>(.*?)</\1>\s*</\1>", r"<\1>\2</\1>", t, flags=re.S)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<li[^>]*>", "\n- ", t)
    t = re.sub(r"</(p|div|ul|li)>", "\n", t)
    t = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", t, flags=re.S)
    t = re.sub(r"<(i|em)[^>]*>(.*?)</\1>", r"_\2_", t, flags=re.S)
    t = re.sub(r'<span class="kwb[^"]*">(.*?)</span>', lambda m: "**" + m.group(1).upper() + "**", t)
    t = re.sub(r"<[^>]+>", "", t).replace("&nbsp;", " ").replace(" ", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", t)
    return "\n".join(x.strip() for x in t.strip().split("\n"))


# ------------------------------------------------------------------ Waha tables
fac = {r["id"]: r["name"] for r in rd("Factions")}
src = {r["id"]: r for r in rd("Source")}
ds = {r["id"]: r for r in rd("Datasheets")}
models = defaultdict(list); [models[r["datasheet_id"]].append(r) for r in rd("Datasheets_models")]
gear = defaultdict(list); [gear[r["datasheet_id"]].append(r) for r in rd("Datasheets_wargear")]
kws = defaultdict(list); [kws[r["datasheet_id"]].append(r) for r in rd("Datasheets_keywords")]
abil_lib = {r["id"]: r for r in rd("Abilities")}
abils = defaultdict(list); [abils[r["datasheet_id"]].append(r) for r in rd("Datasheets_abilities")]
leads = defaultdict(set); [leads[r["leader_id"]].add(r["attached_id"]) for r in rd("Datasheets_leader")]
comp = defaultdict(list); [comp[r["datasheet_id"]].append(r) for r in rd("Datasheets_unit_composition")]
opts = defaultdict(list); [opts[r["datasheet_id"]].append(r) for r in rd("Datasheets_options")]


def is_legends(d):
    return "Legends" in src.get(d["source_id"], {}).get("name", "")


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
MFM_SLUG = {"Space_Marines": "space-marines", "Orks": "orks", "SM_Black_Templars": "black-templars", "SM_Blood_Angels": "blood-angels",
            "SM_Dark_Angels": "dark-angels", "SM_Space_Wolves": "space-wolves", "SM_Deathwatch": "deathwatch",
            "Adeptus_Titanicus": "titan-legions"}


def book_of_datasheet(d):
    f = fac.get(d["faction_id"], "")
    if f == "Space Marines":
        for kw in kws[d["id"]]:
            if kw["keyword"] in CHAPTERS:
                return CHAPTERS[kw["keyword"]]
        for ch, b in CHAPTERS.items():
            if n(ch) in n(d.get("link", "")):
                return b
    return FACTION_BOOK.get(f)


def datasheets_for(stem):
    return [d for d in ds.values() if d.get("virtual") != "true" and book_of_datasheet(d) == stem]


# ------------------------------------------------------------------ Rz books
def load_books():
    return {p.stem: json.load(open(p)) for p in RZ.glob("*.rulebook")}


def chain(books, stem):
    by_name = {d["name"].strip(): s for s, d in books.items()}
    out = []
    for dep in books[stem].get("dependencies") or []:
        s = by_name.get(dep["name"].strip())
        if s and s not in out:
            out += [x for x in chain(books, s) if x not in out] + [s]
    return out


def resolved(books, stem):
    cat = {}
    for s in chain(books, stem) + [stem]:
        for k, v in books[s]["rulebook"]["assetCatalog"].items():
            cat[k] = merge_entry(cat[k], v) if k in cat else v
    return cat


def find_unit(cat, name):
    k = next((x for x in cat if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]) == n(name)), None)
    if k:
        return k
    cands = [x for x in cat if x.split("§")[0] in UNITCLS and n(re.sub(r"\s*\(.*?\)\s*$", "", x.split("§", 1)[1])) == n(name)]
    if len(cands) == 1:
        return cands[0]
    plural = [x for x in cat if x.split("§")[0] in UNITCLS and n(x.split("§", 1)[1]).rstrip("s") == n(name).rstrip("s")]   # Sentry Pylon(s)
    return plural[0] if len(plural) == 1 else None


def item(t):
    return t.get("item") if isinstance(t, dict) else t


def mfm_units(stem, factions_txt):
    """normalised MFM unit names for the book (MFM decides inclusion)"""
    import yaml
    slug = MFM_SLUG.get(stem)
    if not slug:
        for line in open(factions_txt):
            b, _, s = line.split()
            if b == stem:
                slug = s
    if not slug:
        return set()
    m = yaml.safe_load(open(Path(os.environ["MFM_DIR"]) / "data" / f"{slug}.yaml"))
    return {n(u["name"]) for u in m.get("units") or []}


# ------------------------------------------------------------------ value / weapon-keyword parsing (same as gdc_codex_units.py)
UNKNOWN = set()

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
    UNKNOWN.add(k); return None

