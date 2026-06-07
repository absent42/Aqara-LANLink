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
