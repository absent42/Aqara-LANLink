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

from .device import catalog
from .device.composites.setup import composite_entities_for_platform
from .device.descriptors import TextDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform

# HA's final `TextEntity.state` raises ValueError when native_value exceeds the
# entity's `max`, which is itself capped at MAX_LENGTH_STATE_STATE (255). We must
# cap the CACHED state mirror so `async_write_ha_state` never throws, even though
# the full value is still written to the device.
_MAX_STATE_LEN = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a text entity per `TextDescriptor` per device."""
    controllers = getattr(entry.runtime_data, "composite_controllers", {})

    def build(hub, device, subentry) -> list:
        entities = build_descriptor_entities(
            hub, device, subentry, TextDescriptor, AqaraText,
        )
        decls = catalog.composites_for_model(device.MODEL)
        entities.extend(composite_entities_for_platform(
            hub, device, subentry, controllers, "text", decls))
        return entities

    await setup_descriptor_platform(entry, async_add_entities, build)


class AqaraText(AqaraEntity, TextEntity):
    """Free-text input backed by an attr name."""

    _attr_mode = TextMode.TEXT
    _attr_native_max = _MAX_STATE_LEN

    def __init__(self, hub, device, subentry, descriptor: TextDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        self._attr_native_value = None

    def _cap_state(self, value: str) -> str:
        """Cap a cached state mirror to the entity's max so HA can render it."""
        limit = getattr(self, "max", None) or _MAX_STATE_LEN
        return str(value)[:limit]

    def apply_value(self, raw: str) -> None:
        """Mirror an incoming report (or cloud seed) onto `native_value`.

        The cached state is capped; a seed longer than `max` would otherwise
        make HA's final `state` property raise on `async_write_ha_state`.
        """
        self._attr_native_value = self._cap_state(raw)
        self.write_state_if_added()

    async def async_set_value(self, value: str) -> None:
        """Write the raw string via the device's `async_write` (bare rid).

        The FULL value is sent to the device; only the cached state mirror is
        capped so HA can render it without raising.
        """
        await self.device.async_write({self._write_attr: value})
        if self.descriptor.optimistic:
            self._attr_native_value = self._cap_state(value)
            self.write_state_if_added()


__all__ = ["AqaraText", "async_setup_entry"]
