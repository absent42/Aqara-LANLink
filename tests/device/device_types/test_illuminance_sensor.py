"""Tests for the IlluminanceSensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, illuminance_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.sensor_motion.agl02")


def _illuminance_trait(unit: str = "lx") -> TraitSpec:
    return TraitSpec(
        id="2.154.32989", wire_path="2.154.32989",
        function_code="Illuminance", trait_code="CurrentIlluminance",
        name="CurrentIlluminance", data_type="float", unit=unit,
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_illuminance_becomes_sensor():
    descs = illuminance_sensor.compose(endpoint_id=2, traits={"2.154.32989": _illuminance_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_illuminance_device_class_and_state_class():
    descs = illuminance_sensor.compose(endpoint_id=2, traits={"2.154.32989": _illuminance_trait()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.ILLUMINANCE
    assert descs[0].state_class == SensorStateClass.MEASUREMENT


def test_illuminance_unit_normalised_to_lx():
    # V3 may report "lux"; we must always produce HA's canonical "lx".
    descs = illuminance_sensor.compose(endpoint_id=2, traits={"2.154.32989": _illuminance_trait(unit="lux")}, context=_ctx())
    assert descs[0].native_unit_of_measurement == "lx"


def test_illuminance_composer_registered():
    assert get_composer("IlluminanceSensor") is illuminance_sensor.compose


def test_no_illuminance_trait_emits_nothing():
    assert illuminance_sensor.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_illuminance_descriptor_has_default_display_precision():
    """Illuminance ships at 0 dp -- lux is integer-natured."""
    descs = illuminance_sensor.compose(
        endpoint_id=2, traits={"2.154.32989": _illuminance_trait()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 0
