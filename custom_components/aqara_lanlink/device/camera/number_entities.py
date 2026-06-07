"""HA-local number entities for camera devices.

DetectionClearDelayNumber is a user preference, not a device trait: it
sets how long a camera's motion / occupancy binary sensors stay "on"
after a detection pulse before auto-clearing. It is not descriptor-driven
and never writes to the device. RestoreNumber persists the value across
restarts; the owning camera device reads `native_value` at binary-sensor
arm time via `resolve_auto_clear_seconds`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime

from ...entity import AqaraEntity

DEFAULT_DETECTION_CLEAR_DELAY_S = 30.0
MIN_DETECTION_CLEAR_DELAY_S = 15.0
MAX_DETECTION_CLEAR_DELAY_S = 600.0
DETECTION_CLEAR_DELAY_STEP_S = 15.0


class DetectionClearDelayNumber(AqaraEntity, RestoreNumber):
    """HA-local, user-tunable auto-clear delay for camera detection sensors."""

    _attr_translation_key = "detection_clear_delay"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = MIN_DETECTION_CLEAR_DELAY_S
    _attr_native_max_value = MAX_DETECTION_CLEAR_DELAY_S
    _attr_native_step = DETECTION_CLEAR_DELAY_STEP_S
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hub: Any, device: Any, subentry: Any) -> None:
        AqaraEntity.__init__(
            self, hub, device, subentry,
            descriptor=None, unique_id_suffix="detection_clear_delay",
        )
        RestoreNumber.__init__(self)
        self._attr_native_value = DEFAULT_DETECTION_CLEAR_DELAY_S

    async def async_added_to_hass(self) -> None:
        # AqaraEntity.async_added_to_hass is a no-op for descriptor=None
        # entities, but call it for forward-compatibility.
        await AqaraEntity.async_added_to_hass(self)
        await RestoreNumber.async_added_to_hass(self)
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Store the new delay locally. No device write -- this is a preference."""
        self._attr_native_value = value
        if self.hass is not None:
            self.async_write_ha_state()


__all__ = [
    "DEFAULT_DETECTION_CLEAR_DELAY_S",
    "DetectionClearDelayNumber",
]
