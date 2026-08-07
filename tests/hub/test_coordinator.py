"""Tests for HubCoordinator -- owns the session lifecycle and reconnects.

The real network is not involved: we patch EncryptedTunnel to a fake, so the
tests exercise the retry/backoff/dispatch logic without TCP or AES.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.hub import coordinator
from custom_components.aqara_lanlink.hub.coordinator import HubCoordinator
from custom_components.aqara_lanlink.hub.protocol import (
    LANReport,
    LANLINK_CMD_CHECKIN_DONE, LANLINK_CMD_READ, LANLINK_CMD_READ_DONE,
    LANLINK_CMD_REPORT, LANLINK_CMD_TOPOLOGY, LANLINK_CMD_WRITE,
    LANLINK_CMD_WRITE_DONE, LANLINK_TYPE_DEVICE, LANLINK_TYPE_SESSION,
)
from custom_components.aqara_lanlink.hub.topology import TopologyDevice


# Stand-in for AttrSpec (lands in Chunk 3). Frozen dataclasses are
# hashable by construction, matching the real AttrSpec's behavior.
@dataclass(frozen=True)
class _FakeAttr:
    name: str


CHILD_DID = "lumi3.TESTDOORBELL1"
CHILD_MODEL = "lumi.camera.agl013"


HUB_IP = "192.0.2.20"
HUB_PORT = 59703
HUB_DID = "lumi1.TESTHUB00001"
HUB_MODEL = "lumi.gateway.agl004"
USER_ID = "u123"
TOKEN = "tok456"


class FakeTunnel:
    """Minimal stand-in for EncryptedTunnel used by the coordinator."""

    def __init__(self, *, device_id: str) -> None:
        self.device_id = device_id
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.sent: list[dict] = []
        self._closed = False
        self._connected = False
        self.connect_error: Exception | None = None
        self.checkin_code: int = 0

    async def connect(self, host: str, port: int) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self._connected = True
        # Pre-seed the checkin_done response the coordinator will wait for.
        self._queue.put_nowait({
            "seq": 1,
            "type": LANLINK_TYPE_SESSION,
            "cmd": LANLINK_CMD_CHECKIN_DONE,
            "data": {"code": self.checkin_code},
        })

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self) -> dict | None:
        item = await self._queue.get()
        return item

    async def close(self) -> None:
        self._closed = True
        # Unblock any in-flight receive().
        self._queue.put_nowait(None)

    def is_connected(self) -> bool:
        return self._connected and not self._closed

    # --- test helpers ---

    def push_report(self, values: dict[str, Any]) -> None:
        self._queue.put_nowait({
            "seq": 99,
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_REPORT,
            "data": {
                "did": "lumi3.doorbell",
                "sdid": "lumi3.doorbell",
                "src": "",
                "time": 0,
                "value": values,
            },
        })

    def push_eof(self) -> None:
        self._queue.put_nowait(None)


class FactoryState:
    """Container that produces successive FakeTunnels for each connect attempt."""

    def __init__(self, tunnels: list[FakeTunnel]) -> None:
        self._tunnels = list(tunnels)
        self.created: list[FakeTunnel] = []

    def __call__(self, *, device_id: str) -> FakeTunnel:
        if not self._tunnels:
            raise AssertionError("FakeTunnel factory ran out of tunnels")
        t = self._tunnels.pop(0)
        t.device_id = device_id
        self.created.append(t)
        return t


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    """Make reconnect delays instantaneous so tests run quickly."""
    # The coordinator uses asyncio.sleep for backoff; override the default
    # timings on each instance via constructor args, but also cap max sleep.
    yield


async def _coord_with(
    monkeypatch, tunnels: list[FakeTunnel], on_report=None,
    report_did: str = "lumi3.doorbell",
    reconnect_min_delay=0.01, reconnect_max_delay=0.02,
    model: str | None = None,
) -> tuple[HubCoordinator, FactoryState]:
    """Construct a coordinator wired to a FakeTunnel factory.

    By default, a report-appending callback is registered for
    ``report_did`` (matching the DID that ``FakeTunnel.push_report``
    stamps onto its fixtures). Tests that want to drive a different
    callback or DID can override both knobs.

    ``model`` overrides the coordinator's hub model. When ``None`` the
    HubCoordinator default (G400_MODEL) is used, matching the original
    Chunk 1/2 fixture behavior.
    """
    factory = FactoryState(tunnels)
    monkeypatch.setattr(coordinator, "EncryptedTunnel", factory)

    reports: list[LANReport] = []
    cb = on_report if on_report is not None else reports.append

    kwargs: dict[str, Any] = dict(
        host=HUB_IP, port=HUB_PORT,
        device_id=HUB_DID, user_id=USER_ID, token=TOKEN,
        reconnect_min_delay=reconnect_min_delay,
        reconnect_max_delay=reconnect_max_delay,
    )
    if model is not None:
        kwargs["model"] = model
    coord = HubCoordinator(**kwargs)
    coord.register_report_handler(report_did, cb)
    coord._reports_sink = reports  # type: ignore[attr-defined] -- for tests
    return coord, factory


def test_apply_jitter_spreads_within_bounds(monkeypatch):
    monkeypatch.setattr(coordinator.random, "uniform", lambda a, b: a)
    assert coordinator._apply_jitter(10.0) == 5.0
    monkeypatch.setattr(coordinator.random, "uniform", lambda a, b: b)
    assert coordinator._apply_jitter(10.0) == 15.0


class TestHappyPath:
    async def test_connects_and_dispatches_report(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, factory = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        t.push_report({"5.160.33000.1": "1"})
        # give the listen loop a tick to dispatch
        await asyncio.sleep(0.05)

        sink = coord._reports_sink  # type: ignore[attr-defined]
        assert len(sink) == 1
        assert sink[0].values == {"5.160.33000.1": "1"}

        await coord.stop()
        assert t._closed

    async def test_sends_checkin_with_correct_credentials(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        assert len(t.sent) == 1
        sent = t.sent[0]
        assert sent["cmd"] == "checkin"
        assert sent["data"]["user"] == USER_ID
        assert sent["data"]["did"] == HUB_DID
        assert sent["data"]["aid"] == TOKEN

        await coord.stop()

    async def test_fires_on_session_up_after_checkin(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        calls: list[int] = []
        coord.on_session_up = lambda: calls.append(1)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        assert calls  # fired once the session is up (push subscription re-arm)

        await coord.stop()


class TestReconnect:
    async def test_reconnects_after_tunnel_eof(self, monkeypatch):
        t1 = FakeTunnel(device_id=HUB_DID)
        t2 = FakeTunnel(device_id=HUB_DID)
        coord, factory = await _coord_with(monkeypatch, [t1, t2])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        # First session ends cleanly with EOF. Coordinator should reconnect.
        t1.push_eof()

        # Wait for second session to come up.
        async def wait_second():
            while len(factory.created) < 2:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_second(), timeout=1.0)

        # And it should also go through checkin.
        await asyncio.sleep(0.05)
        assert len(t2.sent) == 1
        assert t2.sent[0]["cmd"] == "checkin"

        await coord.stop()

    async def test_retries_after_connect_error(self, monkeypatch):
        failing = FakeTunnel(device_id=HUB_DID)
        failing.connect_error = OSError("connection refused")
        working = FakeTunnel(device_id=HUB_DID)

        coord, factory = await _coord_with(monkeypatch, [failing, working])
        coord.start()
        await coord.wait_connected(timeout=1.0)
        assert len(factory.created) == 2
        await coord.stop()


class TestCheckinRejection:
    async def test_rejected_checkin_reconnects(self, monkeypatch):
        bad = FakeTunnel(device_id=HUB_DID)
        bad.checkin_code = 2  # auth fail
        good = FakeTunnel(device_id=HUB_DID)

        coord, factory = await _coord_with(monkeypatch, [bad, good])
        coord.start()
        await coord.wait_connected(timeout=1.0)
        assert len(factory.created) == 2
        await coord.stop()


class TestHandlerDispatch:
    async def test_sync_callback_exception_does_not_kill_session(self, monkeypatch):
        def bad_cb(report: LANReport) -> None:
            raise RuntimeError("boom")

        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], on_report=bad_cb)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        t.push_report({"5.160.33000.1": "1"})
        await asyncio.sleep(0.05)
        # Session still alive despite callback exception.
        assert coord.is_connected
        await coord.stop()


class TestPerDidDispatch:
    """register_report_handler routes by report.sdid (falling back to did)
    so reports for device A never reach handlers registered for device B."""

    def _make_coord(self, monkeypatch) -> HubCoordinator:
        # No network interaction for these dispatch-unit tests: we feed
        # LANReport objects directly into ``_dispatch_report``.
        monkeypatch.setattr(
            coordinator, "EncryptedTunnel", FactoryState([]),
        )
        return HubCoordinator(
            host=HUB_IP, port=HUB_PORT,
            device_id=HUB_DID, user_id=USER_ID, token=TOKEN,
            reconnect_min_delay=0.01, reconnect_max_delay=0.02,
        )

    def _report(self, *, sdid: str, did: str = "", values=None, src: str = "") -> LANReport:
        return LANReport(
            did=did or sdid,
            sdid=sdid,
            src=src,
            time=0,
            values=values or {"5.160.33000.1": "1"},
        )

    def test_reports_dispatched_regardless_of_src_origin(self, monkeypatch):
        """Every origin reaches the handler, including a local (type-3) src.

        A type-3 src is either HA's own write-echo or another LAN
        controller's change; both carry real device state, and for
        report-driven entities the echo is the only state source.
        """
        coord = self._make_coord(monkeypatch)
        seen: list = []
        coord.register_report_handler("lumi.dev", seen.append)
        for src in ("3,,42,,", "4,,99,app,id,", "10,,43,lumi.dev.trg=0,,", ""):
            coord._dispatch_report(
                self._report(sdid="lumi.dev", values={"2.1.1.1": "1"}, src=src)
            )
        assert len(seen) == 4

    def test_report_bumps_liveness(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        coord.register_report_handler("lumi.dev", lambda _r: None)
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 7000.0)
        coord._dispatch_report(
            self._report(sdid="lumi.dev", values={"2.1.1.1": "1"}, src="3,,42,,")
        )
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 7002.0)
        assert coord.seconds_since_last_report() == pytest.approx(2.0)

    def test_first_report_after_arm_logs_latency_once(self, monkeypatch, caplog):
        coord = self._make_coord(monkeypatch)
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 100.0)
        coord.note_subscription_armed("lumi.dev")
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 100.4)
        with caplog.at_level(logging.DEBUG):
            coord._dispatch_report(self._report(sdid="lumi.dev", values={"x": "1"}))
        assert any(
            "first report after subscribe arm" in r.getMessage().lower()
            for r in caplog.records
        )
        # A second report for the same did must not re-log the latency.
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            coord._dispatch_report(self._report(sdid="lumi.dev", values={"x": "2"}))
        assert not any(
            "first report after subscribe arm" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_dispatch_report_refreshes_liveness_clock(self, monkeypatch):
        # Any inbound report (incl. the hub's 1.176.20009.1 heartbeat) marks the
        # forwarding path alive; the watchdog uses seconds_since_last_report.
        coord = self._make_coord(monkeypatch)
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 5000.0)
        coord._dispatch_report(
            self._report(sdid="lumi.x", values={"1.176.20009.1": "hb"}),
        )
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: 5003.0)
        assert coord.seconds_since_last_report() == pytest.approx(3.0)

    def test_report_routes_to_handler_for_matching_sdid_only(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen_a: list[LANReport] = []
        seen_b: list[LANReport] = []
        coord.register_report_handler("did-a", seen_a.append)
        coord.register_report_handler("did-b", seen_b.append)

        coord._dispatch_report(self._report(sdid="did-a"))

        assert len(seen_a) == 1
        assert seen_a[0].sdid == "did-a"
        assert seen_b == []

    def test_unregister_removes_handler(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen: list[LANReport] = []
        unregister = coord.register_report_handler("did-a", seen.append)

        coord._dispatch_report(self._report(sdid="did-a"))
        assert len(seen) == 1

        unregister()
        coord._dispatch_report(self._report(sdid="did-a"))
        # Still 1 -- the second report was dropped.
        assert len(seen) == 1

    def test_multiple_handlers_same_did_all_fire(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen_1: list[LANReport] = []
        seen_2: list[LANReport] = []
        coord.register_report_handler("did-a", seen_1.append)
        coord.register_report_handler("did-a", seen_2.append)

        coord._dispatch_report(self._report(sdid="did-a"))

        assert len(seen_1) == 1
        assert len(seen_2) == 1

    def test_exception_in_one_handler_does_not_block_others(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen_after: list[LANReport] = []

        def raiser(report: LANReport) -> None:
            raise RuntimeError("handler boom")

        coord.register_report_handler("did-a", raiser)
        coord.register_report_handler("did-a", seen_after.append)

        # Should not raise out of dispatch.
        coord._dispatch_report(self._report(sdid="did-a"))

        assert len(seen_after) == 1

    def test_empty_sdid_falls_back_to_did(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen: list[LANReport] = []
        coord.register_report_handler("did-x", seen.append)

        coord._dispatch_report(self._report(sdid="", did="did-x"))

        assert len(seen) == 1
        assert seen[0].did == "did-x"
        assert seen[0].sdid == ""

    def test_unregister_is_idempotent(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        unregister = coord.register_report_handler("did-a", lambda r: None)
        unregister()
        # Second call must not raise.
        unregister()

    def test_report_for_unregistered_did_does_not_reach_other_handlers(self, monkeypatch):
        coord = self._make_coord(monkeypatch)
        seen: list[LANReport] = []
        coord.register_report_handler("did-a", seen.append)

        # Report for did-z is buffered (no handler yet) and never reaches did-a.
        coord._dispatch_report(self._report(sdid="did-z"))

        assert seen == []

    def test_report_before_handler_is_buffered_and_replayed(self, monkeypatch):
        # Startup race: the hub forwards a report the instant the session is up,
        # before the per-device handler is registered. Buffer it and replay on
        # registration instead of dropping the first post-reboot update.
        coord = self._make_coord(monkeypatch)
        received: list[LANReport] = []
        coord._dispatch_report(self._report(sdid="lumi.late", values={"a": "1"}))
        assert received == []  # nothing registered yet

        coord.register_report_handler("lumi.late", received.append)
        assert len(received) == 1
        assert received[0].values == {"a": "1"}

    def test_buffered_reports_delivered_once_then_cleared(self, monkeypatch):
        # Replayed buffer is cleared, so a second registration doesn't re-deliver.
        coord = self._make_coord(monkeypatch)
        coord._dispatch_report(self._report(sdid="lumi.late", values={"a": "1"}))
        first: list[LANReport] = []
        coord.register_report_handler("lumi.late", first.append)
        assert len(first) == 1
        second: list[LANReport] = []
        coord.register_report_handler("lumi.late", second.append)
        assert second == []  # buffer already drained


class TestAsyncDispatch:
    """_dispatch_async must not spam INFO for benign known frames like
    keepalive_done, while still surfacing genuinely unknown async frames."""

    def _make_coord(self, monkeypatch) -> HubCoordinator:
        monkeypatch.setattr(coordinator, "EncryptedTunnel", FactoryState([]))
        return HubCoordinator(
            host=HUB_IP, port=HUB_PORT,
            device_id=HUB_DID, user_id=USER_ID, token=TOKEN,
            reconnect_min_delay=0.01, reconnect_max_delay=0.02,
        )

    def test_keepalive_done_not_logged_as_unrecognized(self, monkeypatch, caplog):
        coord = self._make_coord(monkeypatch)
        with caplog.at_level(logging.INFO):
            coord._dispatch_async(
                {"cmd": "keepalive_done", "type": "session", "seq": 1,
                 "data": {"timestamp": 1}},
            )
        assert "unrecognized async" not in caplog.text

    def test_unknown_async_still_logged_at_info(self, monkeypatch, caplog):
        coord = self._make_coord(monkeypatch)
        with caplog.at_level(logging.INFO):
            coord._dispatch_async(
                {"cmd": "mystery_signal", "type": "session", "seq": 1, "data": {}},
            )
        assert "unrecognized async" in caplog.text

    def test_is_lan_direct_offline_tracks_dropped_topology(self, monkeypatch):
        # Topology lists LAN-direct DIDs (hub + cameras). A DID that was present
        # and then drops out is offline; one never seen (a Zigbee sub-device) is
        # not judged by topology; the hub itself is never offline while connected.
        coord = self._make_coord(monkeypatch)
        coord._record_topology(frozenset({coord._device_id, "lumi3.cam"}))
        assert coord.is_lan_direct_offline("lumi3.cam") is False
        coord._record_topology(frozenset({coord._device_id}))
        assert coord.is_lan_direct_offline("lumi3.cam") is True
        assert coord.is_lan_direct_offline("lumi.zigbee_sub") is False
        assert coord.is_lan_direct_offline(coord._device_id) is False

    def test_keepalive_fires_on_keepalive_hook(self, monkeypatch):
        # The watchdog rides the keepalive cadence instead of a wall-clock timer.
        coord = self._make_coord(monkeypatch)
        calls: list[int] = []
        coord.on_keepalive = lambda: calls.append(1)
        coord._dispatch_async(
            {"cmd": "keepalive_done", "type": "session", "seq": 1, "data": {}},
        )
        assert calls


class TestEndpointRefresh:
    async def test_mdns_discovery_updates_host_port_on_connect_failure(self, monkeypatch):
        """First connect fails; mDNS discovers new port; second connect succeeds."""
        from custom_components.aqara_lanlink.hub.mdns import AqaraServiceRecord

        t_fail = FakeTunnel(device_id=HUB_DID)
        t_fail.connect_error = OSError("connection refused")
        t_good = FakeTunnel(device_id=HUB_DID)

        coord, factory = await _coord_with(monkeypatch, [t_fail, t_good])

        # Track whether persist callback was invoked with the new endpoint.
        persisted: list = []
        coord._on_endpoint_change = lambda h, p: persisted.append((h, p))

        new_record = AqaraServiceRecord(
            host=HUB_IP, port=99999, did=HUB_DID, name="Aqara-Hub",
        )
        monkeypatch.setattr(
            "custom_components.aqara_lanlink.hub.coordinator.discover_hub_by_did",
            AsyncMock(return_value=new_record),
        )

        coord.start()
        await coord.wait_connected(timeout=1.0)

        # After the first connect failure, the coordinator should have swapped
        # the port and fired the persist callback.
        assert coord._port == 99999
        assert persisted == [(HUB_IP, 99999)]
        await coord.stop()

    async def test_refresh_reports_whether_endpoint_changed(self, monkeypatch):
        from custom_components.aqara_lanlink.hub.mdns import AqaraServiceRecord

        coord, _ = await _coord_with(monkeypatch, [FakeTunnel(device_id=HUB_DID)])
        rec = AqaraServiceRecord(host=HUB_IP, port=99999, did=HUB_DID, name="x")
        monkeypatch.setattr(
            "custom_components.aqara_lanlink.hub.coordinator.discover_hub_by_did",
            AsyncMock(return_value=rec),
        )
        assert await coord._refresh_endpoint_via_mdns() is True   # changed
        assert await coord._refresh_endpoint_via_mdns() is False  # same now
        monkeypatch.setattr(
            "custom_components.aqara_lanlink.hub.coordinator.discover_hub_by_did",
            AsyncMock(return_value=None),
        )
        assert await coord._refresh_endpoint_via_mdns() is False  # no result

    async def test_endpoint_change_skips_reconnect_backoff(self, monkeypatch):
        """When mDNS adopts a new port, retry immediately (no backoff sleep)."""
        from custom_components.aqara_lanlink.hub.mdns import AqaraServiceRecord

        t_fail = FakeTunnel(device_id=HUB_DID)
        t_fail.connect_error = OSError("connection refused")
        t_good = FakeTunnel(device_id=HUB_DID)

        coord, _ = await _coord_with(monkeypatch, [t_fail, t_good])
        sleeps: list = []

        async def fake_sleep(backoff):
            sleeps.append(backoff)

        monkeypatch.setattr(coord, "_reconnect_sleep", fake_sleep)
        monkeypatch.setattr(
            "custom_components.aqara_lanlink.hub.coordinator.discover_hub_by_did",
            AsyncMock(return_value=AqaraServiceRecord(
                host=HUB_IP, port=99999, did=HUB_DID, name="x",
            )),
        )

        coord.start()
        await coord.wait_connected(timeout=1.0)
        assert coord._port == 99999
        assert sleeps == []  # endpoint changed -> reconnected without backoff
        await coord.stop()

    async def test_mdns_no_result_keeps_old_endpoint(self, monkeypatch):
        t_fail = FakeTunnel(device_id=HUB_DID)
        t_fail.connect_error = OSError("connection refused")
        t_good = FakeTunnel(device_id=HUB_DID)

        coord, _ = await _coord_with(monkeypatch, [t_fail, t_good])
        original_port = coord._port

        monkeypatch.setattr(
            "custom_components.aqara_lanlink.hub.coordinator.discover_hub_by_did",
            AsyncMock(return_value=None),
        )

        coord.start()
        await coord.wait_connected(timeout=1.0)
        assert coord._port == original_port
        await coord.stop()


class TestListenBeforeCheckinOrdering:
    """After the seq-keyed-futures refactor the coordinator must start
    listen() before checkin() so the pending-future for the checkin
    response can be resolved by the listen loop."""

    async def test_report_arriving_during_checkin_is_dispatched(self, monkeypatch):
        """Push a report BEFORE the checkin_done so the listen loop sees
        the report first. If checkin fired before listen started the
        report would sit in the queue ahead of the response and checkin
        would read the report, misinterpret it, and likely fail. With
        listen running first the report is routed to on_report and the
        checkin_done resolves the pending future cleanly."""

        class PreReportTunnel(FakeTunnel):
            async def connect(self, host, port):
                if self.connect_error is not None:
                    raise self.connect_error
                self._connected = True
                # A report sneaks in ahead of checkin_done.
                self._queue.put_nowait({
                    "seq": 77,
                    "type": LANLINK_TYPE_DEVICE,
                    "cmd": LANLINK_CMD_REPORT,
                    "data": {
                        "did": HUB_DID,
                        "sdid": HUB_DID,
                        "src": "",
                        "time": 0,
                        "value": {"pre.checkin": "1"},
                    },
                })
                # Then the checkin_done that the coordinator is waiting for.
                self._queue.put_nowait({
                    "seq": 1,
                    "type": LANLINK_TYPE_SESSION,
                    "cmd": LANLINK_CMD_CHECKIN_DONE,
                    "data": {"code": self.checkin_code},
                })

        t = PreReportTunnel(device_id=HUB_DID)
        # The pre-checkin report carries sdid=HUB_DID, so register under
        # HUB_DID to catch it.
        coord, _ = await _coord_with(monkeypatch, [t], report_did=HUB_DID)
        coord.start()
        # wait_connected times out here if the checkin future never resolves
        # (which would happen if listen wasn't running in time).
        await coord.wait_connected(timeout=1.0)

        sink = coord._reports_sink  # type: ignore[attr-defined]
        # Give the listen loop a tick in case the report hadn't been
        # dispatched yet when wait_connected returned.
        await asyncio.sleep(0.05)
        assert any(r.values == {"pre.checkin": "1"} for r in sink)

        await coord.stop()


class TestStop:
    async def test_stop_closes_tunnel(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)
        await coord.stop()
        assert t._closed
        assert not coord.is_connected

    async def test_stop_before_start_is_noop(self, monkeypatch):
        coord, _ = await _coord_with(monkeypatch, [])
        await coord.stop()  # should not raise


class TestAsyncRead:
    """async_read unwraps AttrSpec.name, builds a read message, and returns
    the result dict from the read_done response. Sub-device reads use
    gateway-relay framing; hub-scoped reads use direct framing."""

    async def test_builds_read_message_for_sub_device_uses_gateway_relay_framing(
        self, monkeypatch,
    ):
        """Sub-device read: did=hub, sdid=sub, model=hub_model."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        # Drive: inject the read_done response keyed to the seq the
        # coordinator will allocate (checkin took seq=1, so read=2).
        async def run_read() -> dict[str, Any]:
            return await coord.async_read(
                CHILD_DID, CHILD_MODEL,
                [_FakeAttr(name="4.1.85"), _FakeAttr(name="14.85.85")],
            )

        read_task = asyncio.create_task(run_read())
        # Wait until the read request hits the wire so we know its seq.
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send read request"
        read_msg = t.sent[1]
        assert read_msg["cmd"] == LANLINK_CMD_READ
        assert read_msg["type"] == LANLINK_TYPE_DEVICE
        # Gateway-relay framing: did is the hub, sdid is the sub-device,
        # model is the hub's model (NOT the per-device model).
        assert read_msg["data"]["did"] == HUB_DID
        assert read_msg["data"]["sdid"] == CHILD_DID
        assert read_msg["data"]["model"] == HUB_MODEL
        assert read_msg["data"]["model"] != CHILD_MODEL
        # Names are unwrapped from the fake AttrSpec.
        assert read_msg["data"]["value"] == ["4.1.85", "14.85.85"]

        # Send back a matching read_done with the real-firmware ``result``
        # key. Coordinator should return that mapping.
        t._queue.put_nowait({
            "seq": read_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_READ_DONE,
            "data": {
                "did": HUB_DID,
                "sdid": CHILD_DID,
                "result": {"4.1.85": "1", "14.85.85": "42"},
            },
        })

        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == {"4.1.85": "1", "14.85.85": "42"}

        await coord.stop()

    async def test_builds_read_message_for_hub_did_uses_direct_framing(
        self, monkeypatch,
    ):
        """Hub-scoped read (did==hub_did): direct framing, did==sdid==hub."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        async def run_read() -> dict[str, Any]:
            return await coord.async_read(
                HUB_DID, HUB_MODEL, [_FakeAttr(name="8.0.2002")],
            )

        read_task = asyncio.create_task(run_read())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2
        read_msg = t.sent[1]
        # Direct framing: did == sdid == hub_did, model unchanged.
        assert read_msg["data"]["did"] == HUB_DID
        assert read_msg["data"]["sdid"] == HUB_DID
        assert read_msg["data"]["model"] == HUB_MODEL
        assert read_msg["data"]["value"] == ["8.0.2002"]

        t._queue.put_nowait({
            "seq": read_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_READ_DONE,
            "data": {
                "did": HUB_DID,
                "sdid": HUB_DID,
                "result": {"8.0.2002": "ok"},
            },
        })

        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == {"8.0.2002": "ok"}

        await coord.stop()

    async def test_parses_result_key_not_value_key(self, monkeypatch):
        """Real hub firmware returns ``data.result``, not ``data.value``."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        async def run_read() -> dict[str, Any]:
            return await coord.async_read(
                CHILD_DID, CHILD_MODEL, [_FakeAttr(name="8.0.2002")],
            )

        read_task = asyncio.create_task(run_read())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        read_msg = t.sent[1]

        # Response uses ``result`` -- the bug-1-fixed coordinator parses
        # this; the previous (buggy) implementation looked at ``value``
        # and would have returned an empty dict.
        t._queue.put_nowait({
            "seq": read_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_READ_DONE,
            "data": {
                "did": HUB_DID,
                "sdid": CHILD_DID,
                "result": {"8.0.2002": None},
            },
        })

        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == {"8.0.2002": None}

        await coord.stop()

    async def test_returns_empty_dict_when_response_has_neither_result_nor_value(
        self, monkeypatch,
    ):
        """Defensive: tolerate firmware variants that omit both keys."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        async def run_read() -> dict[str, Any]:
            return await coord.async_read(
                CHILD_DID, CHILD_MODEL, [_FakeAttr(name="8.0.2002")],
            )

        read_task = asyncio.create_task(run_read())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        read_msg = t.sent[1]

        t._queue.put_nowait({
            "seq": read_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_READ_DONE,
            "data": {"did": HUB_DID, "sdid": CHILD_DID},
        })

        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == {}

        await coord.stop()

    async def test_tolerates_legacy_value_key_in_response(self, monkeypatch):
        """Backward-compat: older fixtures use ``data.value``; still parsed."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        async def run_read() -> dict[str, Any]:
            return await coord.async_read(
                CHILD_DID, CHILD_MODEL, [_FakeAttr(name="8.0.2002")],
            )

        read_task = asyncio.create_task(run_read())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        read_msg = t.sent[1]

        t._queue.put_nowait({
            "seq": read_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_READ_DONE,
            "data": {
                "did": HUB_DID,
                "sdid": CHILD_DID,
                "value": {"8.0.2002": "legacy"},
            },
        })

        result = await asyncio.wait_for(read_task, timeout=1.0)
        assert result == {"8.0.2002": "legacy"}

        await coord.stop()

    async def test_raises_when_not_connected(self, monkeypatch):
        coord, _ = await _coord_with(monkeypatch, [])
        # Never started, so no session exists.
        with pytest.raises(HomeAssistantError, match="hub not connected"):
            await coord.async_read(
                CHILD_DID, CHILD_MODEL, [_FakeAttr(name="4.1.85")],
            )


class TestAsyncWrite:
    """async_write unwraps AttrSpec.name into the wire payload, awaits
    write_done, and raises HomeAssistantError on hub-reported errors.
    Sub-device writes use gateway-relay framing; hub-scoped writes use
    direct framing."""

    async def test_builds_write_message_for_sub_device_of_connected_hub(
        self, monkeypatch,
    ):
        """Sub-device whose parent IS the hub we connected to.

        Wire shape: ``did = parent_did = HUB_DID``, ``sdid = CHILD_DID``,
        ``model = CHILD_MODEL`` (the target's own model -- previously
        the coordinator incorrectly used the hub's model here, the
        second half of the bug verified by Frida capture against the
        Aqara Home app on 2026-05-09).
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec_a = _FakeAttr(name="4.1.85")
        spec_b = _FakeAttr(name="14.85.85")

        async def run_write() -> None:
            await coord.async_write(
                CHILD_DID, CHILD_MODEL,
                {spec_a: "1", spec_b: "7"},
                parent_did=HUB_DID,
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send write request"
        write_msg = t.sent[1]
        assert write_msg["cmd"] == LANLINK_CMD_WRITE
        assert write_msg["type"] == LANLINK_TYPE_DEVICE
        # Wire: did = parent_did, sdid = target, model = target's own model.
        assert write_msg["data"]["did"] == HUB_DID
        assert write_msg["data"]["sdid"] == CHILD_DID
        assert write_msg["data"]["model"] == CHILD_MODEL
        # Dict keys are unwrapped from the fake AttrSpec objects, and
        # _to_wire_path appends the LANLink ``.1`` resource-instance
        # suffix to 3-part paths so the device firmware accepts the
        # write (without the suffix the cluster delivers but the
        # device silently rejects -- verified against the Aqara Home
        # app's wire format on 2026-05-09).
        assert write_msg["data"]["value"] == {"4.1.85.1": "1", "14.85.85.1": "7"}

        # Send back a successful write_done.
        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_async_write_stamps_local_origin_src(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = _FakeAttr(name="4.1.85")

        async def run_write() -> None:
            await coord.async_write(HUB_DID, HUB_MODEL, {spec: "1"}, parent_did=HUB_DID)

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        write_msg = t.sent[1]
        src = write_msg["data"]["src"]
        assert re.fullmatch(r"3,,\d+,,", src)            # stamped a local-write src

        # complete the write_done so run_write() finishes (mirror sibling tests)
        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": "write_done",
            "data": {"code": 0},
        })
        await write_task

        await coord.stop()

    async def test_write_echo_reaches_report_handler(self, monkeypatch):
        """Regression: the hub's echo of our own write must be dispatched.

        Report-driven entities (lights, wire-path traits) never set
        optimistic state - `descriptor.optimistic` is opt-in and used only
        by rid-keyed settings, which receive no reports at all. So for a
        light the echo is the ONLY state source. Dropping it left HA's
        state frozen at the pre-write value, and a `light.toggle`
        automation re-sent turn_on on every press instead of turning off.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        seen: list = []
        coord.register_report_handler(HUB_DID, seen.append)
        spec = _FakeAttr(name="4.1.85")

        async def run_write() -> None:
            await coord.async_write(HUB_DID, HUB_MODEL, {spec: "1"}, parent_did=HUB_DID)

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        write_msg = t.sent[1]
        src = write_msg["data"]["src"]
        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": "write_done",
            "data": {"code": 0},
        })
        await write_task

        # The hub echoes the write back as a report carrying the same src.
        coord._dispatch_report(
            LANReport(
                did=HUB_DID, sdid=HUB_DID, src=src, time=0,
                values={"4.1.85.1": "1"},
            ),
        )
        assert [r.values for r in seen] == [{"4.1.85.1": "1"}]

        await coord.stop()

    async def test_builds_write_message_for_cross_cluster_sub_device(
        self, monkeypatch,
    ):
        """Sub-device behind a PEER hub in the cluster (not our connected hub).

        This is the bug case that motivated the fix. When the integration
        connects to M3 but the target sub-device is a child of M100, the
        wire `did` MUST be M100 (the parent), not M3 -- otherwise M3
        ack's with `code=0` and the device silently no-ops because M3
        doesn't own that sub-device.

        Wire shape: ``did = peer_hub_did`` (parent), ``sdid = CHILD_DID``,
        ``model = CHILD_MODEL`` (the target's model). The connected hub
        we're sending through (HUB_DID = M3) appears nowhere in the
        outgoing data -- the cluster leader routes by `did`.
        """
        peer_hub_did = "lumi3.PEERHUB000001"
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = _FakeAttr(name="2.132.32920.1")

        async def run_write() -> None:
            await coord.async_write(
                CHILD_DID, CHILD_MODEL,
                {spec: "1"},
                parent_did=peer_hub_did,
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send write request"
        write_msg = t.sent[1]
        assert write_msg["cmd"] == LANLINK_CMD_WRITE
        # Wire: did = peer hub (parent), sdid = sub-device,
        # model = target's model. The connected hub HUB_DID is NOT in
        # the wire data at all.
        assert write_msg["data"]["did"] == peer_hub_did
        assert write_msg["data"]["sdid"] == CHILD_DID
        assert write_msg["data"]["model"] == CHILD_MODEL
        assert write_msg["data"]["did"] != HUB_DID
        assert write_msg["data"]["value"] == {"2.132.32920.1": "1"}

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_builds_write_message_for_hub_did_uses_direct_framing(
        self, monkeypatch,
    ):
        """Hub-scoped write (did==hub_did): direct framing, did==sdid==hub."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = _FakeAttr(name="8.0.2002")

        async def run_write() -> None:
            await coord.async_write(HUB_DID, HUB_MODEL, {spec: 1})

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2
        write_msg = t.sent[1]
        # Direct framing: did == sdid == hub_did, model unchanged.
        assert write_msg["data"]["did"] == HUB_DID
        assert write_msg["data"]["sdid"] == HUB_DID
        assert write_msg["data"]["model"] == HUB_MODEL
        # 3-part path gets the .1 LANLink suffix on the wire (see
        # _to_wire_path).
        assert write_msg["data"]["value"] == {"8.0.2002.1": 1}

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_own_parent_node_uses_direct_framing(self, monkeypatch):
        """Standalone Wi-Fi node (camera/FP2): parent_did="" -> did==sdid==node.

        PROVEN LIVE 2026-06-30: a relayed write with did==sdid==G350 (over the M3
        tunnel) toggled the camera's OSD overlay; did=hub/sdid=G350 did nothing.
        So an own-parent node (empty PARENT_DID, not the connected hub) must be
        addressed directly by its own DID, NOT framed as a child of the hub.
        """
        node_did = "lumi3.CAMERA00000001"
        node_model = "lumi.camera.agl010"
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        async def run_write() -> None:
            await coord.async_write(
                node_did, node_model, {_FakeAttr(name="14.4.85"): "0"},
                parent_did="",
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send write request"
        write_msg = t.sent[1]
        assert write_msg["cmd"] == LANLINK_CMD_WRITE
        # Direct framing: did == sdid == the node, model unchanged, hub absent.
        assert write_msg["data"]["did"] == node_did
        assert write_msg["data"]["sdid"] == node_did
        assert write_msg["data"]["model"] == node_model
        assert write_msg["data"]["did"] != HUB_DID

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)
        await coord.stop()

    async def test_raises_on_nonzero_code(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = _FakeAttr(name="4.1.85")

        async def run_write() -> None:
            await coord.async_write(
                CHILD_DID, CHILD_MODEL, {spec: "1"},
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        write_msg = t.sent[1]

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 5, "message": "attr not writable"},
        })

        with pytest.raises(HomeAssistantError, match="attr not writable"):
            await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_raises_when_not_connected(self, monkeypatch):
        coord, _ = await _coord_with(monkeypatch, [])
        spec = _FakeAttr(name="4.1.85")
        with pytest.raises(HomeAssistantError, match="hub not connected"):
            await coord.async_write(
                CHILD_DID, CHILD_MODEL, {spec: "1"},
            )

    async def test_tunnel_closed_mid_write_raises_home_assistant_error(
        self, monkeypatch,
    ):
        """If the tunnel drops while a write is in flight, the raw
        ConnectionError from session.async_send_and_await must be
        converted to HomeAssistantError so the HA WebSocket API surfaces
        a clean service-call failure instead of an "Unexpected exception"
        traceback in the log. The coordinator's reconnect loop is already
        handling the tunnel-down recovery; this is purely about the
        user-facing surface for the in-flight write that got cut off."""
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = _FakeAttr(name="4.1.85")

        async def run_write() -> None:
            await coord.async_write(CHILD_DID, CHILD_MODEL, {spec: "1"})

        write_task = asyncio.create_task(run_write())
        # Wait for the write request to leave the coordinator (sent[0] is
        # the checkin, sent[1] is the write).
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) >= 2, "coordinator did not send write request"

        # Drop the tunnel: listen() sees EOF and fails every pending
        # future with ConnectionError("Tunnel closed").
        t.push_eof()

        with pytest.raises(HomeAssistantError, match="hub disconnected"):
            await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_rid_setting_sends_bare_resource_id_not_wire_path(
        self, monkeypatch,
    ):
        """A spec carrying ``resource_id`` writes the BARE 3-part rid.

        Settings entities pass ``AttrSpec(name=rid, resource_id=rid)``.
        The hub's al2bc actuates the bare rid (e.g. ``4.4.85``); the
        ``.1`` resource-instance suffix that ``_to_wire_path`` appends to
        wire-path traits must NOT be added here -- proven live, see
        docs/dev/LOCAL_RID_WRITE_FINDINGS.md.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = AttrSpec(name="4.4.85", resource_id="4.4.85")

        async def run_write() -> None:
            await coord.async_write(
                CHILD_DID, CHILD_MODEL, {spec: 1}, parent_did=HUB_DID,
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send write request"
        write_msg = t.sent[1]
        assert write_msg["cmd"] == LANLINK_CMD_WRITE
        # Bare rid -- NOT "4.4.85.1".
        assert write_msg["data"]["value"] == {"4.4.85": 1}

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_wire_path_spec_without_resource_id_keeps_dot_one_suffix(
        self, monkeypatch,
    ):
        """Control: a 3-part ``name`` with ``resource_id=None`` is unchanged.

        Normal wire-path traits still get the ``.1`` resource-instance
        suffix from ``_to_wire_path``.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t], model=HUB_MODEL)
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = AttrSpec(name="4.1.85")  # resource_id defaults to None

        async def run_write() -> None:
            await coord.async_write(
                CHILD_DID, CHILD_MODEL, {spec: "1"}, parent_did=HUB_DID,
            )

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(t.sent) == 2, "coordinator did not send write request"
        write_msg = t.sent[1]
        assert write_msg["data"]["value"] == {"4.1.85.1": "1"}

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"code": 0},
        })
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()

    async def test_null_result_without_code_does_not_raise(self, monkeypatch):
        """A ``write_done`` of ``{"result": None}`` with NO ``code`` key
        returns normally. ``result`` is intentionally NOT a success signal
        -- rid writes ack with ``result: null`` even when they actuate
        (a false negative; see docs/dev/LOCAL_RID_WRITE_FINDINGS.md). The
        coordinator only fails on a non-zero ``code``.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        spec = AttrSpec(name="4.4.85", resource_id="4.4.85")

        async def run_write() -> None:
            await coord.async_write(CHILD_DID, CHILD_MODEL, {spec: 1})

        write_task = asyncio.create_task(run_write())
        for _ in range(100):
            if len(t.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        write_msg = t.sent[1]

        t._queue.put_nowait({
            "seq": write_msg["seq"],
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_WRITE_DONE,
            "data": {"result": None},
        })

        # Must return normally -- no raise.
        await asyncio.wait_for(write_task, timeout=1.0)

        await coord.stop()


class TestLanlinkTopologyPushCapture:
    """The coordinator captures spontaneous `cmd=topology` push frames
    delivered through the session client and stores their parsed DID set
    on ``lanlink_topology_dids``."""

    async def test_topology_push_updates_lanlink_topology_dids(self, monkeypatch):
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        # Initial state: no topology push observed yet.
        assert coord.lanlink_topology_dids == frozenset()

        # Push a topology frame -- shape matches what real M3 firmware sends
        # spontaneously after checkin (seq=1, type=device, cmd=topology).
        sub_did = "lumi3.TESTDEV000002"
        t._queue.put_nowait({
            "seq": 99,
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_TOPOLOGY,
            "data": {HUB_DID: [HUB_DID, sub_did]},
        })

        # Give the listen loop a tick to dispatch.
        for _ in range(100):
            if coord.lanlink_topology_dids:
                break
            await asyncio.sleep(0.01)

        assert coord.lanlink_topology_dids == frozenset({HUB_DID, sub_did})
        await coord.stop()

    async def test_topology_property_returns_frozenset_default(self, monkeypatch):
        coord, _ = await _coord_with(monkeypatch, [])
        # No session ever started, but the property is still safe to read.
        assert coord.lanlink_topology_dids == frozenset()
        assert isinstance(coord.lanlink_topology_dids, frozenset)

    async def test_topology_push_invokes_on_topology_changed_callback(
        self, monkeypatch,
    ):
        """A topology push dispatched through the session loop calls
        on_topology_changed with the updated DID frozenset.

        Verifies that ``_dispatch_async`` fires the installed callback after
        updating ``_lanlink_topology_dids``, so callers (e.g. async_setup_entry)
        can keep derived state (host_kind) in sync without reaching into
        config-entry internals from the coordinator.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        received: list[frozenset[str]] = []
        coord.on_topology_changed = received.append

        sub_did = "lumi3.TESTDEV_CB001"
        t._queue.put_nowait({
            "seq": 100,
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_TOPOLOGY,
            "data": {HUB_DID: [HUB_DID, sub_did]},
        })

        # Give the listen loop time to dispatch.
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.01)

        assert len(received) == 1
        assert received[0] == frozenset({HUB_DID, sub_did})
        await coord.stop()

    async def test_topology_push_callback_exception_does_not_break_dispatch(
        self, monkeypatch,
    ):
        """A raising on_topology_changed callback must not propagate out of
        _dispatch_async. The coordinator logs and continues; _lanlink_topology_dids
        is still updated correctly.
        """
        t = FakeTunnel(device_id=HUB_DID)
        coord, _ = await _coord_with(monkeypatch, [t])
        coord.start()
        await coord.wait_connected(timeout=1.0)

        def _bad_callback(dids: frozenset[str]) -> None:
            raise RuntimeError("simulated callback failure")

        coord.on_topology_changed = _bad_callback

        sub_did = "lumi3.TESTDEV_CB002"
        t._queue.put_nowait({
            "seq": 101,
            "type": LANLINK_TYPE_DEVICE,
            "cmd": LANLINK_CMD_TOPOLOGY,
            "data": {HUB_DID: [HUB_DID, sub_did]},
        })

        # Give the listen loop time to dispatch.
        for _ in range(100):
            if coord.lanlink_topology_dids:
                break
            await asyncio.sleep(0.01)

        # _lanlink_topology_dids is updated despite the callback raising.
        assert coord.lanlink_topology_dids == frozenset({HUB_DID, sub_did})
        # The session loop must still be alive (coordinator not crashed).
        assert not coord._task.done()
        await coord.stop()


class TestAsyncGetTopology:
    """``async_get_topology`` filters the cloud device list to entries
    belonging to this hub, parses them via ``topology.parse_cloud_device``,
    and cross-checks against the captured LANLink topology push frame."""

    def _make_coord(self, monkeypatch) -> HubCoordinator:
        # No tunnel I/O for these tests -- we drive the cloud-client mock
        # and inspect the returned TopologyDevice list directly.
        monkeypatch.setattr(
            coordinator, "EncryptedTunnel", FactoryState([]),
        )
        return HubCoordinator(
            host=HUB_IP, port=HUB_PORT,
            device_id=HUB_DID, user_id=USER_ID, token=TOKEN,
            reconnect_min_delay=0.01, reconnect_max_delay=0.02,
        )

    async def test_returns_hub_plus_sub_devices_only(self, monkeypatch):
        coord = self._make_coord(monkeypatch)

        other_hub = "lumi1.OTHERHUB00001"
        records = [
            # This hub itself.
            {
                "did": HUB_DID,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "state": 1,
                "firmwareVersion": "4.5.45_0017",
            },
            # Sub-device of this hub.
            {
                "did": "lumi.TESTZIG0000000A",
                "model": "lumi.remote.b28ac1",
                "deviceName": "Wireless Remote Switch",
                "parentDeviceId": HUB_DID,
                "state": 1,
                "firmwareVersion": "0.0.0_0023",
            },
            # Another sub-device of this hub.
            {
                "did": "lumi.TESTZIG0000000B",
                "model": "lumi.magnet.agl02",
                "deviceName": "Door Sensor",
                "parentDeviceId": HUB_DID,
                "state": 0,
                "firmwareVersion": "0.0.0_0030",
            },
            # A different hub (must NOT appear in the result).
            {
                "did": other_hub,
                "model": "lumi.gateway.agl008",
                "deviceName": "Hub M100",
                "parentDeviceId": "",
                "state": 1,
                "firmwareVersion": "0.0.0_0001",
            },
            # Sub-device belonging to the OTHER hub (must NOT appear).
            {
                "did": "lumi.TESTOTHER0001",
                "model": "lumi.example",
                "deviceName": "Other-hub child",
                "parentDeviceId": other_hub,
                "state": 1,
                "firmwareVersion": "0.0.0_0001",
            },
        ]

        cloud_client = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=records)

        result = await coord.async_get_topology(cloud_client)

        cloud_client.query_device_list.assert_awaited_once_with(TOKEN)
        assert all(isinstance(d, TopologyDevice) for d in result)
        result_dids = {d.did for d in result}
        assert result_dids == {
            HUB_DID, "lumi.TESTZIG0000000A", "lumi.TESTZIG0000000B",
        }
        # The other hub and its child are excluded.
        assert other_hub not in result_dids
        assert "lumi.TESTOTHER0001" not in result_dids

    async def test_skips_virtual_dids(self, monkeypatch):
        coord = self._make_coord(monkeypatch)

        records = [
            {
                "did": HUB_DID,
                "model": "lumi.gateway.agl004",
                "parentDeviceId": "",
                "state": 1,
            },
            {
                "did": "lumi.TESTZIG0000000A",
                "model": "lumi.remote.b28ac1",
                "parentDeviceId": HUB_DID,
                "state": 1,
            },
            # Soft Sensor -- cloud-only, no LANLink path. Must be skipped.
            {
                "did": "virtual.TESTVIRTUAL01",
                "model": "lumi.models.4447_8249",
                "deviceName": "Person detection",
                "parentDeviceId": HUB_DID,
                "state": 1,
            },
        ]

        cloud_client = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=records)

        result = await coord.async_get_topology(cloud_client)
        result_dids = {d.did for d in result}

        assert "virtual.TESTVIRTUAL01" not in result_dids
        assert result_dids == {HUB_DID, "lumi.TESTZIG0000000A"}

    async def test_warns_when_lanlink_has_did_cloud_does_not(
        self, monkeypatch, caplog,
    ):
        coord = self._make_coord(monkeypatch)

        # Prime the LANLink topology with an extra DID the cloud will not
        # report.
        ghost_did = "lumi.TESTGHOST0001"
        coord._lanlink_topology_dids = frozenset({HUB_DID, ghost_did})

        records = [
            {
                "did": HUB_DID,
                "model": "lumi.gateway.agl004",
                "parentDeviceId": "",
                "state": 1,
            },
            # Cloud knows about the hub but not about ghost_did.
        ]

        cloud_client = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=records)

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.aqara_lanlink.hub.coordinator",
        ):
            await coord.async_get_topology(cloud_client)

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and ghost_did in r.getMessage()
        ]
        assert len(warning_records) == 1, (
            f"expected one mismatch warning mentioning {ghost_did!r}, "
            f"got {[r.getMessage() for r in caplog.records]}"
        )

    async def test_no_warning_when_cloud_is_superset_of_lanlink(
        self, monkeypatch, caplog,
    ):
        coord = self._make_coord(monkeypatch)
        # LANLink saw only the hub; cloud reports the hub + a sub-device.
        coord._lanlink_topology_dids = frozenset({HUB_DID})

        records = [
            {
                "did": HUB_DID,
                "model": "lumi.gateway.agl004",
                "parentDeviceId": "",
                "state": 1,
            },
            {
                "did": "lumi.TESTZIG0000000A",
                "model": "lumi.remote.b28ac1",
                "parentDeviceId": HUB_DID,
                "state": 1,
            },
        ]
        cloud_client = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=records)

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.aqara_lanlink.hub.coordinator",
        ):
            await coord.async_get_topology(cloud_client)

        # No mismatch warning should fire when cloud is a superset.
        mismatch_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "topology reports DIDs the cloud does not list" in r.getMessage()
        ]
        assert mismatch_warnings == []


class TestEntityFacingAliases:
    """`connected` and `did` are spec-named aliases used by the entity layer."""

    def test_connected_property_mirrors_is_connected(self):
        coord = HubCoordinator(
            host=HUB_IP, port=HUB_PORT, device_id=HUB_DID,
            user_id=USER_ID, token=TOKEN, model=HUB_MODEL,
        )
        assert coord.connected is False
        coord._connected_event.set()
        assert coord.connected is True
        assert coord.connected == coord.is_connected

    def test_did_property_returns_device_id(self):
        coord = HubCoordinator(
            host=HUB_IP, port=HUB_PORT, device_id=HUB_DID,
            user_id=USER_ID, token=TOKEN, model=HUB_MODEL,
        )
        assert coord.did == HUB_DID
