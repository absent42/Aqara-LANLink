"""Tests for the Knob deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, get_composer, knob,
)
from custom_components.aqara_lanlink.device.descriptors import (
    EventDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.remote.rkba01")


def _rotation_event() -> TraitSpec:
    return TraitSpec(
        id="2.162.33005", wire_path="2.162.33005",
        function_code="Knob", trait_code="RotationEvent",
        name="RotationEvent", data_type="enum",
        readable=True, subscribable=True, endpoint_id=2,
        enum_values={"0": "Rotate", "1": "HoldToRotate"},
    )


def _button_event() -> TraitSpec:
    return TraitSpec(
        id="2.135.32928", wire_path="2.135.32928",
        function_code="Button", trait_code="ButtonEvent",
        name="ButtonEvent", data_type="enum",
        readable=True, subscribable=True, endpoint_id=2,
        enum_values={"0": "SinglePress", "1": "DoublePress", "2": "LongPress"},
    )


def _rotation_angle() -> TraitSpec:
    return TraitSpec(
        id="2.162.33003", wire_path="2.162.33003",
        function_code="Knob", trait_code="RotationAngle",
        name="RotationAngle", data_type="float", unit="°",
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_rotation_event_becomes_event_descriptor():
    """Knob.RotationEvent must emit an EventDescriptor with event_types
    derived from enum_values, not fall through to _fallback's
    enum-as-sensor handling.
    """
    traits = {t.id: t for t in (_rotation_event(),)}
    descs = knob.compose(endpoint_id=2, traits=traits, context=_ctx())
    events = [d for d in descs if isinstance(d, EventDescriptor)]
    assert len(events) == 1
    e = events[0]
    assert e.trigger_trait.id == "2.162.33005"
    assert set(e.event_types) == {"Rotate", "HoldToRotate"}


def test_button_event_on_knob_endpoint_also_becomes_event():
    """rkba01/rkna01 Knob endpoints carry Button.ButtonEvent alongside
    Knob.RotationEvent. Both must yield Event entities -- press automations
    need the same trigger surface as on a Button deviceType endpoint.
    """
    traits = {t.id: t for t in (_button_event(),)}
    descs = knob.compose(endpoint_id=2, traits=traits, context=_ctx())
    events = [d for d in descs if isinstance(d, EventDescriptor)]
    assert len(events) == 1
    assert set(events[0].event_types) == {"SinglePress", "DoublePress", "LongPress"}


def test_rotation_angle_falls_through_to_sensor():
    """Knob.RotationAngle is numeric (deg), not an event -- _fallback
    produces a SensorDescriptor for it.
    """
    traits = {t.id: t for t in (_rotation_event(), _rotation_angle())}
    descs = knob.compose(endpoint_id=2, traits=traits, context=_ctx())
    events = [d for d in descs if isinstance(d, EventDescriptor)]
    sensors = [d for d in descs if isinstance(d, SensorDescriptor)]
    assert len(events) == 1
    assert len(sensors) >= 1
    angle = next((s for s in sensors if s.trait.id == "2.162.33003"), None)
    assert angle is not None
    assert angle.native_unit_of_measurement == "°"


def test_composer_registered():
    assert get_composer("Knob") is knob.compose
