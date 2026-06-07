"""Tests for the generic button platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import ButtonDescriptor
from custom_components.aqara_lanlink.button import AqaraButton, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _identify_descriptor(press_value: str = "1") -> ButtonDescriptor:
    return ButtonDescriptor(
        key="test_identify",
        attr=AttrSpec(name="1.131.32917"),
        press_value=press_value,
    )


def test_construction_carries_descriptor_and_unique_id():
    desc = _identify_descriptor()
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc], subentry=sub)
    entity = AqaraButton(hub, device, sub, desc)
    assert entity.descriptor is desc
    assert entity.entity_description is desc
    assert entity._attr_unique_id == "sub-1_test_identify"


@pytest.mark.asyncio
async def test_async_press_writes_press_value_to_attr():
    """Pressing the button writes press_value to the trait. This is the
    one-shot device-action contract -- the device blinks/beeps in response."""
    desc = _identify_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()
    entity = AqaraButton(hub, device, sub, desc)
    await entity.async_press()
    device.async_write.assert_awaited_once_with({desc.attr: "1"})


@pytest.mark.asyncio
async def test_async_press_honours_custom_press_value():
    desc = _identify_descriptor(press_value="42")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()
    entity = AqaraButton(hub, device, sub, desc)
    await entity.async_press()
    device.async_write.assert_awaited_once_with({desc.attr: "42"})


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_descriptor():
    desc1 = _identify_descriptor()
    desc2 = ButtonDescriptor(
        key="other_action", attr=AttrSpec(name="2.131.32917"),
    )
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc1, desc2], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    keys = sorted(e.descriptor.key for e in added)
    assert keys == ["other_action", "test_identify"]


@pytest.mark.asyncio
async def test_async_setup_entry_skips_non_button_descriptors():
    from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor
    desc_button = _identify_descriptor()
    desc_switch = SwitchDescriptor(
        key="not_a_button", attr=AttrSpec(name="2.132.32920"),
    )
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc_button, desc_switch], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    keys = [e.descriptor.key for e in added]
    assert keys == ["test_identify"]
