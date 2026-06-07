"""Shared base entity for Aqara LANLink platforms.

Every platform-specific entity class extends `AqaraEntity` plus the HA
entity base for its platform. The base class wires up:

- A unique id derived from `subentry.subentry_id` and either the
  descriptor's `key` (descriptor-driven entities) or a caller-supplied
  `unique_id_suffix` (camera entities, which don't use descriptors).
- A `DeviceInfo` block linking the entity's HA device to the parent hub
  via `via_device`.
- An `available` property reflecting `hub.connected`.
- A descriptor-routing hook in `async_added_to_hass` that calls
  `device.register_entity(descriptor, self)` for descriptor-driven
  entities. Camera entities (descriptor=None) opt out.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .device.base import Device
from .device.descriptors import AnyDescriptor


class AqaraEntity(Entity):
    """Base class for every Aqara LANLink entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hub: Any,
        device: Device,
        subentry: Any,
        descriptor: AnyDescriptor | None = None,
        *,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Wire shared metadata.

        Either `descriptor` or `unique_id_suffix` must be provided.
        Descriptor-driven entities pass the descriptor; camera entities
        (which have no descriptor) pass ``descriptor=None,
        unique_id_suffix="camera"``.
        """
        self.hub = hub
        self.device = device
        self.descriptor = descriptor
        if descriptor is None and not unique_id_suffix:
            raise ValueError(
                "AqaraEntity requires either descriptor or unique_id_suffix"
            )
        if descriptor is not None:
            self._attr_unique_id = f"{subentry.subentry_id}_{descriptor.key}"
        else:
            self._attr_unique_id = f"{subentry.subentry_id}_{unique_id_suffix}"
        # Omit via_device when the entity's own device IS the hub (self-device
        # entities). HA silently ignores a self-referential via_device, but
        # including it is a code smell; skip it to keep the registry clean.
        own_did = subentry.data["did"]
        device_info_kwargs: dict = {
            "identifiers": {(DOMAIN, own_did)},
            "name": device.DISPLAY_NAME,
            "manufacturer": device.MANUFACTURER,
            "model": device.MODEL,
        }
        if own_did != hub.did:
            device_info_kwargs["via_device"] = (DOMAIN, hub.did)
        self._attr_device_info = DeviceInfo(**device_info_kwargs)

    @property
    def available(self) -> bool:
        """Available while the hub tunnel is up -- unless this device is a
        LAN-direct endpoint (e.g. a camera) that has dropped out of the hub's
        topology, in which case it is offline even though the tunnel is up.
        Zigbee sub-devices (never in the LAN-direct topology) are unaffected.
        """
        if not self.hub.connected:
            return False
        offline = getattr(self.hub, "is_lan_direct_offline", None)
        if callable(offline) and offline(self.device.did):
            return False
        return True

    async def async_added_to_hass(self) -> None:
        """Register the entity with its device for trait-routed updates."""
        if self.descriptor is not None:
            self.async_on_remove(
                self.device.register_entity(self.descriptor, self)
            )

    def write_state_if_added(self) -> None:
        """Write HA state, but only once the entity is added to hass.

        Descriptor ``apply_value`` hooks can fire from device-layer seeding
        before ``async_added_to_hass``; at that point ``self.hass`` is None and
        ``async_write_ha_state()`` would raise.
        """
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def _write_attr(self):
        """AttrSpec used for outgoing writes: ``attr_write`` or ``attr``.

        ``getattr`` guards keep this safe for descriptor types that declare
        neither (e.g. sensors), though only the writable platforms read it.
        """
        desc = self.descriptor
        return getattr(desc, "attr_write", None) or getattr(desc, "attr", None)


def build_descriptor_entities(hub, device, subentry, descriptor_cls, entity_cls):
    """Instantiate ``entity_cls`` for each ``descriptor_cls`` on ``device``.

    Replaces the per-platform ``_<platform>_entities_for_device`` helpers, which
    were all the same isinstance-filtered comprehension differing only in the
    descriptor and entity classes.
    """
    return [
        entity_cls(hub, device, subentry, desc)
        for desc in device.descriptors
        if isinstance(desc, descriptor_cls)
    ]


async def setup_descriptor_platform(
    entry: Any,
    async_add_entities: Any,
    build_fn: Callable[[Any, Device, Any], list | Awaitable[list]],
) -> None:
    """Shared platform setup: per-device entities plus the self_device tail.

    ``build_fn(hub, device, subentry)`` returns -- or awaits to -- the list of
    entities for one device. Sub-device entities are added under their config
    subentry; the hub's own ``self_device`` entities are added without one.
    Centralises the devices-loop + self_device-tail skeleton every descriptor
    platform repeated.
    """
    data = entry.runtime_data

    async def _build(device, subentry) -> list:
        result = build_fn(data.hub, device, subentry)
        if inspect.isawaitable(result):
            result = await result
        return result

    for subentry_id, device in data.devices.items():
        subentry = entry.subentries[subentry_id]
        entities = await _build(device, subentry)
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)
    self_device = data.self_device
    if self_device is not None:
        entities = await _build(self_device, self_device.subentry)
        if entities:
            async_add_entities(entities)


__all__ = [
    "AqaraEntity",
    "build_descriptor_entities",
    "setup_descriptor_platform",
]
