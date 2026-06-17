# Per-model trait overrides

The catalogue shipped with `aqara_lanlink` is generated from Aqara's V3 cloud
spec, so it is occasionally wrong: a label leaks through untranslated, a
trait is mis-typed, or the desired Home Assistant entity type differs from
what the automatic classifier picks. Each device-model package can correct
this without a catalogue regeneration via its `overrides.py` file.

This is a **maintainer / contributor** mechanism. End users don't normally
edit these files; the changes ship in the integration.

See [catalogue-and-traits.md](catalogue-and-traits.md) for the data-model
vocabulary used throughout this document (TraitSpec, wire paths, endpoints,
deviceTypes, composers). See [adding-device-support.md](adding-device-support.md)
for where overrides fit in the broader contribution flow.

## How it works

Every model package under `custom_components/aqara_lanlink/device/models/<pkg>/`
has an `overrides.py` exporting an `OVERRIDES` dict keyed by wire path:

```python
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    "5.160.33001": TraitSpec(...),   # replace the generated entry at this wire path
    "4.219.20217": None,             # drop the entry entirely (no entity)
}
```

At load time the override is merged over the generated `data.json` entry at
the same wire path (replace-on-conflict; `None` removes it). The merged
`TraitSpec` then flows through the normal derive, so **every field you set
on it is authoritative**.

The simplest way to get a starting `TraitSpec` to edit is the
`export_overlay` service, which emits a ready-to-paste full `TraitSpec(...)`
snippet (the complete constructor call) for a live device; many authors paste
that directly instead of using `replace()`.

## What you can override

### Data / labelling

`name`, `enum_values`, `unit`, `min_value` / `max_value` / `step`,
`writable`, `readable`, `entity_category`, `default_enabled`. These have
always been honoured. Typical use: fixing an untranslated enum label.

```python
import json
from dataclasses import replace
from pathlib import Path

from custom_components.aqara_lanlink.device.traits import TraitSpec

# Rebuild the generated TraitSpecs for this package from the sibling data.json.
# Reading the file directly avoids a circular import of the model loader.
_raw = json.loads((Path(__file__).parent / "data.json").read_text())["traits"]
_base = {wp: TraitSpec(id=wp, wire_path=wp, **fields) for wp, fields in _raw.items()}

# Correct a CN-region label that leaked into the catalogue.
OVERRIDES: dict[str, TraitSpec | None] = {
    "5.160.33001": replace(
        _base["5.160.33001"], enum_values={
            "0": "PIR", "1": "Ultrasonic", "2": "Infrared and Ultrasonic",
            "3": "Physical Contact", "4": "Radar", "5": "Vision",
        },
    ),
}
```

### Entity type - `platform`

Set `platform` to force the HA entity type, overriding the classifier's
default for that trait. Valid values:

`sensor`, `binary_sensor`, `switch`, `number`, `select`, `event`, `button`.

```python
import json
from dataclasses import replace
from pathlib import Path

from custom_components.aqara_lanlink.device.traits import TraitSpec

_raw = json.loads((Path(__file__).parent / "data.json").read_text())["traits"]
_base = {wp: TraitSpec(id=wp, wire_path=wp, **fields) for wp, fields in _raw.items()}

# Force a recognition report back to a plain binary sensor instead of the
# default event entity.
OVERRIDES: dict[str, TraitSpec | None] = {
    "6.218.20216": replace(_base["6.218.20216"], platform="binary_sensor"),
}
```

The override beats **every** per-deviceType composer, including the camera
detector composers. For `event`, the event types are taken from the trait's
`enum_values` (or a single `triggered` type if it has none); for `select`,
the options come from `enum_values` (an override to `select` with no
`enum_values` is skipped with a warning). An explicit `platform="button"`
override always writes `"1"` when pressed.

### Decoration

When the chosen entity type supports them, these are carried onto the
descriptor: `device_class`, `state_class`, `icon`,
`suggested_display_precision`, `scale`, `unit_of_measurement`. They are
honoured both when you set `platform` explicitly **and** when the classifier
infers the type itself (so you can add a `device_class` to an
auto-classified sensor without also setting `platform`).

> `TraitSpec` validates field combinations at construction: `state_class`
> requires `platform="sensor"`; `scale`, `unit_of_measurement`, and
> `suggested_display_precision` require `platform="sensor"` or `"number"`;
> `auto_clear_seconds` requires `platform="binary_sensor"`. An illegal
> combination raises at load with a clear message - use the legacy `unit`
> field if you only want a display unit without committing to a platform.

## Off-catalogue capabilities: the CAPABILITIES dict

Besides `OVERRIDES`, a model's `overrides.py` may export an optional
`CAPABILITIES` dict to declare capabilities that are not in the V3 trait
catalogue. Currently the only supported key is `"ptz"` (pan/tilt/zoom):

```python
CAPABILITIES: dict[str, frozenset[str]] = {
    "ptz": frozenset({"pan_tilt", "zoom", "presets"}),
}
```

Declare only the sub-features the camera actually supports. The model loader
reads `CAPABILITIES` alongside `OVERRIDES` and indexes it in the registry;
`catalog.ptz_features_for_model()` then returns the declared set. PTZ is kept
separate from the catalogue because it operates over a distinct local P2P
control plane (not LANLink). See [ptz.md](ptz.md) for the full picture.

## Local device settings: SETTINGS_OVERRIDES

Some sub-device configuration - child lock, indicator light, button mode,
power-off memory, max power, find/restart - is not in the V3 wire-path trait
catalogue. The hub actuates these by a 3-part **resource ID** (rid, e.g.
`4.4.85`) rather than a wire path, and the integration exposes them as
switch / select / number / button entities written fully locally over LANLink.
For the protocol and state model, see [architecture.md](architecture.md)
section 12.

Settings are not `TraitSpec`s. Each is a `SettingSpec` keyed by rid, and they
live in their own map, parallel to `OVERRIDES`:

- The confirmed, shipped settings for a model live in a `settings` block in the
  model package's `data.json`.
- A model's `overrides.py` may export a `SETTINGS_OVERRIDES` dict that tunes
  that block at load time, with the same replace / drop / add semantics as
  `OVERRIDES` but **no wire-path coercion** (settings have no `wire_path`).

```python
from custom_components.aqara_lanlink.device.settings import SettingSpec

SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
    # replace the data.json entry at this rid
    "8.0.2042": SettingSpec(
        rid="8.0.2042", name="Max power", platform="number",
        min=100, max=4000, unit="W",
    ),
    # drop a data.json entry so no entity is created
    "8.0.2096": None,
    # add a rid not present in data.json
    "9.9.99": SettingSpec(rid="9.9.99", name="New setting", platform="switch"),
}
```

`SettingSpec` fields:

| Field | Applies to | Meaning |
|-------|-----------|---------|
| `rid` | all | The 3-part resource ID, written bare over LANLink. Must match the dict key. |
| `name` | all | Entity name. |
| `platform` | all | One of `switch`, `select`, `number`, `button`. |
| `enum_values` | select | `{wire_value: label}` map for the options. |
| `min` / `max` / `unit` | number | Numeric bounds and display unit. |
| `press_value` | button | Value written on press (default `"1"`). |
| `on_value` / `off_value` | switch | Wire values; some settings invert (e.g. indicator `on == "0"`). |
| `entity_category` | all | `"config"` (default) or `"diagnostic"`. |
| `default_enabled` | all | Whether the entity is enabled by default. |
| `optimistic` | switch/select/number | Set own state after a write (default `True`). |

Because the catalogue generator is not yet settings-aware, `SETTINGS_OVERRIDES`
is the contributor-editable home for new settings: author and validate them
here, and a maintainer folds confirmed entries into the model's `data.json`
`settings` block. See [adding-device-support.md](adding-device-support.md) for
discovering the rid for a setting via the `scan_device` resource-ID discovery
notification.

## Limitations

- **Fused entities can't be reshaped.** Endpoints whose composer merges
  several traits into one entity - currently only `Light` (Output.OnOff +
  LevelControl + ColorControl → one light) - ignore per-trait `platform`
  overrides, logging a warning, because pulling one of those traits out would
  break the composite. Composers that absorb a *single* trait (camera
  detectors, `Doorbell`) or pass everything through (`VideoDoorbell`) are
  **not** fused and accept overrides normally.
- **Runtime behaviour isn't authored here.** Camera/go2rtc wiring, custom
  services, and other imperative behaviour live in code, not trait data.
- **A field only matters where its entity type uses it.** Setting
  `min_value` on an enum trait, or `scale` on a switch, is inert.
