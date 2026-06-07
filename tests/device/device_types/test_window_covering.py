"""Tests for the WindowCovering deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, window_covering, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.curtain.acn010")


def _current_pos() -> TraitSpec:
    return TraitSpec(
        id="2.180.33050", wire_path="2.180.33050",
        function_code="WindowCovering", trait_code="CurrentPositionPercentage",
        name="CurrentPositionPercentage", data_type="int", unit="%",
        readable=True, writable=False, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=100.0,
    )


def _target_pos() -> TraitSpec:
    return TraitSpec(
        id="2.180.33051", wire_path="2.180.33051",
        function_code="WindowCovering", trait_code="TargetPositionPercentage",
        name="TargetPositionPercentage", data_type="int", unit="%",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=100.0, step=1.0,
    )


def _motor_status() -> TraitSpec:
    return TraitSpec(
        id="2.180.33055", wire_path="2.180.33055",
        function_code="WindowCovering", trait_code="MotorOperationStatus",
        name="MotorOperationStatus", data_type="enum",
        enum_values={"0": "stopped", "1": "opening", "2": "closing"},
        readable=True, writable=False, subscribable=True, endpoint_id=2,
    )


def test_current_position_becomes_readonly_sensor():
    descs = window_covering.compose(
        endpoint_id=2, traits={"2.180.33050": _current_pos()}, context=_ctx(),
    )
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].native_unit_of_measurement == "%"


def test_target_position_becomes_writable_number():
    descs = window_covering.compose(
        endpoint_id=2, traits={"2.180.33051": _target_pos()}, context=_ctx(),
    )
    assert len(descs) == 1
    assert isinstance(descs[0], NumberDescriptor)
    assert descs[0].min_value == 0.0
    assert descs[0].max_value == 100.0


def test_motor_status_becomes_enum_sensor():
    descs = window_covering.compose(
        endpoint_id=2, traits={"2.180.33055": _motor_status()}, context=_ctx(),
    )
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].device_class == SensorDeviceClass.ENUM
    assert set(descs[0].options or ()) == {"stopped", "opening", "closing"}


def test_full_set_emits_three_descriptors():
    traits = {t.id: t for t in (_current_pos(), _target_pos(), _motor_status())}
    descs = window_covering.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 3


def test_unknown_window_covering_trait_falls_through():
    """A WindowCovering trait this composer doesn't recognise is delegated to
    _fallback (which dispatches on data_type)."""
    other = TraitSpec(
        id="2.180.32999", wire_path="2.180.32999",
        function_code="WindowCovering", trait_code="MotorSpeedSetting",
        name="MotorSpeedSetting", data_type="int",
        readable=True, writable=True, endpoint_id=2,
    )
    descs = window_covering.compose(
        endpoint_id=2, traits={"2.180.32999": other}, context=_ctx(),
    )
    # _fallback emits Number for writable int -> at least one descriptor.
    assert len(descs) >= 1


def test_window_covering_composer_registered():
    assert get_composer("WindowCovering") is window_covering.compose
