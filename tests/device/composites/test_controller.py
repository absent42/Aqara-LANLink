from datetime import time

import pytest

from custom_components.aqara_lanlink.device.composites import CODECS
from custom_components.aqara_lanlink.device.composites.controller import (
    CompositeController,
)


class FakeDevice:
    def __init__(self):
        self.writes = []

    async def async_write(self, attrs):
        self.writes.append(attrs)


def make(rid="14.92.85", codec="packed_period"):
    return CompositeController(FakeDevice(), rid, CODECS[codec])


# --- 2.1: seed + get + defaults + listeners ---------------------------------


def test_get_defaults_before_seed():
    c = make()
    assert c.get("enabled") is True and c.get("start") == time(0, 0)


def test_seed_then_get():
    c = make()
    c.seed("5162040")
    assert c.get("start") == time(21, 0) and c.get("enabled") is True


def test_malformed_seed_keeps_defaults_and_does_not_raise():
    c = make()
    c.seed("not-an-int")  # must not raise
    assert c.get("start") == time(0, 0)
    assert c.get("end") == time(23, 59)
    assert c.get("enabled") is True


def test_listener_called_on_seed():
    c = make()
    calls = []
    c.add_listener(lambda: calls.append(1))
    c.seed("5162040")
    assert calls == [1]


def test_remove_listener():
    c = make()
    calls = []
    remove = c.add_listener(lambda: calls.append(1))
    remove()
    c.seed("5162040")
    assert calls == []


def test_rid_and_codec_properties():
    c = make()
    assert c.rid == "14.92.85"
    assert c.codec is CODECS["packed_period"]


# --- 2.2: async_set read-modify-write ---------------------------------------


@pytest.mark.asyncio
async def test_async_set_repacks_all_fields():
    c = make()
    c.seed("5162040")  # (21:00, 09:00, on)
    await c.async_set("end", time(6, 0))  # change only end
    assert len(c._device.writes[-1]) == 1  # one-key {AttrSpec: wire}
    spec = next(iter(c._device.writes[-1]))  # the single key
    # end 09:00->06:00, start 21:00 on: (1260<<12)|(360<<1)|0 = 5161680
    assert list(c._device.writes[-1].values())[0] == str(
        (1260 << 12) | (360 << 1) | 0
    )
    assert getattr(spec, "resource_id", None) == "14.92.85"  # AttrSpec key
    assert c.get("end") == time(6, 0)  # optimistic


@pytest.mark.asyncio
async def test_async_set_key_is_attrspec_not_string():
    from custom_components.aqara_lanlink.device.attrs import AttrSpec

    c = make()
    c.seed("5162040")
    await c.async_set("enabled", False)
    spec = next(iter(c._device.writes[-1]))
    assert isinstance(spec, AttrSpec)
    assert not isinstance(spec, str)


@pytest.mark.asyncio
async def test_async_set_notifies_listeners():
    c = make()
    c.seed("5162040")
    calls = []
    c.add_listener(lambda: calls.append(1))
    await c.async_set("enabled", False)
    assert calls == [1]


# --- review fixes: state-poisoning, seeded gating ---

def test_seeded_flag():
    c = make()
    assert c.seeded is False
    c.seed("5162040")
    assert c.seeded is True

def test_malformed_seed_keeps_unseeded():
    c = make()
    c.seed("not-an-int")
    assert c.seeded is False and c.get("start") == __import__("datetime").time(0, 0)

async def test_failed_encode_does_not_poison_state():
    from datetime import time
    c = make(rid="14.107.85", codec="schedule_json")
    c.seed('{"starttime":"01:00","endtime":"23:59","repeat":[1,1,1,1,1,1,1]}')
    # invalid repeat -> encode raises, state must be untouched
    import pytest
    with pytest.raises(ValueError):
        await c.async_set("repeat", "12")
    assert c.get("repeat") == "1111111"          # not poisoned
    # a valid sibling write still works
    await c.async_set("start", time(2, 0))
    assert c.get("start") == time(2, 0)

async def test_failed_write_does_not_commit():
    from datetime import time
    class BoomDevice:
        async def async_write(self, attrs): raise RuntimeError("boom")
    from custom_components.aqara_lanlink.device.composites import CODECS
    from custom_components.aqara_lanlink.device.composites.controller import CompositeController
    c = CompositeController(BoomDevice(), "14.92.85", CODECS["packed_period"])
    c.seed("5162040")
    import pytest
    with pytest.raises(RuntimeError):
        await c.async_set("end", time(6, 0))
    assert c.get("end") == time(9, 0)            # not committed
