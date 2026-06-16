"""Verification test: lumi.plug.aeu002 rid-keyed settings (Task 4).

Loads the real shipped model package. Discovery is triggered implicitly the
first time a catalog accessor calls _reg() / _ensure_discovered(), which walks
all real model subpackages (including plug_aeu002) and indexes their
data.json SETTINGS/POWER_CLASS via _loader.load_model_data.
"""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.build_descriptors import (
    build_setting_descriptors,
)
from custom_components.aqara_lanlink.device.descriptors import (
    ButtonDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
)

_MODEL = "lumi.plug.aeu002"

_EXPECTED_RIDS = {
    "4.4.85",
    "4.5.85",
    "8.0.2032",
    "8.0.2114",
    "8.0.2259",
    "14.11.85",
    "14.12.85",
    "8.0.2042",
    "8.0.2096",
}


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    registry.reset_for_tests()
    yield
    catalog.reset_for_tests()
    registry.reset_for_tests()


def test_settings_for_model_has_nine_rids():
    settings = catalog.settings_for_model(_MODEL)
    assert set(settings) == _EXPECTED_RIDS
    assert len(settings) == 9


def test_power_class_is_mains():
    assert catalog.power_class_for_model(_MODEL) == "mains"


def test_build_setting_descriptors_breakdown():
    descs = build_setting_descriptors(_MODEL)
    assert len(descs) == 9
    by_type = lambda t: [d for d in descs if isinstance(d, t)]  # noqa: E731
    assert len(by_type(SwitchDescriptor)) == 4
    assert len(by_type(SelectDescriptor)) == 3
    assert len(by_type(NumberDescriptor)) == 1
    assert len(by_type(ButtonDescriptor)) == 1


def test_find_device_enabled_by_default():
    by_name = {d.name: d for d in build_setting_descriptors(_MODEL)}
    assert by_name["Find device"].entity_registry_enabled_default is True


def test_indicator_light_switch_is_inverted():
    by_key = {d.key: d for d in build_setting_descriptors(_MODEL)}
    indicator = by_key["8.0.2032"]
    assert isinstance(indicator, SwitchDescriptor)
    assert indicator.on_value == "0"
    assert indicator.off_value == "1"
    # Child lock keeps the default (non-inverted) wire values.
    child_lock = by_key["4.4.85"]
    assert isinstance(child_lock, SwitchDescriptor)
    assert child_lock.on_value == "1"
    assert child_lock.off_value == "0"


def test_button_mode_socket_1_options_map():
    by_name = {d.name: d for d in build_setting_descriptors(_MODEL)}
    d = by_name["Button mode socket 1"]
    assert isinstance(d, SelectDescriptor)
    assert d.options_map == (
        ("Single-Press", "1"),
        ("Multi-Function", "2"),
    )
