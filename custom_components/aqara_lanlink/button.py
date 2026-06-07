"""Generic button platform.

One `AqaraButton` per `ButtonDescriptor` per device. Press triggers a
one-shot write of `press_value` to the descriptor's `attr` -- canonical
use is writing to Identify.IdentifyType to make the physical device's
LED blink for visual identification.

Stateless: ButtonEntity has no state to mirror from incoming reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import ButtonDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform
from .ptz import commands as _ptz_cmd

if TYPE_CHECKING:
    from .ptz.controller import PtzController


def _ptz_buttons_for_device(
    hub, device, subentry, controller: "PtzController",
) -> list["AqaraEntity"]:
    """Build PTZ button entities gated by the controller's sub-features.

    pan_tilt -> up/down/left/right + stop; zoom -> zoom_in/zoom_out.
    """
    feats = controller.features
    out: list[AqaraEntity] = []
    if _ptz_cmd.PAN_TILT in feats:
        for direction in ("up", "down", "left", "right"):
            out.append(
                AqaraPtzDirectionButton(
                    hub, device, subentry, controller, direction,
                )
            )
        out.append(AqaraPtzStopButton(hub, device, subentry, controller))
    if _ptz_cmd.ZOOM in feats:
        out.append(
            AqaraPtzZoomButton(
                hub, device, subentry, controller,
                _ptz_cmd.ZOOM_BUTTON_STEP, "zoom_in",
            )
        )
        out.append(
            AqaraPtzZoomButton(
                hub, device, subentry, controller,
                -_ptz_cmd.ZOOM_BUTTON_STEP, "zoom_out",
            )
        )
    return out


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a button per `ButtonDescriptor` per device.

    PTZ-capable cameras additionally get imperative pan/tilt/zoom buttons
    wrapping their warm-session `PtzController` (looked up by DID).
    """
    ptz_controllers = getattr(entry.runtime_data, "ptz_controllers", {})

    def build(hub, device, subentry) -> list:
        entities = build_descriptor_entities(
            hub, device, subentry, ButtonDescriptor, AqaraButton,
        )
        controller = ptz_controllers.get(device.did)
        if controller is not None:
            entities.extend(_ptz_buttons_for_device(hub, device, subentry, controller))
        return entities

    await setup_descriptor_platform(entry, async_add_entities, build)


class AqaraButton(AqaraEntity, ButtonEntity):
    """Stateless press-to-trigger entity backed by a writable trait id."""

    def __init__(self, hub, device, subentry, descriptor: ButtonDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor

    async def async_press(self) -> None:
        """Fire the one-shot device action by writing press_value to the trait."""
        await self.device.async_write(
            {self.descriptor.attr: self.descriptor.press_value}
        )


class AqaraPtzDirectionButton(AqaraEntity, ButtonEntity):
    """One-tap pan/tilt nudge in a fixed direction via the PTZ controller."""

    def __init__(self, hub, device, subentry, controller, direction: str):
        super().__init__(
            hub, device, subentry,
            descriptor=None, unique_id_suffix=f"ptz_{direction}",
        )
        self._controller = controller
        self._direction = direction
        self._attr_translation_key = f"ptz_{direction}"

    async def async_press(self) -> None:
        await self._controller.ptz_move(self._direction)


class AqaraPtzStopButton(AqaraEntity, ButtonEntity):
    """Stop an in-progress continuous pan/tilt move."""

    def __init__(self, hub, device, subentry, controller):
        super().__init__(
            hub, device, subentry,
            descriptor=None, unique_id_suffix="ptz_stop",
        )
        self._controller = controller
        self._attr_translation_key = "ptz_stop"

    async def async_press(self) -> None:
        await self._controller.ptz_stop()


class AqaraPtzZoomButton(AqaraEntity, ButtonEntity):
    """Step the optical zoom in/out by a fixed delta via the controller."""

    def __init__(self, hub, device, subentry, controller, delta: float, suffix: str):
        super().__init__(
            hub, device, subentry,
            descriptor=None, unique_id_suffix=f"ptz_{suffix}",
        )
        self._controller = controller
        self._delta = delta
        self._attr_translation_key = f"ptz_{suffix}"

    async def async_press(self) -> None:
        await self._controller.zoom_step(self._delta)


__all__ = [
    "AqaraButton",
    "AqaraPtzDirectionButton",
    "AqaraPtzStopButton",
    "AqaraPtzZoomButton",
    "async_setup_entry",
]
