"""Tests for the TemperatureSensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, temperature_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_ht.agl02")


def _temp_trait() -> TraitSpec:
    return TraitSpec(
        id="4.143.32952", wire_path="4.143.32952",
        function_code="Temperature", trait_code="CurrentTemperature",
        name="CurrentTemperature", data_type="float", unit="°C",
        readable=True, subscribable=True, endpoint_id=4,
    )


def test_temperature_becomes_sensor():
    descs = temperature_sensor.compose(endpoint_id=4, traits={"4.143.32952": _temp_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_temperature_device_class_and_state_class():
    descs = temperature_sensor.compose(endpoint_id=4, traits={"4.143.32952": _temp_trait()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.TEMPERATURE
    assert descs[0].state_class == SensorStateClass.MEASUREMENT


def test_temperature_unit_preserved():
    descs = temperature_sensor.compose(endpoint_id=4, traits={"4.143.32952": _temp_trait()}, context=_ctx())
    assert descs[0].native_unit_of_measurement == "°C"


def test_temperature_composer_registered():
    assert get_composer("TemperatureSensor") is temperature_sensor.compose


def test_no_temperature_trait_emits_nothing():
    assert temperature_sensor.compose(endpoint_id=4, traits={}, context=_ctx()) == []


def test_temperature_descriptor_has_default_display_precision():
    """`suggested_display_precision=1` is the default for temperature."""
    descs = temperature_sensor.compose(
        endpoint_id=4, traits={"4.143.32952": _temp_trait()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 1
