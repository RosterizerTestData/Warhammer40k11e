#!/usr/bin/env python3
"""
enrich_stubs.py — Fetch 11e rules text from game-datacards/datasources and
append it to WARNING stub entries in the Rz 11e rulebook files.

The WARNING line is kept so stubs remain identifiable for human review.

Usage:
    python3 enrich_stubs.py [--apply] [--faction <slug>]

    --apply    write enriched text back to .rulebook files (default: dry-run)
    --faction  only process one faction (same slugs as mfm_update.py)

GDC JSON files are fetched live and cached in /tmp/gdc-11e-cache/.
"""

import json
import re
import sys
import os
import urllib.request
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
RZ_11E_DIR = SCRIPT_DIR.parent.parent
CACHE_DIR  = Path("/tmp/gdc-11e-cache")
GDC_RAW    = "https://raw.githubusercontent.com/game-datacards/datasources/main/11th/gdc/{filename}"

WARNING_RE = re.compile(r"WARNING:")

# Map Rz file stem -> GDC json filename(s) to search (order: specific first)
RZ_TO_GDC: dict[str, list[str]] = {
    "Orks":                ["orks.json"],
    "Space_Marines":       ["space_marines.json"],
    "Aeldari":             ["aeldari.json"],
    "Astra_Militarum":     ["astramilitarum.json"],
    "Adeptus_Mechanicus":  ["adeptusmechanicus.json"],
    "Adeptus_Custodes":    ["adeptuscustodes.json"],
    "Adepta_Sororitas":    ["adeptasororitas.json"],
    "Chaos_Space_Marines": ["chaos_spacemarines.json"],
    "Chaos_Daemons":       ["chaosdaemons.json"],
    "Chaos_Knights":       ["chaosknights.json"],
    "Death_Guard":         ["deathguard.json"],
    "Drukhari":            ["drukhari.json"],
    "Emperor's_Children":  ["emperors_children.json"],
    "Genestealer_Cults":   ["gsc.json"],
    "Grey_Knights":        ["greyknights.json"],
    "Imperial_Agents":     ["agents.json"],
    "Imperial_Knights":    ["imperialknights.json"],
    "Leagues_of_Votann":   ["votann.json"],
    "Necrons":             ["necrons.json"],
    "T'au_Empire":         ["tau.json"],
    "Thousand_Sons":       ["thousandsons.json"],
    "Tyranids":            ["tyranids.json"],
    "World_Eaters":        ["worldeaters.json"],
    "SM_Black_Templars":   ["blacktemplar.json", "space_marines.json"],
    "SM_Blood_Angels":     ["bloodangels.json",  "space_marines.json"],
    "SM_Dark_Angels":      ["darkangels.json",   "space_marines.json"],
    "SM_Deathwatch":       ["deathwatch.json",   "space_marines.json"],
    "SM_Space_Wolves":     ["spacewolves.json",  "space_marines.json"],
    "Adeptus_Titanicus":   ["titan.json"],
    "Titanicus_Traitoris": ["titan.json"],
    "Chaos":               ["chaos_spacemarines.json", "chaosdaemons.json",
                            "chaosknights.json", "deathguard.json",
                            "emperors_children.json", "thousandsons.json",
                            "worldeaters.json"],
    "Imperium":            ["space_marines.json", "astramilitarum.json",
                            "adeptasororitas.json", "adeptuscustodes.json",
                            "adeptusmechanicus.json", "greyknights.json",
                            "deathwatch.json", "imperialknights.json",
                            "agents.json"],
    "Harlequins":          ["aeldari.json"],
    "Combat_Patrol":       [],
}

SLUG_TO_FILE = {
    "orks": "Orks", "space-marines": "Space_Marines", "aeldari": "Aeldari",
    "astra-militarum": "Astra_Militarum", "adeptus-mechanicus": "Adeptus_Mechanicus",
    "adeptus-custodes": "Adeptus_Custodes", "adepta-sororitas": "Adepta_Sororitas",
    "chaos-space-marines": "Chaos_Space_Marines", "chaos-daemons": "Chaos_Daemons",
    "chaos-knights": "Chaos_Knights", "death-guard": "Death_Guard", "drukhari": "Drukhari",
    "emperors-children": "Emperor's_Children", "genestealer-cults": "Genestealer_Cults",
    "grey-knights": "Grey_Knights", "imperial-agents": "Imperial_Agents",
    "imperial-knights": "Imperial_Knights", "leagues-of-votann": "Leagues_of_Votann",
    "necrons": "Necrons", "tau-empire": "T'au_Empire", "thousand-sons": "Thousand_Sons",
    "tyranids": "Tyranids", "world-eaters": "World_Eaters",
    "black-templars": "SM_Black_Templars", "blood-angels": "SM_Blood_Angels",
    "dark-angels": "SM_Dark_Angels", "deathwatch": "SM_Deathwatch",
    "space-wolves": "SM_Space_Wolves", "titan-legions": "Adeptus_Titanicus",
    "chaos-titan-legions": "Titanicus_Traitoris",
    "chaos-module": "Chaos", "imperium-module": "Imperium", "harlequins-module": "Harlequins",
}

# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------

def fetch_gdc(filename: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / filename
    if not cache_path.exists():
        url = GDC_RAW.format(filename=urllib.parse.quote(filename))
        print(f"  [fetch] {filename}")
        with urllib.request.urlopen(url) as resp:
            data = resp.read()
        cache_path.write_bytes(data)
    with open(cache_path) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------

def gdc_to_markdown(text: str) -> str:
    """Convert GDC HTML tags to Markdown."""
    # Normalise \r\n and bare \r to \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # <ul><li>…</li>…</ul> → bullet list
    # Handle nested content: replace </li> with newline marker, strip tags
    text = re.sub(r"<li>", "\n- ", text)
    text = re.sub(r"</li>", "", text)
    text = re.sub(r"</?ul>", "", text)
    # <k>keyword</k> → **keyword** (game keywords shown in bold)
    text = re.sub(r"<k>(.*?)</k>", r"**\1**", text, flags=re.DOTALL)
    # <b>…</b> → **…**
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    # <i>…</i> → _…_
    text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.DOTALL)
    # <u>…</u> → __…__ (underline → markdown underline via double underscore)
    text = re.sub(r"<u>(.*?)</u>", r"__\1__", text, flags=re.DOTALL)
    # Strip any remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse runs of 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def normalise(s: str) -> str:
    # Strip trailing parentheticals, lowercase, remove non-alphanumeric
    s = re.sub(r"\s*\(.*?\)\s*$", "", s).strip()
    return re.sub(r"[^a-z0-9]", "", s.lower())

# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def rules_block_to_text(rules: list) -> str:
    """Convert a GDC rules array (with order/type/text entries) to Markdown."""
    parts = []
    for r in sorted(rules, key=lambda x: x.get("order", 0)):
        if r.get("type") == "text":
            t = (r.get("text") or {}).get("en", "")
            t = gdc_to_markdown(t)
            if t:
                parts.append(t)
    return "\n\n".join(parts)


def detachment_text(det_name: str, gdc_data: dict) -> str | None:
    """Find detachment rule text by name."""
    norm = normalise(det_name)
    for dr in gdc_data.get("rules", {}).get("detachment", []):
        if normalise(dr.get("detachment", "")) == norm:
            parts = []
            for rule in dr.get("rules", []):
                name = (rule.get("name") or {}).get("en", "")
                body = rules_block_to_text(rule.get("rules", []))
                if name and body:
                    parts.append(f"**{name}**\n{body}")
                elif body:
                    parts.append(body)
            return "\n\n".join(parts) if parts else None
    return None


def enhancement_text(enh_name: str, gdc_data: dict) -> str | None:
    """Find enhancement description text by name."""
    norm = normalise(enh_name)
    for e in gdc_data.get("enhancements", []):
        if normalise((e.get("name") or {}).get("en", "")) == norm:
            desc = (e.get("description") or {}).get("en", "")
            desc = gdc_to_markdown(desc)
            return desc if desc else None
    return None


def unit_text(unit_name: str, gdc_data: dict) -> str | None:
    """Find unit ability text from datasheets."""
    norm = normalise(unit_name)
    for ds in gdc_data.get("datasheets", []):
        if normalise((ds.get("name") or {}).get("en", "")) == norm:
            parts = []
            abilities = ds.get("abilities", {})
            # Only include ability types that carry description text
            for atype in ("other", "special", "wargear", "damaged", "primarch"):
                entries = abilities.get(atype)
                if not entries:
                    continue
                if isinstance(entries, dict):
                    # e.g. invul: {"value": "5+"}
                    continue
                for ab in entries:
                    name = (ab.get("name") or {}).get("en", "")
                    desc = (ab.get("description") or {}).get("en", "")
                    desc_md = gdc_to_markdown(desc)
                    if desc_md:
                        if name:
                            parts.append(f"**{name}**\n{desc_md}")
                        else:
                            parts.append(desc_md)
            return "\n\n".join(parts) if parts else None
    return None


def get_text(asset_key: str, asset_name: str, gdc_datasets: list[dict]) -> str | None:
    """Try all loaded GDC datasets for the best match."""
    prefix = asset_key.split("§")[0] + "§" if "§" in asset_key else ""

    for gdc in gdc_datasets:
        if prefix == "Detachment§":
            t = detachment_text(asset_name, gdc)
        elif prefix == "Enhancement§":
            t = enhancement_text(asset_name, gdc)
        else:
            # Unit§, Character§, Vehicle§, etc.
            t = unit_text(asset_name, gdc)
        if t:
            return t
    return None

# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_file(rz_stem: str, apply: bool) -> None:
    rz_path = RZ_11E_DIR / f"{rz_stem}.rulebook"
    if not rz_path.exists():
        return

    gdc_files = RZ_TO_GDC.get(rz_stem, [])
    if not gdc_files:
        print(f"  [SKIP] {rz_stem}: no GDC mapping")
        return

    gdc_datasets = []
    for gf in gdc_files:
        try:
            gdc_datasets.append(fetch_gdc(gf))
        except Exception as e:
            print(f"  [WARN] Could not fetch {gf}: {e}")

    if not gdc_datasets:
        print(f"  [SKIP] {rz_stem}: no GDC files loaded")
        return

    with open(rz_path) as f:
        rz_data = json.load(f)
    catalog = rz_data["rulebook"]["assetCatalog"]

    enriched = 0
    not_found = []

    for key, entry in catalog.items():
        text = entry.get("text", "")
        if not WARNING_RE.search(text):
            continue

        asset_name = key[key.index("§") + 1:] if "§" in key else key

        new_text = get_text(key, asset_name, gdc_datasets)
        if new_text:
            # Keep only the WARNING line itself, replace any previously appended text
            warning_line = text.split("\n")[0].rstrip()
            entry["text"] = warning_line + "\n\n" + new_text
            enriched += 1
            print(f"  [ENRICHED] {key}")
        else:
            not_found.append(key)

    if not_found:
        print(f"  [NOT FOUND] ({len(not_found)}):")
        for k in not_found:
            print(f"    {k}")

    print(f"  => {enriched} enriched, {len(not_found)} still text-less")

    if apply and enriched:
        if "revision" in rz_data:
            rz_data["revision"] = "11.0.4"
        with open(rz_path, "w") as f:
            json.dump(rz_data, f, indent=2, ensure_ascii=False)
        print(f"  [APPLIED] Written to {rz_path.name} (revision -> 11.0.4)")


def main() -> None:
    args = sys.argv[1:]
    apply = "--apply" in args
    args = [a for a in args if a != "--apply"]

    faction_filter = None
    if "--faction" in args:
        idx = args.index("--faction")
        faction_filter = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if faction_filter:
        stem = SLUG_TO_FILE.get(faction_filter)
        if stem is None:
            sys.exit(f"Unknown faction slug: {faction_filter!r}")
        stems = [stem]
    else:
        stems = list(RZ_TO_GDC.keys())

    for stem in stems:
        rz_path = RZ_11E_DIR / f"{stem}.rulebook"
        if not rz_path.exists():
            continue
        print(f"\n=== {stem} ===")
        process_file(stem, apply)

    if not apply:
        print("\n(dry-run — pass --apply to write changes)")


if __name__ == "__main__":
    main()
