"""Composer for the ContactSensor deviceType."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.aqara_lanlink.device.descriptors import BinarySensorDescriptor

from ._base import _ec, make_single_trait_composer

compose = make_single_trait_composer(
    function_code="Contact",
    trait_code="ContactSensorState",
    descriptor_factory=lambda spec: BinarySensorDescriptor(
        key=f"auto_{spec.id.replace('.', '_')}",
        name=spec.name,
        trait=spec,
        device_class=BinarySensorDeviceClass.OPENING,
        # Invert: wire 1 = closed/contact; HA opening treats true = open.
        on_values=frozenset({"0"}),
        entity_category=_ec(spec),
        entity_registry_enabled_default=spec.default_enabled,
    ),
)
