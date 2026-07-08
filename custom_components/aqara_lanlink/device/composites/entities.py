"""Descriptor-less HA sub-entities for composite rids.

A composite rid packs several logical fields into one wire value. Each field
is surfaced as its own native HA entity (time / number / switch / text) bound
to a shared :class:`CompositeController`. The entity reads the current field
value through ``controller.get(field)`` and writes through
``controller.async_set(field, pyvalue)`` (a read-modify-write that re-encodes
all sibling fields). State is push-driven: each entity subscribes to the
controller in ``async_added_to_hass`` and never polls.

These entities are deliberately descriptor-less -- there is no per-field CSV
descriptor. They mirror the precedent set by ``AqaraPtzZoomNumber`` (a
controller-backed number that carries ``descriptor=None`` and a
``unique_id_suffix``). Naming/translation is applied by the wiring chunk; here
the entities are unnamed and identified only by their unique id.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.text import TextEntity
from homeassistant.components.time import TimeEntity

from ...entity import AqaraEntity
from .codecs import CompositeField
from .controller import CompositeController


class _CompositeBase(AqaraEntity):
    """Shared wiring for every composite sub-entity.

    Binds one (controller, field) pair, derives a unique id from the rid and
    field name, and subscribes to the controller for push updates.
    """

    def __init__(
        self,
        hub: Any,
        device: Any,
        subentry: Any,
        controller: CompositeController,
        field: CompositeField,
    ) -> None:
        super().__init__(
            hub,
            device,
            subentry,
            descriptor=None,
            unique_id_suffix=f"{controller.rid}_{field.name}",
        )
        self._controller = controller
        self._field = field
        self._attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        # write_state_if_added guards `hass is None` (parity with descriptor
        # entities whose apply_value can fire before add).
        self.async_on_remove(
            self._controller.add_listener(self.write_state_if_added)
        )


class CompositeSwitch(_CompositeBase, SwitchEntity):
    """One boolean field of a composite rid."""

    @property
    def is_on(self) -> bool:
        return bool(self._controller.get(self._field.name))

    async def async_turn_on(self, **_: Any) -> None:
        await self._controller.async_set(self._field.name, True)

    async def async_turn_off(self, **_: Any) -> None:
        await self._controller.async_set(self._field.name, False)


class CompositeNumber(_CompositeBase, NumberEntity):
    """One numeric field of a composite rid."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        p = self._field.params
        if "min" in p:
            self._attr_native_min_value = p["min"]
        if "max" in p:
            self._attr_native_max_value = p["max"]
        if "unit" in p:
            self._attr_native_unit_of_measurement = p["unit"]
        self._attr_native_step = p.get("step", 1)

    @property
    def native_value(self) -> Any:
        return self._controller.get(self._field.name)

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.async_set(self._field.name, int(value))


class CompositeText(_CompositeBase, TextEntity):
    """One free-text field of a composite rid (codec validates on write)."""

    @property
    def native_value(self) -> str:
        return str(self._controller.get(self._field.name))

    async def async_set_value(self, value: str) -> None:
        # The codec validates on encode; an invalid value propagates ValueError.
        await self._controller.async_set(self._field.name, value)


class CompositeTime(_CompositeBase, TimeEntity):
    """One ``datetime.time`` field of a composite rid."""

    @property
    def native_value(self) -> time:
        return self._controller.get(self._field.name)

    async def async_set_value(self, value: time) -> None:
        await self._controller.async_set(self._field.name, value)


__all__ = [
    "CompositeNumber",
    "CompositeSwitch",
    "CompositeText",
    "CompositeTime",
]
