# Cashback mappings

Bank rules live in `src/<label>-<cc>_<bank-id>/categories/*.json`.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for the source format and evidence requirements.
Keep agent instructions outside `src/`: its strict validator only permits bank directories and category JSON files.

## Unified category names

When adding a bank/category, changing MCCs, or editing `unified_category`, follow
[UNIFIED_CATEGORIES.md](UNIFIED_CATEGORIES.md). It defines title selection,
the preview/write/catalog workflow, conflict handling, and verification.

## Verification

- Data edits: `python3 scripts/cashbacks.py validate` and inspect the scoped
  unification preview again.
- Script edits: run the relevant unittest module(s). CI runs
  `python3 -m unittest discover -s tests` (server tests require
  `server/requirements.txt`).
