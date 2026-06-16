"""Tests for catalog settings/power_class accessors (Task 2c)."""

from __future__ import annotations

import types

import pytest

from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.settings import SettingSpec


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    yield
    catalog.reset_for_tests()


def _index_settings_pkg():
    registry.reset_for_tests()
    fake = types.ModuleType("fake_settings_pkg")
    fake.MODELS = ("lumi.fake.set",)
    fake.MANUFACTURER = "Aqara"
    fake.DISPLAY_NAME = "Fake Settings"
    fake.SETTINGS = {
        "4.4.85": SettingSpec(rid="4.4.85", name="Lock", platform="switch"),
    }
    fake.POWER_CLASS = "mains"
    registry._index_package(fake, "fake_settings_pkg")


def test_settings_for_model_returns_merged_settings():
    _index_settings_pkg()
    result = catalog.settings_for_model("lumi.fake.set")
    assert result["4.4.85"].name == "Lock"


def test_settings_for_model_returns_copy():
    _index_settings_pkg()
    result = catalog.settings_for_model("lumi.fake.set")
    result["junk"] = None
    assert "junk" not in catalog.settings_for_model("lumi.fake.set")


def test_settings_for_unknown_model_returns_empty():
    assert catalog.settings_for_model("lumi.unknown.zzz") == {}


def test_power_class_for_model():
    _index_settings_pkg()
    assert catalog.power_class_for_model("lumi.fake.set") == "mains"


def test_power_class_for_unknown_model_returns_none():
    assert catalog.power_class_for_model("lumi.unknown.zzz") is None
