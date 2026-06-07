"""Composer for the VibrationSensor deviceType (Vibration cluster)."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, EventDescriptor, SensorDescriptor,
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
        if spec.function_code != "Vibration":
            others[wp] = spec
            continue
        tc = spec.trait_code
        if tc == "VibrationEvent":
            out.append(EventDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trigger_trait=spec,
                event_types=tuple(spec.enum_values.values()) if spec.enum_values else (),
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "VibrationDuration":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                native_unit_of_measurement=spec.unit,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
