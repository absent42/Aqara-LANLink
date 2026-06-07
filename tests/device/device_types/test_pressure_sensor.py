"""Tests for the PressureSensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, pressure_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_ht.agl02")


def _pressure_trait() -> TraitSpec:
    return TraitSpec(
        id="2.155.32990", wire_path="2.155.32990",
        function_code="Pressure", trait_code="CurrentPressure",
        name="CurrentPressure", data_type="float", unit="kPa",
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_pressure_becomes_sensor():
    descs = pressure_sensor.compose(endpoint_id=2, traits={"2.155.32990": _pressure_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_pressure_device_class_and_state_class():
    descs = pressure_sensor.compose(endpoint_id=2, traits={"2.155.32990": _pressure_trait()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.PRESSURE
    assert descs[0].state_class == SensorStateClass.MEASUREMENT


def test_pressure_unit_preserved():
    descs = pressure_sensor.compose(endpoint_id=2, traits={"2.155.32990": _pressure_trait()}, context=_ctx())
    assert descs[0].native_unit_of_measurement == "kPa"


def test_pressure_composer_registered():
    assert get_composer("PressureSensor") is pressure_sensor.compose


def test_atmospheric_pressure_sensor_routes_to_same_composer():
    """V3 catalogue declares the deviceType as 'AtmosphericPressureSensor'
    (e.g. lumi.sensor_ht.agl02, lumi.weather.v1). The composer dispatch
    must recognise this name -- without the alias every paired weather
    device produces a classify_v3 unknown-deviceType WARNING and falls
    through to per-trait classification.
    """
    assert get_composer("AtmosphericPressureSensor") is pressure_sensor.compose


def test_no_pressure_trait_emits_nothing():
    assert pressure_sensor.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_pressure_descriptor_has_default_display_precision():
    """Atmospheric pressure ships at 1 dp (e.g. `101.3 kPa`)."""
    descs = pressure_sensor.compose(
        endpoint_id=2, traits={"2.155.32990": _pressure_trait()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 1
