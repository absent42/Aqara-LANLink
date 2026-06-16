"""Generic select platform.

One `AqaraSelect` per `SelectDescriptor` per device. The descriptor's
`options_map` describes the `label -> wire-value` mapping in both
directions; incoming reports reverse-look-up the wire value to find the
option label, while `async_select_option` does the forward lookup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import SelectDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform
from .ptz import commands as _ptz_cmd

if TYPE_CHECKING:
    from .ptz.controller import PtzController

_LOGGER = logging.getLogger(__name__)


def _ptz_selects_for_device(
    hub, device, subentry, controller: "PtzController", cloud, token,
) -> list["AqaraEntity"]:
    """Build the PTZ preset select when the controller supports presets."""
    if _ptz_cmd.PRESETS not in controller.features:
        return []
    return [
        AqaraPtzPresetSelect(
            hub, device, subentry, controller,
            cloud=cloud, token=token, did=device.did,
        )
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a select per `SelectDescriptor` per device.

    Preset-capable PTZ cameras additionally get an imperative preset select
    populated from the cloud-stored position list and recalling positions
    locally through the warm-session `PtzController`.
    """
    data = entry.runtime_data
    ptz_controllers = getattr(data, "ptz_controllers", {})
    cloud = getattr(data, "cloud_client", None)
    token = getattr(data, "cloud_token", "")

    def build(hub, device, subentry) -> list:
        entities = build_descriptor_entities(
            hub, device, subentry, SelectDescriptor, AqaraSelect,
        )
        controller = ptz_controllers.get(device.did)
        if controller is not None:
            entities.extend(
                _ptz_selects_for_device(
                    hub, device, subentry, controller, cloud, token,
                )
            )
        return entities

    await setup_descriptor_platform(entry, async_add_entities, build)


class AqaraSelect(AqaraEntity, SelectEntity):
    """Multi-state selector backed by `descriptor.options_map`."""

    def __init__(self, hub, device, subentry, descriptor: SelectDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor
        self._attr_options = [label for label, _ in descriptor.options_map]
        self._attr_current_option = None

    def apply_value(self, raw: str) -> None:
        """Reverse-look-up the wire value and set `current_option`."""
        for label, wire_value in self.descriptor.options_map:
            if wire_value == raw:
                self._attr_current_option = label
                self.write_state_if_added()
                return
        # Unknown wire value -- log and leave current_option unchanged.
        _LOGGER.warning(
            "select %s: unknown wire value %r (known: %s)",
            self.descriptor.key, raw,
            sorted(wire for _, wire in self.descriptor.options_map),
        )

    async def async_select_option(self, option: str) -> None:
        """Forward the option's wire value to the device."""
        for label, wire_value in self.descriptor.options_map:
            if label == option:
                await self.device.async_write({self._write_attr: wire_value})
                if self.descriptor.optimistic:
                    self._attr_current_option = option
                    self.write_state_if_added()
                return
        raise KeyError(option)


class AqaraPtzPresetSelect(AqaraEntity, SelectEntity):
    """Preset-recall selector for a PTZ camera.

    Presets live in the cloud account (not the camera): the option list is the
    saved positions' names, fetched via `query_ptz_positions`; selecting a name
    recalls its opaque `position` string locally via the controller's 4140
    IOCTL. The selection is optimistic (the camera reports no preset state on
    the LAN plane). The name->position map self-heals: if a requested name is
    missing it is refetched once before failing, so a preset added in the Aqara
    app becomes recallable without a reload.
    """

    # Steady-state is local: the option list is fetched once on add-to-hass and
    # self-heals on a select miss, so there is no recurring cloud poll.
    _attr_should_poll = False

    def __init__(self, hub, device, subentry, controller, *, cloud, token, did):
        super().__init__(
            hub, device, subentry,
            descriptor=None, unique_id_suffix="ptz_preset",
        )
        self._controller = controller
        self._cloud = cloud
        self._token = token
        self._did = did
        self._attr_translation_key = "ptz_preset"
        self._by_name: dict[str, str] = {}
        self._attr_options = []
        self._attr_current_option = None

    async def _refresh_options(self) -> None:
        """Fetch the cloud preset list and rebuild the name->position map."""
        if self._cloud is None:
            return
        try:
            positions = await self._cloud.query_ptz_positions(self._token, self._did)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "ptz preset select %s: failed to fetch positions: %s",
                self._did, err,
            )
            return
        by_name: dict[str, str] = {}
        for entry in positions or ():
            name = entry.get("name")
            position = entry.get("position")
            if name and position:
                by_name[name] = position
        self._by_name = by_name
        self._attr_options = list(by_name)
        if self._attr_current_option not in by_name:
            self._attr_current_option = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._refresh_options()
        self.write_state_if_added()

    async def async_select_option(self, option: str) -> None:
        """Recall the named preset, refetching once if it is unknown."""
        position = self._by_name.get(option)
        if position is None:
            # Self-heal: a preset added in the Aqara app after the last fetch.
            await self._refresh_options()
            position = self._by_name.get(option)
        if position is None:
            raise KeyError(option)
        await self._controller.goto_preset(position)
        self._attr_current_option = option
        self.write_state_if_added()


__all__ = ["AqaraPtzPresetSelect", "AqaraSelect", "async_setup_entry"]
