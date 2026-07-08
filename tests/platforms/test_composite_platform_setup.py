"""Tests for composite sub-entities wired into the writable platforms (Task 5.2).

Each writable platform's `async_setup_entry` enumerates its device's
(did, rid)-keyed CompositeControllers and yields one entity per matching codec
field, named from the model's composites catalog block, alongside the existing
descriptor-driven entities. These drive the real `async_setup_entry` with a
SimpleNamespace runtime_data (the no-HA-harness style of test_ptz_entities.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.device import catalog
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
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor,
    SwitchDescriptor,
    TextDescriptor,
)
from custom_components.aqara_lanlink.number import (
    AqaraNumber,
    async_setup_entry as number_setup,
)
from custom_components.aqara_lanlink.switch import (
    AqaraSwitch,
    async_setup_entry as switch_setup,
)
from custom_components.aqara_lanlink.text import (
    AqaraText,
    async_setup_entry as text_setup,
)
from custom_components.aqara_lanlink.time import async_setup_entry as time_setup

from .conftest import make_device, make_hub, make_subentry


class _FakeDevice:
    def __init__(self):
        self.writes = []

    async def async_write(self, attrs):
        self.writes.append(attrs)


# rid -> (codec_name, display name) fixtures
_PERIOD_RID = "14.92.85"
_BRIGHT_RID = "4.1.85"
_SCHED_RID = "14.55.85"

_DECLS = {
    _PERIOD_RID: {"codec": "packed_period", "name": "Do Not Disturb"},
    _BRIGHT_RID: {"codec": "brightness", "name": "Brightness"},
    _SCHED_RID: {"codec": "schedule_json", "name": "Schedule"},
}


@pytest.fixture(autouse=True)
def _patch_catalog(monkeypatch):
    """Every platform reads decls via catalog.composites_for_model(MODEL)."""
    monkeypatch.setattr(
        catalog, "composites_for_model", lambda model: dict(_DECLS)
    )


def _make_entry(*, rids, descriptors=None, did="dev-comp"):
    """Build an entry wiring one device with the given rid->codec controllers.

    `rids` maps rid -> codec_name; each becomes a (did, rid)-keyed controller.
    """
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1", did=did)
    device = make_device(descriptors or [], subentry=sub)

    controllers = {
        (did, rid): CompositeController(_FakeDevice(), rid, CODECS[codec])
        for rid, codec in rids.items()
    }
    runtime = SimpleNamespace(
        hub=hub,
        devices={"sub-1": device},
        self_device=None,
        composite_controllers=controllers,
    )
    entry = SimpleNamespace(runtime_data=runtime, subentries={"sub-1": sub})
    return entry


async def _collect(setup, entry):
    added: list = []
    await setup(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    return added


# --- time -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_time_yields_two_for_packed_period():
    entry = _make_entry(rids={_PERIOD_RID: "packed_period"})
    added = await _collect(time_setup, entry)

    assert all(isinstance(e, CompositeTime) for e in added)
    assert [e._attr_name for e in added] == [
        "Do Not Disturb Start", "Do Not Disturb End",
    ]
    assert {e.unique_id for e in added} == {
        "sub-1_14.92.85_start", "sub-1_14.92.85_end",
    }


# --- switch -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_yields_one_for_packed_period_plus_descriptor():
    desc = SwitchDescriptor(key="power", attr=AttrSpec(name="power_attr"))
    entry = _make_entry(rids={_PERIOD_RID: "packed_period"}, descriptors=[desc])
    added = await _collect(switch_setup, entry)

    composites = [e for e in added if isinstance(e, CompositeSwitch)]
    descriptors = [e for e in added if isinstance(e, AqaraSwitch)]
    assert len(descriptors) == 1  # the descriptor switch still present
    assert len(composites) == 1
    assert composites[0]._attr_name == "Do Not Disturb Enabled"
    assert composites[0].unique_id == "sub-1_14.92.85_enabled"


# --- number + switch for brightness ----------------------------------------


@pytest.mark.asyncio
async def test_number_yields_two_for_brightness():
    entry = _make_entry(rids={_BRIGHT_RID: "brightness"})
    added = await _collect(number_setup, entry)

    composites = [e for e in added if isinstance(e, CompositeNumber)]
    assert len(composites) == 2
    assert {e._attr_name for e in composites} == {
        "Brightness Colour brightness", "Brightness B&W brightness",
    }
    assert {e.unique_id for e in composites} == {
        "sub-1_4.1.85_colour", "sub-1_4.1.85_bw",
    }


@pytest.mark.asyncio
async def test_switch_yields_one_for_brightness_auto():
    entry = _make_entry(rids={_BRIGHT_RID: "brightness"})
    added = await _collect(switch_setup, entry)

    composites = [e for e in added if isinstance(e, CompositeSwitch)]
    assert len(composites) == 1
    assert composites[0]._attr_name == "Brightness Auto"
    assert composites[0].unique_id == "sub-1_4.1.85_auto"


@pytest.mark.asyncio
async def test_number_descriptor_and_composite_coexist():
    desc = NumberDescriptor(key="lvl", attr=AttrSpec(name="lvl_attr"))
    entry = _make_entry(rids={_BRIGHT_RID: "brightness"}, descriptors=[desc])
    added = await _collect(number_setup, entry)

    assert any(isinstance(e, AqaraNumber) for e in added)
    assert sum(isinstance(e, CompositeNumber) for e in added) == 2


# --- text -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_yields_one_for_schedule_repeat():
    desc = TextDescriptor(key="note", attr=AttrSpec(name="note_attr"))
    entry = _make_entry(rids={_SCHED_RID: "schedule_json"}, descriptors=[desc])
    added = await _collect(text_setup, entry)

    composites = [e for e in added if isinstance(e, CompositeText)]
    descriptors = [e for e in added if isinstance(e, AqaraText)]
    assert len(descriptors) == 1
    assert len(composites) == 1
    assert composites[0]._attr_name == "Schedule Repeat days"
    assert composites[0].unique_id == "sub-1_14.55.85_repeat"


# --- isolation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_device_controllers_ignored():
    """A controller keyed to a different did contributes no entities."""
    entry = _make_entry(rids={_PERIOD_RID: "packed_period"}, did="other-did")
    # The device wired into the entry has did "other-did"; but point its
    # controllers at a foreign did to prove the filter.
    foreign = {
        ("stranger", _PERIOD_RID): CompositeController(
            _FakeDevice(), _PERIOD_RID, CODECS["packed_period"]
        )
    }
    entry.runtime_data.composite_controllers = foreign
    added = await _collect(time_setup, entry)
    assert added == []
