"""Tests for build_setting_descriptors / build_descriptors settings wiring (Task 3)."""

from __future__ import annotations

import types

import pytest
from homeassistant.const import EntityCategory

from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.build_descriptors import (
    build_descriptors,
    build_setting_descriptors,
)
from custom_components.aqara_lanlink.device.descriptors import (
    ButtonDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.settings import SettingSpec

_MODEL = "lumi.fake.setdesc"


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    registry.reset_for_tests()
    yield
    catalog.reset_for_tests()
    registry.reset_for_tests()


def _index_settings_pkg():
    fake = types.ModuleType("fake_setdesc_pkg")
    fake.MODELS = (_MODEL,)
    fake.MANUFACTURER = "Aqara"
    fake.DISPLAY_NAME = "Fake Setting Descriptors"
    fake.SETTINGS = {
        "4.4.85": SettingSpec(rid="4.4.85", name="Child lock", platform="switch"),
        "4.5.85": SettingSpec(
            rid="4.5.85",
            name="Button mode",
            platform="select",
            enum_values={"1": "Single-Press", "2": "Multi-Function"},
        ),
        "4.6.85": SettingSpec(
            rid="4.6.85",
            name="Sensitivity",
            platform="number",
            min=0,
            max=100,
            unit="%",
        ),
        "4.7.85": SettingSpec(
            rid="4.7.85", name="Identify", platform="button", press_value="7",
        ),
        "4.8.85": SettingSpec(
            rid="4.8.85",
            name="Hidden",
            platform="switch",
            default_enabled=False,
        ),
        "4.9.85": SettingSpec(
            rid="4.9.85",
            name="Diag toggle",
            platform="switch",
            entity_category="diagnostic",
        ),
        "4.10.85": SettingSpec(
            rid="4.10.85",
            name="Inverted toggle",
            platform="switch",
            on_value="0",
            off_value="1",
        ),
    }
    registry._index_package(fake, "fake_setdesc_pkg")


def _by_key(descs):
    return {d.key: d for d in descs}


def test_switch_descriptor_built():
    _index_settings_pkg()
    by_key = _by_key(build_setting_descriptors(_MODEL))
    d = by_key["4.4.85"]
    assert isinstance(d, SwitchDescriptor)
    assert d.name == "Child lock"
    assert d.attr.resource_id == "4.4.85"
    assert d.attr.name == "4.4.85"
    assert d.on_value == "1"
    assert d.off_value == "0"
    assert d.optimistic is True
    assert d.entity_category == EntityCategory.CONFIG
    assert d.entity_registry_enabled_default is True


def test_switch_descriptor_inverted_on_off_values():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.10.85"]
    assert isinstance(d, SwitchDescriptor)
    assert d.on_value == "0"
    assert d.off_value == "1"


def test_select_descriptor_inverts_enum_in_order():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.5.85"]
    assert isinstance(d, SelectDescriptor)
    assert d.options_map == (
        ("Single-Press", "1"),
        ("Multi-Function", "2"),
    )
    assert d.optimistic is True
    assert d.attr.resource_id == "4.5.85"


def test_number_descriptor_min_max_unit():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.6.85"]
    assert isinstance(d, NumberDescriptor)
    assert d.min_value == 0
    assert d.max_value == 100
    assert d.native_unit_of_measurement == "%"
    assert d.optimistic is True
    assert d.attr.resource_id == "4.6.85"


def test_button_descriptor_press_value():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.7.85"]
    assert isinstance(d, ButtonDescriptor)
    assert d.press_value == "7"
    assert d.attr.resource_id == "4.7.85"
    assert d.attr.name == "4.7.85"


def test_default_enabled_false_propagates():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.8.85"]
    assert d.entity_registry_enabled_default is False


def test_diagnostic_entity_category():
    _index_settings_pkg()
    d = _by_key(build_setting_descriptors(_MODEL))["4.9.85"]
    assert d.entity_category == EntityCategory.DIAGNOSTIC


def test_unknown_platform_skipped(caplog):
    fake = types.ModuleType("fake_unknown_pkg")
    fake.MODELS = ("lumi.fake.unknownplat",)
    fake.MANUFACTURER = "Aqara"
    fake.DISPLAY_NAME = "Unknown Plat"
    fake.SETTINGS = {
        "4.4.85": SettingSpec(rid="4.4.85", name="Weird", platform="banana"),
    }
    registry._index_package(fake, "fake_unknown_pkg")
    import logging

    with caplog.at_level(logging.WARNING):
        descs = build_setting_descriptors("lumi.fake.unknownplat")
    assert descs == []
    assert any("banana" in r.message for r in caplog.records)


def test_build_descriptors_includes_settings():
    _index_settings_pkg()
    descs = build_descriptors(_MODEL, Overlay())
    keys = {d.key for d in descs}
    # All six settings appear (the model has no traits, so these are the only ones).
    assert {"4.4.85", "4.5.85", "4.6.85", "4.7.85", "4.8.85", "4.9.85"} <= keys
