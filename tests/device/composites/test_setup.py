"""Tests for the shared composite per-platform enumerator (Task 5.1).

`composite_entities_for_platform` produces one entity per codec field whose
`field.platform` matches, across a single device's (did, rid)-keyed
controllers, applying display naming from the model's composites catalog block.
"""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device.composites import CODECS
from custom_components.aqara_lanlink.device.composites.controller import (
    CompositeController,
)
from custom_components.aqara_lanlink.device.composites.entities import (
    CompositeSwitch,
    CompositeTime,
)
from custom_components.aqara_lanlink.device.composites.setup import (
    composite_entities_for_platform,
)

from tests.platforms.conftest import make_device, make_hub, make_subentry


class FakeDevice:
    """Records writes; the controller's device is unused by the enumerator."""

    def __init__(self):
        self.writes = []

    async def async_write(self, attrs):
        self.writes.append(attrs)


_RID = "14.92.85"
_DECLS = {_RID: {"codec": "packed_period", "name": "Do Not Disturb"}}


def _wire(did: str):
    """Build a (did, rid)-keyed controller store for a packed_period rid."""
    return {(did, _RID): CompositeController(FakeDevice(), _RID, CODECS["packed_period"])}


def test_time_yields_two_named_entities():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-dnd", did="dev-1")
    device = make_device([], subentry=sub)
    controllers = _wire(device.did)

    ents = composite_entities_for_platform(
        hub, device, sub, controllers, "time", _DECLS
    )

    assert all(isinstance(e, CompositeTime) for e in ents)
    names = [e._attr_name for e in ents]
    assert names == ["Do Not Disturb Start", "Do Not Disturb End"]
    uids = {e.unique_id for e in ents}
    assert uids == {"sub-dnd_14.92.85_start", "sub-dnd_14.92.85_end"}


def test_switch_yields_one_named_entity():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-dnd", did="dev-1")
    device = make_device([], subentry=sub)
    controllers = _wire(device.did)

    ents = composite_entities_for_platform(
        hub, device, sub, controllers, "switch", _DECLS
    )

    assert len(ents) == 1
    assert isinstance(ents[0], CompositeSwitch)
    assert ents[0]._attr_name == "Do Not Disturb Enabled"
    assert ents[0].unique_id == "sub-dnd_14.92.85_enabled"


def test_non_matching_did_returns_empty():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-dnd", did="dev-1")
    device = make_device([], subentry=sub)
    # Controllers belong to a different device.
    controllers = _wire("some-other-did")

    ents = composite_entities_for_platform(
        hub, device, sub, controllers, "time", _DECLS
    )
    assert ents == []


def test_missing_decl_falls_back_to_rid_name():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-dnd", did="dev-1")
    device = make_device([], subentry=sub)
    controllers = _wire(device.did)

    ents = composite_entities_for_platform(
        hub, device, sub, controllers, "switch", {}
    )
    assert ents[0]._attr_name == "14.92.85 Enabled"
