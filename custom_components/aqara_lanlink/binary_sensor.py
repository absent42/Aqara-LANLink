"""Generic binary-sensor platform.

Iterates `entry.runtime_data.devices`; for each device, walks
`device.descriptors` and instantiates one `AqaraBinarySensor`
per `BinarySensorDescriptor`. The entity class implements the
descriptor-driven `apply_value(raw)` hook plus optional auto-clear.
"""

from __future__ import annotations


from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, PUSH_STALL_TTL_SECONDS
from .device.base import _should_replay_seed
from .device.descriptors import BinarySensorDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a binary sensor per `BinarySensorDescriptor` per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, BinarySensorDescriptor, AqaraBinarySensor,
        ),
    )
    # One hub-level diagnostic: is the tunnel actually forwarding right now?
    async_add_entities([AqaraHubForwardingHealth(entry.runtime_data.hub)])


class AqaraBinarySensor(AqaraEntity, RestoreEntity, BinarySensorEntity):
    """Binary sensor backed by a single trait id.

    On a matching report the entity sets `is_on` and (re)schedules an
    auto-clear timer when `device.resolve_auto_clear_seconds` returns a
    truthy delay. The handle is cancelled on next trigger or on entity
    removal.

    On HA restart / integration reload, stateful descriptors (door/leak/
    presence sensors -- no `auto_clear_seconds`, no `on_any_value`)
    restore their last persisted is_on from the recorder so the entity
    doesn't boot as off until the next push report arrives. Momentary
    descriptors deliberately skip the restore: their last persisted
    state could be a stale "on" from a trigger that never got cleared,
    and the auto-clear timer would not fire on restore.
    """

    _attr_is_on = False

    def __init__(self, hub, device, subentry, descriptor: BinarySensorDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        self._auto_clear_handle = None

    async def async_added_to_hass(self) -> None:
        """Restore last is_on (stateful only), then run normal registration.

        Order matters: restore BEFORE super().async_added_to_hass() so
        the cloud-seed replay inside register_entity (when available)
        overrides the restored value. If the cloud is unreachable on
        startup the restored value is the fallback.
        """
        if _should_replay_seed(self.descriptor):
            last = await self.async_get_last_state()
            if last is not None and last.state == STATE_ON:
                self._attr_is_on = True
        await super().async_added_to_hass()

    def apply_value(self, raw: str) -> None:
        """Apply a wire value: on if in `on_values` (or any non-empty
        non-"0" payload when descriptor.on_any_value is set), else off."""
        if self.descriptor.on_any_value:
            is_on = raw not in ("", "0")
        else:
            is_on = raw in self.descriptor.on_values
        self._attr_is_on = is_on
        if is_on and self.device.resolve_auto_clear_seconds(self.descriptor):
            self._reschedule_auto_clear()
        elif not is_on:
            self._cancel_auto_clear()
        self.write_state_if_added()

    def _reschedule_auto_clear(self) -> None:
        self._cancel_auto_clear()
        if self.hass is None:
            return
        delay = self.device.resolve_auto_clear_seconds(self.descriptor)
        if not delay:
            return
        self._auto_clear_handle = self.hass.loop.call_later(
            delay, self._auto_clear,
        )

    def _cancel_auto_clear(self) -> None:
        handle = self._auto_clear_handle
        self._auto_clear_handle = None
        if handle is not None:
            handle.cancel()

    def _auto_clear(self) -> None:
        """Fire-and-forget callback that clears the sensor after the timeout."""
        self._auto_clear_handle = None
        if not self._attr_is_on:
            return
        self._attr_is_on = False
        self.write_state_if_added()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the auto-clear timer on unload."""
        self._cancel_auto_clear()


class AqaraHubForwardingHealth(BinarySensorEntity):
    """Diagnostic connectivity sensor: on when the hub is actually forwarding.

    'On' means the tunnel is up, the hub reports a populated LAN-direct
    topology, and a report (incl. the hub's own heartbeat) has arrived within
    the push-stall TTL. It flips off exactly in the wedged-forwarding case the
    watchdog acts on, giving users and automations a signal for it.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Tunnel forwarding"

    def __init__(self, hub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.did}_forwarding_health"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub.did)})

    @property
    def available(self) -> bool:
        return bool(self._hub.connected)

    @property
    def is_on(self) -> bool:
        return (
            bool(self._hub.connected)
            and len(self._hub.lanlink_topology_dids) > 0
            and self._hub.seconds_since_last_report() < PUSH_STALL_TTL_SECONDS
        )


__all__ = [
    "AqaraBinarySensor",
    "AqaraHubForwardingHealth",
    "async_setup_entry",
]
