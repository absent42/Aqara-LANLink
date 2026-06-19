"""Generic text platform.

One `AqaraText` per `TextDescriptor` per device. Backs string/packed
rid-settings (e.g. hex-encoded light segment paragraphs) that have no
clean enum/range and so cannot be a switch/select/number. The cached
value is driven by the same seed/apply_value mechanism as the other
setting entities; writes send `{rid: value}` (the bare rid) to the device.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import TextDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a text entity per `TextDescriptor` per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, TextDescriptor, AqaraText,
        ),
    )


class AqaraText(AqaraEntity, TextEntity):
    """Free-text input backed by an attr name."""

    _attr_mode = TextMode.TEXT
    _attr_native_max = 255

    def __init__(self, hub, device, subentry, descriptor: TextDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        self._attr_native_value = None

    def apply_value(self, raw: str) -> None:
        """Mirror an incoming report (or cloud seed) onto `native_value`."""
        self._attr_native_value = raw
        self.write_state_if_added()

    async def async_set_value(self, value: str) -> None:
        """Write the raw string via the device's `async_write` (bare rid)."""
        await self.device.async_write({self._write_attr: value})
        if self.descriptor.optimistic:
            self._attr_native_value = value
            self.write_state_if_added()


__all__ = ["AqaraText", "async_setup_entry"]
