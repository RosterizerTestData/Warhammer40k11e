#!/usr/bin/env python3
"""
mfm_update.py — Update Rz 11e rulebook files from BSData MFM YAML data.

Usage:
    python3 mfm_update.py <faction_slug> [--apply]

    faction_slug: matches data/<slug>.yaml in the MFM repo
                  and <FactionName>.rulebook in the Rz 11e dir.
                  Use 'orks' for Orks, 'space-marines' for Space Marines, etc.

    --apply: write changes back to the rulebook file (default: dry-run only)

MFM repo must be cloned at /tmp/wh40k-11e-mfm/ or set MFM_DIR env var.
Pinned commit: 23fae956001ba2a7cb390309b6a60269d9c38c8c

Transformations applied:
  §4.1  Unit cost table (pricier tier -> Rz ranked cost stat)
  §4.2  unitDiscount + unitDiscountThreshold
  §4.3  Leader attachTo values
  §4.4  Detachment DP, Role keyword, enhancements (points)
  §5    Gap handling: new MFM items get WARNING text; Rz-only items left untouched

Enhancement keyword fix:
  Enhancements whose keywords.Keywords detachment name no longer matches any
  MFM detachment for this faction have that Keywords property removed, so they
  stop appearing under detachments they don't belong to. MFM-matched
  enhancements keep (or gain) the correct detachment keyword.
"""

import json
import os
import sys
import re
import unicodedata
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml  (or use miniconda python)")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
RZ_11E_DIR = Path(__file__).parent.parent.parent  # .../Warhammer40k11e/
MFM_DIR = Path(os.environ.get("MFM_DIR", "/tmp/wh40k-11e-mfm"))

# All catalog prefixes that represent a playable unit (not weapons/abilities/etc.)
UNIT_PREFIXES = ("Unit§", "Character§", "Vehicle§", "Infantry/Mounted§", "Titan§")

WARNING_UNIT = "WARNING: this unit was not in 10e and needs importing."
WARNING_DETACHMENT = "WARNING: this detachment was not in 10e and needs importing."
WARNING_ENHANCEMENT = "WARNING: this enhancement was not in 10e and needs importing."

# ---------------------------------------------------------------------------
# Name normalisation for fuzzy matching
# ---------------------------------------------------------------------------
def fix_mfm_name(name: str) -> str:
    """Fix known capitalisation bugs in MFM source data before using as a key.
    e.g. "The Phaeron’S Armoury" -> "The Phaeron’s Armoury"
    """
    # Lowercase any letter immediately following an apostrophe (any variant, including U+0027)
    return re.sub(r"([\x27‘’‚‛`])([A-Z])", lambda m: m.group(1) + m.group(2).lower(), name)


def normalise(name: str) -> str:
    """Case-fold, strip, collapse whitespace, normalise punctuation."""
    s = name.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[‘’’‚‛`]", "", s)   # apostrophes
    s = re.sub(r"&", "and", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_match_map(rz_names: list[str], mfm_names: list[str]) -> tuple[dict, list, list]:
    """
    Returns (matched, rz_only, mfm_only).
    matched: {mfm_name: rz_name}
    """
    rz_norm = {normalise(n): n for n in rz_names}
    mfm_norm = {normalise(n): n for n in mfm_names}

    matched = {}
    for norm, mfm_name in mfm_norm.items():
        if norm in rz_norm:
            matched[mfm_name] = rz_norm[norm]

    matched_rz = set(matched.values())
    matched_mfm = set(matched.keys())
    rz_only = [n for n in rz_names if n not in matched_rz]
    mfm_only = [n for n in mfm_names if n not in matched_mfm]
    return matched, rz_only, mfm_only


# ---------------------------------------------------------------------------
# Discount validation
# ---------------------------------------------------------------------------
def compute_discount(cheaper_costs: list[dict], pricier_costs: list[dict]) -> Optional[int]:
    """
    Returns the constant per-unit discount (pricier - cheaper) if it's
    uniform across all shared model counts, else None.
    """
    cheaper_map = {c["models"]: c["points"] for c in cheaper_costs}
    pricier_map = {c["models"]: c["points"] for c in pricier_costs}
    shared = set(cheaper_map) & set(pricier_map)
    if not shared:
        return None
    discounts = {pricier_map[m] - cheaper_map[m] for m in shared}
    if len(discounts) != 1:
        return None
    return discounts.pop()


def threshold_from_range(range_str: str) -> int:
    """'[1,2)' or '[1,2]' -> upper bound as int. '[1,)' -> 0 (open = no threshold)."""
    m = re.match(r"\[1,(\d+)[)\]]", range_str)
    if m:
        return int(m.group(1))
    return 0


# ---------------------------------------------------------------------------
# Rz cost stat helpers
# ---------------------------------------------------------------------------
# Rz stores costs as a set of parallel numeric stats on the unit:
#   Points          — base cost at the first (smallest) model count (pricier tier)
#   model1stTally   — the first/smallest legal model count
#   model2ndCost    — cost at second model count
#   model2ndTally   — second model count
#   model3rdCost / model3rdTally, model4thCost / model4thTally, ...
#
# unitDiscount / unitDiscountThreshold handle the two-tier pricing inversion.

ORDINALS = ["1st", "2nd", "3rd", "4th", "5th", "6th"]


def has_cost_stats(unit: dict) -> bool:
    stats = unit.get("stats", {})
    return "Points" in stats and "model1stTally" in stats


def is_flat_cost_unit(unit: dict) -> bool:
    """Vehicles and single-model units: have Points but no model1stTally."""
    stats = unit.get("stats", {})
    return "Points" in stats and "model1stTally" not in stats


def read_rz_costs(stats: dict) -> list[tuple[int, int]]:
    """Return [(models, points), ...] in order from the existing Rz stats."""
    first_models = stats["model1stTally"]["value"]
    first_points = stats["Points"]["value"]
    result = [(first_models, first_points)]
    for i, ord_ in enumerate(ORDINALS[1:], 2):
        tally_key = f"model{ord_}Tally"
        cost_key = f"model{ord_}Cost"
        if tally_key in stats and cost_key in stats:
            result.append((stats[tally_key]["value"], stats[cost_key]["value"]))
        else:
            break
    return result


def write_rz_costs(stats: dict, costs: list[tuple[int, int]]) -> None:
    """
    Write [(models, points), ...] into Rz stats in place.
    Preserves all existing stat properties (hidden, visibility, etc.),
    only updating 'value'. Removes stale higher-ordinal pairs.
    """
    # First entry: model1stTally + Points
    stats["model1stTally"]["value"] = costs[0][0]
    stats["Points"]["value"] = costs[0][1]

    # Subsequent entries
    for i, (models, points) in enumerate(costs[1:], 2):
        ord_ = ORDINALS[i - 1]
        tally_key = f"model{ord_}Tally"
        cost_key = f"model{ord_}Cost"
        if tally_key in stats:
            stats[tally_key]["value"] = models
        else:
            stats[tally_key] = {"value": models}
        if cost_key in stats:
            stats[cost_key]["value"] = points
        else:
            stats[cost_key] = {"value": points}

    # Remove any stale higher-ordinal pairs beyond what MFM provides
    for i in range(len(costs) + 1, len(ORDINALS) + 1):
        ord_ = ORDINALS[i - 1]
        stats.pop(f"model{ord_}Tally", None)
        stats.pop(f"model{ord_}Cost", None)


# ---------------------------------------------------------------------------
# Core transformation functions
# ---------------------------------------------------------------------------
def update_unit_costs(rz_unit: dict, mfm_unit: dict, unit_name: str) -> list[str]:
    """Apply §4.1 and §4.2. Returns list of warning messages."""
    warnings = []
    pricing = mfm_unit.get("pricing", [])
    if not pricing:
        warnings.append(f"  [SKIP] {unit_name}: no pricing in MFM")
        return warnings

    pricier = pricing[-1]
    cheaper = pricing[0] if len(pricing) == 2 else None

    # §4.2 discount
    if cheaper:
        discount = compute_discount(cheaper["costs"], pricier["costs"])
        if discount is None:
            warnings.append(f"  [ABORT] {unit_name}: non-constant discount — skipping cost update")
            return warnings
        threshold = threshold_from_range(cheaper.get("range", "[1,2]")) + 1
    else:
        discount = 0
        threshold = 0

    # §4.1 write costs
    # Seed Points on stubs that have no stats yet so the flat-cost path can write
    if "stats" not in rz_unit:
        rz_unit["stats"] = {}
    if "Points" not in rz_unit.get("stats", {}) and "model1stTally" not in rz_unit.get("stats", {}):
        rz_unit["stats"]["Points"] = {"value": 0}
    stats = rz_unit["stats"]
    if has_cost_stats(rz_unit):
        mfm_costs = [(c["models"], c["points"]) for c in pricier["costs"]]
        write_rz_costs(stats, mfm_costs)
    elif is_flat_cost_unit(rz_unit):
        # Vehicle / single-model: flat Points, plus discount stats if two-tier
        flat_points = pricier["costs"][0]["points"]
        stats["Points"]["value"] = flat_points
        if cheaper:
            flat_discount = flat_points - cheaper["costs"][0]["points"]
            flat_threshold = threshold_from_range(cheaper.get("range", "[1,2]")) + 1
            if "unitDiscount" in stats:
                stats["unitDiscount"]["value"] = flat_discount
            elif flat_discount != 0:
                stats["unitDiscount"] = {"value": flat_discount}
            if "unitDiscountThreshold" in stats:
                stats["unitDiscountThreshold"]["value"] = flat_threshold
            elif flat_threshold != 0:
                stats["unitDiscountThreshold"] = {"value": flat_threshold}
    else:
        warnings.append(f"  [SKIP] {unit_name}: no cost stats (Points/model1stTally) found in Rz")
        return warnings

    # §4.2 write discount stats
    if "unitDiscount" in stats:
        stats["unitDiscount"]["value"] = discount
    elif discount != 0:
        stats["unitDiscount"] = {"value": discount}

    if "unitDiscountThreshold" in stats:
        stats["unitDiscountThreshold"]["value"] = threshold
    elif threshold != 0:
        stats["unitDiscountThreshold"] = {"value": threshold}

    # Clear discount if single-tier
    if discount == 0 and "unitDiscount" in stats:
        stats["unitDiscount"]["value"] = 0
    if threshold == 0 and "unitDiscountThreshold" in stats:
        stats["unitDiscountThreshold"]["value"] = 0

    return warnings


def update_leader(rz_unit: dict, mfm_unit: dict) -> None:
    """§4.3 — overwrite attachTo list on the unit's leader stat/aspect if present."""
    attach_to = mfm_unit.get("attachTo", [])
    if not attach_to:
        return
    # Rz stores attachTo as allowed items or as a stat — look for 'Leader' stat or aspects
    stats = rz_unit.get("stats", {})
    if "Leader" in stats:
        stats["Leader"]["ranks"] = {name: {"order": i} for i, name in enumerate(attach_to)}
    # Also update allowed items if that's how it's modelled
    allowed = rz_unit.get("allowed", {}).get("items", [])
    if allowed:
        # Replace Unit§ entries with new attach_to list; preserve non-Unit§ items
        non_unit = [i for i in allowed if not (isinstance(i, str) and i.startswith("Unit§"))
                    and not (isinstance(i, dict) and i.get("item", "").startswith("Unit§"))]
        new_units = [f"Unit§{n}" for n in attach_to]
        rz_unit["allowed"]["items"] = non_unit + new_units


def update_detachment(rz_det: dict, mfm_det: dict) -> list[str]:
    """§4.4 — DP, Role, enhancement points."""
    warnings = []

    # DP
    dp = mfm_det.get("dp")
    if dp is not None:
        rz_det.setdefault("stats", {}).setdefault("Detachment Points", {})["value"] = dp

    # Role keyword
    objective = mfm_det.get("objective", "")
    if objective:
        role_val = objective.title()
        rz_det.setdefault("keywords", {})["Role"] = [role_val]

    return warnings


def update_enhancement_keywords(catalog: dict, mfm_detachments: list[dict]) -> list[str]:
    """
    For each Enhancement§ in the catalog:
    - If its keywords.Keywords detachment name matches an MFM detachment -> keep/set it.
    - If it has a detachment keyword that is NOT in the MFM for this faction -> remove Keywords.
    - If it has no Keywords -> leave alone.

    Also ensures MFM-matched enhancements have the correct detachment keyword.
    """
    warnings = []
    mfm_det_names = {d["name"] for d in mfm_detachments}
    # Build MFM enhancement -> detachment map
    mfm_enh_to_det: dict[str, str] = {}
    for det in mfm_detachments:
        for enh in det.get("enhancements", []):
            mfm_enh_to_det[normalise(enh["name"])] = det["name"]

    enhancements = {k: v for k, v in catalog.items() if k.startswith("Enhancement§")}
    for key, enh in enhancements.items():
        enh_name = key.replace("Enhancement§", "")
        norm = normalise(enh_name)
        kws = enh.get("keywords", {})
        current_kw_list = kws.get("Keywords", None)  # None = key absent

        # Also try matching with parenthetical suffixes stripped before normalising
        enh_name_stripped = re.sub(r"\s*\(.*?\)\s*$", "", enh_name).strip()
        norm_stripped = normalise(enh_name_stripped)
        matched_det = mfm_enh_to_det.get(norm) or mfm_enh_to_det.get(norm_stripped)

        if matched_det:
            # MFM-matched: ensure correct detachment keyword is set
            enh.setdefault("keywords", {})["Keywords"] = [matched_det]
        elif current_kw_list is not None:
            # Has Keywords but not in MFM — check if any of those keywords is a known MFM det
            stale = [k for k in current_kw_list if k in mfm_det_names]
            if stale:
                # Remove Keywords entirely — Rz-only enhancement, no detachment
                del enh["keywords"]["Keywords"]
                if not enh["keywords"]:
                    del enh["keywords"]
                warnings.append(f"  [FIXED] Enhancement§{enh_name}: removed stale keyword(s) {stale}")

    return warnings


# ---------------------------------------------------------------------------
# Gap handling §5
# ---------------------------------------------------------------------------
def create_stub_unit(mfm_unit: dict) -> dict:
    pricing = mfm_unit.get("pricing", [])
    pricier = pricing[-1] if pricing else None
    points = pricier["costs"][0]["points"] if pricier else 0
    return {
        "stats": {"Points": {"value": points}},
        "text": WARNING_UNIT,
    }


def create_stub_detachment(mfm_det: dict) -> dict:
    return {
        "keywords": {"Role": []},
        "stats": {"Detachment Points": {"value": mfm_det.get("dp", 0)}},
        "text": WARNING_DETACHMENT,
    }


def create_stub_enhancement(mfm_enh: dict, det_name: str) -> dict:
    return {
        "keywords": {"Keywords": [det_name]},
        "stats": {"Points": {"value": mfm_enh.get("points", 0)}},
        "text": WARNING_ENHANCEMENT,
    }


# Slug -> Rz file stem mapping
SLUG_TO_FILE = {
    "orks": "Orks",
    "space-marines": "Space_Marines",
    "aeldari": "Aeldari",
    "astra-militarum": "Astra_Militarum",
    "adeptus-mechanicus": "Adeptus_Mechanicus",
    "adeptus-custodes": "Adeptus_Custodes",
    "adepta-sororitas": "Adepta_Sororitas",
    "chaos-space-marines": "Chaos_Space_Marines",
    "chaos-daemons": "Chaos_Daemons",
    "chaos-knights": "Chaos_Knights",
    "death-guard": "Death_Guard",
    "drukhari": "Drukhari",
    "emperors-children": "Emperor's_Children",
    "genestealer-cults": "Genestealer_Cults",
    "grey-knights": "Grey_Knights",
    "imperial-agents": "Imperial_Agents",
    "imperial-knights": "Imperial_Knights",
    "leagues-of-votann": "Leagues_of_Votann",
    "necrons": "Necrons",
    "tau-empire": "T'au_Empire",
    "thousand-sons": "Thousand_Sons",
    "tyranids": "Tyranids",
    "world-eaters": "World_Eaters",
    "black-templars": "SM_Black_Templars",
    "blood-angels": "SM_Blood_Angels",
    "dark-angels": "SM_Dark_Angels",
    "deathwatch": "SM_Deathwatch",
    "space-wolves": "SM_Space_Wolves",
    "titan-legions": "Adeptus_Titanicus",
    "chaos-titan-legions": "Titanicus_Traitoris",
    # Module rulebooks sourced from multiple MFM slugs
    "chaos-module": "Chaos",
    "imperium-module": "Imperium",
    "harlequins-module": "Harlequins",
}

# Faction dependency map: Rz file stem -> list of Rz file stems whose catalogs
# also contain units for this faction (e.g. SM chapters share Space_Marines.rulebook)
FACTION_DEPS = {
    "SM_Black_Templars": ["Space_Marines"],
    "SM_Blood_Angels":   ["Space_Marines"],
    "SM_Dark_Angels":    ["Space_Marines"],
    "SM_Deathwatch":     ["Space_Marines"],
    "SM_Space_Wolves":   ["Space_Marines"],
    "Grey_Knights":      ["Space_Marines"],
}

# Module rulebooks: Rz file stem -> list of MFM slugs to draw units from
MODULE_SOURCES = {
    "Chaos": ["chaos-daemons", "chaos-knights", "chaos-space-marines", "chaos-titan-legions",
              "death-guard", "emperors-children", "thousand-sons", "world-eaters"],
    "Imperium": ["imperial-agents", "imperial-knights", "adeptus-mechanicus",
                 "adeptus-custodes", "adepta-sororitas", "grey-knights", "deathwatch",
                 "space-marines", "astra-militarum"],
    "Harlequins": ["aeldari"],
}


def load_mfm(slug: str) -> dict:
    mfm_path = MFM_DIR / "data" / f"{slug}.yaml"
    if not mfm_path.exists():
        sys.exit(f"MFM file not found: {mfm_path}")
    with open(mfm_path) as f:
        return yaml.safe_load(f)


def merge_mfm_units(slugs: list[str]) -> dict:
    """Merge units from multiple MFM slugs into one name->unit map (last writer wins on collision)."""
    merged = {}
    for slug in slugs:
        data = load_mfm(slug)
        for u in data.get("units", []):
            merged[u["name"]] = u
    return merged


# Reverse map: faction file stem -> which module file stems include it as a source
_MODULE_REVERSE: dict[str, list[str]] = {}
for _mod, _slugs in MODULE_SOURCES.items():
    for _slug in _slugs:
        _stem = SLUG_TO_FILE.get(_slug)
        if _stem:
            _MODULE_REVERSE.setdefault(_stem, []).append(_mod)


def unit_exists_in_source_faction(rz_name: str, module_stem: str) -> bool:
    """
    Return True if a populated entry for rz_name exists in any of the source
    faction rulebooks for this module.
    """
    source_slugs = MODULE_SOURCES.get(module_stem, [])
    norm = normalise(rz_name)
    for slug in source_slugs:
        faction_stem = SLUG_TO_FILE.get(slug)
        if not faction_stem:
            continue
        faction_path = RZ_11E_DIR / f"{faction_stem}.rulebook"
        if not faction_path.exists():
            continue
        with open(faction_path) as f:
            faction_data = json.load(f)
        faction_cat = faction_data["rulebook"]["assetCatalog"]
        for prefix in UNIT_PREFIXES:
            match = next(
                (k for k in faction_cat
                 if k.startswith(prefix) and
                 normalise(k.replace(prefix, "")) == norm),
                None
            )
            if match:
                entry = faction_cat[match]
                if "Points" in entry.get("stats", {}):
                    return True
    return False


def unit_exists_in_module(rz_name: str, faction_stem: str) -> bool:
    """
    Return True if a populated (non-stub) entry for rz_name already exists
    in any module rulebook that sources from this faction.
    Strips trailing parentheticals (e.g. "(Agents)") before comparing.
    """
    module_stems = _MODULE_REVERSE.get(faction_stem, [])
    for mod_stem in module_stems:
        mod_path = RZ_11E_DIR / f"{mod_stem}.rulebook"
        if not mod_path.exists():
            continue
        with open(mod_path) as f:
            mod_data = json.load(f)
        mod_cat = mod_data["rulebook"]["assetCatalog"]
        norm = normalise(rz_name)
        for prefix in UNIT_PREFIXES:
            for k in mod_cat:
                if not k.startswith(prefix):
                    continue
                raw = k[len(prefix):]
                stripped = re.sub(r"\s*\(.*?\)\s*$", "", raw).strip()
                if normalise(raw) == norm or normalise(stripped) == norm:
                    entry = mod_cat[k]
                    if "Points" in entry.get("stats", {}):
                        return True
    return False


def det_exists_in_dep(mfm_name: str, faction_stem: str) -> bool:
    """Return True if a Detachment§ entry exists in any dependency rulebook."""
    dep_stems = FACTION_DEPS.get(faction_stem, [])
    norm = normalise(mfm_name)
    for dep_stem in dep_stems:
        dep_path = RZ_11E_DIR / f"{dep_stem}.rulebook"
        if not dep_path.exists():
            continue
        with open(dep_path) as f:
            dep_data = json.load(f)
        dep_cat = dep_data["rulebook"]["assetCatalog"]
        if any(normalise(k.replace("Detachment§", "")) == norm
               for k in dep_cat if k.startswith("Detachment§")):
            return True
    return False


def unit_exists_in_dep(rz_name: str, faction_stem: str) -> bool:
    """
    Return True if a populated entry for rz_name exists in any dependency
    rulebook for this faction (e.g. Space_Marines for SM chapters).
    """
    dep_stems = FACTION_DEPS.get(faction_stem, [])
    norm = normalise(rz_name)
    for dep_stem in dep_stems:
        dep_path = RZ_11E_DIR / f"{dep_stem}.rulebook"
        if not dep_path.exists():
            continue
        with open(dep_path) as f:
            dep_data = json.load(f)
        dep_cat = dep_data["rulebook"]["assetCatalog"]
        for prefix in UNIT_PREFIXES:
            match = next(
                (k for k in dep_cat
                 if k.startswith(prefix) and
                 normalise(k[len(prefix):]) == norm),
                None
            )
            if match:
                entry = dep_cat[match]
                if "Points" in entry.get("stats", {}):
                    return True
    return False


# ---------------------------------------------------------------------------
# Main per-faction update
# ---------------------------------------------------------------------------
def update_faction(mfm_slug: str, apply: bool = False) -> None:
    rz_file_stem = SLUG_TO_FILE.get(mfm_slug)
    if rz_file_stem is None:
        sys.exit(f"No Rz file mapping for slug '{mfm_slug}'. Add it to SLUG_TO_FILE.")

    is_module = rz_file_stem in MODULE_SOURCES

    if is_module:
        # Module: no single MFM file; merge units from all source slugs
        mfm_data = {"name": rz_file_stem, "units": [], "detachments": []}
        mfm_unit_map = merge_mfm_units(MODULE_SOURCES[rz_file_stem])
    else:
        mfm_data = load_mfm(mfm_slug)
        mfm_unit_map = {u["name"]: u for u in mfm_data.get("units", [])}

    faction_name = mfm_data["name"]
    print(f"\n{'='*60}")
    print(f"Faction: {faction_name} (slug: {mfm_slug})")
    print(f"{'='*60}")

    rz_path = RZ_11E_DIR / f"{rz_file_stem}.rulebook"
    if not rz_path.exists():
        sys.exit(f"Rz rulebook not found: {rz_path}")

    with open(rz_path) as f:
        rz_data = json.load(f)

    catalog = rz_data["rulebook"]["assetCatalog"]

    # -----------------------------------------------------------------------
    # UNITS
    # -----------------------------------------------------------------------
    print("\n--- Units ---")
    rz_unit_names = [
        k[len(p):]
        for k in catalog
        for p in UNIT_PREFIXES
        if k.startswith(p)
    ]
    mfm_unit_names = list(mfm_unit_map.keys())

    unit_matched, unit_rz_only, unit_mfm_only = build_match_map(rz_unit_names, mfm_unit_names)

    print(f"  Matched: {len(unit_matched)}  Rz-only: {len(unit_rz_only)}  MFM-only: {len(unit_mfm_only)}")
    if unit_rz_only:
        print(f"  Rz-only (untouched): {unit_rz_only}")
    if unit_mfm_only:
        print(f"  MFM-only (new stubs): {unit_mfm_only}")

    all_warnings = []
    for mfm_name, rz_name in unit_matched.items():
        mfm_unit = mfm_unit_map[mfm_name]
        # Find the actual key under any unit-like prefix
        rz_key = next(
            (k for k in catalog
             for p in UNIT_PREFIXES
             if k.startswith(p) and normalise(k[len(p):]) == normalise(rz_name)),
            None
        )
        if rz_key is None:
            all_warnings.append(f"  [SKIP] {rz_name}: not found under any unit prefix")
            continue
        # For module rulebooks: if this entry is a stub and the unit is fully
        # populated in a source faction file, remove the stub and skip.
        if is_module and unit_exists_in_source_faction(mfm_name, rz_file_stem):
            rz_unit = catalog[rz_key]
            if "WARNING" in rz_unit.get("text", ""):
                del catalog[rz_key]
                all_warnings.append(f"  [PURGED] {rz_key}: stub removed, unit lives in source faction")
                continue
        # For chapter files: if this entry is a stub and the unit lives in a dep, remove it.
        if not is_module and unit_exists_in_dep(mfm_name, rz_file_stem):
            rz_unit = catalog[rz_key]
            if "WARNING" in rz_unit.get("text", ""):
                del catalog[rz_key]
                all_warnings.append(f"  [PURGED] {rz_key}: stub removed, unit lives in dependency rulebook")
                continue
        # For any non-module faction: if this entry is a stub and the unit lives in
        # a module that sources this faction, remove the stub (e.g. IA units in Imperium).
        if not is_module and unit_exists_in_module(mfm_name, rz_file_stem):
            rz_unit = catalog[rz_key]
            if "WARNING" in rz_unit.get("text", ""):
                del catalog[rz_key]
                all_warnings.append(f"  [PURGED] {rz_key}: stub removed, unit lives in module rulebook")
                continue
        rz_unit = catalog[rz_key]
        # Clean up stale stub cruft (aspects/keywords added by old stub format)
        if "WARNING" in rz_unit.get("text", ""):
            rz_unit.pop("aspects", None)
            rz_unit.pop("keywords", None)
        w = update_unit_costs(rz_unit, mfm_unit, rz_name)
        all_warnings.extend(w)
        update_leader(rz_unit, mfm_unit)

    # New units from MFM
    for mfm_name in unit_mfm_only:
        key = f"Unit§{fix_mfm_name(mfm_name)}"
        # Check if already exists under any unit-like prefix (case-insensitive)
        existing_key = next(
            (k for k in catalog
             for p in UNIT_PREFIXES
             if k.startswith(p) and normalise(k[len(p):]) == normalise(mfm_name)),
            None
        )
        if existing_key:
            all_warnings.append(f"  [EXISTS] {key}: already present as {existing_key} — skipping stub")
        elif key not in catalog:
            if not is_module and unit_exists_in_module(mfm_name, rz_file_stem):
                all_warnings.append(f"  [MODULE] {key}: populated in module rulebook — skipping stub")
            elif is_module and unit_exists_in_source_faction(mfm_name, rz_file_stem):
                all_warnings.append(f"  [SOURCE] {key}: populated in source faction rulebook — skipping stub")
            elif not is_module and unit_exists_in_dep(mfm_name, rz_file_stem):
                all_warnings.append(f"  [DEP] {key}: populated in dependency rulebook — skipping stub")
            else:
                catalog[key] = create_stub_unit(mfm_unit_map[mfm_name])
                all_warnings.append(f"  [NEW] {key}: stub created")

    # -----------------------------------------------------------------------
    # DETACHMENTS
    # -----------------------------------------------------------------------
    print("\n--- Detachments ---")
    mfm_dets = mfm_data.get("detachments", [])
    rz_det_names = [k.replace("Detachment§", "") for k in catalog if k.startswith("Detachment§")]
    mfm_det_names_list = [d["name"] for d in mfm_dets]
    mfm_det_map = {d["name"]: d for d in mfm_dets}

    det_matched, det_rz_only, det_mfm_only = build_match_map(rz_det_names, mfm_det_names_list)

    print(f"  Matched: {len(det_matched)}  Rz-only: {len(det_rz_only)}  MFM-only: {len(det_mfm_only)}")
    if det_rz_only:
        print(f"  Rz-only (untouched): {det_rz_only}")
    if det_mfm_only:
        print(f"  MFM-only (new stubs): {det_mfm_only}")

    for mfm_name, rz_name in det_matched.items():
        mfm_det = mfm_det_map[mfm_name]
        rz_key = f"Detachment§{rz_name}"
        # For chapter files: purge detachment stubs that live in the dep rulebook
        if not is_module and det_exists_in_dep(mfm_name, rz_file_stem):
            rz_det_entry = catalog[rz_key]
            if "WARNING" in rz_det_entry.get("text", ""):
                del catalog[rz_key]
                all_warnings.append(f"  [PURGED] {rz_key}: stub removed, detachment lives in dependency rulebook")
                continue
        rz_det = catalog[rz_key]
        w = update_detachment(rz_det, mfm_det)
        all_warnings.extend(w)

        # Enhancements within this detachment
        mfm_enhs = mfm_det.get("enhancements", [])
        mfm_enh_names = [e["name"] for e in mfm_enhs]
        mfm_enh_map = {e["name"]: e for e in mfm_enhs}
        rz_enh_names = [
            k.replace("Enhancement§", "")
            for k, v in catalog.items()
            if k.startswith("Enhancement§")
            and rz_name in v.get("keywords", {}).get("Keywords", [])
        ]
        # Match enhancements: try exact normalised name first, then strip trailing
        # parentheticals (e.g. "(Aura)") from Rz names before normalising.
        mfm_enh_norms = {normalise(n) for n in mfm_enh_names}
        for k in catalog:
            if not k.startswith("Enhancement§"):
                continue
            enh_raw = k.replace("Enhancement§", "")
            if enh_raw in rz_enh_names:
                continue
            stripped = re.sub(r"\s*\(.*?\)\s*$", "", enh_raw).strip()
            if normalise(stripped) in mfm_enh_norms:
                rz_enh_names.append(enh_raw)

        # Custom match: build rz_norm using stripped form so "(Aura)" variants match MFM names
        rz_norm_enh = {}
        for n in rz_enh_names:
            stripped = re.sub(r"\s*\(.*?\)\s*$", "", n).strip()
            norm = normalise(stripped)
            if norm not in rz_norm_enh:
                rz_norm_enh[norm] = n
        mfm_norm_enh = {normalise(n): n for n in mfm_enh_names}
        enh_matched = {}
        for norm, mfm_name in mfm_norm_enh.items():
            if norm in rz_norm_enh:
                enh_matched[mfm_name] = rz_norm_enh[norm]
        matched_rz_enh = set(enh_matched.values())
        matched_mfm_enh = set(enh_matched.keys())
        enh_rz_only = [n for n in rz_enh_names if n not in matched_rz_enh]
        enh_mfm_only = [n for n in mfm_enh_names if n not in matched_mfm_enh]

        if enh_rz_only:
            all_warnings.append(f"  [NOTE] Det '{rz_name}' Rz-only enhancements (untouched): {enh_rz_only}")
        for mfm_enh_name, rz_enh_name in enh_matched.items():
            mfm_enh = mfm_enh_map[mfm_enh_name]
            rz_key = f"Enhancement§{rz_enh_name}"
            if rz_key not in catalog:
                # Rz name may have a trailing parenthetical (e.g. "(Aura)") — find actual key
                rz_key = next(
                    (k for k in catalog if k.startswith("Enhancement§") and
                     normalise(re.sub(r"\s*\(.*?\)\s*$", "", k.replace("Enhancement§", ""))) == normalise(mfm_enh_name)),
                    None
                )
            if rz_key:
                target_key = f"Enhancement§{fix_mfm_name(mfm_enh_name)}"
                if rz_key != target_key:
                    # Rename: move entry to the MFM-correct key name
                    catalog[target_key] = catalog.pop(rz_key)
                    all_warnings.append(f"  [RENAME] {rz_key} -> {target_key}")
                    rz_key = target_key
                rz_enh = catalog[rz_key]
                rz_enh.setdefault("stats", {}).setdefault("Points", {})["value"] = mfm_enh["points"]
        for mfm_enh_name in enh_mfm_only:
            key = f"Enhancement§{fix_mfm_name(mfm_enh_name)}"
            if key not in catalog:
                catalog[key] = create_stub_enhancement(mfm_enh_map[mfm_enh_name], rz_name)
                all_warnings.append(f"  [NEW] {key}: stub created")

    for mfm_name in det_mfm_only:
        key = f"Detachment§{fix_mfm_name(mfm_name)}"
        if key not in catalog:
            if not is_module and det_exists_in_dep(mfm_name, rz_file_stem):
                all_warnings.append(f"  [DEP] {key}: exists in dependency rulebook — skipping stub")
            else:
                catalog[key] = create_stub_detachment(mfm_det_map[mfm_name])
                all_warnings.append(f"  [NEW] {key}: stub created")

    # -----------------------------------------------------------------------
    # KEY PUNCTUATION CLEANUP
    # Rename any catalog key that has capitalisation bugs from MFM source data
    # (e.g. "Detachment§The Phaeron'S Armoury" -> "...Phaeron's Armoury")
    # -----------------------------------------------------------------------
    for old_key in list(catalog.keys()):
        fixed = re.sub(r"§", "§", old_key)  # keep prefix separator intact
        # Fix apostrophe + uppercase: Emperor'S -> Emperor's
        prefix_end = old_key.index("§") + 1 if "§" in old_key else 0
        prefix = old_key[:prefix_end]
        name_part = old_key[prefix_end:]
        fixed_name = fix_mfm_name(name_part)
        new_key = prefix + fixed_name
        if new_key != old_key and new_key not in catalog:
            catalog[new_key] = catalog.pop(old_key)
            all_warnings.append(f"  [KEY-FIX] {old_key!r} -> {new_key!r}")

    # -----------------------------------------------------------------------
    # ENHANCEMENT KEYWORD CLEANUP
    # -----------------------------------------------------------------------
    print("\n--- Enhancement keyword cleanup ---")
    w = update_enhancement_keywords(catalog, mfm_dets)
    all_warnings.extend(w)
    for line in w:
        print(line)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    if all_warnings:
        print("\n--- Warnings / Notes ---")
        for w in all_warnings:
            print(w)

    # -----------------------------------------------------------------------
    # Write or dry-run
    # -----------------------------------------------------------------------
    if apply:
        out_path = rz_path
        with open(out_path, "w") as f:
            json.dump(rz_data, f, indent=2, ensure_ascii=False)
        print(f"\n[APPLIED] Written to {out_path}")
    else:
        out_path = Path(f"/tmp/{rz_file_stem}_mfm_updated.rulebook")
        with open(out_path, "w") as f:
            json.dump(rz_data, f, indent=2, ensure_ascii=False)
        print(f"\n[DRY-RUN] Preview written to {out_path}")
        print("  Re-run with --apply to write in place.")


# ---------------------------------------------------------------------------
# Normalisation (matches Rosterizer port window export)
#
# Top-level key order from rulebookHistOrder (constants.ts):
#   name, revision, game, genre, publisher, url, notes, wip, isModule,
#   dependencies, rulebook
#
# rulebook sub-key order (modal-port.component.ts line 234):
#   assetTaxonomy, assetCatalog, gameModes, theme
#
# assetCatalog and assetTaxonomy: top-level keys shallow-sorted
# case-insensitively (helpers.orderObject, line 126).
# All other key order left as-is.
# Export: JSON.stringify(fieldJson, null, 2)
# ---------------------------------------------------------------------------
RULEBOOK_HIST_ORDER = [
    "name", "revision", "game", "genre", "publisher", "url", "notes",
    "wip", "isModule", "dependencies", "rulebook",
]
RULEBOOK_SUB_ORDER = ["assetTaxonomy", "assetCatalog", "gameModes", "theme"]


import locale as _locale
_locale.setlocale(_locale.LC_ALL, "")


def shallow_sort_keys(obj: dict) -> dict:
    """Shallow locale-aware key sort matching JS localeCompare / helpers.orderObject."""
    if isinstance(obj, dict):
        return {k: obj[k] for k in sorted(obj.keys(), key=lambda k: _locale.strxfrm(k.lower()))}
    return obj


def order_object_recurse(obj, seen=None):
    """
    Mirrors helpers.orderObjectRecurse: recursively shallow-sort all object
    keys. Arrays are left in order; 'ranks' keys are not sorted (handled
    separately by Rz). Matches the hash/export path used by Rosterizer.
    """
    if seen is None:
        seen = set()
    if not isinstance(obj, (dict, list)):
        return obj
    obj_id = id(obj)
    if obj_id in seen:
        return obj
    seen.add(obj_id)
    if isinstance(obj, list):
        result = [order_object_recurse(v, seen) for v in obj]
    else:
        ordered = shallow_sort_keys(obj)
        result = {k: order_object_recurse(v, seen) for k, v in ordered.items()}
    seen.discard(obj_id)
    return result


def order_clean_entry(entry: dict) -> dict:
    """
    Mirrors helpers.orderClean applied to a single catalog entry:
      1. Delete designation / displayName
      2. Shallow-sort the entry itself
      3. Per property:
           stats, rules  -> orderObjectRecurse (recursive sort)
           aspects, assets, allowed -> orderObject (shallow sort)
           keywords, ranks -> left untouched
      4. Delete empty properties from the checked set
    """
    entry.pop("designation", None)
    entry.pop("displayName", None)
    entry = shallow_sort_keys(entry)
    for prop in ("aspects", "assets", "allowed", "stats", "rules"):
        if prop not in entry:
            continue
        if prop in ("stats", "rules"):
            entry[prop] = order_object_recurse(entry[prop])
        else:
            # aspects / assets / allowed: shallow sort only
            entry[prop] = shallow_sort_keys(entry[prop])
        # Delete if empty after sorting
        if not entry[prop]:
            del entry[prop]
    return entry


def reorder_keys(obj: dict, order: list) -> dict:
    """Reorder obj keys per order list; append any unrecognised keys after."""
    result = {}
    for k in order:
        if k in obj:
            result[k] = obj[k]
    for k in obj:
        if k not in result:
            result[k] = obj[k]
    return result


def normalize_rulebook(path: Path) -> None:
    with open(path) as f:
        data = json.load(f)

    # Top-level key order
    data = reorder_keys(data, RULEBOOK_HIST_ORDER)

    # rulebook sub-key order
    if "rulebook" in data:
        data["rulebook"] = reorder_keys(data["rulebook"], RULEBOOK_SUB_ORDER)
        # Sort catalog/taxonomy keys, then orderClean each entry
        for section in ("assetCatalog", "assetTaxonomy"):
            if section in data["rulebook"]:
                sec = shallow_sort_keys(data["rulebook"][section])
                data["rulebook"][section] = {
                    k: order_clean_entry(v) for k, v in sec.items()
                }

    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[NORMALIZED] {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if args[0] == "--normalize":
        # Normalize all rulebooks (or a specific one)
        targets = args[1:] if len(args) > 1 else [
            str(p) for p in RZ_11E_DIR.glob("*.rulebook")
        ]
        for t in targets:
            normalize_rulebook(Path(t))
    else:
        mfm_slug = args[0]
        apply = "--apply" in args
        update_faction(mfm_slug, apply=apply)
