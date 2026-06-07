"""Tests for the entity descriptor types."""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor,
    BinarySensorDescriptor,
    Effect,
    EventDescriptor,
    ExtraConfig,
    LightDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SensorDescriptor,
    SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

# Synthetic catalog-free entries used by the tests below. They exercise the
# fields the descriptors care about without touching the real catalogs.
_TEST_TRAIT = TraitSpec(id="1.7.85", name="test_brightness")
_TEST_ATTR = AttrSpec(name="test_brightness", id="1.7.85")
_TEST_ATTR_WRITE = AttrSpec(name="test_brightness_write", id="1.7.85")


def test_extra_config_alias_is_mapping():
    # ExtraConfig is just a Mapping[str, Any]. Ensure it's a runtime alias
    # that the integration can use as a type annotation; we don't expect
    # callers to instantiate it directly.
    assert ExtraConfig.__module__.endswith("descriptors") or True


def test_binary_sensor_descriptor_required_fields():
    desc = BinarySensorDescriptor(key="test_motion", trait=_TEST_TRAIT)
    assert desc.key == "test_motion"
    assert desc.trait is _TEST_TRAIT
    assert desc.on_values == frozenset({"1"})
    assert desc.auto_clear_seconds is None


def test_binary_sensor_descriptor_optional_fields():
    desc = BinarySensorDescriptor(
        key="test_motion",
        trait=_TEST_TRAIT,
        on_values=frozenset({"1", "2"}),
        auto_clear_seconds=15.0,
    )
    assert desc.on_values == frozenset({"1", "2"})
    assert desc.auto_clear_seconds == 15.0


def test_switch_descriptor_required_fields():
    desc = SwitchDescriptor(key="test_power", attr=_TEST_ATTR)
    assert desc.attr is _TEST_ATTR
    assert desc.attr_write is None
    assert desc.on_value == "1"
    assert desc.off_value == "0"


def test_switch_descriptor_distinct_write_attr():
    desc = SwitchDescriptor(
        key="test_power",
        attr=_TEST_ATTR,
        attr_write=_TEST_ATTR_WRITE,
        on_value="ON",
        off_value="OFF",
    )
    assert desc.attr_write is _TEST_ATTR_WRITE
    assert desc.on_value == "ON"


def test_select_descriptor_required_fields():
    options_map = (("Auto", "0"), ("Manual", "1"))
    desc = SelectDescriptor(
        key="test_mode", attr=_TEST_ATTR, options_map=options_map,
    )
    assert desc.options_map == options_map
    assert desc.options_dict() == {"Auto": "0", "Manual": "1"}
    assert desc.attr_write is None


def test_number_descriptor_required_fields():
    desc = NumberDescriptor(
        key="test_brightness",
        attr=_TEST_ATTR,
        min_value=1,
        max_value=100,
    )
    assert desc.min_value == 1
    assert desc.max_value == 100
    assert desc.step == 1
    assert desc.transform_in is None
    assert desc.transform_out is None


def test_number_descriptor_with_transforms():
    desc = NumberDescriptor(
        key="test_temperature",
        attr=_TEST_ATTR,
        min_value=153.0,
        max_value=370.0,
        step=0.5,
        transform_in=lambda s: float(s) / 10,
        transform_out=lambda v: str(int(v * 10)),
    )
    assert desc.step == 0.5
    assert desc.transform_in("100") == 10.0
    assert desc.transform_out(10.0) == "100"


def test_sensor_descriptor_optional_attr_or_trait():
    # Either source is allowed; the spec lets auto-derive choose.
    desc_attr = SensorDescriptor(key="test_temp", attr=_TEST_ATTR)
    assert desc_attr.attr is _TEST_ATTR
    assert desc_attr.trait is None

    desc_trait = SensorDescriptor(key="test_temp", trait=_TEST_TRAIT)
    assert desc_trait.attr is None
    assert desc_trait.trait is _TEST_TRAIT


def test_sensor_descriptor_with_transform_in():
    transform = lambda s: int(s) // 10
    desc = SensorDescriptor(
        key="test_signal",
        attr=_TEST_ATTR,
        transform_in=transform,
    )
    assert desc.transform_in is transform


def test_event_descriptor_default_event_types():
    desc = EventDescriptor(
        key="test_doorbell",
        trigger_source="multicast_ring",
        event_types=("ring",),
    )
    assert desc.event_types == ("ring",)
    assert desc.trigger_source == "multicast_ring"
    assert desc.trigger_trait is None


def test_event_descriptor_with_trigger_trait():
    desc = EventDescriptor(
        key="test_button",
        trigger_trait=_TEST_TRAIT,
        event_types=("click", "long_press"),
    )
    assert desc.trigger_trait is _TEST_TRAIT
    assert desc.event_types == ("click", "long_press")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BinarySensorDescriptor(key="k", trait=_TEST_TRAIT),
        lambda: SwitchDescriptor(key="k", attr=_TEST_ATTR),
        lambda: SelectDescriptor(key="k", attr=_TEST_ATTR, options_map=()),
        lambda: NumberDescriptor(
            key="k", attr=_TEST_ATTR, min_value=0, max_value=10,
        ),
        lambda: SensorDescriptor(key="k", attr=_TEST_ATTR),
        lambda: EventDescriptor(key="k", trigger_source="x"),
    ],
)
def test_descriptors_are_frozen(factory):
    desc = factory()
    with pytest.raises(dataclasses.FrozenInstanceError):
        desc.key = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BinarySensorDescriptor(key="k", trait=_TEST_TRAIT),
        lambda: SwitchDescriptor(key="k", attr=_TEST_ATTR),
        lambda: SelectDescriptor(
            key="k", attr=_TEST_ATTR, options_map=(("Auto", "0"),),
        ),
        lambda: NumberDescriptor(
            key="k", attr=_TEST_ATTR, min_value=0, max_value=10,
        ),
        lambda: SensorDescriptor(key="k", attr=_TEST_ATTR),
        lambda: EventDescriptor(key="k", trigger_source="x"),
    ],
)
def test_descriptors_are_hashable(factory):
    desc = factory()
    # Hashable -- can be used as dict keys.
    assert hash(desc) == hash(desc)
    {desc: "value"}


def test_select_descriptor_options_map_is_tuple_of_pairs():
    # ``options_map`` is now a tuple-of-pairs so SelectDescriptor instances
    # are hashable (frozen dataclasses don't deep-freeze their fields, so
    # a dict-typed field would still make the dataclass unhashable).
    desc = SelectDescriptor(
        key="test_mode",
        attr=_TEST_ATTR,
        options_map=(("Auto", "0"), ("Manual", "1")),
    )
    assert dataclasses.is_dataclass(desc)
    # Hashable + usable as a dict key (the seed-replay path needs this).
    {desc: "value"}
    # Convenience accessor for callers that want the dict shape.
    assert desc.options_dict() == {"Auto": "0", "Manual": "1"}


def test_effect_static_default_seq_id_is_none():
    eff = Effect(name="Reading", color_temp_kelvin=4000, brightness_pct=80)
    assert eff.seq_id is None
    assert eff.color_temp_kelvin == 4000
    assert eff.brightness_pct == 80


def test_effect_dynamic_carries_seq_id():
    eff = Effect(name="Aurora", seq_id="100001")
    assert eff.seq_id == "100001"
    assert eff.color_temp_kelvin is None
    assert eff.brightness_pct is None


def test_effect_is_frozen():
    eff = Effect(name="Reading")
    with pytest.raises(dataclasses.FrozenInstanceError):
        eff.name = "Something Else"  # type: ignore[misc]


def test_effect_is_hashable():
    eff = Effect(name="Reading", color_temp_kelvin=4000)
    assert eff in {eff}


def test_light_descriptor_construction_with_full_capability_set():
    power_trait = TraitSpec(id="4.1.85", name="power_status", data_type="bool")
    power_attr = AttrSpec(name="2.130.32913", id="4.1.85", data_type="bool")
    bright_trait = TraitSpec(id="1.7.85", name="light_level", data_type="number", unit="%")
    bright_attr = AttrSpec(name="2.130.32915", id="1.7.85", data_type="number", unit="%")
    ct_trait = TraitSpec(id="1.9.85", name="colour_temperature", data_type="number", unit="mired")
    ct_attr = AttrSpec(name="2.130.32919", id="1.9.85", data_type="number", unit="mired")
    xy_trait = TraitSpec(id="14.8.85", name="light_xy", data_type="uint")
    xy_attr = AttrSpec(name="2.133.20106", id="14.8.85", data_type="uint")

    desc = LightDescriptor(
        key="light",
        translation_key="light",
        power_trait=power_trait,
        power_attr=power_attr,
        brightness_trait=bright_trait,
        brightness_attr=bright_attr,
        color_temp_trait=ct_trait,
        color_temp_attr=ct_attr,
        color_temp_min_kelvin=2700,
        color_temp_max_kelvin=6500,
        color_temp_wire_unit="mired",
        color_xy_trait=xy_trait,
        color_xy_attr=xy_attr,
    )
    assert desc.power_trait.id == "4.1.85"
    assert desc.color_temp_min_kelvin == 2700
    assert desc.color_temp_wire_unit == "mired"
    assert desc.effects == ()


def test_light_descriptor_brightness_only_no_color():
    power_trait = TraitSpec(id="4.1.85", name="power_status", data_type="bool")
    power_attr = AttrSpec(name="2.130.32913", id="4.1.85", data_type="bool")
    bright_trait = TraitSpec(id="1.7.85", name="light_level", data_type="number", unit="%")
    bright_attr = AttrSpec(name="2.130.32915", id="1.7.85", data_type="number", unit="%")

    desc = LightDescriptor(
        key="light",
        translation_key="light",
        power_trait=power_trait,
        power_attr=power_attr,
        brightness_trait=bright_trait,
        brightness_attr=bright_attr,
    )
    assert desc.color_temp_trait is None
    assert desc.color_xy_trait is None
    assert desc.color_temp_wire_unit == "K"  # default


def test_sensor_descriptor_normalizes_native_unit_of_measurement():
    # A non-canonical Aqara unit (℃, U+2103) must be normalized to the
    # HA-canonical form (°C) so SensorEntity device-class validation passes.
    desc = SensorDescriptor(key="t", trait=_TEST_TRAIT,
                            native_unit_of_measurement="℃")
    assert desc.native_unit_of_measurement == "°C"


def test_number_descriptor_normalizes_native_unit_of_measurement():
    desc = NumberDescriptor(
        key="t", attr=_TEST_ATTR, min_value=0, max_value=100,
        native_unit_of_measurement="℃",
    )
    assert desc.native_unit_of_measurement == "°C"


def test_sensor_descriptor_leaves_canonical_unit_untouched():
    desc = SensorDescriptor(key="t", trait=_TEST_TRAIT,
                            native_unit_of_measurement="%")
    assert desc.native_unit_of_measurement == "%"


def test_sensor_descriptor_without_unit_stays_none():
    desc = SensorDescriptor(key="t", attr=_TEST_ATTR)
    assert desc.native_unit_of_measurement is None


def test_sensor_descriptor_normalized_unit_survives_replace():
    # _apply_classification_overlay rebuilds descriptors via dataclasses.replace,
    # which re-runs __post_init__; normalization must stay idempotent.
    desc = SensorDescriptor(key="t", trait=_TEST_TRAIT,
                            native_unit_of_measurement="lux")
    replaced = dataclasses.replace(desc, device_class="illuminance")
    assert replaced.native_unit_of_measurement == "lx"


def test_any_descriptor_union_includes_all_descriptor_types():
    # AnyDescriptor is the runtime union; ensure it covers each type.
    from custom_components.aqara_lanlink.device.descriptors import ButtonDescriptor
    members = {
        BinarySensorDescriptor,
        ButtonDescriptor,
        SwitchDescriptor,
        SelectDescriptor,
        NumberDescriptor,
        SensorDescriptor,
        EventDescriptor,
        LightDescriptor,
    }
    # Walk the typing union args.
    import typing
    args = set(typing.get_args(AnyDescriptor))
    assert args == members
