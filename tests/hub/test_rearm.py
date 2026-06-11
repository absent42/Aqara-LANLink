"""Tests for RearmManager -- re-activates standalone Wi-Fi devices.

A standalone device (the FP2) is relayed by the hub only after we "activate"
it (TLS handshake to its :443). A hub reboot or device power-cycle drops it from
the hub's LANLink topology; the device may be OFFLINE while booting, so
re-activation must RETRY until reachable, and a periodic sweep covers the case
where no topology push arrives when it returns.

The network is never touched: activate_relay / validate_aqara_endpoint /
discover_hub_by_did are patched in the rearm module namespace.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.aqara_lanlink.const import (
    CONF_ACTIVATION_HOST,
    CONF_ACTIVATION_PORT,
)
from custom_components.aqara_lanlink.hub import rearm
from custom_components.aqara_lanlink.hub.rearm import RearmManager

FP2_DID = "lumi1.fp2"
FP2_HOST = "10.1.20.160"
FP2_PORT = 443


class _FakeHub:
    """Stand-in for HubCoordinator: only lanlink_topology_dids is read."""

    def __init__(self, dids: frozenset[str] = frozenset()) -> None:
        self.lanlink_topology_dids = dids


def _make_entry(hub: _FakeHub, *, activated: bool = True) -> SimpleNamespace:
    """A config entry with one (optionally activated) standalone subentry."""
    data: dict = {"did": FP2_DID, "model": "lumi.motion.agl001"}
    if activated:
        data[CONF_ACTIVATION_HOST] = FP2_HOST
        data[CONF_ACTIVATION_PORT] = FP2_PORT
    subentry = SimpleNamespace(subentry_id="sub-fp2", data=data)
    return SimpleNamespace(
        subentries={"sub-fp2": subentry},
        runtime_data=SimpleNamespace(hub=hub),
    )


def _patch_relay(monkeypatch, *, validate, topology_after=None, hub=None):
    """Patch the three network entry points in the rearm namespace.

    ``validate`` is the return value (or side_effect list) for
    validate_aqara_endpoint. When ``topology_after`` and ``hub`` are given,
    a successful activate_relay flips the hub topology to ``topology_after``.
    """
    activate = AsyncMock()
    if topology_after is not None and hub is not None:
        async def _activate(host, did, port):  # noqa: ANN001
            hub.lanlink_topology_dids = topology_after
        activate.side_effect = _activate

    validate_mock = AsyncMock()
    if isinstance(validate, list):
        validate_mock.side_effect = validate
    else:
        validate_mock.return_value = validate

    discover = AsyncMock(return_value=None)

    monkeypatch.setattr(rearm, "activate_relay", activate)
    monkeypatch.setattr(rearm, "validate_aqara_endpoint", validate_mock)
    monkeypatch.setattr(rearm, "discover_hub_by_did", discover)
    return activate, validate_mock, discover


def _manager(hass, entry, **kw) -> RearmManager:
    # Core tests disable the debounce/cooldown so they exercise the retry logic
    # directly; the debounce/cooldown have their own dedicated tests.
    opts: dict = dict(
        backoffs=(0,), settle_timeout=0.05, poll_interval=0.01, max_rounds=3,
        absence_grace=0.0, rearm_cooldown=0.0,
    )
    opts.update(kw)
    return RearmManager(hass, entry, **opts)


async def _drain(manager: RearmManager, did: str) -> None:
    """Await the in-flight re-arm task for ``did`` if one exists."""
    task = manager._tasks.get(did)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)


async def test_note_topology_absent_device_schedules_one_rearm(hass, monkeypatch):
    hub = _FakeHub(frozenset())
    entry = _make_entry(hub)

    # Gate validate so the first task is provably still in-flight when the
    # second note_topology arrives (proves idempotency, not a timing race).
    gate = asyncio.Event()

    async def _gated_validate(host, port):  # noqa: ANN001
        await gate.wait()
        return True

    activate = AsyncMock()

    async def _activate(host, did, port):  # noqa: ANN001
        hub.lanlink_topology_dids = frozenset({FP2_DID})
    activate.side_effect = _activate

    monkeypatch.setattr(rearm, "activate_relay", activate)
    monkeypatch.setattr(
        rearm, "validate_aqara_endpoint", AsyncMock(side_effect=_gated_validate),
    )
    monkeypatch.setattr(rearm, "discover_hub_by_did", AsyncMock(return_value=None))
    manager = _manager(hass, entry)

    manager.note_topology(frozenset())
    task = manager._tasks[FP2_DID]
    # A second note while the task is running must NOT start a 2nd task.
    manager.note_topology(frozenset())
    assert manager._tasks[FP2_DID] is task

    gate.set()
    await _drain(manager, FP2_DID)

    activate.assert_awaited_once_with(FP2_HOST, FP2_DID, FP2_PORT)


async def test_note_topology_present_device_no_rearm(hass, monkeypatch):
    hub = _FakeHub(frozenset({FP2_DID}))
    entry = _make_entry(hub)
    activate, _validate, _ = _patch_relay(monkeypatch, validate=True)
    manager = _manager(hass, entry)

    manager.note_topology(frozenset({FP2_DID}))
    assert FP2_DID not in manager._tasks
    await asyncio.sleep(0)

    activate.assert_not_awaited()


async def test_rearm_retries_until_reachable(hass, monkeypatch):
    hub = _FakeHub(frozenset())
    entry = _make_entry(hub)
    activate, validate, _ = _patch_relay(
        monkeypatch, validate=[False, False, True],
        topology_after=frozenset({FP2_DID}), hub=hub,
    )
    manager = _manager(hass, entry)

    manager.note_topology(frozenset())
    await _drain(manager, FP2_DID)

    activate.assert_awaited_once_with(FP2_HOST, FP2_DID, FP2_PORT)
    assert validate.await_count == 3


async def test_sweep_reactivates_absent_device(hass, monkeypatch):
    hub = _FakeHub(frozenset())
    entry = _make_entry(hub)
    # First round: device never settles -> task ends after max_rounds.
    activate, validate, _ = _patch_relay(monkeypatch, validate=False)
    manager = _manager(hass, entry)

    manager.note_topology(frozenset())
    await _drain(manager, FP2_DID)
    assert FP2_DID not in manager._tasks  # task cleaned itself up

    # Device is still absent; sweep should schedule a FRESH re-arm.
    manager.sweep()
    assert FP2_DID in manager._tasks
    await _drain(manager, FP2_DID)


async def test_cancel_all_stops_inflight(hass, monkeypatch):
    hub = _FakeHub(frozenset())
    entry = _make_entry(hub)

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_validate(host, port):  # noqa: ANN001
        started.set()
        await release.wait()  # block forever until cancelled
        return True

    activate = AsyncMock()
    monkeypatch.setattr(rearm, "activate_relay", activate)
    monkeypatch.setattr(
        rearm, "validate_aqara_endpoint", AsyncMock(side_effect=_slow_validate),
    )
    monkeypatch.setattr(rearm, "discover_hub_by_did", AsyncMock(return_value=None))
    manager = _manager(hass, entry)

    manager.note_topology(frozenset())
    task = manager._tasks[FP2_DID]
    await asyncio.wait_for(started.wait(), timeout=2.0)

    manager.cancel_all()
    assert manager._tasks == {}
    with pytest.raises(asyncio.CancelledError):
        await task

    activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_absence_grace_skips_when_device_returns_during_grace(hass, monkeypatch):
    # The relay flaps and self-recovers: if the device returns during the
    # absence-grace window, re-arm must NOT activate.
    hub = _FakeHub(frozenset())  # absent
    entry = _make_entry(hub)
    activate, _validate, _discover = _patch_relay(monkeypatch, validate=True)
    manager = _manager(hass, entry, absence_grace=0.05, rearm_cooldown=0.0)

    manager.note_topology(frozenset())          # absent -> start loop (grace begins)
    await asyncio.sleep(0)                       # let the loop reach the grace sleep
    hub.lanlink_topology_dids = frozenset({FP2_DID})  # returns on its own during grace
    await _drain(manager, FP2_DID)

    activate.assert_not_called()


@pytest.mark.asyncio
async def test_cooldown_blocks_reactivation_of_recently_activated_device(hass, monkeypatch):
    # A device we activated moments ago that drops again must not be re-poked
    # within the cooldown -- repeated :443 handshakes can keep it unstable.
    hub = _FakeHub(frozenset())  # absent
    entry = _make_entry(hub)
    activate, _validate, _discover = _patch_relay(monkeypatch, validate=True)
    manager = _manager(
        hass, entry, absence_grace=0.0, rearm_cooldown=90.0, clock=lambda: 1000.0,
    )
    manager._last_activation[FP2_DID] = 1000.0   # activated "now" -> since=0 < cooldown

    # While we're cooling down, the device returns on its own; the cooldown
    # wait re-checks topology and exits without ever calling activate_relay.
    real_sleep = asyncio.sleep

    async def _sleep(_delay):
        hub.lanlink_topology_dids = frozenset({FP2_DID})
        await real_sleep(0)

    monkeypatch.setattr("asyncio.sleep", _sleep)
    manager.note_topology(frozenset())           # absent -> start loop
    await _drain(manager, FP2_DID)

    activate.assert_not_called()
