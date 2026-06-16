"""Tests for the generic select platform."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import SelectDescriptor
from custom_components.aqara_lanlink.select import AqaraSelect, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _sensitivity_descriptor(
    attr_write: AttrSpec | None = None,
    optimistic: bool = False,
) -> SelectDescriptor:
    return SelectDescriptor(
        key="test_sensitivity",
        attr=AttrSpec(name="test_sensitivity_attr"),
        attr_write=attr_write,
        options_map=(("low", "1"), ("medium", "2"), ("high", "3")),
        optimistic=optimistic,
    )


def test_options_match_options_map_keys_in_order():
    desc = _sensitivity_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSelect(hub, device, sub, desc)
    assert entity._attr_options == ["low", "medium", "high"]


def test_apply_value_reverse_lookup_sets_current_option():
    desc = _sensitivity_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSelect(hub, device, sub, desc)
    entity.apply_value("2")
    assert entity._attr_current_option == "medium"
    entity.apply_value("3")
    assert entity._attr_current_option == "high"


def test_apply_value_unknown_wire_value_logs_and_keeps_state(caplog):
    desc = _sensitivity_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSelect(hub, device, sub, desc)
    entity._attr_current_option = "low"
    with caplog.at_level(logging.WARNING):
        entity.apply_value("99")
    assert entity._attr_current_option == "low"
    assert any("unknown wire value" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_async_select_option_writes_mapped_wire_value():
    desc = _sensitivity_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSelect(hub, device, sub, desc)
    await entity.async_select_option("medium")
    device.async_write.assert_awaited_once_with({desc.attr: "2"})


@pytest.mark.asyncio
async def test_attr_write_alias_used_when_set():
    write_attr = AttrSpec(name="test_sensitivity_write")
    desc = _sensitivity_descriptor(attr_write=write_attr)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSelect(hub, device, sub, desc)
    await entity.async_select_option("high")
    device.async_write.assert_awaited_once_with({write_attr: "3"})


@pytest.mark.asyncio
async def test_optimistic_select_sets_current_option_without_report():
    desc = _sensitivity_descriptor(optimistic=True)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSelect(hub, device, sub, desc)
    await entity.async_select_option("medium")
    device.async_write.assert_awaited_once_with({desc.attr: "2"})
    # No apply_value call -- optimistic state set directly after the write.
    assert entity._attr_current_option == "medium"


@pytest.mark.asyncio
async def test_non_optimistic_select_leaves_state_for_report():
    desc = _sensitivity_descriptor(optimistic=False)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSelect(hub, device, sub, desc)
    await entity.async_select_option("medium")
    device.async_write.assert_awaited_once_with({desc.attr: "2"})
    # State stays None until a report arrives via apply_value.
    assert entity._attr_current_option is None


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_descriptor():
    desc = _sensitivity_descriptor()
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(hass=MagicMock(), entry=entry, async_add_entities=lambda es, **_: added.extend(es))
    assert len(added) == 1
    assert added[0].descriptor is desc
