## Linked issue

Closes #

## Models added

List the Aqara model string(s) covered by this change:

- `lumi.example.model01`
- `lumi.example.model02`

## What this PR does

Brief description of which corrections are applied and why the generated
`data.json` output alone is insufficient for these models.

## Change type

- [ ] New `overrides.py` entry in an existing model package
- [ ] New model package (new directory under
      `custom_components/aqara_lanlink/device/models/<package>/`)
- [ ] White-label addition (display-name correction for a model that shares
      a package with a related device)

## Artifact

The change is in one or more `overrides.py` files. Briefly note which entries
were added or corrected (full `TraitSpec(...)` overrides, `None` drops, or
display-name fixes). If you used `aqara_lanlink.export_overlay` to generate
the initial snippet, mention it.

See `docs/adding-device-support.md` and `docs/overrides.md` for the mechanics.

## Test plan

- [ ] `python3 -m pytest tests/ -v` passes with no failures.
- [ ] Tests added or updated under
      `tests/device/models/<package>/` for every model
      covered by this PR.
- [ ] `catalog.get_trait`, `catalog.get_enum_labels`, and
      `catalog.get_display_metadata` lookups are verified by tests.
- [ ] White-label display-name corrections (if any) are verified by tests.
- [ ] Device exercised end-to-end in a running Home Assistant instance
      (check if applicable; skip with a note if hardware is unavailable).

## Hardware tested

| Model | Firmware version | Region |
|-------|-----------------|--------|
|       |                 |        |

## Conventions checklist

- [ ] No emojis or icons in code, comments, or documentation.
- [ ] Autouse reset fixture (`_reset_catalog`) present in every new test
      module that exercises catalog lookups.
- [ ] `overrides.py` entries use full `TraitSpec(...)` objects or `None`;
      no partial dicts.
- [ ] `__init__.py` is the standard 3-line shim; no hand-authored
      `MODELS`, `TRAITS`, or `TraitSpec` constructions in that file.
- [ ] White-label keys in `overrides.py` are subsets of the models the
      package covers.
