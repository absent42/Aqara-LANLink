"""Composer for the TemperatureSensor deviceType."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

from ._base import make_single_trait_composer, measurement_sensor

compose = make_single_trait_composer(
    function_code="Temperature",
    trait_code="CurrentTemperature",
    descriptor_factory=lambda spec: measurement_sensor(
        spec, SensorDeviceClass.TEMPERATURE, "°C",
    ),
)
