"""Composer for the Thermostat and AirConditioner deviceTypes (HeaterCooler cluster).

Both deviceTypes share the HeaterCooler trait family; the AC just adds
the symmetric cooling target plus a humidity sensor. The Output.OnOff,
FanControl.*, and any RockSetting traits delegate to _fallback (writable
bool -> Switch, writable enum -> Select) which produces sensible HA
entities without dedicated handling here.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, NumberDescriptor, SelectDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

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
        if spec.function_code != "HeaterCooler":
            others[wp] = spec
            continue
        tc = spec.trait_code
        if tc == "CurrentTemperature":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                device_class=SensorDeviceClass.TEMPERATURE,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=spec.unit or "°C",
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "HeaterCoolerMode":
            out.append(SelectDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                options_map=tuple(
                    (label, wire) for wire, label in (spec.enum_values or {}).items()
                ),
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc in ("HeatingTemperature", "CoolingTemperature"):
            out.append(NumberDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                device_class=NumberDeviceClass.TEMPERATURE,
                min_value=spec.min_value if spec.min_value is not None else 5.0,
                max_value=spec.max_value if spec.max_value is not None else 35.0,
                step=spec.step or 0.5,
                native_unit_of_measurement=spec.unit or "°C",
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "CurrentHumidity":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                device_class=SensorDeviceClass.HUMIDITY,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=spec.unit or "%",
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "AntiFreezeTemperature":
            out.append(NumberDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                attr=AttrSpec(name=spec.id),
                device_class=NumberDeviceClass.TEMPERATURE,
                min_value=spec.min_value if spec.min_value is not None else 5.0,
                max_value=spec.max_value if spec.max_value is not None else 15.0,
                step=spec.step or 0.5,
                native_unit_of_measurement=spec.unit or "°C",
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
