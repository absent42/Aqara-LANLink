"""Tests for the AqaraLight platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    Effect,
    LightDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.light import AqaraLight

from .conftest import make_device, make_hub, make_subentry


def _full_descriptor(
    *,
    color_temp_wire_unit: str = "mired",
    effects: tuple[Effect, ...] = (),
) -> LightDescriptor:
    return LightDescriptor(
        key="light",
        translation_key="light",
        power_trait=TraitSpec(id="path_power", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path_power", id="4.1.85", data_type="bool"),
        brightness_trait=TraitSpec(id="path_bright", name="bright", data_type="number", unit="%"),
        brightness_attr=AttrSpec(name="path_bright", id="1.7.85", data_type="number", unit="%"),
        color_temp_trait=TraitSpec(id="path_ct", name="ct", data_type="number", unit=color_temp_wire_unit),
        color_temp_attr=AttrSpec(name="path_ct", id="1.9.85", data_type="number", unit=color_temp_wire_unit),
        color_temp_min_kelvin=2700,
        color_temp_max_kelvin=6500,
        color_temp_wire_unit=color_temp_wire_unit,
        color_xy_trait=TraitSpec(id="path_xy", name="xy", data_type="uint"),
        color_xy_attr=AttrSpec(name="path_xy", id="14.8.85", data_type="uint"),
        effects=effects,
    )


def _brightness_only_descriptor() -> LightDescriptor:
    return LightDescriptor(
        key="light",
        translation_key="light",
        power_trait=TraitSpec(id="path_power", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path_power", id="4.1.85", data_type="bool"),
        brightness_trait=TraitSpec(id="path_bright", name="bright", data_type="number", unit="%"),
        brightness_attr=AttrSpec(name="path_bright", id="1.7.85", data_type="number", unit="%"),
    )


def test_supported_color_modes_full_descriptor():
    from homeassistant.components.light import ColorMode
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    assert light._attr_supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.XY}
    assert light._attr_color_mode == ColorMode.COLOR_TEMP  # priority order
    assert light._attr_min_color_temp_kelvin == 2700
    assert light._attr_max_color_temp_kelvin == 6500


def test_supported_color_modes_brightness_only():
    from homeassistant.components.light import ColorMode
    desc = _brightness_only_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    assert light._attr_supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light._attr_color_mode == ColorMode.BRIGHTNESS


def test_supported_color_modes_power_only_falls_to_onoff():
    """Pathological: power-only LightDescriptor (the pattern detector should not produce one)."""
    from homeassistant.components.light import ColorMode

    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="p", name="power", data_type="bool"),
        power_attr=AttrSpec(name="p", id="4.1.85", data_type="bool"),
    )
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    assert light._attr_supported_color_modes == {ColorMode.ONOFF}
    assert light._attr_color_mode == ColorMode.ONOFF


def test_apply_value_power_component():
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    light.apply_value("1", component="power")
    assert light._attr_is_on is True
    light.apply_value("0", component="power")
    assert light._attr_is_on is False


def test_apply_value_brightness_component_scales_to_255():
    """Wire is 1-100 percent (hub-translated); HA wants 0-255."""
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    light.apply_value("100", component="brightness")
    assert light._attr_brightness == 255
    light.apply_value("50", component="brightness")
    assert light._attr_brightness == 128  # 50*255/100 = 127.5, round to 128


def test_apply_value_color_temp_mired_to_kelvin():
    """Wire is mired (T2 default); apply_value converts to Kelvin."""
    from homeassistant.components.light import ColorMode

    desc = _full_descriptor(color_temp_wire_unit="mired")
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    light.apply_value("250", component="color_temp")  # 4000K
    assert light._attr_color_temp_kelvin == 4000
    assert light._attr_color_mode == ColorMode.COLOR_TEMP


def test_apply_value_color_temp_kelvin_passthrough():
    """Wire is K (some firmwares); apply_value passes through."""
    desc = _full_descriptor(color_temp_wire_unit="K")
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    light.apply_value("4000", component="color_temp")
    assert light._attr_color_temp_kelvin == 4000


def test_apply_value_empty_string_skips_update_without_crashing():
    """The hub occasionally reports `""` for an individual component
    (observed for brightness mid-scene-transition on lumi.light.agl003,
    user log 2026-05-26 23:01:40). Without a guard, ``float("")``/
    ``int("")`` raises ValueError and propagates up to
    Device._apply, polluting the log with a stack trace. The guard
    must leave the existing attribute value alone (no fresh data to
    apply) and not crash. Parallel to AqaraSensor.apply_value, which
    treats empty as None.
    """
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    # Seed the entity with a known brightness, then push an empty
    # update on each numeric component -- none should crash, and the
    # seeded value must be preserved.
    light.apply_value("75", component="brightness")
    assert light._attr_brightness == round(75 * 255 / 100)
    seeded = light._attr_brightness
    for comp in ("brightness", "color_temp", "color_xy"):
        light.apply_value("", component=comp)  # must not raise
    assert light._attr_brightness == seeded, (
        "empty-string update must leave brightness untouched"
    )


def test_apply_value_color_xy_unpacks_uint32():
    """Packed uint32 = (x*65535 << 16) | y*65535."""
    from homeassistant.components.light import ColorMode

    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    # x = 0.4, y = 0.4 -> x_int = 26214, y_int = 26214 -> packed = 26214 << 16 | 26214
    packed = (26214 << 16) | 26214
    light.apply_value(str(packed), component="color_xy")
    assert light._attr_color_mode == ColorMode.XY
    x, y = light._attr_xy_color
    assert abs(x - 0.4) < 0.001
    assert abs(y - 0.4) < 0.001


@pytest.mark.parametrize(
    "wire_value, expected_x, expected_y",
    [
        # Empirically captured res/write payloads from a live T2 RGB CCT
        # (2026-05-05, lumi.light.agl003 paired against a hub M3). Each
        # wire value was sent by the Aqara mobile app when the user picked
        # a colour in the picker. Decoded under (x*65535 << 16) | y*65535;
        # the resulting (x, y) coordinates are within the CIE 1931 visible
        # gamut, which is the sanity check that the encoding is right.
        ("2646824561", 0.6163, 0.3377),  # 0x9DC3_5671 = saturated red
        ("1161130849", 0.2703, 0.4507),  # 0x4535_7361 = green-cyan
        ( "958540858", 0.2232, 0.1728),  # 0x3922_2C3A = deep blue
        ("1540115938", 0.3586, 0.3042),  # 0x5BCC_4DE2 = neutral / pale
    ],
)
def test_apply_value_color_xy_real_captures(wire_value, expected_x, expected_y):
    """Empirical-regression coverage: real captured xy writes decode to
    plausible chromaticity coordinates under (x*65535 << 16) | y*65535.
    All decoded points are inside the CIE 1931 visible gamut (no clipping
    artefacts).
    """
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    light.apply_value(wire_value, component="color_xy")
    x, y = light._attr_xy_color
    assert abs(x - expected_x) < 0.001, f"x={x} expected ~{expected_x}"
    assert abs(y - expected_y) < 0.001, f"y={y} expected ~{expected_y}"


@pytest.mark.asyncio
async def test_async_turn_off_writes_power_zero():
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(make_hub(), device, sub, desc)
    await light.async_turn_off()
    device.async_write.assert_called_once_with({desc.power_attr: "0"})


@pytest.mark.asyncio
async def test_async_turn_on_with_brightness_scales_255_to_100():
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(make_hub(), device, sub, desc)
    from homeassistant.components.light import ATTR_BRIGHTNESS
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})
    # 128 * 100 / 255 = 50.196 -> 50
    args = device.async_write.call_args[0][0]
    assert args[desc.brightness_attr] == "50"
    assert args[desc.power_attr] == "1"


@pytest.mark.asyncio
async def test_async_turn_on_with_color_temp_kelvin_converts_to_mired_for_wire():
    desc = _full_descriptor(color_temp_wire_unit="mired")
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(make_hub(), device, sub, desc)
    from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN
    await light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    args = device.async_write.call_args[0][0]
    # 1_000_000 / 4000 = 250
    assert args[desc.color_temp_attr] == "250"


@pytest.mark.asyncio
async def test_async_turn_on_with_xy_packs_uint32():
    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(make_hub(), device, sub, desc)
    from homeassistant.components.light import ATTR_XY_COLOR
    await light.async_turn_on(**{ATTR_XY_COLOR: (0.4, 0.4)})
    args = device.async_write.call_args[0][0]
    packed = int(args[desc.color_xy_attr])
    assert ((packed >> 16) & 0xFFFF) == 26214
    assert (packed & 0xFFFF) == 26214


@pytest.mark.asyncio
async def test_async_turn_on_static_effect_writes_color_temp_and_brightness_no_cloud():
    """Static scene: write color_temp + brightness via LANLink, no cloud call."""
    static = Effect(name="Reading", color_temp_kelvin=4000, brightness_pct=80)
    desc = _full_descriptor(effects=(static,))
    sub = make_subentry()
    hub = make_hub()
    hub.cloud_client = MagicMock()
    hub.cloud_client.run_sequence = AsyncMock()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(hub, device, sub, desc)
    from homeassistant.components.light import ATTR_EFFECT
    await light.async_turn_on(**{ATTR_EFFECT: "Reading"})
    args = device.async_write.call_args[0][0]
    assert args[desc.power_attr] == "1"
    assert args[desc.color_temp_attr] == "250"  # mired (4000K -> 250 mired)
    assert args[desc.brightness_attr] == "80"
    hub.cloud_client.run_sequence.assert_not_called()


@pytest.mark.asyncio
async def test_async_turn_on_dynamic_effect_calls_cloud_run_sequence():
    """Dynamic scene: LANLink power write + cloud run_sequence."""
    dyn = Effect(name="Aurora", seq_id="100001")
    desc = _full_descriptor(effects=(dyn,))
    sub = make_subentry()
    hub = make_hub()
    hub.cloud_client = MagicMock()
    hub.cloud_client.run_sequence = AsyncMock()
    hub.token = "test_token"
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(hub, device, sub, desc)
    from homeassistant.components.light import ATTR_EFFECT
    await light.async_turn_on(**{ATTR_EFFECT: "Aurora"})
    # Power LANLink write happened.
    device.async_write.assert_called_once_with({desc.power_attr: "1"})
    # Cloud run_sequence called with the seqId.
    hub.cloud_client.run_sequence.assert_awaited_once_with(
        "test_token", did=device.did, seq_id="100001",
    )


@pytest.mark.asyncio
async def test_async_turn_on_dynamic_effect_cloud_failure_raises_home_assistant_error():
    """Cloud unreachable during dynamic-effect activation: raise HomeAssistantError."""
    from custom_components.aqara_lanlink.hub.cloud_client import AqaraAuthError
    from homeassistant.exceptions import HomeAssistantError

    dyn = Effect(name="Aurora", seq_id="100001")
    desc = _full_descriptor(effects=(dyn,))
    sub = make_subentry()
    hub = make_hub()
    hub.cloud_client = MagicMock()
    hub.cloud_client.run_sequence = AsyncMock(side_effect=AqaraAuthError("boom"))
    hub.token = "test_token"
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(hub, device, sub, desc)
    from homeassistant.components.light import ATTR_EFFECT
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on(**{ATTR_EFFECT: "Aurora"})
    # LANLink power write still happened (we don't roll back).
    device.async_write.assert_called_once()


@pytest.mark.asyncio
async def test_async_turn_on_dynamic_effect_no_cloud_client_raises():
    """If hub.cloud_client is None, dynamic-effect activation raises HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.components.light import ATTR_EFFECT

    dyn = Effect(name="Aurora", seq_id="100001")
    desc = _full_descriptor(effects=(dyn,))
    sub = make_subentry()
    hub = make_hub()
    hub.cloud_client = None  # explicit
    hub.token = "test_token"
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(hub, device, sub, desc)
    with pytest.raises(HomeAssistantError, match="not initialised"):
        await light.async_turn_on(**{ATTR_EFFECT: "Aurora"})


@pytest.mark.asyncio
async def test_async_turn_on_unknown_effect_raises_home_assistant_error():
    """Unknown effect name -> HomeAssistantError, not KeyError."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.components.light import ATTR_EFFECT

    eff = Effect(name="Reading", color_temp_kelvin=4000, brightness_pct=80)
    desc = _full_descriptor(effects=(eff,))
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    light = AqaraLight(make_hub(), device, sub, desc)
    with pytest.raises(HomeAssistantError, match="Unknown effect"):
        await light.async_turn_on(**{ATTR_EFFECT: "DoesNotExist"})


def test_effect_list_only_set_when_effects_present():
    from homeassistant.components.light import LightEntityFeature

    desc = _full_descriptor()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    assert getattr(light, "_attr_effect_list", None) is None
    assert not (light._attr_supported_features or 0) & LightEntityFeature.EFFECT


def test_effect_list_set_when_effects_present():
    from homeassistant.components.light import LightEntityFeature

    eff = Effect(name="Reading", color_temp_kelvin=4000, brightness_pct=80)
    desc = _full_descriptor(effects=(eff,))
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    light = AqaraLight(make_hub(), device, sub, desc)
    assert light._attr_effect_list == ["Reading"]
    assert light._attr_supported_features & LightEntityFeature.EFFECT
