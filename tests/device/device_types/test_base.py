"""Tests for shared composer helpers in device_types/_base.py."""
from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.aqara_lanlink.device.device_types._base import (
    _default_precision,
)


@pytest.mark.parametrize("device_class,expected", [
    (SensorDeviceClass.HUMIDITY, 1),
    (SensorDeviceClass.TEMPERATURE, 1),
    (SensorDeviceClass.ATMOSPHERIC_PRESSURE, 1),
    (SensorDeviceClass.ILLUMINANCE, 0),
    (SensorDeviceClass.BATTERY, 0),
    (SensorDeviceClass.VOLTAGE, 1),
    (SensorDeviceClass.CURRENT, 2),
    (SensorDeviceClass.POWER, 1),
    (SensorDeviceClass.ENERGY, 3),
    (SensorDeviceClass.SIGNAL_STRENGTH, 0),
    # String forms also accepted (composers sometimes pass the bare value).
    ("humidity", 1),
    ("battery", 0),
])
def test_default_precision_known_device_class(device_class, expected):
    assert _default_precision(device_class) == expected


def test_default_precision_unknown_device_class_int_data_type():
    """Unknown device_class with int data_type -> 0 (so int values render
    as `87` not `87.0`)."""
    assert _default_precision(None, data_type="int") == 0
    assert _default_precision(SensorDeviceClass.ENUM, data_type="int") == 0


def test_default_precision_unknown_device_class_float_data_type():
    """Unknown device_class with float data_type -> None (HA renders the
    float at its native precision; user can override per-entity)."""
    assert _default_precision(None, data_type="float") is None


def test_default_precision_unknown_device_class_no_data_type():
    assert _default_precision(None) is None


def test_default_precision_accepts_string_form():
    """Composers building from generic device_class strings should still
    resolve cleanly."""
    assert _default_precision("temperature") == 1
    assert _default_precision("unknown_class") is None
