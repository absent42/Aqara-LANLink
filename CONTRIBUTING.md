# Contributing to Aqara LANLink

The most common contribution to this integration is editing a device model's
`overrides.py` file. This guide covers that workflow from start to finish.

Device support is data-driven. Each supported model has a package under
`custom_components/aqara_lanlink/device/models/<pkg>/` that contains three
files: `data.json` (auto-generated from Aqara's V3 cloud spec, should not be hand edited), `overrides.py` (the hand-edited correction layer, preserved across
regeneration), and a three-line `__init__.py` shim. The integration reads those
packages at startup and builds Home Assistant entities from the combined
metadata.

---

## Contents

1. [Dev setup](#dev-setup)
2. [Adding or fixing device support](#adding-or-fixing-device-support)
3. [The overrides reference](#the-overrides-reference)
4. [Testing](#testing)
5. [Submitting a pull request](#submitting-a-pull-request)

---

## Dev setup

Clone the repository and point a Home Assistant development instance at it as a
custom component:

```
git clone https://github.com/<your-fork>/aqara_lan_talk.git
```

The integration source lives at `custom_components/aqara_lanlink/` inside the
repo root. Symlink or mount that directory into your HA instance's
`custom_components/` folder. The standard HA developer setup docs cover the
rest.

---

## Adding or fixing device support

Determine which situation applies before changing anything. The full mechanics
and decision tree are in [docs/adding-device-support.md](docs/adding-device-support.md).
A brief summary of each tier follows.

### Situation A: device is catalogued, but a label, unit, entity type, or device_class is wrong

The model's `data.json` is present but the generated output is incorrect in
some way: a label leaked through untranslated, the entity should be a
`binary_sensor` rather than a `sensor`, the `device_class` is missing, and so
on.

Edit that model's `overrides.py` and add or replace the relevant entry in its
`OVERRIDES` dict. See [docs/overrides.md](docs/overrides.md) for the full field
reference.

### Situation B: device is catalogued, but a trait it physically has is missing

The model appears in the catalogue but one or more traits are absent from the
generated entities.

Run the `aqara_lanlink.scan_device` service against the device. The integration
scans the Aqara V1 API cloud and raises a `scan_review` Repair flow where you can accept the missing traits. Once accepted, run `aqara_lanlink.export_overlay`; it posts a
Persistent Notification with a ready-to-paste `TraitSpec` snippet. Paste that
snippet into the model's `overrides.py`, test the trait and assess its usefullness and functionality. If the trait proves useful and works correctly open a PR.

The landed artifact is the same as Situation A: an edit to `overrides.py`.

### Situation C: device is absent from the catalogue (no data.json)

If the model string does not appear in [docs/catalogue/index.md](docs/catalogue/index.md),
no package exists for it yet.

Open a "New Device Support" issue using the issue template and include the
device's Aqara model string(s). A maintainer will regenerate the catalogue to
include the new model. Once the `data.json` is merged, you can follow up with
an `overrides.py` polish PR (Situation A or B above).

---

## The overrides reference

Each model package has an `overrides.py` with a single exported dict:

```python
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # Replace the generated entry at this wire path:
    "5.160.33001": TraitSpec(
        id="5.160.33001",
        name="contact",
        description="Door/window contact state.",
        data_type="bool",
        enum_values={"0": "Open", "1": "Closed"},
        platform="binary_sensor",
        device_class="door",
    ),
    # Drop a generated entry entirely (no entity created):
    "4.219.20217": None,
}
```

The keys are wire paths (three-part dotted numeric strings). Each value is
either a full `TraitSpec(...)` that replaces the generated entry at that wire
path, or `None` to suppress the entry entirely.

`overrides.py` is executed standalone by the loader, so every `TraitSpec` in
`OVERRIDES` must be self-contained. The `aqara_lanlink.export_overlay` service
emits a ready-to-paste snippet with all required fields populated.

The full field reference, valid `platform` values, `device_class` constraints,
and worked examples are in [docs/overrides.md](docs/overrides.md).

---

## Testing

### Running the suite

```
python3 -m pytest tests/ -v
```

The suite does not require a real hub or device.

### Fixture pattern

There are two layers of fixture isolation in the test suite:

**Repo-wide autouse (tests/conftest.py):** The
`auto_enable_custom_integrations` fixture is declared `autouse=True` and applies
to every test automatically. It enables custom integrations for the HA test
environment. It does NOT reset the catalogue.

**Per-module catalogue reset:** Test modules that exercise catalogue or registry
lookups declare their own autouse fixture that calls `reset_for_tests()` before
and after each test. The specific call depends on which layer the module touches:

- `traits_catalog.reset_for_tests()` for modules that test the trait
  specification catalog (`device/traits.py`)
- `registry.reset_for_tests()` for modules that test the model registry
  (`device/registry.py`)
- `catalog.reset_for_tests()` for modules that test the combined catalog facade
  (`device/catalog.py`); this delegates to the registry internally
- `attrs_catalog.reset_for_tests()` for modules that test the attribute catalog

A typical per-module reset fixture looks like:

```python
import pytest
from custom_components.aqara_lanlink.device import traits as traits_catalog

@pytest.fixture(autouse=True)
def _reset_traits():
    traits_catalog.reset_for_tests()
    yield
    traits_catalog.reset_for_tests()
```

Do not assume a single global `catalog.reset_for_tests()` covers all state.
Match the reset call to the catalog layer your tests actually exercise. If in
doubt, look at an existing test module in the same subdirectory.

### What to assert for an overrides.py change

For a new or edited `OVERRIDES` entry, the test should verify:

- The corrected `enum_values`, `platform`, or `device_class` is what the
  catalog returns for that model and wire path after the package is loaded.
- If `None`, the entry is absent from the catalog.
- Any `WHITELABELS` entry returns the correct display name for its model string.

Mirror the test file location under `tests/device/models/<pkg>/`:

```
tests/device/models/<pkg>/
    __init__.py        (empty)
    test_<pkg>.py
```

---

## Submitting a pull request

1. If the PR adds a model or covers a missing-trait case, open a "New Device
   Support" issue first (or reference an existing one). Device-absent cases
   (Situation C) require the maintainer to merge the new `data.json` before
   your overrides PR can be based on it.
2. Edit `overrides.py`. Run `python3 -m pytest tests/ -v` and
   confirm all tests pass.
3. Open a pull request. For device-related PRs, use the template at
   `.github/PULL_REQUEST_TEMPLATE/new-device.md`. Reference the issue with
   `Closes #<number>` in the PR body.

### Conventions checklist

- Tests pass: `python3 -m pytest tests/ -v`.
- Every `OVERRIDES` entry is a full `TraitSpec(...)` or `None`; partial
  construction is not supported.
- `WHITELABELS` and display-name handling lives in `data.json`; corrections to the generated display metadata belong in an issue comment, not a hand-edited `data.json`.
- Per-module autouse reset fixture present in any test module that exercises
  catalogue, registry, or traits lookups (see the Testing section).
- Issue referenced in the PR body when the change covers a new or missing
  device.
