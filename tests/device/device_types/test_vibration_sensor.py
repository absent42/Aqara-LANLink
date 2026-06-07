"""Tests for the VibrationSensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, vibration_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    EventDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.vibration.agl002")


def _vib_event() -> TraitSpec:
    return TraitSpec(
        id="2.200.33070", wire_path="2.200.33070",
        function_code="Vibration", trait_code="VibrationEvent",
        name="VibrationEvent", data_type="enum",
        enum_values={"0": "vibrate", "1": "tilt", "2": "drop"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def _vib_duration() -> TraitSpec:
    return TraitSpec(
        id="2.200.33071", wire_path="2.200.33071",
        function_code="Vibration", trait_code="VibrationDuration",
        name="VibrationDuration", data_type="float", unit="s",
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_vibration_event_becomes_event_descriptor():
    descs = vibration_sensor.compose(endpoint_id=2, traits={"2.200.33070": _vib_event()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, EventDescriptor)
    assert set(d.event_types or ()) == {"vibrate", "tilt", "drop"}


def test_vibration_duration_becomes_sensor():
    descs = vibration_sensor.compose(endpoint_id=2, traits={"2.200.33071": _vib_duration()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].native_unit_of_measurement == "s"


def test_full_vibration_set():
    traits = {t.id: t for t in (_vib_event(), _vib_duration())}
    descs = vibration_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 2


def test_empty_traits_returns_empty():
    assert vibration_sensor.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_vibration_composer_registered():
    assert get_composer("VibrationSensor") is vibration_sensor.compose
