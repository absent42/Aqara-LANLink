"""Composer for the Button deviceType.

Emits one EventDescriptor for the Button.ButtonEvent trait. The event_types
tuple comes from the trait's enum_values (label side); the entity's
apply_value maps the wire code to the matching event_type. Other traits
on a Button endpoint (config knobs, diagnostics) delegate to _fallback.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import EventDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec

from ._base import _ec, make_single_trait_composer


def _button_event(spec: TraitSpec) -> EventDescriptor:
    event_types = (
        tuple(spec.enum_values.values()) if spec.enum_values else ("press",)
    )
    return EventDescriptor(
        key=f"auto_{spec.id.replace('.', '_')}",
        name=spec.name,
        trigger_trait=spec,
        event_types=event_types,
        entity_category=_ec(spec),
        entity_registry_enabled_default=spec.default_enabled,
    )


compose = make_single_trait_composer(
    function_code="Button",
    trait_code="ButtonEvent",
    descriptor_factory=_button_event,
)
