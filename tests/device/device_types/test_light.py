"""Tests for the Light composite composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, light, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import LightDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.light.agl003")


def _onoff(wp="2.130.32945") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp, function_code="Output", trait_code="OnOff",
        name="OnOff", data_type="bool",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
    )

def _level(wp="2.132.32920") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp, function_code="LevelControl", trait_code="CurrentLevel",
        name="CurrentLevel", data_type="float", unit="%",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=100.0, step=1.0,
    )

def _color_temp(wp="2.134.32927", unit="°K") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="ColorControl", trait_code="ColorTemperature",
        name="ColorTemperature", data_type="float", unit=unit,
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=153.0 if unit == "mired" else 2700.0,
        max_value=370.0 if unit == "mired" else 6500.0,
    )

def _color_x(wp="2.134.32925") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="ColorControl", trait_code="CurrentX",
        name="CurrentX", data_type="float",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=65279.0,
    )

def _color_y(wp="2.134.32926") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="ColorControl", trait_code="CurrentY",
        name="CurrentY", data_type="float",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=65279.0,
    )

def _color_xy_combined(wp="2.134.20192") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="ColorControl", trait_code="ColorXY",
        name="ColorXY", data_type="int",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=4294967295.0,
    )


def test_light_prefers_combined_colorxy_over_currentx_y():
    """Aqara RGB bulbs expose ColorXY (uint32 packed) alongside CurrentX/Y
    (uint16 each). The combined attribute is the bulb's canonical destination
    for atomic XY writes; writing a uint32 packed value to CurrentX (uint16)
    clips and produces wrong colours. Composer must pick ColorXY when present.
    """
    traits = {
        t.id: t for t in (
            _onoff(), _level(), _color_xy_combined(), _color_x(), _color_y(),
        )
    }
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.color_xy_trait is not None
    assert d.color_xy_trait.id == "2.134.20192", (
        f"expected ColorXY trait, got {d.color_xy_trait.id}"
    )
    assert d.color_xy_attr is not None
    assert d.color_xy_attr.name == "2.134.20192"


def test_light_falls_back_to_currentx_when_no_combined_colorxy():
    """Defensive: if a model exposes CurrentX/Y but no ColorXY (not seen on
    any catalogued Aqara bulb yet), the composer still produces a descriptor
    so the light renders -- using CurrentX as a best-effort destination."""
    traits = {t.id: t for t in (_onoff(), _color_x(), _color_y())}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.color_xy_trait is not None
    assert d.color_xy_trait.id == "2.134.32925"


def test_light_with_full_color_set():
    """All four traits present -> one LightDescriptor with all fields populated."""
    traits = {t.id: t for t in (_onoff(), _level(), _color_temp(), _color_x(), _color_y())}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, LightDescriptor)
    assert d.power_trait.id == "2.130.32945"
    assert d.brightness_trait is not None and d.brightness_trait.id == "2.132.32920"
    assert d.color_temp_trait is not None and d.color_temp_trait.id == "2.134.32927"
    assert d.color_xy_trait is not None and d.color_xy_trait.id == "2.134.32925"


def test_light_onoff_only():
    """OnOff alone is a valid (simple) Light."""
    traits = {t.id: t for t in (_onoff(),)}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.power_trait.id == "2.130.32945"
    assert d.brightness_trait is None
    assert d.color_temp_trait is None
    assert d.color_xy_trait is None


def test_light_dimmable_no_color():
    """OnOff + Level, no color, is still one LightDescriptor."""
    traits = {t.id: t for t in (_onoff(), _level())}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].brightness_trait is not None
    assert descs[0].color_temp_trait is None


def test_light_cct_only_no_brightness():
    """OnOff + ColorTemp without brightness is still one LightDescriptor."""
    traits = {t.id: t for t in (_onoff(), _color_temp())}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].color_temp_trait is not None


def test_light_without_onoff_emits_nothing():
    """A Light endpoint missing OnOff is malformed; return []."""
    traits = {t.id: t for t in (_level(), _color_temp())}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs == []


def test_light_color_temp_in_mireds_normalised_to_kelvin():
    """When the cloud reports color_temp in mireds, the descriptor stores Kelvin
    bounds and remembers the wire unit for write-back. Convert via K = 1e6/mired."""
    traits = {t.id: t for t in (_onoff(), _color_temp(unit="mired"))}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.color_temp_trait is not None
    # mired_min=153 -> K_max = round(1e6/153) = 6536
    # mired_max=370 -> K_min = round(1e6/370) = 2703
    assert d.color_temp_min_kelvin == 2703
    assert d.color_temp_max_kelvin == 6536
    assert d.color_temp_wire_unit == "mired"
    # Inner TraitSpec is unchanged (the wire-format values stay in mireds).
    assert d.color_temp_trait.unit == "mired"


def test_light_color_temp_unit_none_defaults_to_mired():
    """V3 catalogue emits unit=None for every ColorTemperature trait but the
    min/max sit in the mired range (e.g. 153-370). Treat unit=None as mired
    so Kelvin bounds populate and writes go out as mireds. Without this every
    shipped CCT light's HA color-temp slider is broken.
    """
    ct = TraitSpec(
        id="2.134.32927", wire_path="2.134.32927",
        function_code="ColorControl", trait_code="ColorTemperature",
        name="ColorTemperature", data_type="float", unit=None,
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=153.0, max_value=370.0,
    )
    traits = {t.id: t for t in (_onoff(), ct)}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.color_temp_min_kelvin == 2703
    assert d.color_temp_max_kelvin == 6536
    assert d.color_temp_wire_unit == "mired"


def test_light_color_temp_in_kelvin_passes_through():
    """When the cloud reports color_temp in Kelvin, min/max copy across and
    wire unit is recorded as 'K'."""
    traits = {t.id: t for t in (_onoff(), _color_temp(unit="°K"))}
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert d.color_temp_min_kelvin == 2700
    assert d.color_temp_max_kelvin == 6500
    assert d.color_temp_wire_unit == "K"


def test_light_xy_uses_currentx_y_pair_is_paired():
    """The composer reads CurrentX as the descriptor's color_xy_trait but must
    also notice CurrentY exists; if only X without Y, color is incomplete and
    color_xy_trait stays None."""
    traits = {t.id: t for t in (_onoff(), _color_x())}  # X but no Y
    descs = light.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].color_xy_trait is None, "CurrentX alone (no Y) is incomplete"


def test_light_composer_registered():
    assert get_composer("Light") is light.compose
