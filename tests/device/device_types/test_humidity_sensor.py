"""Tests for the HumiditySensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, humidity_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_ht.agl02")


def _humidity_trait() -> TraitSpec:
    return TraitSpec(
        id="4.144.32953", wire_path="4.144.32953",
        function_code="RelativeHumidity", trait_code="CurrentHumidity",
        name="CurrentHumidity", data_type="float", unit="%",
        readable=True, subscribable=True, endpoint_id=4,
    )


def test_humidity_becomes_sensor():
    descs = humidity_sensor.compose(endpoint_id=4, traits={"4.144.32953": _humidity_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_humidity_device_class_and_state_class():
    descs = humidity_sensor.compose(endpoint_id=4, traits={"4.144.32953": _humidity_trait()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.HUMIDITY
    assert descs[0].state_class == SensorStateClass.MEASUREMENT


def test_humidity_unit_preserved():
    descs = humidity_sensor.compose(endpoint_id=4, traits={"4.144.32953": _humidity_trait()}, context=_ctx())
    assert descs[0].native_unit_of_measurement == "%"


def test_humidity_composer_registered():
    assert get_composer("HumiditySensor") is humidity_sensor.compose


def test_no_humidity_trait_emits_nothing():
    assert humidity_sensor.compose(endpoint_id=4, traits={}, context=_ctx()) == []


def test_humidity_descriptor_has_default_display_precision():
    """Humidity sensors ship with `suggested_display_precision=1` so HA
    renders `48.97 %` and `56.08 %` from differently-precision firmwares
    consistently as `49.0 %` / `56.1 %` (user override via Settings ->
    Devices -> entity -> Configure)."""
    descs = humidity_sensor.compose(
        endpoint_id=4, traits={"4.144.32953": _humidity_trait()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 1
