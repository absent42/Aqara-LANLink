"""Tests for the ElectricalSensor deviceType composer."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.aqara_lanlink.device.device_types import (
    _base, electrical_sensor, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.plug.aeu001")


def _voltage() -> TraitSpec:
    return TraitSpec(
        id="2.220.33090", wire_path="2.220.33090",
        function_code="EnergyManagement", trait_code="CurrentVoltage",
        name="CurrentVoltage", data_type="float", unit="V",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _power() -> TraitSpec:
    return TraitSpec(
        id="2.220.33091", wire_path="2.220.33091",
        function_code="EnergyManagement", trait_code="CurrentPower",
        name="CurrentPower", data_type="float", unit="W",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _energy() -> TraitSpec:
    return TraitSpec(
        id="2.220.33092", wire_path="2.220.33092",
        function_code="EnergyManagement", trait_code="CumulativeEnergyConsumption",
        name="CumulativeEnergyConsumption", data_type="float", unit="kWh",
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_voltage_becomes_voltage_sensor():
    descs = electrical_sensor.compose(endpoint_id=2, traits={"2.220.33090": _voltage()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, SensorDescriptor)
    assert d.device_class == SensorDeviceClass.VOLTAGE
    assert d.state_class == SensorStateClass.MEASUREMENT


def test_power_becomes_power_sensor():
    descs = electrical_sensor.compose(endpoint_id=2, traits={"2.220.33091": _power()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.POWER
    assert descs[0].state_class == SensorStateClass.MEASUREMENT


def test_cumulative_energy_becomes_total_increasing_energy_sensor():
    descs = electrical_sensor.compose(endpoint_id=2, traits={"2.220.33092": _energy()}, context=_ctx())
    assert descs[0].device_class == SensorDeviceClass.ENERGY
    assert descs[0].state_class == SensorStateClass.TOTAL_INCREASING


def test_full_electrical_set():
    traits = {t.id: t for t in (_voltage(), _power(), _energy())}
    descs = electrical_sensor.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 3


def test_empty_traits_returns_empty():
    assert electrical_sensor.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_electrical_composer_registered():
    assert get_composer("ElectricalSensor") is electrical_sensor.compose


def test_voltage_descriptor_has_default_display_precision():
    descs = electrical_sensor.compose(
        endpoint_id=2, traits={"2.220.33090": _voltage()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 1


def test_power_descriptor_has_default_display_precision():
    descs = electrical_sensor.compose(
        endpoint_id=2, traits={"2.220.33091": _power()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 1


def test_energy_descriptor_has_default_display_precision():
    """Energy uses 3 dp to capture sub-Wh resolution in kWh totals."""
    descs = electrical_sensor.compose(
        endpoint_id=2, traits={"2.220.33092": _energy()}, context=_ctx(),
    )
    assert descs[0].suggested_display_precision == 3
