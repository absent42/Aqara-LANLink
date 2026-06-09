"""Composer for the Doorbell deviceType.

Emits one EventDescriptor for the Doorbell.ButtonEvent trait. Same pattern
as the Button composer, but with device_class=DOORBELL and event_types
derived from the trait's enum_values (or defaulting to ("ring",) if the
spec has none). Other Doorbell traits (Volume, etc.) fall through to
_fallback.
"""
from __future__ import annotations

import dataclasses

from homeassistant.components.event import EventDeviceClass

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, EventDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

from . import _fallback
from ._base import ComposeContext, _ec, find_trait


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    button_event = find_trait(traits, "Doorbell", "ButtonEvent")
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    if button_event is not None:
        # HA deprecates DOORBELL event entities that don't support the "ring"
        # event_type (removal in 2027.4), so "ring" must always be present.
        ev = button_event.enum_values or {}
        if len(ev) > 1:
            # Multi-press doorbell (e.g. Single/Double press): the primary
            # (first) press IS the ring; additional presses keep their
            # humanized labels so automations can distinguish them.
            items = list(ev.items())
            normalised_enum = {items[0][0]: "ring"}
            extra = []
            for wire, label in items[1:]:
                normalised_enum[wire] = label
                extra.append(label)
            trigger_trait = dataclasses.replace(button_event, enum_values=normalised_enum)
            event_types = ("ring", *extra)
        else:
            # Single press (or a bare signal with no enum) -> just "ring".
            normalised_enum = {wire: "ring" for wire in ev} or None
            trigger_trait = dataclasses.replace(button_event, enum_values=normalised_enum)
            event_types = ("ring",)
        out.append(EventDescriptor(
            key=f"auto_{button_event.id.replace('.', '_')}",
            name=button_event.name or "Doorbell",
            trigger_trait=trigger_trait,
            event_types=event_types,
            device_class=EventDeviceClass.DOORBELL,
            entity_category=_ec(button_event),
            entity_registry_enabled_default=button_event.default_enabled,
        ))
    for wp, spec in traits.items():
        if spec.function_code == "Doorbell" and spec.trait_code == "ButtonEvent":
            continue
        others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
