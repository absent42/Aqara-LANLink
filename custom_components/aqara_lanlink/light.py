"""Light platform.

Builds one `AqaraLight` per `LightDescriptor` per device. The Light
absorbs power, brightness, color_temp, and xy color into one HA entity
(see `device/auto_derive.py:_detect_light_pattern`). Effects are
populated post-construction via `enrich_light_effects` from
`hub/cloud_client.py`.

Color temperature is Kelvin-native end-to-end (HA modern API; mireds
deprecated in HA 2026.1). The descriptor's `color_temp_wire_unit` field
remembers what the firmware reports ("K" or "mired") so reads convert
to Kelvin and writes convert back.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.descriptors import Effect, LightDescriptor
from .entity import AqaraEntity, build_descriptor_entities, setup_descriptor_platform
from .hub.cloud_client import AqaraAuthError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build an AqaraLight per LightDescriptor per device."""
    await setup_descriptor_platform(
        entry,
        async_add_entities,
        lambda hub, device, subentry: build_descriptor_entities(
            hub, device, subentry, LightDescriptor, AqaraLight,
        ),
    )


class AqaraLight(AqaraEntity, LightEntity):
    """Composite light entity backed by a LightDescriptor."""

    def __init__(self, hub, device, subentry, descriptor: LightDescriptor):
        super().__init__(hub, device, subentry, descriptor)
        self.entity_description = descriptor

        # color_mode must always be one of supported_color_modes (HA contract);
        # apply_value updates the active mode later for COLOR_TEMP/XY toggles.
        self._attr_supported_color_modes, self._attr_color_mode = (
            self._resolve_color_modes(descriptor)
        )

        # HA modern API: Kelvin natively.
        if descriptor.color_temp_min_kelvin is not None:
            self._attr_min_color_temp_kelvin = descriptor.color_temp_min_kelvin
        if descriptor.color_temp_max_kelvin is not None:
            self._attr_max_color_temp_kelvin = descriptor.color_temp_max_kelvin

        self._effects_by_name: dict[str, Effect] = {}
        if descriptor.effects:
            self._attr_effect_list = [e.name for e in descriptor.effects]
            self._effects_by_name = {e.name: e for e in descriptor.effects}
            self._attr_supported_features = LightEntityFeature.EFFECT

    # ---------------------------------------------------------------- inbound

    def apply_value(self, raw: str, component: str | None = None) -> None:
        """Mirror an incoming report onto the matching HA attribute."""
        desc = self.descriptor
        # Empty string means "no reading available" -- the hub occasionally
        # emits this for individual components (e.g. brightness when the
        # bulb is mid-scene transition). float("")/int("") would crash;
        # leave the existing attribute value alone and skip the state
        # write -- nothing has actually changed for this component.
        # Same guard pattern as AqaraSensor.apply_value (sensor.py).
        if raw == "":
            return
        if component == "power":
            self._attr_is_on = raw == "1"
        elif component == "brightness":
            # Wire 1-100 percent (hub-translated from Zigbee 0-254); HA wants 0-255.
            self._attr_brightness = round(float(raw) * 255 / 100)
        elif component == "color_temp":
            wire_value = float(raw)
            if desc.color_temp_wire_unit == "mired":
                self._attr_color_temp_kelvin = int(round(1_000_000 / wire_value))
            else:
                self._attr_color_temp_kelvin = int(round(wire_value))
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif component == "color_xy":
            # Wire format: uint32 packed (x_high8, x_low8, y_high8, y_low8) =
            # (x_uint16 << 16) | y_uint16. Aqara's app and Z2M's converter
            # both empirically scale to 65535 (vs the ZCL-spec max of 65279);
            # the bulb accepts both, the diff is below 0.4%, so match the
            # tooling used to generate our captured fixtures.
            packed = int(raw)
            x = ((packed >> 16) & 0xFFFF) / 65535.0
            y = (packed & 0xFFFF) / 65535.0
            self._attr_xy_color = (x, y)
            self._attr_color_mode = ColorMode.XY
        self.write_state_if_added()

    # --------------------------------------------------------------- outbound

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply incoming HA state to the bulb."""
        if (effect_name := kwargs.get(ATTR_EFFECT)) is not None:
            await self._async_apply_effect(effect_name)
            return
        await self.device.async_write(self._build_turn_on_writes(kwargs))

    async def _async_apply_effect(self, effect_name: str) -> None:
        """Activate a named effect: cloud-mediated dynamic scene (seq_id) or a
        static LANLink color_temp+brightness scene."""
        desc = self.descriptor
        effect = self._effects_by_name.get(effect_name)
        if effect is None:
            raise HomeAssistantError(
                f"Unknown effect {effect_name!r}; available: "
                f"{sorted(self._effects_by_name)}"
            )
        if effect.seq_id is not None:
            # Dynamic scene: cloud-mediated activation. Wrap cloud-client
            # exceptions in HomeAssistantError for a uniform HA error class.
            if self.hub.cloud_client is None:
                raise HomeAssistantError(
                    f"Aqara cloud client not initialised; cannot activate dynamic "
                    f"effect {effect_name!r}"
                )
            await self.device.async_write({desc.power_attr: "1"})
            try:
                await self.hub.cloud_client.run_sequence(
                    self.hub.token,
                    did=self.device.did,
                    seq_id=effect.seq_id,
                )
            except (AqaraAuthError, aiohttp.ClientError) as exc:
                raise HomeAssistantError(
                    f"Aqara cloud unreachable while activating effect "
                    f"{effect_name!r}: {exc}"
                ) from exc
        else:
            # Static scene: write color_temp + brightness via LANLink.
            writes: dict[Any, str] = {desc.power_attr: "1"}
            if effect.color_temp_kelvin is not None and desc.color_temp_attr:
                writes[desc.color_temp_attr] = self._color_temp_to_wire(
                    effect.color_temp_kelvin,
                )
            if effect.brightness_pct is not None and desc.brightness_attr:
                writes[desc.brightness_attr] = str(effect.brightness_pct)
            await self.device.async_write(writes)
        self._attr_effect = effect_name

    def _build_turn_on_writes(self, kwargs: dict[str, Any]) -> dict[Any, str]:
        """Build the LANLink write map for a standard (non-effect) turn-on."""
        desc = self.descriptor
        writes: dict[Any, str] = {desc.power_attr: "1"}
        if (b := kwargs.get(ATTR_BRIGHTNESS)) is not None and desc.brightness_attr:
            writes[desc.brightness_attr] = str(round(b * 100 / 255))
        if (ct_k := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None and desc.color_temp_attr:
            writes[desc.color_temp_attr] = self._color_temp_to_wire(ct_k)
        if (xy := kwargs.get(ATTR_XY_COLOR)) is not None and desc.color_xy_attr:
            # Pack as (x << 16) | y so the wire bytes serialise (big-endian)
            # as [x_high, x_low, y_high, y_low] -- the layout the Aqara
            # firmware (and Z2M converters) expect. Each coord uses the
            # 65535 scale Z2M uses empirically.
            x_int = round(xy[0] * 65535) & 0xFFFF
            y_int = round(xy[1] * 65535) & 0xFFFF
            writes[desc.color_xy_attr] = str((x_int << 16) | y_int)
        return writes

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.device.async_write({self.descriptor.power_attr: "0"})

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _resolve_color_modes(
        descriptor: LightDescriptor,
    ) -> tuple[set[ColorMode], ColorMode]:
        """Return (supported_color_modes, default active color_mode) for the
        descriptor. XY/COLOR_TEMP win over BRIGHTNESS, which wins over ONOFF."""
        modes: set[ColorMode] = set()
        if descriptor.color_xy_trait is not None:
            modes.add(ColorMode.XY)
        if descriptor.color_temp_trait is not None:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes and descriptor.brightness_trait is not None:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        if ColorMode.COLOR_TEMP in modes:
            default = ColorMode.COLOR_TEMP
        elif ColorMode.XY in modes:
            default = ColorMode.XY
        elif ColorMode.BRIGHTNESS in modes:
            default = ColorMode.BRIGHTNESS
        else:
            default = ColorMode.ONOFF
        return modes, default

    def _color_temp_to_wire(self, kelvin: int) -> str:
        """Convert HA's Kelvin to whatever the wire expects (mired or K)."""
        if self.descriptor.color_temp_wire_unit == "mired":
            return str(int(round(1_000_000 / kelvin)))
        return str(int(kelvin))


__all__ = ["AqaraLight", "async_setup_entry"]
