"""Generic switch platform.

One `AqaraSwitch` per `SwitchDescriptor` per device. State is driven by
incoming reports (`apply_value`); writes are sent via the device's
`async_write` method using the descriptor's `attr_write` (when set) or
`attr` as the wire key.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import SwitchDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a switch per `SwitchDescriptor` per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, SwitchDescriptor, AqaraSwitch,
        ),
    )


class AqaraSwitch(AqaraEntity, SwitchEntity):
    """Two-state switch backed by an attr name."""

    _attr_is_on = False

    def __init__(self, hub, device, subentry, descriptor: SwitchDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor

    def apply_value(self, raw: str) -> None:
        """Mirror an incoming report onto `is_on`."""
        self._attr_is_on = raw == self.descriptor.on_value
        self.write_state_if_added()

    async def async_turn_on(self, **kwargs) -> None:
        """Send the on-value via the device's `async_write`."""
        await self.device.async_write(
            {self._write_attr: self.descriptor.on_value}
        )
        if self.descriptor.optimistic:
            self._attr_is_on = True
            self.write_state_if_added()

    async def async_turn_off(self, **kwargs) -> None:
        """Send the off-value via the device's `async_write`."""
        await self.device.async_write(
            {self._write_attr: self.descriptor.off_value}
        )
        if self.descriptor.optimistic:
            self._attr_is_on = False
            self.write_state_if_added()


__all__ = ["AqaraSwitch", "async_setup_entry"]
