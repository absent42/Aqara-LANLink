"""Tests for the generic binary-sensor platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from homeassistant.const import STATE_OFF, STATE_ON

from custom_components.aqara_lanlink.binary_sensor import (
    AqaraBinarySensor,
    async_setup_entry,
)
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

from .conftest import make_device, make_hub, make_subentry


def test_forwarding_health_is_on_reflects_hub_liveness():
    from custom_components.aqara_lanlink import PUSH_STALL_TTL_SECONDS
    from custom_components.aqara_lanlink.binary_sensor import (
        AqaraHubForwardingHealth,
    )

    hub = MagicMock()
    hub.did = "lumi1.HUB"
    hub.connected = True
    hub.lanlink_topology_dids = frozenset({"lumi3.cam"})
    hub.seconds_since_last_report = lambda: 5.0
    ent = AqaraHubForwardingHealth(hub)
    assert ent.is_on is True  # connected, topology ready, recent report

    hub.seconds_since_last_report = lambda: PUSH_STALL_TTL_SECONDS + 1
    assert ent.is_on is False  # silent past TTL

    hub.seconds_since_last_report = lambda: 5.0
    hub.lanlink_topology_dids = frozenset()
    assert ent.is_on is False  # topology not ready

    hub.lanlink_topology_dids = frozenset({"lumi3.cam"})
    hub.connected = False
    assert ent.is_on is False  # tunnel down


async def test_async_setup_entry_adds_forwarding_health():
    from custom_components.aqara_lanlink.binary_sensor import (
        AqaraHubForwardingHealth,
    )

    hub = make_hub()
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={}, self_device=None),
        subentries={},
    )
    added: list = []
    await async_setup_entry(None, entry, lambda ents, **kw: added.extend(ents))
    assert any(isinstance(e, AqaraHubForwardingHealth) for e in added)


def _motion_descriptor(auto_clear: float | None = 30.0) -> BinarySensorDescriptor:
    return BinarySensorDescriptor(
        key="test_motion",
        trait=TraitSpec(id="3.1.85", name="test_motion"),
        on_values=frozenset({"1"}),
        auto_clear_seconds=auto_clear,
    )


# ---------------------------------------------------------------------------
# Construction + descriptor wiring.
# ---------------------------------------------------------------------------


def test_construction_carries_descriptor_and_unique_id():
    desc = _motion_descriptor()
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    assert entity.descriptor is desc
    assert entity.entity_description is desc
    assert entity._attr_unique_id == "sub-1_test_motion"


def test_apply_value_sets_on_for_value_in_on_values():
    desc = _motion_descriptor(auto_clear=None)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    entity.apply_value("1")
    assert entity._attr_is_on is True


def test_apply_value_clears_on_value_outside_on_values():
    desc = _motion_descriptor(auto_clear=None)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    entity.apply_value("1")
    entity.apply_value("0")
    assert entity._attr_is_on is False


def test_package_detector_appeared_turns_on_disappeared_turns_off():
    """PackageRecognitionReport encodes its own clear: 1=PackageAppeared,
    2=PackageDisappeared. As a stateful binary sensor the explicit
    'disappeared' signal must turn the sensor OFF -- not register as 'on'
    (the bug when it used the momentary on_any_value archetype)."""
    from custom_components.aqara_lanlink.device.device_types import _detectors
    from custom_components.aqara_lanlink.device.device_types._base import ComposeContext

    spec = TraitSpec(
        id="8.223.20221", wire_path="8.223.20221", name="Package recognition report",
        data_type="enum", function_code="PackageRecognition",
        trait_code="PackageRecognitionReport",
        enum_values={"1": "PackageAppeared", "2": "PackageDisappeared"},
        readable=False, subscribable=True, endpoint_id=8,
    )
    descs = _detectors.package_detector_compose(
        endpoint_id=8, traits={spec.id: spec}, context=ComposeContext(model="lumi.camera.agl005"),
    )
    desc = descs[0]
    assert desc.auto_clear_seconds is None, "firmware drives the clear; no timer"
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    entity.apply_value("1")  # PackageAppeared
    assert entity._attr_is_on is True
    entity.apply_value("2")  # PackageDisappeared
    assert entity._attr_is_on is False


def test_apply_value_with_on_any_value_treats_arbitrary_payload_as_on():
    """on_any_value=True (set on detector recognition-report descriptors)
    must flip the sensor 'on' for any non-empty, non-'0' wire payload.
    The recognition reports carry varying payload codes per detection,
    so enumerating them in on_values is unworkable.
    """
    desc = BinarySensorDescriptor(
        key="test_human_recognition",
        trait=TraitSpec(id="3.216.20215", name="HumanRecognitionReport"),
        on_any_value=True,
        auto_clear_seconds=None,
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    # Various recognition-report payload shapes -- all mean "detected".
    for payload in ("1", "42", '{"person_id":7}', "anything"):
        entity._attr_is_on = False  # reset
        entity.apply_value(payload)
        assert entity._attr_is_on is True, f"payload {payload!r} should toggle on"
    # Empty + "0" stay off.
    entity._attr_is_on = True
    entity.apply_value("")
    assert entity._attr_is_on is False
    entity._attr_is_on = True
    entity.apply_value("0")
    assert entity._attr_is_on is False


# ---------------------------------------------------------------------------
# Auto-clear behaviour.
# ---------------------------------------------------------------------------


class _FakeHass:
    """Just enough of HA's hass surface to drive call_later."""

    def __init__(self) -> None:
        self.loop = MagicMock()
        self.loop.call_later = MagicMock()


def test_apply_value_schedules_auto_clear_when_descriptor_has_seconds():
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    fake_hass = _FakeHass()
    entity.hass = fake_hass  # type: ignore[assignment]
    # Stub async_write_ha_state so we don't need a full HA harness.
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    entity.apply_value("1")

    fake_hass.loop.call_later.assert_called_once()
    args, _ = fake_hass.loop.call_later.call_args
    assert args[0] == 30.0
    assert args[1] == entity._auto_clear


def test_re_triggering_cancels_previous_handle_and_schedules_new_one():
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    fake_hass = _FakeHass()
    entity.hass = fake_hass  # type: ignore[assignment]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    handle1 = MagicMock()
    handle2 = MagicMock()
    fake_hass.loop.call_later.side_effect = [handle1, handle2]

    entity.apply_value("1")  # schedules handle1
    entity.apply_value("1")  # cancels handle1, schedules handle2

    handle1.cancel.assert_called_once()
    assert entity._auto_clear_handle is handle2


def test_apply_zero_cancels_pending_auto_clear():
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    fake_hass = _FakeHass()
    entity.hass = fake_hass  # type: ignore[assignment]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    handle = MagicMock()
    fake_hass.loop.call_later.return_value = handle
    entity.apply_value("1")
    entity.apply_value("0")
    handle.cancel.assert_called_once()
    assert entity._auto_clear_handle is None


def test_auto_clear_callback_clears_state():
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    fake_hass = _FakeHass()
    entity.hass = fake_hass  # type: ignore[assignment]
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]

    entity._attr_is_on = True
    entity._auto_clear()
    assert entity._attr_is_on is False
    assert entity._auto_clear_handle is None


@pytest.mark.asyncio
async def test_async_will_remove_from_hass_cancels_pending_handle():
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    handle = MagicMock()
    entity._auto_clear_handle = handle
    await entity.async_will_remove_from_hass()
    handle.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# Platform setup hook.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_descriptor():
    desc1 = _motion_descriptor(auto_clear=None)
    desc2 = BinarySensorDescriptor(
        key="test_person",
        trait=TraitSpec(id="3.1.86", name="test_person"),
    )
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([desc1, desc2], subentry=sub)

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            hub=hub, devices={"sub-1": device}, self_device=None,
        ),
        subentries={"sub-1": sub},
    )
    added: list = []

    def add_entities(entities, _update_before_add=False, config_subentry_id=None):
        added.extend(entities)

    await async_setup_entry(hass=MagicMock(), entry=entry, async_add_entities=add_entities)
    keys = sorted(e.descriptor.key for e in added if hasattr(e, "descriptor"))
    assert keys == ["test_motion", "test_person"]


@pytest.mark.asyncio
async def test_async_setup_entry_skips_non_binary_sensor_descriptors():
    from custom_components.aqara_lanlink.device.attrs import AttrSpec
    from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor

    bs = _motion_descriptor(auto_clear=None)
    sw = SwitchDescriptor(key="other", attr=AttrSpec(name="other_attr"))
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([bs, sw], subentry=sub)

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(hass=MagicMock(), entry=entry, async_add_entities=lambda es, **_: added.extend(es))
    descriptor_entities = [e for e in added if hasattr(e, "descriptor")]
    assert len(descriptor_entities) == 1
    assert descriptor_entities[0].descriptor is bs


# ---------------------------------------------------------------------------
# Task 6: auto-clear delay comes from device hook.
# ---------------------------------------------------------------------------


def test_apply_value_arms_clear_when_hook_truthy_and_descriptor_none(monkeypatch):
    desc = _motion_descriptor(auto_clear=None)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    monkeypatch.setattr(device, "resolve_auto_clear_seconds", lambda d: 75.0)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    captured = {}
    entity.hass.loop.call_later = lambda delay, cb: captured.setdefault("delay", delay)
    entity.apply_value("1")
    assert captured["delay"] == 75.0


def test_auto_clear_delay_comes_from_device_hook(monkeypatch):
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    # Device override returns a different live value.
    monkeypatch.setattr(
        device, "resolve_auto_clear_seconds", lambda d: 90.0,
    )
    entity = AqaraBinarySensor(hub, device, sub, desc)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    captured = {}
    entity.hass.loop.call_later = lambda delay, cb: captured.setdefault("delay", delay)
    entity.apply_value("1")
    assert captured["delay"] == 90.0


# ---------------------------------------------------------------------------
# RestoreEntity: stateful binary sensors restore their last is_on from the
# recorder on HA restart / integration reload, so the entity doesn't boot as
# off until the next push report arrives. Momentary descriptors deliberately
# skip the restore (their last persisted state could be a stale "on" from a
# trigger that never got cleared).
# ---------------------------------------------------------------------------


def _contact_descriptor() -> BinarySensorDescriptor:
    """Stateful: no auto_clear, no on_any_value. Closest analogue is a
    door/window contact sensor or leak presence sensor."""
    return BinarySensorDescriptor(
        key="contact", trait=TraitSpec(id="2.155.32990", name="Contact"),
    )


async def _drive_added_to_hass(entity, last_state):
    """Invoke async_added_to_hass with the recorder mock returning
    last_state (or None). Short-circuits super().async_added_to_hass --
    its only real side effect is register_entity, exercised in
    test_base.py -- so we test just the restore step here."""
    async def fake_get_last_state():
        return last_state

    async def fake_super_added():
        return None

    entity.async_get_last_state = fake_get_last_state
    # Stub the parent chain (AqaraEntity -> RestoreEntity -> Entity)
    # since their behaviour is exercised in their own test suites.
    from custom_components.aqara_lanlink import binary_sensor as bs_mod
    original_super = bs_mod.AqaraEntity.async_added_to_hass

    async def stubbed_super(self):
        return await fake_super_added()

    try:
        bs_mod.AqaraEntity.async_added_to_hass = stubbed_super  # type: ignore[assignment]
        await entity.async_added_to_hass()
    finally:
        bs_mod.AqaraEntity.async_added_to_hass = original_super  # type: ignore[assignment]


async def test_restore_sets_is_on_when_last_state_was_on_for_stateful_sensor():
    """A stateful binary sensor (door contact / leak / presence) restores
    is_on=True from the HA recorder when its last persisted state was
    'on'. Without this the entity would boot as off until the next push
    report arrives -- bad UX when the cloud is unreachable on startup."""
    desc = _contact_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    assert entity._attr_is_on is False  # class default
    await _drive_added_to_hass(entity, SimpleNamespace(state=STATE_ON))
    assert entity._attr_is_on is True


async def test_restore_leaves_is_on_false_when_last_state_was_off():
    desc = _contact_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    await _drive_added_to_hass(entity, SimpleNamespace(state=STATE_OFF))
    assert entity._attr_is_on is False


async def test_restore_no_op_when_no_last_state():
    """First boot or recorder unavailable -- no restored state. Entity
    must stay at its class default (False), not raise."""
    desc = _contact_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    await _drive_added_to_hass(entity, None)
    assert entity._attr_is_on is False


async def test_restore_skipped_for_momentary_sensor_with_auto_clear():
    """auto_clear_seconds marks the descriptor as momentary. The last
    persisted state could be a stale 'on' from a trigger that never
    got cleared (auto-clear timer doesn't survive HA restart). Don't
    restore -- start the entity off so a fresh push report is the only
    way it goes on."""
    desc = _motion_descriptor(auto_clear=30.0)
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    await _drive_added_to_hass(entity, SimpleNamespace(state=STATE_ON))
    assert entity._attr_is_on is False, (
        "momentary sensor must not restore last persisted ON"
    )


async def test_restore_skipped_for_momentary_sensor_with_on_any_value():
    """on_any_value descriptors (recognition reports) treat any payload
    as 'detected just now'. Restoring 'on' from the recorder would
    refire the detection on every reload, same hazard as auto-clear."""
    desc = BinarySensorDescriptor(
        key="recognition",
        trait=TraitSpec(id="3.216.20215", name="HumanRecognitionReport"),
        on_any_value=True,
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraBinarySensor(hub, device, sub, desc)
    await _drive_added_to_hass(entity, SimpleNamespace(state=STATE_ON))
    assert entity._attr_is_on is False
