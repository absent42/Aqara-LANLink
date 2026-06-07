"""Composer for the ElectricalSensor deviceType (energy monitoring)."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from . import _fallback
from ._base import ComposeContext, _default_precision, _ec


_TRAITS: dict[str, tuple[SensorDeviceClass, SensorStateClass]] = {
    "CurrentVoltage": (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
    "CurrentPower": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    "CumulativeEnergyConsumption": (
        SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING,
    ),
}


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        if spec.function_code != "EnergyManagement":
            others[wp] = spec
            continue
        mapping = _TRAITS.get(spec.trait_code or "")
        if mapping is None:
            others[wp] = spec
            continue
        dc, sc = mapping
        out.append(SensorDescriptor(
            key=f"auto_{spec.id.replace('.', '_')}",
            name=spec.name, trait=spec,
            device_class=dc, state_class=sc,
            native_unit_of_measurement=spec.unit,
            suggested_display_precision=_default_precision(dc, spec.data_type),
            entity_category=_ec(spec),
            entity_registry_enabled_default=spec.default_enabled,
        ))
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
