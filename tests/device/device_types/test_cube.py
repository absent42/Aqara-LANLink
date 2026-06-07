"""Tests for the Cube deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, cube, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    EventDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_cube.aqgl01")


def _cube_event() -> TraitSpec:
    return TraitSpec(
        id="2.210.33080", wire_path="2.210.33080",
        function_code="Cube", trait_code="CubeEvent",
        name="CubeEvent", data_type="enum",
        enum_values={"0": "flip90", "1": "flip180", "2": "shake", "3": "tap_twice"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def _rotation_event() -> TraitSpec:
    return TraitSpec(
        id="2.210.33081", wire_path="2.210.33081",
        function_code="Cube", trait_code="RotationEvent",
        name="RotationEvent", data_type="enum",
        enum_values={"0": "start", "1": "stop"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def _rotation_angle() -> TraitSpec:
    return TraitSpec(
        id="2.210.33082", wire_path="2.210.33082",
        function_code="Cube", trait_code="RotationAngle",
        name="RotationAngle", data_type="float", unit="°",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _rotation_direction() -> TraitSpec:
    return TraitSpec(
        id="2.210.33083", wire_path="2.210.33083",
        function_code="Cube", trait_code="RotationDirection",
        name="RotationDirection", data_type="enum",
        enum_values={"0": "cw", "1": "ccw"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_cube_event_becomes_event_descriptor():
    descs = cube.compose(endpoint_id=2, traits={"2.210.33080": _cube_event()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], EventDescriptor)


def test_rotation_event_becomes_event_descriptor():
    descs = cube.compose(endpoint_id=2, traits={"2.210.33081": _rotation_event()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], EventDescriptor)


def test_rotation_angle_becomes_sensor():
    descs = cube.compose(endpoint_id=2, traits={"2.210.33082": _rotation_angle()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].native_unit_of_measurement == "°"


def test_rotation_direction_becomes_enum_sensor():
    descs = cube.compose(endpoint_id=2, traits={"2.210.33083": _rotation_direction()}, context=_ctx())
    assert len(descs) == 1
    assert descs[0].device_class == SensorDeviceClass.ENUM


def test_rotation_direction_maps_wire_value_to_label():
    """The enum sensor must render the label, not the raw wire value."""
    descs = cube.compose(endpoint_id=2, traits={"2.210.33083": _rotation_direction()}, context=_ctx())
    desc = descs[0]
    assert desc.options == ("cw", "ccw")
    assert desc.transform_in is not None
    assert desc.transform_in("1") == "ccw"


def test_top_face_becomes_enum_sensor_with_labels():
    """A read-only enum cube trait with no special branch (Top face) routes
    through the fallback and still surfaces its labels."""
    top_face = TraitSpec(
        id="2.163.20165", wire_path="2.163.20165", name="Top face",
        function_code="Cube", trait_code="TopFace", data_type="enum",
        enum_values={"0": "Face 1 Up", "1": "Face 2 Up", "2": "Face 3 Up"},
        subscribable=True, endpoint_id=2,
    )
    descs = cube.compose(endpoint_id=2, traits={"2.163.20165": top_face}, context=_ctx())
    assert len(descs) == 1
    assert descs[0].device_class == SensorDeviceClass.ENUM
    assert descs[0].transform_in("2") == "Face 3 Up"


def test_cube_composer_registered():
    assert get_composer("Cube") is cube.compose
