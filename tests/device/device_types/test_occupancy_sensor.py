"""Tests for the OccupancySensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, occupancy_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import BinarySensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.binary_sensor import BinarySensorDeviceClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_occupy.agl8")


def _occupancy_trait() -> TraitSpec:
    return TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, writable=False, subscribable=True, endpoint_id=2,
    )


def test_occupancy_becomes_binary_sensor():
    traits = {"2.160.33000": _occupancy_trait()}
    descs = occupancy_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], BinarySensorDescriptor)


def test_occupancy_device_class_is_occupancy():
    """HA's binary_sensor.occupancy device class maps to the right UI."""
    traits = {"2.160.33000": _occupancy_trait()}
    descs = occupancy_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs[0].device_class == BinarySensorDeviceClass.OCCUPANCY


def test_occupancy_wire_path_preserved():
    traits = {"2.160.33000": _occupancy_trait()}
    descs = occupancy_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs[0].trait.id == "2.160.33000"


def test_occupancy_composer_registered():
    assert get_composer("OccupancySensor") is occupancy_sensor.compose


def _motion_ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.camera.agl010")


def test_motion_compose_renders_occupancy_as_motion():
    """On a MotionSensor-deviceType endpoint (cameras, PIR motion sensors) the
    OccupancySensing.Occupancy trait IS motion detection -- it must surface as
    device_class=motion named 'Motion', not occupancy."""
    spec = TraitSpec(
        id="5.160.33000", wire_path="5.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=5,
    )
    descs = occupancy_sensor.motion_compose(
        endpoint_id=5, traits={spec.id: spec}, context=_motion_ctx(),
    )
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, BinarySensorDescriptor)
    assert d.device_class == BinarySensorDeviceClass.MOTION
    assert d.name == "Motion"
    assert d.trait.id == "5.160.33000"


def test_motion_composer_registered_for_motionsensor():
    assert get_composer("MotionSensor") is occupancy_sensor.motion_compose


def test_motion_occupancy_is_momentary_with_auto_clear():
    """Camera motion is a momentary pulse: the descriptor must carry
    auto_clear_seconds so it (a) self-clears and (b) is treated as momentary by
    _should_replay_seed -- otherwise it restores a stale 'on' from the recorder
    and boots activated on every restart, and the per-camera Detection clear
    delay setting (resolve_auto_clear_seconds) never applies to it."""
    from custom_components.aqara_lanlink.device.base import _should_replay_seed
    spec = TraitSpec(
        id="5.160.33000", wire_path="5.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=5,
    )
    d = occupancy_sensor.motion_compose(
        endpoint_id=5, traits={spec.id: spec}, context=_motion_ctx(),
    )[0]
    assert d.auto_clear_seconds is not None
    assert _should_replay_seed(d) is False  # momentary -> no stale-on restore


def test_occupancy_presence_stays_stateful():
    """OccupancySensor presence is genuine state: no auto_clear, so it restores
    its last value on restart (the device drives both on and off)."""
    from custom_components.aqara_lanlink.device.base import _should_replay_seed
    d = occupancy_sensor.compose(
        endpoint_id=2, traits={"2.160.33000": _occupancy_trait()}, context=_ctx(),
    )[0]
    assert d.auto_clear_seconds is None
    assert _should_replay_seed(d) is True  # stateful -> restores last state


def test_occupancy_composer_unaffected_by_motion_variant():
    """OccupancySensor endpoints still render Occupancy as occupancy."""
    traits = {"2.160.33000": _occupancy_trait()}
    descs = occupancy_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs[0].device_class == BinarySensorDeviceClass.OCCUPANCY
    assert descs[0].name == "Occupancy"


def test_motion_detected_trait_also_supported():
    """Some sensors expose MotionDetected (enum) instead of Occupancy (bool).
    Both should route to a BinarySensorDescriptor."""
    traits = {"2.160.33002": TraitSpec(
        id="2.160.33002", wire_path="2.160.33002",
        function_code="OccupancySensing", trait_code="MotionDetected",
        name="MotionDetected", data_type="enum",
        readable=True, subscribable=True, endpoint_id=2,
        enum_values={"0": "no_motion", "1": "motion"},
    )}
    descs = occupancy_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], BinarySensorDescriptor)
    assert descs[0].device_class == BinarySensorDeviceClass.MOTION
