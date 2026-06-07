"""Tests for the generic sensor platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.sensor import AqaraSensor, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _battery_descriptor(transform_in=None) -> SensorDescriptor:
    return SensorDescriptor(
        key="test_battery",
        attr=AttrSpec(name="test_battery_attr"),
        transform_in=transform_in,
    )


def _trait_only_descriptor() -> SensorDescriptor:
    return SensorDescriptor(
        key="test_temperature",
        trait=TraitSpec(id="9.1.85", name="test_temperature"),
        transform_in=lambda raw: float(raw),
    )


def test_apply_value_default_transform_is_identity_passthrough():
    desc = _battery_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("hello")
    assert entity._attr_native_value == "hello"


def test_apply_value_custom_transform_in_runs():
    desc = _battery_descriptor(transform_in=lambda raw: int(raw))
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("87")
    assert entity._attr_native_value == 87


def test_apply_value_trait_descriptor_runs_transform():
    desc = _trait_only_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("23.5")
    assert entity._attr_native_value == pytest.approx(23.5)


def test_apply_value_float_trait_coerces_to_float_by_default():
    """Numeric trait without explicit transform_in coerces to float so HA
    formats consistently across firmwares (regression: weather_v1 emits
    `"48.970000"`, sensor_ht_agl02 emits `"56.08"` -- both should land
    as floats so HA renders the same precision for both)."""
    desc = SensorDescriptor(
        key="auto_3_144_32953",
        trait=TraitSpec(
            id="3.144.32953", name="Current humidity",
            data_type="float", function_code="RelativeHumidity",
            trait_code="CurrentHumidity",
        ),
        native_unit_of_measurement="%",
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("48.970000")
    assert entity._attr_native_value == pytest.approx(48.97)
    assert isinstance(entity._attr_native_value, float)


def test_apply_value_int_trait_coerces_to_int_by_default():
    """Integer-typed traits coerce to int so HA doesn't render trailing `.0`."""
    desc = SensorDescriptor(
        key="auto_battery",
        trait=TraitSpec(
            id="0.130.32915", name="Bat percent remaining",
            data_type="int",
        ),
        native_unit_of_measurement="%",
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("87")
    assert entity._attr_native_value == 87
    assert isinstance(entity._attr_native_value, int)


def test_apply_value_default_falls_back_to_string_on_parse_failure():
    """A numeric-typed trait that pushes an unparseable value (firmware
    bug) does NOT crash -- the wire value is stored verbatim so the
    entity remains visible (HA may complain about non-numeric state but
    that's preferable to silently dropping the report)."""
    desc = SensorDescriptor(
        key="auto_humidity",
        trait=TraitSpec(id="3.144.32953", name="Current humidity", data_type="float"),
        native_unit_of_measurement="%",
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("not-a-number")
    assert entity._attr_native_value == "not-a-number"


def test_apply_value_string_trait_passes_through():
    """Non-numeric data types (string, unknown, missing) still pass
    through as raw strings -- the coerce-by-default applies only to
    int/float."""
    desc = SensorDescriptor(
        key="auto_label",
        trait=TraitSpec(id="2.130.32913", name="Endpoint name", data_type="string"),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("Kitchen light")
    assert entity._attr_native_value == "Kitchen light"


def test_apply_value_enum_sensor_renders_label_not_wire_value():
    """A read-only enum trait built via the auto-derive pipeline must
    display its label. Regression: Cube "Top face" showed "2" instead of
    "Face 3 Up" because the sensor descriptor dropped enum_values."""
    from custom_components.aqara_lanlink.device.device_types._build import build_descriptor

    spec = TraitSpec(
        id="2.163.20165", wire_path="2.163.20165", name="Top face",
        data_type="enum", function_code="Cube", trait_code="TopFace",
        enum_values={"0": "Face 1 Up", "1": "Face 2 Up", "2": "Face 3 Up"},
        subscribable=True, endpoint_id=2,
    )
    desc = build_descriptor(spec, "sensor")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("2")
    assert entity._attr_native_value == "Face 3 Up"
    # HA's SensorEntity.options prefers _attr_options and expects a list.
    assert entity.options == ["Face 1 Up", "Face 2 Up", "Face 3 Up"]
    assert isinstance(entity.options, list)


def _bare_sensor(descriptor: SensorDescriptor) -> AqaraSensor:
    """Construct an AqaraSensor without exercising AqaraEntity infrastructure."""
    entity = AqaraSensor.__new__(AqaraSensor)
    entity.entity_description = descriptor
    entity.descriptor = descriptor  # AqaraEntity stores this alias
    entity.hass = None
    entity._attr_native_value = None
    return entity


def test_apply_value_with_scale():
    """apply_value(raw) sets native_value to transform(raw) * scale."""
    descriptor = SensorDescriptor(
        key="auto_2_135_8888",
        name="Voltage",
        scale=0.001,
        native_unit_of_measurement="V",
    )
    entity = _bare_sensor(descriptor)
    entity.apply_value("3200")
    assert entity._attr_native_value == pytest.approx(3.2)


def test_apply_value_no_scale_passes_through():
    """No scale set -> apply_value preserves existing behavior (identity)."""
    descriptor = SensorDescriptor(key="auto_0_0_32896", name="x")
    entity = _bare_sensor(descriptor)
    entity.apply_value("42")
    assert entity._attr_native_value == "42"  # default _identity transform


def test_apply_value_non_numeric_when_scale_set_passes_through():
    """If transform(raw) isn't numeric (e.g. string trait), scale is bypassed."""
    descriptor = SensorDescriptor(key="x", name="x", scale=0.001)
    entity = _bare_sensor(descriptor)
    entity.apply_value("not a number")
    assert entity._attr_native_value == "not a number"


def test_apply_value_with_numeric_transform_then_scale():
    """When transform_in returns numeric, scale is applied."""
    descriptor = SensorDescriptor(
        key="x", name="x", scale=0.5, transform_in=lambda r: int(r),
    )
    entity = _bare_sensor(descriptor)
    entity.apply_value("100")
    assert entity._attr_native_value == 50.0


def test_apply_value_truncates_oversized_string():
    desc = SensorDescriptor(key="auto_x", name="x")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    big = "a" * 400
    entity.apply_value(big)
    assert len(entity._attr_native_value) == 255
    assert entity._attr_native_value.endswith("…")
    assert entity._attr_extra_state_attributes["full_value"] == big


def test_apply_value_short_string_not_truncated():
    desc = SensorDescriptor(key="auto_x", name="x")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    entity.apply_value("short")
    assert entity._attr_native_value == "short"
    assert entity._attr_extra_state_attributes is None


def test_apply_value_empty_string_renders_as_none():
    """Aqara cloud returns value='' for traits that have no reading yet
    (e.g. p100 tilt angles before first measurement). On a numeric sensor
    (unit set), HA rejects '' as non-numeric and refuses to register the
    entity. Treat '' as None so the entity registers cleanly and updates
    when a real value arrives.
    """
    desc = _trait_only_descriptor()  # has transform_in=float()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSensor(hub, device, sub, desc)
    # Without the empty-string guard, transform_in=float('') raises ValueError
    # during apply_value and the entity ends up with native_value=''.
    entity.apply_value("")
    assert entity._attr_native_value is None
    assert entity._attr_extra_state_attributes is None


@pytest.mark.asyncio
async def test_async_setup_entry_creates_entity_per_descriptor():
    desc1 = _battery_descriptor()
    desc2 = _trait_only_descriptor()
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc1, desc2], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    keys = sorted(e.descriptor.key for e in added)
    assert keys == ["test_battery", "test_temperature"]
