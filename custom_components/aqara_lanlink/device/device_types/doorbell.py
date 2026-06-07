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
        ev = button_event.enum_values or {}
        if len(ev) > 1:
            # Multi-press doorbell (e.g. Single/Double press): preserve each
            # press as its own event_type so automations can distinguish them.
            # Labels are humanized at catalogue-generation time -> use verbatim.
            trigger_trait = button_event
            event_types = tuple(ev.values())
        else:
            # Single press (or a bare signal with no enum): collapse to the
            # conventional doorbell "ring" event_type. HA imposes no
            # device_class->event_types contract, so "ring" is a UX choice (the
            # widely-recognized doorbell event), not a requirement.
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
