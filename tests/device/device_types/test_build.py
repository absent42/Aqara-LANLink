"""Tests for build_descriptor: the single (spec, platform) -> descriptor constructor."""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device.device_types._build import build_descriptor
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor, ButtonDescriptor, EventDescriptor, NumberDescriptor,
    SelectDescriptor, SensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass


def _spec(**kw) -> TraitSpec:
    base = dict(
        id="5.160.33001", wire_path="5.160.33001", name="Sensor type",
        function_code="OccupancySensing", trait_code="OccupancySensorType",
        data_type="enum", endpoint_id=5,
    )
    base.update(kw)
    return TraitSpec(**base)


def test_sensor_platform_builds_sensor_with_decoration():
    spec = _spec(
        data_type="int", platform="sensor", device_class="humidity",
        state_class="measurement", suggested_display_precision=1,
        unit_of_measurement="%", icon="mdi:water",
    )
    d = build_descriptor(spec, "sensor")
    assert isinstance(d, SensorDescriptor)
    assert d.key == "auto_5_160_33001"
    assert d.trait is spec
    assert d.device_class == "humidity"
    assert d.state_class == "measurement"
    assert d.suggested_display_precision == 1
    assert d.icon == "mdi:water"
    assert d.native_unit_of_measurement == "%"


def test_sensor_platform_read_only_enum_builds_enum_sensor():
    """A read-only enum trait classified as a sensor must surface its
    labels, not the raw wire value: device_class=ENUM, options=labels, and
    a transform that maps the wire value to its label (regression: Cube
    "Top face" rendered "2" instead of "Face 3 Up")."""
    spec = _spec(
        data_type="enum",
        enum_values={"0": "Face 1 Up", "1": "Face 2 Up", "2": "Face 3 Up"},
    )
    d = build_descriptor(spec, "sensor")
    assert isinstance(d, SensorDescriptor)
    assert d.device_class == SensorDeviceClass.ENUM
    assert d.options == ("Face 1 Up", "Face 2 Up", "Face 3 Up")
    assert d.transform_in is not None
    assert d.transform_in("2") == "Face 3 Up"


def test_enum_sensor_descriptor_is_hashable():
    """The descriptor is used as a dict key in Device._entities_by_descriptor
    (base.py register_entity). A list `options` field would make the frozen
    dataclass unhashable and crash entity registration at runtime."""
    spec = _spec(data_type="enum", enum_values={"0": "A", "1": "B"})
    d = build_descriptor(spec, "sensor")
    hash(d)  # must not raise
    registry = {d: "entity"}  # must be usable as a dict key
    assert registry[d] == "entity"


def test_sensor_platform_enum_transform_passes_through_unknown_wire():
    """A wire value absent from enum_values is returned verbatim so an
    undocumented code stays visible rather than vanishing."""
    spec = _spec(data_type="enum", enum_values={"0": "Face 1 Up"})
    d = build_descriptor(spec, "sensor")
    assert d.transform_in("9") == "9"


def test_sensor_platform_non_enum_has_no_options_or_enum_class():
    """Numeric sensors stay plain -- the enum path must not leak onto them."""
    spec = _spec(data_type="int", enum_values=None)
    d = build_descriptor(spec, "sensor")
    assert d.options is None
    assert d.device_class != SensorDeviceClass.ENUM
    assert d.transform_in is None


def test_binary_sensor_platform_builds_binary_sensor():
    spec = _spec(data_type="bool", platform="binary_sensor", device_class="motion")
    d = build_descriptor(spec, "binary_sensor")
    assert isinstance(d, BinarySensorDescriptor)
    assert d.trait is spec
    assert d.device_class == "motion"


def test_event_platform_builds_event_with_enum_types():
    spec = _spec(platform="event", enum_values={"1": "Cat", "2": "Dog"})
    d = build_descriptor(spec, "event")
    assert isinstance(d, EventDescriptor)
    assert d.trigger_trait is spec
    assert d.event_types == ("Cat", "Dog")


def test_event_platform_without_enum_uses_single_type():
    spec = _spec(data_type="int", platform="event", enum_values=None)
    d = build_descriptor(spec, "event")
    assert d.event_types == ("triggered",)


def test_select_platform_builds_options_from_enum():
    spec = _spec(platform="select", writable=True,
                 enum_values={"0": "PIR", "1": "Radar"})
    d = build_descriptor(spec, "select")
    assert isinstance(d, SelectDescriptor)
    assert d.attr.name == "5.160.33001"
    assert d.options_map == (("PIR", "0"), ("Radar", "1"))


def test_select_platform_without_enum_returns_none():
    spec = _spec(platform="select", enum_values=None)
    assert build_descriptor(spec, "select") is None


def test_number_platform_builds_number():
    spec = _spec(data_type="int", platform="number", writable=True,
                 min_value=0, max_value=100, step=1, unit_of_measurement="%")
    d = build_descriptor(spec, "number")
    assert isinstance(d, NumberDescriptor)
    assert (d.min_value, d.max_value, d.step) == (0, 100, 1)


def test_switch_platform_builds_switch():
    spec = _spec(data_type="bool", platform="switch", writable=True)
    d = build_descriptor(spec, "switch")
    assert isinstance(d, SwitchDescriptor)
    assert d.attr.name == "5.160.33001"


def test_button_platform_builds_button():
    spec = _spec(platform="button", writable=True)
    d = build_descriptor(spec, "button")
    assert isinstance(d, ButtonDescriptor)
    assert d.attr.name == "5.160.33001"


def test_unknown_platform_returns_none():
    spec = _spec()
    assert build_descriptor(spec, "nonsense") is None
