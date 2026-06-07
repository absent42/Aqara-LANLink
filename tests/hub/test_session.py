"""Tests for the LANLinkClient session client."""

import asyncio
import pytest

from custom_components.aqara_lanlink.hub.session import LANLinkClient
from custom_components.aqara_lanlink.hub.protocol import (
    LANReport,
    LANLINK_CMD_CHECKIN,
    LANLINK_CMD_CHECKIN_DONE,
    LANLINK_CMD_READ,
    LANLINK_CMD_READ_DONE,
    LANLINK_CMD_REPORT,
    LANLINK_CMD_WRITE,
    LANLINK_CMD_WRITE_DONE,
    LANLINK_TYPE_SESSION,
    LANLINK_TYPE_DEVICE,
)


# =============================================================================
# MockTunnel
# =============================================================================


class MockTunnel:
    """Simple queue-backed tunnel.

    Pre-seeded ``responses`` are enqueued at construction time. Each
    ``receive()`` awaits the next item on the queue, blocking once the
    queue drains unless ``close()`` (or ``push_eof``) pushes None. This
    makes the tunnel safe to drive from a background ``listen()`` loop
    that runs concurrently with ``async_send_and_await``.
    """

    def __init__(self, responses=None):
        self.sent = []
        self._queue: asyncio.Queue = asyncio.Queue()
        for r in responses or []:
            self._queue.put_nowait(r)
        self._connected = False

    async def connect(self, host, port):
        self._connected = True

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        return await self._queue.get()

    async def close(self):
        self._connected = False
        self._queue.put_nowait(None)

    def is_connected(self):
        return self._connected

    def push_eof(self):
        self._queue.put_nowait(None)


class QueueTunnel:
    """Tunnel that delivers responses via an asyncio.Queue so a test can
    control exactly when each receive() resolves. Used to drive the
    listen-loop concurrently with calls into the client."""

    def __init__(self):
        self.sent: list[dict] = []
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._connected = False

    async def connect(self, host, port):
        self._connected = True

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        return await self._queue.get()

    async def close(self):
        self._connected = False
        self._queue.put_nowait(None)

    def is_connected(self):
        return self._connected

    # test helpers
    def push(self, message):
        self._queue.put_nowait(message)

    def push_eof(self):
        self._queue.put_nowait(None)


# =============================================================================
# Shared test data
# =============================================================================

DEVICE_ID = "lumi.12345678901234"
USER_ID = "u123456"
MODEL = "lumi.camera.agl013"


def make_checkin_done(seq, code=0):
    return {
        "seq": seq,
        "type": LANLINK_TYPE_SESSION,
        "cmd": LANLINK_CMD_CHECKIN_DONE,
        "data": {"code": code},
    }


def make_read_done(seq, data=None):
    return {
        "seq": seq,
        "type": LANLINK_TYPE_DEVICE,
        "cmd": LANLINK_CMD_READ_DONE,
        "data": data or {"human_detect_enable": 1},
    }


def make_write_done(seq):
    return {
        "seq": seq,
        "type": LANLINK_TYPE_DEVICE,
        "cmd": LANLINK_CMD_WRITE_DONE,
        "data": {},
    }


def make_report(seq=99, values=None):
    return {
        "seq": seq,
        "type": LANLINK_TYPE_DEVICE,
        "cmd": LANLINK_CMD_REPORT,
        "data": {
            "did": DEVICE_ID,
            "sdid": DEVICE_ID,
            "src": "10,,1776428273691,0.trg=1,,",
            "time": 1700000000000,
            "value": values or {"5.160.33000.1": "1"},
        },
    }


# =============================================================================
# Tests: constructor
# =============================================================================


class TestLANLinkClientInit:
    def test_stores_device_id(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client.device_id == DEVICE_ID

    def test_stores_user_id(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client.user_id == USER_ID

    def test_stores_model(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client.model == MODEL

    def test_seq_starts_at_zero(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client._seq == 0

    def test_on_report_defaults_to_none(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client.on_report is None


# =============================================================================
# Tests: next_seq / _next_seq
# =============================================================================


class TestNextSeq:
    def test_public_next_seq_increments_from_zero(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client.next_seq() == 1
        assert client.next_seq() == 2
        assert client.next_seq() == 3

    def test_private_alias_still_works(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        assert client._next_seq() == 1
        assert client._next_seq() == 2

    def test_seq_counter_updated(self):
        tunnel = MockTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        client.next_seq()
        assert client._seq == 1


# =============================================================================
# Session test helper
# =============================================================================


class _Session:
    """Context manager that runs listen() in the background.

    Required by the seq-keyed-futures dispatch: async_send_and_await only
    resolves when listen() routes the matching response. Tests use this
    wrapper to keep each test self-contained while honouring the new
    startup invariant.
    """

    def __init__(self, client: LANLinkClient, tunnel: MockTunnel) -> None:
        self.client = client
        self.tunnel = tunnel
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "_Session":
        self._task = asyncio.create_task(self.client.listen())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.tunnel.push_eof()
        assert self._task is not None
        try:
            await asyncio.wait_for(self._task, timeout=1.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            raise


# =============================================================================
# Tests: checkin
# =============================================================================


class TestCheckin:
    async def test_checkin_success_returns_true(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            result = await client.checkin()
        assert result is True

    async def test_checkin_rejected_returns_false(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            result = await client.checkin()
        assert result is False

    async def test_checkin_sends_correct_cmd(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.checkin()
        assert len(tunnel.sent) == 1
        assert tunnel.sent[0]["cmd"] == LANLINK_CMD_CHECKIN

    async def test_checkin_sends_correct_type(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.checkin()
        assert tunnel.sent[0]["type"] == LANLINK_TYPE_SESSION

    async def test_checkin_sends_device_id(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.checkin()
        assert tunnel.sent[0]["data"]["did"] == DEVICE_ID

    async def test_checkin_sends_user_id(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.checkin()
        assert tunnel.sent[0]["data"]["user"] == USER_ID

    async def test_checkin_uses_seq_1(self):
        tunnel = MockTunnel(responses=[make_checkin_done(seq=1, code=0)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.checkin()
        assert tunnel.sent[0]["seq"] == 1

    async def test_checkin_tunnel_closed_raises(self):
        """If listen() observes EOF before the response arrives, the
        pending checkin future is failed with ConnectionError."""
        tunnel = MockTunnel(responses=[])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        listen_task = asyncio.create_task(client.listen())
        tunnel.push_eof()
        with pytest.raises((ConnectionError, asyncio.TimeoutError)):
            await asyncio.wait_for(client.checkin(), timeout=1.0)
        await asyncio.wait_for(listen_task, timeout=1.0)


# =============================================================================
# Tests: read_attrs
# =============================================================================


class TestReadAttrs:
    async def test_read_attrs_returns_data_dict(self):
        data = {"human_detect_enable": 1, "mdtrigger_enable": 0}
        tunnel = MockTunnel(responses=[make_read_done(seq=1, data=data)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            result = await client.read_attrs(["human_detect_enable", "mdtrigger_enable"])
        assert result == data

    async def test_read_attrs_sends_read_cmd(self):
        tunnel = MockTunnel(responses=[make_read_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.read_attrs(["human_detect_enable"])
        assert tunnel.sent[0]["cmd"] == LANLINK_CMD_READ

    async def test_read_attrs_sends_correct_attrs(self):
        tunnel = MockTunnel(responses=[make_read_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        attrs = ["human_detect_enable", "mdtrigger_enable"]
        async with _Session(client, tunnel):
            await client.read_attrs(attrs)
        assert tunnel.sent[0]["data"]["value"] == attrs

    async def test_read_attrs_sends_device_type(self):
        tunnel = MockTunnel(responses=[make_read_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.read_attrs(["human_detect_enable"])
        assert tunnel.sent[0]["type"] == LANLINK_TYPE_DEVICE

    async def test_read_attrs_sends_model(self):
        tunnel = MockTunnel(responses=[make_read_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.read_attrs(["human_detect_enable"])
        assert tunnel.sent[0]["data"]["model"] == MODEL


# =============================================================================
# Tests: write_attrs
# =============================================================================


class TestWriteAttrs:
    async def test_write_attrs_sends_write_cmd(self):
        tunnel = MockTunnel(responses=[make_write_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.write_attrs({"human_detect_enable": 1})
        assert tunnel.sent[0]["cmd"] == LANLINK_CMD_WRITE

    async def test_write_attrs_sends_correct_values(self):
        tunnel = MockTunnel(responses=[make_write_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        attrs = {"human_detect_enable": 1, "body_push_enable": 0}
        async with _Session(client, tunnel):
            await client.write_attrs(attrs)
        assert tunnel.sent[0]["data"]["value"] == attrs

    async def test_write_attrs_sends_device_id(self):
        tunnel = MockTunnel(responses=[make_write_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.write_attrs({"human_detect_enable": 1})
        assert tunnel.sent[0]["data"]["did"] == DEVICE_ID

    async def test_write_attrs_sends_model(self):
        tunnel = MockTunnel(responses=[make_write_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.write_attrs({"human_detect_enable": 1})
        assert tunnel.sent[0]["data"]["model"] == MODEL

    async def test_write_attrs_sends_device_type(self):
        tunnel = MockTunnel(responses=[make_write_done(seq=1)])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        async with _Session(client, tunnel):
            await client.write_attrs({"human_detect_enable": 1})
        assert tunnel.sent[0]["type"] == LANLINK_TYPE_DEVICE


# =============================================================================
# Tests: report callback via listen()
# =============================================================================


class TestListenReportCallback:
    async def test_listen_dumps_every_decoded_frame_at_debug(self, caplog):
        import logging

        # A request-response (read_done) is otherwise consumed silently by its
        # future; the DEBUG frame dump makes ALL decoded tunnel content visible.
        tunnel = MockTunnel(responses=[
            {"cmd": "read_done", "type": "device", "seq": 7, "data": {"x": 1}},
        ])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        with caplog.at_level(
            logging.DEBUG, logger="custom_components.aqara_lanlink.hub.session",
        ):
            await client.listen()
        assert any(
            r.levelname == "DEBUG" and "read_done" in r.getMessage()
            for r in caplog.records
        )

    async def test_report_fires_callback(self):
        report_msg = make_report(seq=10)
        tunnel = MockTunnel(responses=[report_msg])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        await client.listen()

        assert len(received) == 1
        assert isinstance(received[0], LANReport)

    async def test_report_callback_correct_values(self):
        report_msg = make_report(seq=10)
        tunnel = MockTunnel(responses=[report_msg])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        await client.listen()

        assert received[0].values == {"5.160.33000.1": "1"}

    async def test_report_callback_correct_did(self):
        report_msg = make_report(seq=10)
        tunnel = MockTunnel(responses=[report_msg])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        await client.listen()

        assert received[0].did == DEVICE_ID

    async def test_no_callback_does_not_raise(self):
        report_msg = make_report(seq=10)
        tunnel = MockTunnel(responses=[report_msg])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)
        client.on_report = None

        # Should complete without raising
        await client.listen()

    async def test_non_report_does_not_fire_callback(self):
        non_report = make_checkin_done(seq=5, code=0)
        tunnel = MockTunnel(responses=[non_report])
        tunnel.push_eof()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        await client.listen()

        assert received == []


# =============================================================================
# Tests: reports dispatched during _send_and_wait
# =============================================================================


class TestReportsDuringOperation:
    async def test_report_dispatched_during_checkin(self):
        """A report arriving before checkin_done is dispatched to callback."""
        report_msg = make_report(seq=99)
        checkin_done = make_checkin_done(seq=1, code=0)
        tunnel = MockTunnel(responses=[report_msg, checkin_done])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        async with _Session(client, tunnel):
            result = await client.checkin()
            # Allow listen() to dispatch the trailing report.
            await asyncio.sleep(0)

        assert result is True
        assert len(received) == 1
        assert isinstance(received[0], LANReport)

    async def test_report_dispatched_during_read(self):
        """A report arriving before read_done is dispatched to callback."""
        report_msg = make_report(seq=99)
        read_done = make_read_done(seq=1, data={"human_detect_enable": 1})
        tunnel = MockTunnel(responses=[report_msg, read_done])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        async with _Session(client, tunnel):
            result = await client.read_attrs(["human_detect_enable"])
            await asyncio.sleep(0)

        assert result == {"human_detect_enable": 1}
        assert len(received) == 1

    async def test_report_dispatched_during_write(self):
        """A report arriving before write_done is dispatched to callback."""
        report_msg = make_report(seq=99)
        write_done = make_write_done(seq=1)
        tunnel = MockTunnel(responses=[report_msg, write_done])
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received = []
        client.on_report = lambda r: received.append(r)

        async with _Session(client, tunnel):
            await client.write_attrs({"human_detect_enable": 1})
            await asyncio.sleep(0)

        assert len(received) == 1


# =============================================================================
# Tests: concurrent listen() + async_send_and_await
# =============================================================================


class TestConcurrentListenAndSend:
    async def test_listen_routes_response_to_sender_and_report_to_callback(self):
        """With listen() running as a background task, a concurrent
        async_send_and_await must resolve on the matching seq even when an
        unrelated async report arrives between the send and the response."""
        tunnel = QueueTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        received: list[LANReport] = []
        client.on_report = lambda r: received.append(r)

        listen_task = asyncio.create_task(client.listen())
        try:
            seq = client.next_seq()
            message = {
                "seq": seq,
                "type": LANLINK_TYPE_SESSION,
                "cmd": LANLINK_CMD_CHECKIN,
                "data": {"user": USER_ID, "did": DEVICE_ID},
            }

            # Schedule the incoming frames in order:
            #   1) an async report (no matching pending seq)
            #   2) the response to our send, matching on seq
            async def feed():
                # Let the send_and_await register its future and call send()
                await asyncio.sleep(0.01)
                tunnel.push(make_report(seq=9999))
                await asyncio.sleep(0.01)
                tunnel.push(make_checkin_done(seq=seq, code=0))

            feeder = asyncio.create_task(feed())

            response = await asyncio.wait_for(
                client.async_send_and_await(message, timeout=1.0),
                timeout=1.0,
            )
            await feeder

            assert response["seq"] == seq
            assert response["cmd"] == LANLINK_CMD_CHECKIN_DONE
            assert response["data"]["code"] == 0

            # Give the listen loop a tick to dispatch the report that
            # arrived before our response.
            await asyncio.sleep(0.01)
            assert len(received) == 1
            assert isinstance(received[0], LANReport)

            # send() was invoked exactly once with our message
            assert tunnel.sent == [message]
        finally:
            tunnel.push_eof()
            await asyncio.wait_for(listen_task, timeout=1.0)

    async def test_concurrent_sends_each_resolve_on_their_own_seq(self):
        """Two concurrent async_send_and_await calls must each resolve on
        their own seq, regardless of the order the responses arrive in."""
        tunnel = QueueTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        listen_task = asyncio.create_task(client.listen())
        try:
            seq_a = client.next_seq()
            seq_b = client.next_seq()
            msg_a = {
                "seq": seq_a,
                "type": LANLINK_TYPE_DEVICE,
                "cmd": LANLINK_CMD_READ,
                "data": {"did": DEVICE_ID, "model": MODEL, "value": ["a"]},
            }
            msg_b = {
                "seq": seq_b,
                "type": LANLINK_TYPE_DEVICE,
                "cmd": LANLINK_CMD_READ,
                "data": {"did": DEVICE_ID, "model": MODEL, "value": ["b"]},
            }

            async def feed():
                # Let both sends register their futures.
                await asyncio.sleep(0.02)
                # Respond to B first, then A -- out of order.
                tunnel.push(make_read_done(seq=seq_b, data={"b": 2}))
                await asyncio.sleep(0.01)
                tunnel.push(make_read_done(seq=seq_a, data={"a": 1}))

            feeder = asyncio.create_task(feed())

            result_a, result_b = await asyncio.gather(
                client.async_send_and_await(msg_a, timeout=1.0),
                client.async_send_and_await(msg_b, timeout=1.0),
            )
            await feeder

            assert result_a["seq"] == seq_a
            assert result_a["data"] == {"a": 1}
            assert result_b["seq"] == seq_b
            assert result_b["data"] == {"b": 2}
        finally:
            tunnel.push_eof()
            await asyncio.wait_for(listen_task, timeout=1.0)

    async def test_async_send_and_await_timeout_cleans_up_pending(self):
        """If no response arrives within timeout, async_send_and_await
        raises TimeoutError and the pending-future slot is freed."""
        tunnel = QueueTunnel()
        client = LANLinkClient(tunnel, DEVICE_ID, USER_ID, MODEL)

        listen_task = asyncio.create_task(client.listen())
        try:
            seq = client.next_seq()
            msg = {
                "seq": seq,
                "type": LANLINK_TYPE_SESSION,
                "cmd": LANLINK_CMD_CHECKIN,
                "data": {"user": USER_ID, "did": DEVICE_ID},
            }
            with pytest.raises(asyncio.TimeoutError):
                await client.async_send_and_await(msg, timeout=0.05)

            # _pending should no longer hold the future for this seq.
            assert seq not in client._pending
        finally:
            tunnel.push_eof()
            await asyncio.wait_for(listen_task, timeout=1.0)
