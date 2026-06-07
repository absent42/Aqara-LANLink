"""Composer for the Light deviceType.

Fuses up to four traits on one endpoint into a single LightDescriptor:
  - Output.OnOff (required) -> power_trait
  - LevelControl.CurrentLevel (optional) -> brightness_trait
  - ColorControl.ColorTemperature (optional) -> color_temp_trait + Kelvin bounds
  - ColorControl.CurrentX + CurrentY (optional, paired) -> color_xy_trait

LightDescriptor stores the inner TraitSpec untouched (wire-format values
stay in whatever unit the cloud uses) and carries the Kelvin bounds in the
dedicated descriptor fields color_temp_min_kelvin / color_temp_max_kelvin,
plus color_temp_wire_unit ("K" or "mired") so async_turn_on can convert at
write time.

An endpoint with deviceType=Light and no OnOff trait is malformed: return [].
A Light endpoint with ONLY OnOff (no brightness/color) is a valid simple light.
"""
from __future__ import annotations

import logging
from typing import Literal

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, LightDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

from . import _fallback
from ._base import ComposeContext, _ec, find_trait

_LOGGER = logging.getLogger(__name__)

# Trait codes the Light composer FUSES into the single LightDescriptor.
# Everything else on a Light endpoint -- transition times, startup mode,
# music sync, lighting scene presets, color mode, segment count, etc. --
# falls through to _fallback so they surface as Number/Switch/Select.
_LIGHT_ABSORBED: frozenset[tuple[str, str]] = frozenset({
    ("Output", "OnOff"),
    ("LevelControl", "CurrentLevel"),
    ("ColorControl", "ColorTemperature"),
    ("ColorControl", "ColorXY"),
    ("ColorControl", "CurrentX"),
    ("ColorControl", "CurrentY"),
})

_KELVIN_UNITS = ("°K", "K", "Kelvin", "kelvin")
_MIRED_UNITS = ("mired", "mireds")


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    on_off = find_trait(traits, "Output", "OnOff")
    level = find_trait(traits, "LevelControl", "CurrentLevel")
    color_temp = find_trait(traits, "ColorControl", "ColorTemperature")
    # Prefer the combined ColorXY uint32 trait (the bulb's canonical destination
    # for atomic XY writes). Fall back to the CurrentX/CurrentY pair only when
    # the model doesn't expose ColorXY -- not encountered on any catalogued
    # RGB Aqara bulb to date, kept as a defensive path.
    color_xy_combined = find_trait(traits, "ColorControl", "ColorXY")
    color_x = find_trait(traits, "ColorControl", "CurrentX")
    color_y = find_trait(traits, "ColorControl", "CurrentY")
    if color_xy_combined is not None:
        color_xy = color_xy_combined
    else:
        color_xy = color_x if (color_x and color_y) else None

    # Partition traits: absorbed go into the LightDescriptor (or are dropped
    # if absorbed but no LightDescriptor builds, e.g. no OnOff); everything
    # else delegates to _fallback so config knobs / scene-modes / etc.
    # surface as their own entities.
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        if (spec.function_code, spec.trait_code) not in _LIGHT_ABSORBED:
            others[wp] = spec

    out: list[AnyDescriptor] = []
    if on_off is None:
        _LOGGER.warning(
            "Light endpoint %s on %s has no Output.OnOff trait; skipping LightDescriptor.",
            endpoint_id, context.model,
        )
    else:
        ct_min_k, ct_max_k, ct_wire_unit = _color_temp_bounds(color_temp)
        out.append(LightDescriptor(
            key=f"auto_{on_off.id.replace('.', '_')}",
            name=on_off.name or "Light",
            power_trait=on_off,
            power_attr=AttrSpec(name=on_off.id),
            brightness_trait=level,
            brightness_attr=AttrSpec(name=level.id) if level else None,
            color_temp_trait=color_temp,
            color_temp_attr=AttrSpec(name=color_temp.id) if color_temp else None,
            color_temp_min_kelvin=ct_min_k,
            color_temp_max_kelvin=ct_max_k,
            color_temp_wire_unit=ct_wire_unit,
            color_xy_trait=color_xy,
            color_xy_attr=AttrSpec(name=color_xy.id) if color_xy else None,
            entity_category=_ec(on_off),
            entity_registry_enabled_default=on_off.default_enabled,
        ))
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out


def _color_temp_bounds(
    spec: TraitSpec | None,
) -> tuple[int | None, int | None, Literal["K", "mired"]]:
    """Return (min_kelvin, max_kelvin, wire_unit) for a color-temp TraitSpec.

    If `spec` is None, returns (None, None, "K") -- the LightDescriptor default.
    If the wire unit is Kelvin, returns (int(min), int(max), "K").
    If the wire unit is mireds, converts using K = round(1_000_000 / mired).
    Note the min/max swap: smaller mireds = larger Kelvin.
    """
    if spec is None:
        return None, None, "K"
    if spec.unit in _KELVIN_UNITS:
        k_min = int(spec.min_value) if spec.min_value is not None else None
        k_max = int(spec.max_value) if spec.max_value is not None else None
        return k_min, k_max, "K"
    # V3 catalogue empirically omits the unit on ColorTemperature traits and
    # always uses mireds on the wire (min/max sit in the 83-555 mired range
    # across every shipped CCT model). Treat unit=None as mired so HA gets
    # working Kelvin bounds and writes go out as mireds.
    if spec.unit in _MIRED_UNITS or spec.unit is None:
        k_min = round(1_000_000 / spec.max_value) if spec.max_value else None
        k_max = round(1_000_000 / spec.min_value) if spec.min_value else None
        return k_min, k_max, "mired"
    # Unknown unit: log and leave bounds unset; default wire unit "K".
    _LOGGER.warning(
        "Light color_temp trait %s has unrecognised unit %r; bounds unset.",
        spec.id, spec.unit,
    )
    return None, None, "K"
