"""Tests for the ContactSensor deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, contact_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import BinarySensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.binary_sensor import BinarySensorDeviceClass


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.magnet.agl02")


def test_contact_state_becomes_binary_sensor():
    traits = {"2.99.32000": TraitSpec(
        id="2.99.32000", wire_path="2.99.32000",
        function_code="Contact", trait_code="ContactSensorState",
        name="ContactSensorState", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )}
    descs = contact_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], BinarySensorDescriptor)
    assert descs[0].device_class == BinarySensorDeviceClass.OPENING


def test_contact_state_inverted_on_values():
    """ContactSensorState=1 means 'contact' (closed); HA opening device class
    treats true = open. Map on_values={'0'} to invert."""
    traits = {"2.99.32000": TraitSpec(
        id="2.99.32000", wire_path="2.99.32000",
        function_code="Contact", trait_code="ContactSensorState",
        name="ContactSensorState", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )}
    descs = contact_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs[0].on_values == frozenset({"0"})


def test_contact_composer_registered():
    assert get_composer("ContactSensor") is contact_sensor.compose


def test_contact_without_state_trait_emits_nothing():
    descs = contact_sensor.compose(endpoint_id=2, traits={}, context=_ctx())
    assert descs == []
