"""Tests for the descriptor-less composite sub-entities (Chunk 3).

Each composite sub-entity binds a (CompositeController, CompositeField) pair,
reads through ``controller.get`` and writes through ``controller.async_set``.
These are constructed directly with the lightweight platform fixtures (no HA
harness), mirroring tests/platforms/test_ptz_entities.py.
"""

from __future__ import annotations

from datetime import time

import pytest

from custom_components.aqara_lanlink.const import PLATFORMS
from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.composites import CODECS
from custom_components.aqara_lanlink.device.composites.controller import (
    CompositeController,
)
from custom_components.aqara_lanlink.device.composites.entities import (
    CompositeNumber,
    CompositeSwitch,
    CompositeText,
    CompositeTime,
)
from homeassistant.const import Platform

from tests.platforms.conftest import make_device, make_hub, make_subentry


class FakeDevice:
    """Records writes, matching test_controller.py's FakeDevice."""

    def __init__(self):
        self.writes = []

    async def async_write(self, attrs):
        self.writes.append(attrs)


def _field(codec_name: str, name: str):
    codec = CODECS[codec_name]
    return next(f for f in codec.fields if f.name == name)


def _controller(codec_name: str = "packed_period", rid: str = "14.92.85"):
    return CompositeController(FakeDevice(), rid, CODECS[codec_name])


# --- 3.1: shared descriptor-less base ---------------------------------------


def test_unique_id_includes_rid_and_field():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-abc")
    device = make_device([], subentry=sub)
    ctrl = _controller()
    field = CODECS["packed_period"].fields[0]  # "start"
    ent = CompositeTime(hub, device, sub, ctrl, field)
    assert ent.unique_id == "sub-abc_14.92.85_start"


def test_should_not_poll():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ent = CompositeTime(hub, device, sub, ctrl, CODECS["packed_period"].fields[0])
    assert ent.should_poll is False


# --- 3.2: CompositeSwitch ---------------------------------------------------


def test_switch_reflects_controller():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162040")  # enabled -> True
    ent = CompositeSwitch(hub, device, sub, ctrl, _field("packed_period", "enabled"))
    assert ent.is_on is True


async def test_switch_turn_off_reencodes_wire():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162040")  # (21:00, 09:00, enabled)
    ent = CompositeSwitch(hub, device, sub, ctrl, _field("packed_period", "enabled"))
    await ent.async_turn_off()
    wire = list(ctrl._device.writes[-1].values())[0]
    # disabled bit set: start 21:00, end 09:00, off
    assert wire == str((1260 << 12) | (540 << 1) | 1)
    assert ctrl.get("enabled") is False


async def test_switch_turn_on_reencodes_wire():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162041")  # disabled bit set
    ent = CompositeSwitch(hub, device, sub, ctrl, _field("packed_period", "enabled"))
    await ent.async_turn_on()
    wire = list(ctrl._device.writes[-1].values())[0]
    assert wire == str((1260 << 12) | (540 << 1) | 0)
    assert ctrl.get("enabled") is True


# --- 3.2: CompositeNumber ---------------------------------------------------


def test_number_applies_params():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("brightness", rid="4.1.85")
    ent = CompositeNumber(hub, device, sub, ctrl, _field("brightness", "colour"))
    assert ent.native_min_value == 0
    assert ent.native_max_value == 100
    assert ent.native_unit_of_measurement == "%"


def test_number_reflects_controller():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("brightness", rid="4.1.85")
    ctrl.seed(str((80 << 16) | 50))  # colour=50, bw=80
    ent = CompositeNumber(hub, device, sub, ctrl, _field("brightness", "colour"))
    assert ent.native_value == 50


async def test_number_set_reencodes_wire():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("brightness", rid="4.1.85")
    ctrl.seed(str((80 << 16) | 50))  # colour=50, bw=80
    ent = CompositeNumber(hub, device, sub, ctrl, _field("brightness", "colour"))
    await ent.async_set_native_value(60.0)
    wire = list(ctrl._device.writes[-1].values())[0]
    assert wire == str((80 << 16) | 60)  # colour repacked to 60, bw kept
    assert ctrl.get("colour") == 60


# --- 3.2: CompositeText -----------------------------------------------------


def test_text_reflects_controller():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("schedule_json", rid="14.55.85")
    ent = CompositeText(hub, device, sub, ctrl, _field("schedule_json", "repeat"))
    assert ent.native_value == "1111111"


async def test_text_set_valid_reencodes_wire():
    import json

    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("schedule_json", rid="14.55.85")
    ent = CompositeText(hub, device, sub, ctrl, _field("schedule_json", "repeat"))
    await ent.async_set_value("1010101")
    wire = list(ctrl._device.writes[-1].values())[0]
    assert json.loads(wire)["repeat"] == [1, 0, 1, 0, 1, 0, 1]
    assert ctrl.get("repeat") == "1010101"


async def test_text_set_invalid_propagates_codec_error():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller("schedule_json", rid="14.55.85")
    ent = CompositeText(hub, device, sub, ctrl, _field("schedule_json", "repeat"))
    with pytest.raises(ValueError):
        await ent.async_set_value("12")


# --- 3.3: CompositeTime -----------------------------------------------------


def test_time_reflects_controller():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162040")  # start 21:00
    ent = CompositeTime(hub, device, sub, ctrl, _field("packed_period", "start"))
    assert ent.native_value == time(21, 0)


async def test_time_set_roundtrips():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162040")  # (21:00, 09:00, on)
    ent = CompositeTime(hub, device, sub, ctrl, _field("packed_period", "end"))
    await ent.async_set_value(time(6, 0))
    wire = list(ctrl._device.writes[-1].values())[0]
    assert wire == str((1260 << 12) | (360 << 1) | 0)
    assert ctrl.get("end") == time(6, 0)


# --- 3.3: const ------------------------------------------------------------


def test_time_platform_registered():
    assert Platform.TIME in PLATFORMS


# --- AttrSpec key sanity (shared write path) --------------------------------


async def test_write_key_is_attrspec():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    ctrl = _controller()
    ctrl.seed("5162040")
    ent = CompositeSwitch(hub, device, sub, ctrl, _field("packed_period", "enabled"))
    await ent.async_turn_off()
    spec = next(iter(ctrl._device.writes[-1]))
    assert isinstance(spec, AttrSpec)
    assert spec.resource_id == "14.92.85"
