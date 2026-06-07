"""Tests for the Thermostat deviceType composer (HeaterCooler cluster)."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.aqara_lanlink.device.device_types import (
    _base, thermostat, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor, SelectDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.airrtc.aeu005")


def _current_temp() -> TraitSpec:
    return TraitSpec(
        id="2.190.32952", wire_path="2.190.32952",
        function_code="HeaterCooler", trait_code="CurrentTemperature",
        name="CurrentTemperature", data_type="float", unit="°C",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _mode() -> TraitSpec:
    return TraitSpec(
        id="2.190.32960", wire_path="2.190.32960",
        function_code="HeaterCooler", trait_code="HeaterCoolerMode",
        name="HeaterCoolerMode", data_type="enum",
        enum_values={"0": "off", "1": "heat", "2": "cool", "3": "auto"},
        readable=True, writable=True, subscribable=True, endpoint_id=2,
    )


def _heating_temp() -> TraitSpec:
    return TraitSpec(
        id="2.190.32961", wire_path="2.190.32961",
        function_code="HeaterCooler", trait_code="HeatingTemperature",
        name="HeatingTemperature", data_type="float", unit="°C",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=5.0, max_value=35.0, step=0.5,
    )


def _antifreeze() -> TraitSpec:
    return TraitSpec(
        id="2.190.32962", wire_path="2.190.32962",
        function_code="HeaterCooler", trait_code="AntiFreezeTemperature",
        name="AntiFreezeTemperature", data_type="float", unit="°C",
        readable=True, writable=True, endpoint_id=2,
        min_value=5.0, max_value=15.0, step=0.5,
    )


def test_current_temperature_becomes_sensor():
    descs = thermostat.compose(endpoint_id=2, traits={"2.190.32952": _current_temp()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, SensorDescriptor)
    assert d.device_class == SensorDeviceClass.TEMPERATURE
    assert d.state_class == SensorStateClass.MEASUREMENT


def test_mode_becomes_select():
    descs = thermostat.compose(endpoint_id=2, traits={"2.190.32960": _mode()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, SelectDescriptor)
    # options_map stores (label, wire_value) pairs in the order
    # enum_values iterates. We assert label set equality, not order.
    labels = {label for label, _wire in d.options_map}
    assert labels == {"off", "heat", "cool", "auto"}


def test_heating_temperature_becomes_temperature_number():
    descs = thermostat.compose(endpoint_id=2, traits={"2.190.32961": _heating_temp()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, NumberDescriptor)
    assert d.device_class == NumberDeviceClass.TEMPERATURE
    assert d.min_value == 5.0
    assert d.max_value == 35.0


def test_antifreeze_is_config_number():
    descs = thermostat.compose(endpoint_id=2, traits={"2.190.32962": _antifreeze()}, context=_ctx())
    assert len(descs) == 1
    assert descs[0].entity_category == EntityCategory.CONFIG


def test_full_thermostat_set():
    traits = {t.id: t for t in (_current_temp(), _mode(), _heating_temp(), _antifreeze())}
    descs = thermostat.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 4


def test_thermostat_composer_registered():
    assert get_composer("Thermostat") is thermostat.compose


# ---------------------------------------------------------------------------
# AirConditioner deviceType -- reuses thermostat.compose with extensions.
# Captured on lumi.gateway.agl004 ep 3 (M3 hub's IR AC pairing).
# ---------------------------------------------------------------------------


def _cooling_temp() -> TraitSpec:
    return TraitSpec(
        id="3.141.32949", wire_path="3.141.32949",
        function_code="HeaterCooler", trait_code="CoolingTemperature",
        name="CoolingTemperature", data_type="float", unit="℃",
        readable=True, writable=True, subscribable=True, endpoint_id=3,
        min_value=16.0, max_value=30.0, step=1.0,
    )


def _current_humidity() -> TraitSpec:
    return TraitSpec(
        id="3.141.32953", wire_path="3.141.32953",
        function_code="HeaterCooler", trait_code="CurrentHumidity",
        name="CurrentHumidity", data_type="float", unit="%",
        readable=True, subscribable=True, endpoint_id=3,
    )


def test_cooling_temperature_becomes_temperature_number():
    """AirConditioner endpoints carry CoolingTemperature alongside the
    Thermostat-typical HeatingTemperature. Both must produce a TEMPERATURE
    NumberDescriptor with the trait's min/max/step honored.
    """
    descs = thermostat.compose(
        endpoint_id=3, traits={_cooling_temp().id: _cooling_temp()}, context=_ctx(),
    )
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, NumberDescriptor)
    assert d.device_class == NumberDeviceClass.TEMPERATURE
    assert d.min_value == 16.0
    assert d.max_value == 30.0


def test_current_humidity_becomes_humidity_sensor():
    """AirConditioner endpoints carry a HeaterCooler.CurrentHumidity readonly
    sensor. The composer must give it the HUMIDITY device_class so the
    HA card shows the right icon.
    """
    descs = thermostat.compose(
        endpoint_id=3, traits={_current_humidity().id: _current_humidity()}, context=_ctx(),
    )
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, SensorDescriptor)
    assert d.device_class == SensorDeviceClass.HUMIDITY
    assert d.state_class == SensorStateClass.MEASUREMENT


def test_airconditioner_devicetype_routes_to_thermostat_composer():
    """AirConditioner reuses thermostat.compose -- registering it suppresses
    the unknown-deviceType warning and gives proper HeaterCooler handling."""
    assert get_composer("AirConditioner") is thermostat.compose


def test_irdevice_devicetype_routes_to_fallback_composer():
    """IRDevice endpoints only carry writable enums (IRType, IRKey). Registering
    them at _fallback.compose suppresses the unknown-deviceType warning while
    keeping the per-trait Select entity output that _fallback already produces."""
    from custom_components.aqara_lanlink.device.device_types import _fallback
    assert get_composer("IRDevice") is _fallback.compose
