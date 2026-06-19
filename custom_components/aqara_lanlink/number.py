"""Generic number platform.

One `AqaraNumber` per `NumberDescriptor` per device. Round-trips wire
values through `descriptor.transform_in` / `transform_out` (both default
to int <-> str when not explicitly provided).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import NumberDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform
from .ptz import commands as _ptz_cmd

if TYPE_CHECKING:
    from .ptz.controller import PtzController


def _ptz_numbers_for_device(
    hub, device, subentry, controller: "PtzController",
) -> list["AqaraEntity"]:
    """Build the PTZ zoom number entity when the controller supports zoom."""
    if _ptz_cmd.ZOOM not in controller.features:
        return []
    return [AqaraPtzZoomNumber(hub, device, subentry, controller)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a number entity per `NumberDescriptor` per device.

    Zoom-capable PTZ cameras additionally get an imperative zoom number that
    drives their warm-session `PtzController`.
    """
    ptz_controllers = getattr(entry.runtime_data, "ptz_controllers", {})

    async def build(hub, device, subentry) -> list:
        entities = build_descriptor_entities(
            hub, device, subentry, NumberDescriptor, AqaraNumber,
        )
        entities.extend(await device.async_setup_extra_numbers(hub, subentry))
        controller = ptz_controllers.get(device.did)
        if controller is not None:
            entities.extend(_ptz_numbers_for_device(hub, device, subentry, controller))
        return entities

    await setup_descriptor_platform(entry, async_add_entities, build)


def _default_transform_in(raw: str) -> int | float:
    """Default wire->python coercion: int when possible, else float."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return float(raw)


def _default_transform_out(value: int | float) -> str:
    """Default python->wire coercion: stringify."""
    return str(value)


class AqaraNumber(AqaraEntity, NumberEntity):
    """Numeric input backed by an attr name."""

    def __init__(self, hub, device, subentry, descriptor: NumberDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        # Only override HA's NumberEntity defaults (0.0 / 100.0 / 1.0) when the
        # descriptor actually carries a value. A rid-setting number with no CSV
        # range leaves min/max/step None; assigning None to these `_attr_*`
        # fields makes `native_min_value`/`native_max_value` return None, which
        # breaks the frontend slider (the value can't be changed and `step`
        # even raises). Leaving them unset lets HA fall back to 0/100/1.
        if descriptor.min_value is not None:
            self._attr_native_min_value = descriptor.min_value
        if descriptor.max_value is not None:
            self._attr_native_max_value = descriptor.max_value
        if descriptor.step is not None:
            self._attr_native_step = descriptor.step
        self._attr_native_value = None

    def apply_value(self, raw: str) -> None:
        """Coerce wire value via `descriptor.transform_in`, optionally apply scale."""
        # Empty string means "no reading available" -- the hub occasionally
        # emits this for traits whose firmware hasn't produced a value yet
        # (observed for `2.134.20107` StartUpColorTemperature on agl010).
        # int("")/float("") would crash; treat as None so the entity
        # registers cleanly and transitions to a real value on the next push.
        # Same guard pattern as AqaraSensor.apply_value / AqaraLight.apply_value.
        if raw == "":
            self._attr_native_value = None
            self.write_state_if_added()
            return
        transform = self.descriptor.transform_in or _default_transform_in
        value = transform(raw)
        scale = getattr(self.entity_description, "scale", None)
        if scale is not None:
            try:
                value = float(value) * scale
            except (TypeError, ValueError):
                pass  # non-numeric transform output; leave value untransformed
        self._attr_native_value = value
        self.write_state_if_added()

    async def async_set_native_value(self, value: float) -> None:
        """Encode `value` via `descriptor.transform_out` and write.

        When `descriptor.scale` is set, HA's value is in user-facing units;
        divide by scale before encoding so the device receives the wire value.
        """
        wire_value: float = value
        scale = getattr(self.entity_description, "scale", None)
        if scale is not None and scale != 0:
            try:
                wire_value = float(value) / scale
            except (TypeError, ValueError):
                pass  # leave value as-is if non-numeric
        transform = self.descriptor.transform_out or _default_transform_out
        await self.device.async_write({self._write_attr: transform(wire_value)})
        if self.descriptor.optimistic:
            self._attr_native_value = value
            self.write_state_if_added()


class AqaraPtzZoomNumber(AqaraEntity, NumberEntity):
    """Optical-zoom magnification slider backed by the PTZ controller.

    Not descriptor-driven: the value lives in the warm-session controller's
    optimistic zoom tracker (the camera reports no zoom state on the LAN
    plane), so `native_value` reads `controller.current_zoom`.
    """

    # Optimistic in-memory tracker (controller.current_zoom); never polled and
    # not restored across an HA restart (resets to ZOOM_MIN), acceptable here.
    _attr_should_poll = False

    _attr_native_min_value = _ptz_cmd.ZOOM_MIN
    _attr_native_max_value = _ptz_cmd.ZOOM_MAX
    _attr_native_step = _ptz_cmd.ZOOM_STEP

    def __init__(self, hub, device, subentry, controller):
        super().__init__(
            hub, device, subentry,
            descriptor=None, unique_id_suffix="ptz_zoom",
        )
        self._controller = controller
        self._attr_translation_key = "ptz_zoom"

    @property
    def native_value(self) -> float:
        return self._controller.current_zoom

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.ptz_zoom(value)
        self.write_state_if_added()


__all__ = ["AqaraNumber", "AqaraPtzZoomNumber", "async_setup_entry"]
