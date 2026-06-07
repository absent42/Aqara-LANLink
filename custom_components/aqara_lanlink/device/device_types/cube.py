"""Composer for the Cube deviceType (gesture + rotation events)."""
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
        if spec.function_code != "Cube":
            others[wp] = spec
            continue
        tc = spec.trait_code
        if tc in ("CubeEvent", "RotationEvent"):
            out.append(EventDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trigger_trait=spec,
                event_types=tuple(spec.enum_values.values()) if spec.enum_values else (),
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "RotationAngle":
            out.append(SensorDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name, trait=spec,
                native_unit_of_measurement=spec.unit,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            # RotationDirection, TopFace and any other read-only enum cube
            # trait route through _fallback, which builds an ENUM sensor that
            # maps the wire value to its label.
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
