"""Tests for `AqaraEntity` base class."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.const import DOMAIN
from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor
from custom_components.aqara_lanlink.entity import AqaraEntity

from .conftest import make_device, make_hub, make_subentry


def _switch_descriptor() -> SwitchDescriptor:
    return SwitchDescriptor(
        key="test_power",
        attr=AttrSpec(name="test_power_attr"),
    )


def test_descriptor_mode_unique_id_uses_subentry_id_and_descriptor_key():
    desc = _switch_descriptor()
    hub = make_hub(did="hub-did-001")
    subentry = make_subentry(subentry_id="sub_abc", did="dev-did-001")
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity._attr_unique_id == "sub_abc_test_power"


def test_descriptor_mode_device_info_links_via_hub():
    desc = _switch_descriptor()
    hub = make_hub(did="hub-did-001")
    subentry = make_subentry(did="dev-did-002")
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    info = entity._attr_device_info
    assert info["identifiers"] == {(DOMAIN, "dev-did-002")}
    assert info["manufacturer"] == "Aqara"
    # name shows the friendly display name (e.g. "T2 Color Bulb") on the
    # HA Device card; model shows the Aqara model code (e.g. "lumi.light.agl003").
    assert info["name"] == "Test Device"
    assert info["model"] == "test.model.x"
    assert info["via_device"] == (DOMAIN, "hub-did-001")


def test_camera_mode_uses_unique_id_suffix_when_descriptor_none():
    hub = make_hub()
    subentry = make_subentry(subentry_id="sub_xyz")
    device = make_device([], subentry=subentry)
    entity = AqaraEntity(
        hub, device, subentry,
        descriptor=None, unique_id_suffix="camera",
    )
    assert entity._attr_unique_id == "sub_xyz_camera"
    assert entity.descriptor is None


def test_descriptor_none_without_suffix_raises_value_error():
    hub = make_hub()
    subentry = make_subentry()
    device = make_device([], subentry=subentry)
    with pytest.raises(ValueError, match="descriptor or unique_id_suffix"):
        AqaraEntity(hub, device, subentry, descriptor=None)


def test_descriptor_none_with_empty_suffix_raises_value_error():
    hub = make_hub()
    subentry = make_subentry()
    device = make_device([], subentry=subentry)
    with pytest.raises(ValueError, match="descriptor or unique_id_suffix"):
        AqaraEntity(
            hub, device, subentry, descriptor=None, unique_id_suffix=""
        )


def test_available_property_reflects_hub_connected():
    desc = _switch_descriptor()
    hub = make_hub(connected=True)
    subentry = make_subentry()
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity.available is True

    hub.connected = False
    assert entity.available is False


def test_available_reads_hub_connected_directly():
    desc = _switch_descriptor()
    # `available` accesses hub.connected directly (HubCoordinator now
    # exposes it as a defined property; no defensive getattr fallback).
    hub = MagicMock(spec=["did", "connected"])
    hub.did = "hub-did"
    hub.connected = False
    subentry = make_subentry()
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity.available is False


def test_available_false_when_lan_direct_device_offline():
    desc = _switch_descriptor()
    hub = make_hub(connected=True)
    hub.is_lan_direct_offline = lambda did: True  # dropped from LAN-direct topology
    subentry = make_subentry()
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity.available is False


def test_available_true_when_connected_and_not_offline():
    desc = _switch_descriptor()
    hub = make_hub(connected=True)
    hub.is_lan_direct_offline = lambda did: False
    subentry = make_subentry()
    device = make_device([desc], subentry=subentry)
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity.available is True


@pytest.mark.asyncio
async def test_async_added_to_hass_registers_with_device_when_descriptor_set():
    desc = _switch_descriptor()
    hub = make_hub()
    subentry = make_subentry()
    device = make_device([desc], subentry=subentry)
    # Wrap register_entity so we can observe the call without losing the
    # real return value (the unregister callable).
    original_register = device.register_entity
    calls: list = []

    def wrapper(d, e):
        calls.append((d, e))
        return original_register(d, e)

    device.register_entity = wrapper  # type: ignore[method-assign]

    entity = AqaraEntity(hub, device, subentry, desc)
    # `async_on_remove` requires hass; install a minimal stub.
    entity.async_on_remove = lambda fn: None  # type: ignore[method-assign]
    await entity.async_added_to_hass()

    assert len(calls) == 1
    assert calls[0] == (desc, entity)


@pytest.mark.asyncio
async def test_async_added_to_hass_does_not_register_when_descriptor_none():
    hub = make_hub()
    subentry = make_subentry()
    device = make_device([], subentry=subentry)
    device.register_entity = MagicMock()  # type: ignore[method-assign]
    entity = AqaraEntity(
        hub, device, subentry,
        descriptor=None, unique_id_suffix="camera",
    )
    entity.async_on_remove = lambda fn: None  # type: ignore[method-assign]
    await entity.async_added_to_hass()
    device.register_entity.assert_not_called()
