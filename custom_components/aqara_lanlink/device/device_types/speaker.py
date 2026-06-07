"""Composer for the Speaker deviceType (Speaker + MediaPlayback clusters)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, NumberDescriptor, SensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

from . import _fallback
from ._base import ComposeContext, _ec


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        fc, tc = spec.function_code, spec.trait_code
        if fc == "Speaker" and tc == "Volume":
            out.append(NumberDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                min_value=spec.min_value if spec.min_value is not None else 0.0,
                max_value=spec.max_value if spec.max_value is not None else 100.0,
                step=spec.step or 1.0,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif fc == "Speaker" and tc == "Mute":
            out.append(SwitchDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif fc == "MediaPlayback" and tc == "CurrentPlaybackState":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                device_class=SensorDeviceClass.ENUM,
                options=list(spec.enum_values.values()) if spec.enum_values else None,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
