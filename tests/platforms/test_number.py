"""Tests for the generic number platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import NumberDescriptor
from custom_components.aqara_lanlink.number import AqaraNumber, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _volume_descriptor(
    transform_in=None, transform_out=None,
    attr_write: AttrSpec | None = None,
) -> NumberDescriptor:
    return NumberDescriptor(
        key="test_volume",
        attr=AttrSpec(name="test_volume_attr"),
        attr_write=attr_write,
        min_value=0,
        max_value=100,
        step=5,
        transform_in=transform_in,
        transform_out=transform_out,
    )


def test_min_max_step_carry_through_to_attrs():
    desc = _volume_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraNumber(hub, device, sub, desc)
    assert entity._attr_native_min_value == 0
    assert entity._attr_native_max_value == 100
    assert entity._attr_native_step == 5


def test_apply_value_default_transform_int_when_possible():
    desc = _volume_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraNumber(hub, device, sub, desc)
    entity.apply_value("42")
    assert entity._attr_native_value == 42
    assert isinstance(entity._attr_native_value, int)


def test_apply_value_default_transform_falls_back_to_float():
    desc = _volume_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraNumber(hub, device, sub, desc)
    entity.apply_value("3.14")
    assert entity._attr_native_value == pytest.approx(3.14)
    assert isinstance(entity._attr_native_value, float)


def test_apply_value_custom_transform_in():
    desc = _volume_descriptor(transform_in=lambda raw: int(raw) / 10)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraNumber(hub, device, sub, desc)
    entity.apply_value("250")
    assert entity._attr_native_value == 25.0


def test_apply_value_empty_string_clears_to_none_without_crashing():
    """The hub occasionally pushes `""` for a number trait whose firmware
    hasn't produced a value yet (observed for `2.134.20107`
    StartUpColorTemperature on agl010, user log 2026-05-27 16:19:20).
    `_default_transform_in` would otherwise raise ValueError on int("")
    then again on float(""), surfacing as `entity.apply_value raised`
    in the log. Same shape as the sensor.py / light.py empty-string
    guards added earlier in this branch -- treat as None so the entity
    stays visible and transitions on the next real push.
    """
    desc = _volume_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraNumber(hub, device, sub, desc)
    # Seed with a real value so we can verify the empty-string update
    # explicitly transitions to None rather than silently leaving the
    # stale value in place.
    entity.apply_value("42")
    assert entity._attr_native_value == 42
    entity.apply_value("")  # must not raise
    assert entity._attr_native_value is None


@pytest.mark.asyncio
async def test_async_set_native_value_default_transform_str():
    desc = _volume_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, desc)
    await entity.async_set_native_value(75)
    device.async_write.assert_awaited_once_with({desc.attr: "75"})


@pytest.mark.asyncio
async def test_async_set_native_value_custom_transform_out():
    desc = _volume_descriptor(transform_out=lambda v: f"{int(v * 10)}")
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, desc)
    await entity.async_set_native_value(7.5)
    device.async_write.assert_awaited_once_with({desc.attr: "75"})


@pytest.mark.asyncio
async def test_attr_write_alias_used_when_set():
    write_attr = AttrSpec(name="test_volume_write")
    desc = _volume_descriptor(attr_write=write_attr)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, desc)
    await entity.async_set_native_value(40)
    device.async_write.assert_awaited_once_with({write_attr: "40"})


def _bare_number(descriptor: NumberDescriptor) -> AqaraNumber:
    """Construct an AqaraNumber without exercising AqaraEntity infrastructure."""
    entity = AqaraNumber.__new__(AqaraNumber)
    entity.entity_description = descriptor
    entity.descriptor = descriptor
    entity.hass = None
    entity._attr_native_value = None
    return entity


def test_apply_value_with_scale_read_direction():
    """apply_value applies descriptor.scale on top of transform_in."""
    descriptor = NumberDescriptor(
        key="x",
        name="x",
        attr=AttrSpec(name="x_attr"),
        min_value=0,
        max_value=10000,
        scale=0.001,
    )
    entity = _bare_number(descriptor)
    entity.apply_value("3200")
    assert entity._attr_native_value == pytest.approx(3.2)


def test_apply_value_no_scale_passes_through_default_transform():
    """No scale set -> apply_value preserves default int coercion."""
    descriptor = NumberDescriptor(
        key="x",
        name="x",
        attr=AttrSpec(name="x_attr"),
        min_value=0,
        max_value=10000,
    )
    entity = _bare_number(descriptor)
    entity.apply_value("3200")
    assert entity._attr_native_value == 3200  # default int coercion


@pytest.mark.asyncio
async def test_async_set_native_value_with_scale_write_direction():
    """HA hands `value` in user-facing units; the device must receive value/scale."""
    write_attr = AttrSpec(name="x_attr")
    descriptor = NumberDescriptor(
        key="x",
        name="x",
        attr=write_attr,
        min_value=0,
        max_value=10000,
        scale=0.001,
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([descriptor], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, descriptor)

    await entity.async_set_native_value(3.2)  # user enters 3.2 V

    # Wire value: 3.2 / 0.001 = 3200; default transform_out stringifies.
    device.async_write.assert_awaited_once()
    call_args = device.async_write.await_args
    payload = call_args.args[0]
    assert write_attr in payload
    assert payload[write_attr] == "3200" or payload[write_attr] == "3200.0"


@pytest.mark.asyncio
async def test_async_set_native_value_no_scale_passes_through():
    """No scale -> existing write path unchanged."""
    write_attr = AttrSpec(name="x_attr")
    descriptor = NumberDescriptor(
        key="x",
        name="x",
        attr=write_attr,
        min_value=0,
        max_value=10000,
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([descriptor], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, descriptor)

    await entity.async_set_native_value(42)
    device.async_write.assert_awaited_once_with({write_attr: "42"})


@pytest.mark.asyncio
async def test_async_set_native_value_zero_scale_falls_back_safely():
    """scale=0 must not raise ZeroDivisionError; fall back to identity."""
    write_attr = AttrSpec(name="x_attr")
    descriptor = NumberDescriptor(
        key="x",
        name="x",
        attr=write_attr,
        min_value=0,
        max_value=10000,
        scale=0,
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([descriptor], subentry=sub)
    device.async_write = AsyncMock()  # type: ignore[method-assign]
    entity = AqaraNumber(hub, device, sub, descriptor)

    await entity.async_set_native_value(42)
    device.async_write.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_descriptor():
    desc = _volume_descriptor()
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


@pytest.mark.asyncio
async def test_async_setup_entry_includes_extra_numbers():
    from custom_components.aqara_lanlink.number import async_setup_entry
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([], subentry=sub)
    sentinel = object()
    device.async_setup_extra_numbers = AsyncMock(return_value=[sentinel])  # type: ignore[method-assign]
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    assert sentinel in added
