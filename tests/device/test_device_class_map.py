"""Unit tests for device_class_map (static lookup table)."""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device import device_class_map
from custom_components.aqara_lanlink.device.device_class_map import (
    AQARA_DEVICE_TYPE_TO_HA,
    device_class_for,
)


class TestKnownTypes:
    """One test per row of the table; mismatched kinds correctly return None."""

    @pytest.mark.parametrize(
        "aqara_type,master_kind,expected",
        [
            ("OccupancySensor",   "binary_sensor", "occupancy"),
            ("MotionSensor",      "binary_sensor", "motion"),
            ("TemperatureSensor", "sensor",        "temperature"),
            ("HumiditySensor",    "sensor",        "humidity"),
            ("IlluminanceSensor", "sensor",        "illuminance"),
            ("VibrationSensor",   "event",         None),
            ("Button",            "event",         None),
            ("Light",             "light",         None),
            ("Camera",            "camera",        None),
        ],
    )
    def test_returns_device_class_when_kind_matches(self, aqara_type, master_kind, expected):
        assert device_class_for(aqara_type, master_kind) == expected


class TestKindMismatch:
    def test_returns_none_when_master_kind_disagrees(self):
        assert device_class_for("TemperatureSensor", "binary_sensor") is None

    def test_returns_none_when_occupancy_synthesises_as_sensor(self):
        assert device_class_for("OccupancySensor", "sensor") is None


class TestUnknownType:
    def test_returns_none_for_unknown_aqara_type(self):
        assert device_class_for("FutureSensor", "sensor") is None

    def test_returns_none_for_empty_aqara_type(self):
        assert device_class_for("", "sensor") is None


class TestHubExcluded:
    def test_hub_not_in_table(self):
        assert "Hub" not in AQARA_DEVICE_TYPE_TO_HA
