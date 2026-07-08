"""Composite entity controller.

One :class:`CompositeController` exists per (device, rid). It holds the
last-decoded field dict for a composite rid, seeds itself from a wire
string, exposes per-field getters, and performs read-modify-write on any
single-field change by re-encoding ALL fields and writing them back to the
device as one wire value.

This module has no Home Assistant entity code - it is the plain-Python
bridge between the pure codec layer and the device write path. The write
goes out as ``await device.async_write({attr_spec: wire})`` where the key is
an :class:`AttrSpec` object (never a bare string) and the value is the
codec-encoded string.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..attrs import AttrSpec

_LOGGER = logging.getLogger(__name__)


class CompositeController:
    def __init__(self, device, rid: str, codec):
        self._device = device
        self._rid = rid
        self._codec = codec
        self._attr = AttrSpec(name=rid, resource_id=rid)
        self._fields: dict[str, Any] = dict(codec.defaults())
        self._listeners: list[Callable[[], None]] = []

    @property
    def rid(self) -> str:
        return self._rid

    @property
    def codec(self):
        return self._codec

    def seed(self, wire: str) -> None:
        """Decode ``wire`` into the field dict. Bad wire keeps defaults."""
        try:
            self._fields = self._codec.decode(wire)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("composite %s: undecodable seed %r", self._rid, wire)
            return
        self._notify()

    def get(self, field_name: str) -> Any:
        return self._fields.get(field_name, self._codec.defaults()[field_name])

    async def async_set(self, field_name: str, value: Any) -> None:
        """Set one field, re-encode ALL fields, and write to the device."""
        self._fields[field_name] = value
        wire = self._codec.encode(self._fields)
        await self._device.async_write({self._attr: wire})
        self._notify()

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()
