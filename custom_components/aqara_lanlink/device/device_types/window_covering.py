"""Composer for the WindowCovering deviceType.

Specific handling for CurrentPositionPercentage (read-only Sensor),
TargetPositionPercentage (writable Number), and MotorOperationStatus
(enum Sensor, diagnostic). Other WindowCovering traits (limit-point
configs, motor speed, etc.) go through _fallback's data_type dispatch.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, NumberDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass

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
        if spec.function_code != "WindowCovering":
            others[wp] = spec
            continue
        tc = spec.trait_code
        if tc == "CurrentPositionPercentage":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                native_unit_of_measurement=spec.unit or "%",
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "TargetPositionPercentage":
            out.append(NumberDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                min_value=spec.min_value if spec.min_value is not None else 0.0,
                max_value=spec.max_value if spec.max_value is not None else 100.0,
                step=spec.step or 1.0,
                native_unit_of_measurement=spec.unit or "%",
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "MotorOperationStatus":
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
