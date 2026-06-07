"""Generic sensor platform.

One `AqaraSensor` per `SensorDescriptor` per device. SensorDescriptor is
the rule-9 default fallback in the auto-derive pipeline, so it appears
for any trait whose metadata doesn't fit the writable-platform rules.
The entity is read-only: incoming reports update `native_value` via
`descriptor.transform_in` (defaults to identity).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import SensorDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a sensor entity per `SensorDescriptor` per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, SensorDescriptor, AqaraSensor,
        ),
    )


def _identity(raw: str) -> Any:
    """Default wire->python coercion: pass-through string."""
    return raw


def _coerce_float(raw: str) -> Any:
    """Wire->float, falling back to the raw string on parse failure."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _coerce_int(raw: str) -> Any:
    """Wire->int, falling back to the raw string on parse failure."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def _default_transform_for(descriptor: SensorDescriptor):
    """Pick a default wire->python coercion based on descriptor metadata.

    Numeric data types are coerced so HA's SensorEntity sees a real
    number and renders it without firmware-precision artefacts (e.g.
    weather_v1 emits "48.970000" for humidity; sensor_ht_agl02 emits
    "56.08" -- both should display the same once they're floats).
    Non-numeric / unknown / missing type info falls through to identity.
    """
    data_type = None
    if descriptor.trait is not None:
        data_type = descriptor.trait.data_type
    elif descriptor.attr is not None:
        data_type = descriptor.attr.data_type
    if data_type == "float":
        return _coerce_float
    if data_type == "int":
        return _coerce_int
    return _identity


class AqaraSensor(AqaraEntity, SensorEntity):
    """Read-only sensor backed by an attr or trait id."""

    def __init__(self, hub, device, subentry, descriptor: SensorDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        self._attr_native_value = None
        self._attr_extra_state_attributes = None
        # ENUM sensors carry their options as a tuple on the descriptor (kept
        # hashable so the descriptor works as a dict key). HA's options
        # contract is list[str]; expose it as a list here.
        if descriptor.options is not None:
            self._attr_options = list(descriptor.options)

    def apply_value(self, raw: str) -> None:
        """Coerce the wire value via `descriptor.transform_in`, optionally apply scale."""
        # Empty string means "no reading available" -- never a valid numeric
        # value, and on a numeric sensor (unit set) HA rejects the entity.
        # Treat it as a None state so the entity registers cleanly and
        # transitions to a real value on the next push.
        if raw == "":
            self._attr_native_value = None
            self._attr_extra_state_attributes = None
            self.write_state_if_added()
            return
        transform = self.descriptor.transform_in or _default_transform_for(
            self.descriptor,
        )
        value = transform(raw)
        scale = getattr(self.entity_description, "scale", None)
        if scale is not None:
            try:
                value = float(value) * scale
            except (TypeError, ValueError):
                pass  # non-numeric transform output; leave value untransformed
        if isinstance(value, str) and len(value) > MAX_LENGTH_STATE_STATE:
            # HA rejects state strings over MAX_LENGTH_STATE_STATE (255).
            # Keep the full value reachable as an attribute; truncate the
            # state to exactly the limit (254 chars + a 1-codepoint ellipsis).
            self._attr_extra_state_attributes = {"full_value": value}
            value = value[: MAX_LENGTH_STATE_STATE - 1] + "…"
        else:
            self._attr_extra_state_attributes = None
        self._attr_native_value = value
        self.write_state_if_added()


__all__ = ["AqaraSensor", "async_setup_entry"]
