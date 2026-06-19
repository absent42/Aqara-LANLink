"""aeu002 settings reproduction gate (Task 11).

Locks the shipped `lumi.plug.aeu002` setting entity set against the regenerated
catalogue `data.json`. The v1 model was hand-authored as 9 rid-keyed settings;
the v2 generator must reproduce the EXACT same 9 entities, partition, and field
values purely from the regenerated `data.json` (the `plug_aeu002/overrides.py`
`SETTINGS_OVERRIDES` map is empty - the CSV/data.json carries every field).

If this gate fails, the bug is in the generated `data.json` / CSV rows, NOT in
this test - do not weaken the test to pass.
"""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.button import AqaraButton
from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.build_descriptors import build_descriptors
from custom_components.aqara_lanlink.device.descriptors import (
    ButtonDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.models.plug_aeu002.overrides import (
    SETTINGS_OVERRIDES,
)
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.entity import build_descriptor_entities
from custom_components.aqara_lanlink.number import AqaraNumber
from custom_components.aqara_lanlink.select import AqaraSelect
from custom_components.aqara_lanlink.switch import AqaraSwitch

from ..platforms.conftest import make_device, make_hub, make_subentry

_MODEL = "lumi.plug.aeu002"

# The v1 9-setting entity set, partitioned by owning platform.
_SWITCH_RIDS = {"4.4.85", "4.5.85", "8.0.2032", "8.0.2114"}
_SELECT_RIDS = {"8.0.2259", "14.11.85", "14.12.85"}
_NUMBER_RIDS = {"8.0.2042"}
_BUTTON_RIDS = {"8.0.2096"}
_ALL_SETTING_RIDS = _SWITCH_RIDS | _SELECT_RIDS | _NUMBER_RIDS | _BUTTON_RIDS


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    registry.reset_for_tests()
    yield
    catalog.reset_for_tests()
    registry.reset_for_tests()


def _build_aeu002_device():
    descriptors = build_descriptors(_MODEL, Overlay())
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-aeu002", did="did-aeu002", model=_MODEL)
    device = make_device(descriptors, subentry=sub, model=_MODEL)
    return hub, sub, device


def _entities(hub, device, sub):
    return {
        "switch": build_descriptor_entities(
            hub, device, sub, SwitchDescriptor, AqaraSwitch
        ),
        "select": build_descriptor_entities(
            hub, device, sub, SelectDescriptor, AqaraSelect
        ),
        "number": build_descriptor_entities(
            hub, device, sub, NumberDescriptor, AqaraNumber
        ),
        "button": build_descriptor_entities(
            hub, device, sub, ButtonDescriptor, AqaraButton
        ),
    }


def test_overrides_settings_map_is_empty():
    # The CSV/data.json carries every field; no maintainer override is needed.
    assert SETTINGS_OVERRIDES == {}


def test_reproduces_the_nine_v1_setting_rids():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    all_entities = [e for group in ents.values() for e in group]
    setting_rids = {
        e.descriptor.key
        for e in all_entities
        if e.descriptor.key in _ALL_SETTING_RIDS
    }
    assert setting_rids == _ALL_SETTING_RIDS
    assert len(_ALL_SETTING_RIDS) == 9


def test_platform_partition_matches_v1():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)

    switch_rids = {e.descriptor.key for e in ents["switch"]} & _ALL_SETTING_RIDS
    select_rids = {e.descriptor.key for e in ents["select"]} & _ALL_SETTING_RIDS
    number_rids = {e.descriptor.key for e in ents["number"]} & _ALL_SETTING_RIDS
    button_rids = {e.descriptor.key for e in ents["button"]} & _ALL_SETTING_RIDS

    assert switch_rids == _SWITCH_RIDS
    assert select_rids == _SELECT_RIDS
    assert number_rids == _NUMBER_RIDS
    assert button_rids == _BUTTON_RIDS

    # 4 switches / 3 selects / 1 number / 1 button (settings only).
    setting_switches = [e for e in ents["switch"] if e.descriptor.key in _SWITCH_RIDS]
    assert len(setting_switches) == 4
    assert len(ents["select"]) == 3
    assert len(ents["number"]) == 1
    assert len(ents["button"]) == 1


def test_indicator_light_is_inverted_switch():
    # 8.0.2032 is the inverted indicator: on maps to wire "0".
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    indicator = next(e for e in ents["switch"] if e.descriptor.key == "8.0.2032")
    assert indicator.descriptor.on_value == "0"
    assert indicator.descriptor.off_value == "1"


def test_max_power_number_range_and_unit():
    # 8.0.2042 max power: min/max/unit from the CSV columns (no override).
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    max_power = next(e for e in ents["number"] if e.descriptor.key == "8.0.2042")
    assert max_power.descriptor.min_value == 100
    assert max_power.descriptor.max_value == 3250
    assert max_power.descriptor.native_unit_of_measurement == "W"


def test_find_device_button_press_value():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    find_device = next(e for e in ents["button"] if e.descriptor.key == "8.0.2096")
    assert find_device.descriptor.press_value == "1"
