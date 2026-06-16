"""Tests for the generic switch platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor
from custom_components.aqara_lanlink.switch import AqaraSwitch, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _power_descriptor(
    on_value: str = "1", off_value: str = "0",
    attr_write: AttrSpec | None = None,
    optimistic: bool = False,
) -> SwitchDescriptor:
    return SwitchDescriptor(
        key="test_power",
        attr=AttrSpec(name="test_power_attr"),
        attr_write=attr_write,
        on_value=on_value,
        off_value=off_value,
        optimistic=optimistic,
    )


def test_apply_value_on_off_transitions():
    desc = _power_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSwitch(hub, device, sub, desc)
    entity.apply_value("1")
    assert entity._attr_is_on is True
    entity.apply_value("0")
    assert entity._attr_is_on is False


def test_apply_value_with_custom_on_off_values():
    desc = _power_descriptor(on_value="ON", off_value="OFF")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraSwitch(hub, device, sub, desc)
    entity.apply_value("ON")
    assert entity._attr_is_on is True
    entity.apply_value("OFF")
    assert entity._attr_is_on is False


@pytest.mark.asyncio
async def test_async_turn_on_writes_attr_and_on_value():
    desc = _power_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    await entity.async_turn_on()
    device.async_write.assert_awaited_once_with({desc.attr: "1"})


@pytest.mark.asyncio
async def test_async_turn_off_writes_attr_and_off_value():
    desc = _power_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    await entity.async_turn_off()
    device.async_write.assert_awaited_once_with({desc.attr: "0"})


@pytest.mark.asyncio
async def test_attr_write_alias_used_when_set():
    write_attr = AttrSpec(name="test_power_write")
    desc = _power_descriptor(attr_write=write_attr)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    await entity.async_turn_on()
    device.async_write.assert_awaited_once_with({write_attr: "1"})


@pytest.mark.asyncio
async def test_async_write_failure_propagates_and_state_unchanged():
    desc = _power_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock(side_effect=RuntimeError("hub down"))  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    entity._attr_is_on = False
    with pytest.raises(RuntimeError):
        await entity.async_turn_on()
    # Entity state was not mutated by the failed write.
    assert entity._attr_is_on is False


@pytest.mark.asyncio
async def test_optimistic_turn_on_sets_state_without_report():
    desc = _power_descriptor(optimistic=True)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    entity._attr_is_on = False
    await entity.async_turn_on()
    device.async_write.assert_awaited_once_with({desc.attr: "1"})
    # No apply_value call -- optimistic state set directly after the write.
    assert entity._attr_is_on is True


@pytest.mark.asyncio
async def test_optimistic_turn_off_sets_state_without_report():
    desc = _power_descriptor(optimistic=True)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    entity._attr_is_on = True
    await entity.async_turn_off()
    device.async_write.assert_awaited_once_with({desc.attr: "0"})
    assert entity._attr_is_on is False


@pytest.mark.asyncio
async def test_non_optimistic_turn_on_leaves_state_for_report():
    # Control: report-driven switches (optimistic=False) must not self-set.
    desc = _power_descriptor(optimistic=False)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    entity._attr_is_on = False
    await entity.async_turn_on()
    device.async_write.assert_awaited_once_with({desc.attr: "1"})
    # State stays put until a report arrives via apply_value.
    assert entity._attr_is_on is False


@pytest.mark.asyncio
async def test_inverted_switch_turn_on_writes_off_wire_and_seeds_on():
    # Indicator-light style inversion: HA "on" == wire "0".
    desc = _power_descriptor(on_value="0", off_value="1", optimistic=True)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraSwitch(hub, device, sub, desc)
    entity._attr_is_on = False
    await entity.async_turn_on()
    device.async_write.assert_awaited_once_with({desc.attr: "0"})
    assert entity._attr_is_on is True
    # A report of the inverted on-wire value seeds is_on True.
    entity.apply_value("0")
    assert entity._attr_is_on is True
    entity.apply_value("1")
    assert entity._attr_is_on is False


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_descriptor():
    desc1 = _power_descriptor()
    desc2 = SwitchDescriptor(
        key="test_alarm",
        attr=AttrSpec(name="test_alarm_attr"),
    )
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc1, desc2], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(hass=MagicMock(), entry=entry, async_add_entities=lambda es, **_: added.extend(es))
    keys = sorted(e.descriptor.key for e in added)
    assert keys == ["test_alarm", "test_power"]
