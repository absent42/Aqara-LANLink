"""Composer for the IlluminanceSensor deviceType."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

from ._base import make_single_trait_composer, measurement_sensor

# Illuminance is always reported in lux; ignore any unit the trait declares.
compose = make_single_trait_composer(
    function_code="Illuminance",
    trait_code="CurrentIlluminance",
    descriptor_factory=lambda spec: measurement_sensor(
        spec, SensorDeviceClass.ILLUMINANCE, "lx", use_spec_unit=False,
    ),
)
