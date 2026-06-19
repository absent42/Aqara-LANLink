"""End-to-end verification: rid-keyed settings surface as HA entities (Task 9).

Builds a real `lumi.plug.aeu002` device from the shipped catalogue
(`build_descriptors`), runs each writable platform's discovery filter
(`build_descriptor_entities`), and asserts the 10 setting descriptors
become real `AqaraSwitch` / `AqaraSelect` / `AqaraNumber` / `AqaraButton`
entities with stable, unique ids -- and that a switch turn-on and the
find-device button press write the BARE rid to the coordinator.

This proves the feature surfaces end-to-end (entity discovery + write
path) before the live at-device check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.aqara_lanlink.button import AqaraButton
from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.build_descriptors import build_descriptors
from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    ButtonDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
    TextDescriptor,
)
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.entity import build_descriptor_entities
from custom_components.aqara_lanlink.number import AqaraNumber
from custom_components.aqara_lanlink.select import AqaraSelect
from custom_components.aqara_lanlink.switch import AqaraSwitch
from custom_components.aqara_lanlink.text import AqaraText

from homeassistant.const import EntityCategory

from .conftest import make_device, make_hub, make_subentry

_MODEL = "lumi.plug.aeu002"

# The 9 rid-keyed settings, partitioned by the platform that should own them.
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
    """Real-model device + hub + subentry from the shipped catalogue."""
    descriptors = build_descriptors(_MODEL, Overlay())
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-aeu002", did="did-aeu002", model=_MODEL)
    device = make_device(descriptors, subentry=sub, model=_MODEL)
    return hub, sub, device


def _entities(hub, device, sub):
    """Run every writable platform's discovery filter against the device."""
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


def test_each_platform_picks_up_its_setting_descriptors():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)

    # Setting rids each platform produced.
    switch_rids = {e.descriptor.key for e in ents["switch"]} & _ALL_SETTING_RIDS
    select_rids = {e.descriptor.key for e in ents["select"]} & _ALL_SETTING_RIDS
    number_rids = {e.descriptor.key for e in ents["number"]} & _ALL_SETTING_RIDS
    button_rids = {e.descriptor.key for e in ents["button"]} & _ALL_SETTING_RIDS

    assert switch_rids == _SWITCH_RIDS
    assert select_rids == _SELECT_RIDS
    assert number_rids == _NUMBER_RIDS
    assert button_rids == _BUTTON_RIDS


def test_setting_entity_counts_match_expected_breakdown():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)

    # The aeu002 trait set adds 3 trait switches (On off 1/2/3) on the switch
    # platform; the 4 setting switches sit alongside them. Selects, numbers and
    # buttons have no trait entities for this model, so their counts are exactly
    # the setting counts.
    setting_switches = [e for e in ents["switch"] if e.descriptor.key in _SWITCH_RIDS]
    assert len(setting_switches) == 4
    assert len(ents["select"]) == 3
    assert len(ents["number"]) == 1
    assert len(ents["button"]) == 1


def test_setting_entity_unique_ids_are_stable_unique_and_non_colliding():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    all_entities = [e for group in ents.values() for e in group]

    uids = [e.unique_id for e in all_entities]
    # Every entity has a non-empty unique id.
    assert all(uid for uid in uids)
    # No collisions across all writable-platform entities (settings + traits).
    assert len(uids) == len(set(uids))

    # The 9 setting entities' ids are derived from the subentry id + rid key,
    # so they are stable and distinct from each other and from trait entities.
    setting_uids = {
        e.unique_id for e in all_entities if e.descriptor.key in _ALL_SETTING_RIDS
    }
    assert len(setting_uids) == 9
    for e in all_entities:
        if e.descriptor.key in _ALL_SETTING_RIDS:
            assert e.unique_id == f"{sub.subentry_id}_{e.descriptor.key}"


@pytest.mark.asyncio
async def test_switch_turn_on_writes_bare_rid():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]

    child_lock = next(e for e in ents["switch"] if e.descriptor.key == "4.4.85")
    await child_lock.async_turn_on()

    # The recorded write keys on the descriptor's AttrSpec; that AttrSpec
    # carries resource_id == "4.4.85", which the coordinator sends bare.
    device.async_write.assert_awaited_once()
    (payload,), _ = device.async_write.await_args
    assert list(payload.values()) == [child_lock.descriptor.on_value]
    (attr_key,) = payload.keys()
    assert attr_key.resource_id == "4.4.85"


@pytest.mark.asyncio
async def test_find_device_button_press_writes_bare_rid():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]

    find_device = next(e for e in ents["button"] if e.descriptor.key == "8.0.2096")
    await find_device.async_press()

    device.async_write.assert_awaited_once()
    (payload,), _ = device.async_write.await_args
    (attr_key,) = payload.keys()
    assert attr_key.resource_id == "8.0.2096"
    assert payload[attr_key] == "1"


def test_seed_then_register_reaches_switch_state():
    """Seed -> register delivers the staged rid value to a switch's state.

    Realistic ordering: the device stages the cloud rid-seed (the bare rid
    is the wire path for attr-backed setting descriptors) BEFORE the entity
    registers; register_entity must replay it through device._apply ->
    entity.apply_value, leaving the switch's cached is_on reflecting it.
    """
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    switch = next(e for e in ents["switch"] if e.descriptor.key == "4.4.85")

    # The rid IS the wire path for attr-backed descriptors.
    device.seed_initial_value("4.4.85", "1")
    device.register_entity(switch.descriptor, switch)

    # apply_value sets the cached _attr_is_on before the (no-op when not added)
    # HA-state write; is_on reads it back.
    assert switch.is_on is True


def test_seed_then_register_reaches_select_state():
    """Seed -> register delivers the staged rid value to a select's state.

    Exercises the reverse-lookup apply_value path: wire "2" maps to label
    "Off" in the aeu002 power-off-memory map
    {"0":"On","1":"Previous","2":"Off","3":"Inverted"}.
    """
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)
    select = next(e for e in ents["select"] if e.descriptor.key == "8.0.2259")

    device.seed_initial_value("8.0.2259", "2")
    device.register_entity(select.descriptor, select)

    assert select.current_option == "Off"


def test_find_device_button_enabled_by_default():
    hub, sub, device = _build_aeu002_device()
    ents = _entities(hub, device, sub)

    find_device = next(e for e in ents["button"] if e.descriptor.key == "8.0.2096")
    assert find_device.entity_registry_enabled_default is True
    assert find_device.descriptor.press_value == "1"


def _build_text_setting_device():
    """A synthetic device carrying one text-setting descriptor (bare rid)."""
    descriptor = TextDescriptor(
        key="14.8.701",
        name="Color paragraph",
        entity_category=EntityCategory.CONFIG,
        attr=AttrSpec(name="14.8.701", resource_id="14.8.701"),
        optimistic=True,
    )
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-text", did="did-text", model=_MODEL)
    device = make_device([descriptor], subentry=sub, model=_MODEL)
    return hub, sub, device


def test_text_setting_becomes_text_entity():
    hub, sub, device = _build_text_setting_device()
    ents = build_descriptor_entities(hub, device, sub, TextDescriptor, AqaraText)
    assert len(ents) == 1
    assert isinstance(ents[0], AqaraText)
    assert ents[0].descriptor.key == "14.8.701"
    assert ents[0].unique_id == "sub-text_14.8.701"


@pytest.mark.asyncio
async def test_text_set_value_writes_bare_rid():
    hub, sub, device = _build_text_setting_device()
    (text,) = build_descriptor_entities(hub, device, sub, TextDescriptor, AqaraText)
    device.async_write = AsyncMock()  # type: ignore[method-assign]

    await text.async_set_value("FFEE00")

    device.async_write.assert_awaited_once()
    (payload,), _ = device.async_write.await_args
    (attr_key,) = payload.keys()
    assert attr_key.resource_id == "14.8.701"
    assert payload[attr_key] == "FFEE00"
    # Optimistic: the new value is reflected immediately.
    assert text.native_value == "FFEE00"
