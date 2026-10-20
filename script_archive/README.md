This directory contains scripts that were used to make bulk changes to rulebooks, in case they're ever needed again, but there's no expectation that they will be.
## 11e maintenance scripts (2026-10)
- `mfm_points.py` — hub-aware MFM points importer (units, enhancements, detachment DP/Role, wargear, Imperial Agents allied prices). `MFM_DIR=<clone of BSData/wh40k-11e-mfm> python3 mfm_points.py [--apply]`; idempotent; needs PyYAML.
- `detachment_tags.py` — generates detachment `unique:` tag exclusivity rules from the MFM. Re-run after adding detachments.
- `hoist_chaos.py` — one-time shared Chaos unit homogenisation (11.1.2); kept as the pattern for hub + per-faction overrides.
- `rzmerge.py` — Python port of Rosterizer's dependency merge + overlay generator, used to verify overrides.
- `rz_headless/run.ts` — runs v1.rosterizer's real pipeline on a test roster: bundle from `v1.rosterizer/client` with `../node_modules/esbuild/bin/esbuild run.ts --bundle --platform=node --format=esm --tsconfig=tsconfig.json --outfile=out.mjs`, then `node out.mjs <BookStem> roster.json` (Node 20+).
- `gdc_codex_refresh.py` — rebuilds a faction book's detachments (rule text, DP, Role, stratagem wiring, roster ranks), enhancements (text, detachment keyword, points, Upgrade tags, generated eligibility) and stratagems from game-datacards codex data; removes detachments/enhancements/stratagems the codex dropped; prints a REVIEW list of things needing hand authoring. `GDC_DIR=… MFM_DIR=… python3 gdc_codex_refresh.py <BookStem> <gdc.json> <mfm-slug> [--apply]`.
