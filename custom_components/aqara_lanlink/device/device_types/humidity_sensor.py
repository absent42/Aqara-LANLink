"""Composer for the HumiditySensor deviceType."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

from ._base import make_single_trait_composer, measurement_sensor

compose = make_single_trait_composer(
    function_code="RelativeHumidity",
    trait_code="CurrentHumidity",
    descriptor_factory=lambda spec: measurement_sensor(
        spec, SensorDeviceClass.HUMIDITY, "%",
    ),
)
