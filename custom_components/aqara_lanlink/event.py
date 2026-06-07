"""Generic event platform.

One `AqaraEvent` per `EventDescriptor` per device. Two trigger modes:

- `descriptor.trigger_trait` is set: the device's trait-id index routes
  matching reports to the entity's `apply_value(raw)`, which maps the
  wire value to one of `event_types` and fires it via `_trigger_event`.
  When the trait has `enum_values` (most buttons / doorbells), the
  mapping is wire_value -> label (e.g. "1" -> "single_press"). When the
  trait has no enum mapping, the first declared event_type is fired
  (single-type events like a plain doorbell ring).

- `descriptor.trigger_source` is set: the device's imperative
  `async_setup` owns the listener and calls `Device.fire_event(key, ...)`,
  which looks up this entity by descriptor key and invokes its
  `trigger_event(event_type, **payload)` method. `EventEntity` already
  provides `_trigger_event`; we expose `trigger_event` as a thin
  forwarding alias for `Device.fire_event`'s call shape.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import EventDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build an event entity per `EventDescriptor` per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, EventDescriptor, AqaraEvent,
        ),
    )


class AqaraEvent(AqaraEntity, EventEntity):
    """Event entity supporting both trait-driven and imperative triggers."""

    def __init__(self, hub, device, subentry, descriptor: EventDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        # Multi-event-type trait-triggered events ARE supported now: the
        # apply_value method maps the wire value to a label via the trait's
        # enum_values. A trait-triggered event with multiple types but no
        # enum mapping is still a misconfiguration -- there's no way to
        # decide which type to fire.
        if (
            descriptor.trigger_trait is not None
            and len(descriptor.event_types) > 1
            and not (descriptor.trigger_trait.enum_values or {})
        ):
            raise ValueError(
                f"EventDescriptor {descriptor.key!r} has trigger_trait set with "
                f"{len(descriptor.event_types)} event_types but the trait has no "
                "enum_values to map the wire value to an event type. Either "
                "declare a single event_type (e.g. ring) or populate "
                "trigger_trait.enum_values for the wire-value-to-label mapping."
            )
        self.entity_description = descriptor
        self._attr_event_types = list(descriptor.event_types)

    def apply_value(self, raw: str) -> None:
        """Trait-driven trigger.

        Two cases:
        - Trait has `enum_values` (button / doorbell): map the wire value
          to one of `event_types` via the enum dict. Wire values the trait
          does not declare are dropped -- UNLESS the descriptor sets
          `unknown_event_type`, in which case the fallback type fires (used by
          recognition/detection traits whose cloud enum is incomplete).
        - Trait has no enum mapping: fire the single declared event_type.

        When `value_payload_key` is set, the raw wire value is forwarded as
        the event payload under that key (e.g. a recognised face id).
        """
        if not self._attr_event_types:
            return
        desc = self.entity_description
        spec = desc.trigger_trait
        enum_map = (spec.enum_values if spec is not None else None) or {}
        if enum_map:
            event_type = enum_map.get(str(raw))
            if event_type is None or event_type not in self._attr_event_types:
                # Wire value the catalogue did not enumerate, or enum drift
                # where the label isn't a declared event_type. Strict events
                # drop it; recognition/detection events fall back to their
                # declared catch-all type so the detection isn't lost.
                if desc.unknown_event_type in self._attr_event_types:
                    event_type = desc.unknown_event_type
                else:
                    return
        else:
            event_type = self._attr_event_types[0]
        if desc.value_payload_key is not None:
            self._trigger_event(event_type, {desc.value_payload_key: str(raw)})
        else:
            self._trigger_event(event_type)
        self.write_state_if_added()

    def trigger_event(self, event_type: str, **payload: Any) -> None:
        """Forward an imperative trigger to HA's event entity machinery.

        Called by `Device.fire_event` (for `trigger_source` events). HA's
        own `EventEntity._trigger_event` accepts an optional payload dict
        positional arg; passing kwargs as the dict matches the device-side
        contract.
        """
        self._trigger_event(event_type, payload or None)
        self.write_state_if_added()


__all__ = ["AqaraEvent", "async_setup_entry"]
