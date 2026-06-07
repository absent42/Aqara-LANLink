# tests/ptz/test_ptz_session.py
"""Async PtzSession tests - asyncio port of tools/ptz_poc/tests/test_pppp_session.py.

The camera is unreachable here, so these tests drive PtzSession against a
scripted fake transport that plays the role of the real camera: it records every
datagram the session sends and pushes scripted control-packet replies
(f1 43 / f1 44 / f1 e0) - built with the byte-exact-validated build_control() -
onto the session's _RxProtocol.queue.

Oracles reused from the framing tests / handshake notes:
  - HANDSHAKE_WIRE / HANDSHAKE_DID: the real captured f1 41 search packet.
  - LEFT_WIRE etc.: the real outbound PTZ "left" DRW packet.
"""
import asyncio

import pytest

from custom_components.aqara_lanlink.ptz import cs2, framing, ioctl
from custom_components.aqara_lanlink.ptz.session import (
    PptzTimeout,
    PtzSession,
    _RxProtocol,
)

KEY = "aqarager19kn"

CTRL_SEARCH = framing.CTRL_SEARCH
CTRL_REPLY = framing.CTRL_REPLY
CTRL_CONFIRM = framing.CTRL_CONFIRM
CTRL_READY = framing.CTRL_READY
CTRL_KEEPALIVE = framing.CTRL_KEEPALIVE
CTRL_LAN_SEARCH = framing.CTRL_LAN_SEARCH
CTRL_LOGIN = framing.CTRL_LOGIN
LAN_SEARCH_PORT = framing.LAN_SEARCH_PORT

# Real captured f1 41 search (byte-exact oracle, see test_ptz_framing.py).
HANDSHAKE_WIRE = bytes.fromhex(
    "ed1590f366335c1390a313d122d7a89cfbc36e25b8a356bf"
)
HANDSHAKE_DID = bytes.fromhex("415141524144450000057c1f5a5558444c000000")

# Outbound PTZ "left" DRW packet.  The wire is the BARE cs2-encrypted inner.
LEFT_IOCTL_TYPE = 4138
LEFT_IOCTL_ID = 12
LEFT_DRW_SEQ = 0x00000A
LEFT_ACTION = {"action": "left"}

CAMERA_IP = "10.1.20.152"
# The camera answers the LAN-search FROM a DYNAMIC session port -- never one of
# the legacy hardcoded ports.  41234 here proves dynamic-port adoption.
CAMERA_PORT = 41234
BROADCAST_IP = "10.1.20.255"
PORT_RANGE = range(28151, 28158)


def _expected_left_wire(drw_seq, lumi_id):
    """Byte-exact expected wire for a PTZ 'left' DRW with the given seq+id."""
    inner = framing.wrap_ioctl(
        drw_seq, ioctl.build(LEFT_IOCTL_TYPE, LEFT_ACTION, id=lumi_id)
    )
    return cs2.encrypt(KEY, inner)


def _safe_type(wire):
    """parse_control type for a wire, or None if it doesn't decrypt to f1."""
    try:
        return framing.parse_control(KEY, wire)[0]
    except ValueError:
        return None


def _is_drw(wire):
    """True if wire decrypts to a bare f1 d0 DRW IOCTL packet."""
    try:
        inner = framing.deframe_drw(KEY, wire)
    except (ValueError, AssertionError):
        return False
    return len(inner) >= 2 and inner[0] == 0xF1 and inner[1] == 0xD0


def _reply_packets():
    """Scripted camera replies, built with the validated build_control()."""
    f1_43 = framing.build_control(KEY, framing.CTRL_REPLY, HANDSHAKE_DID + bytes(24))
    f1_44 = framing.build_control(KEY, framing.CTRL_CONFIRM, HANDSHAKE_DID)
    f1_e0 = framing.build_control(KEY, framing.CTRL_READY, b"")
    return f1_43, f1_44, f1_e0


# ---------------------------------------------------------------------------
# Task 3.1: receive plumbing
# ---------------------------------------------------------------------------
async def test_rx_protocol_queues_datagrams():
    proto = _RxProtocol()
    proto.datagram_received(b"\x01\x02", ("10.0.0.1", 5000))
    got = await asyncio.wait_for(proto.queue.get(), timeout=1.0)
    assert got == (b"\x01\x02", ("10.0.0.1", 5000))


# ---------------------------------------------------------------------------
# Scripted fake transport (asyncio port of ScriptedCameraSocket)
# ---------------------------------------------------------------------------
class ScriptedCameraTransport:
    """An asyncio DatagramTransport stand-in modelling the real handshake.

    Async port of the PoC's ScriptedCameraSocket. Instead of queueing replies on
    an internal list popped by recvfrom(), sendto() pushes scripted replies
    directly onto the session's _RxProtocol.queue (so the session's
    `await queue.get()` returns them). The reply rules are identical:

      1. Broadcast LAN-search (f1 30 -> :32108) -> reply f1 41 (OUR DID),
         FROM the dynamic session port.  This is what discovery adopts.
      2. The client must MIRROR f1 41 (our DID) >= mirrors_required times.
         Until then the camera only re-sends f1 41.
      3. After enough mirrors -> the camera sends f1 43 (reply) then f1 44.
      4. On the client's f1 e0 the camera replies with f1 e0 (unless
         ready_reply=False).
    """

    def __init__(self, proto, mirrors_required=2, ready_reply=True,
                 peer=(CAMERA_IP, CAMERA_PORT)):
        self._proto = proto
        self.sent = []          # list of (wire, addr)
        self.closed = False
        self._peer = peer
        self._mirrors_required = mirrors_required
        self._ready_reply = ready_reply
        self._mirror_count = 0          # client f1 41 (our DID) seen
        self._reply_sent = False        # f1 43 + f1 44 emitted
        self.f1_43, self.f1_44, self.f1_e0 = _reply_packets()
        self._our_hello = framing.build_control(KEY, CTRL_SEARCH, HANDSHAKE_DID)
        self._reply_to_drw = None       # used in Task 3.3

    # -- DatagramTransport surface used by the session ----------------------
    def sendto(self, wire, addr=None):
        self.sent.append((wire, addr))
        # A DRW IOCTL decrypts to a bare f1 d0 inner; answer it if scripted.
        if _is_drw(wire):
            if self._reply_to_drw is not None:
                self._push(self._reply_to_drw)
                self._reply_to_drw = None
            return
        try:
            typ, payload = framing.parse_control(KEY, wire)
        except ValueError:
            return
        dest_port = addr[1] if addr else None

        if typ == CTRL_LAN_SEARCH and dest_port == LAN_SEARCH_PORT:
            self._push(self._our_hello)
        elif typ == CTRL_SEARCH and payload == HANDSHAKE_DID:
            self._mirror_count += 1
            if self._mirror_count >= self._mirrors_required and not self._reply_sent:
                self._reply_sent = True
                self._push(self.f1_43)
                self._push(self.f1_44)
            else:
                self._push(self._our_hello)
        elif typ == CTRL_READY and self._ready_reply:
            self._push(self.f1_e0)

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    # -- test helpers -------------------------------------------------------
    def _push(self, wire):
        self._proto.queue.put_nowait((wire, self._peer))

    def queue_inbound(self, wire, addr=None):
        self._proto.queue.put_nowait((wire, addr or self._peer))

    def reply_to_next_drw(self, ioctl_bytes, drw_seq=0x000001):
        self._reply_to_drw = framing.build_ioctl_packet(KEY, drw_seq, ioctl_bytes)


def _make_session(transport_cls=ScriptedCameraTransport, *, session_kwargs=None,
                  **transport_kwargs):
    """Build a PtzSession wired to a scripted fake transport.

    The transport_factory closes over a transport instance that shares the
    session's _RxProtocol so scripted replies land on the session's queue.
    """
    holder = {}

    def factory(proto):
        t = transport_cls(proto, **transport_kwargs)
        holder["transport"] = t
        return t

    kwargs = {
        "transport_factory": factory,
        "keepalive_interval": 0,  # no keepalive task in tests unless asked
        "port_range": PORT_RANGE,
    }
    kwargs.update(session_kwargs or {})
    sess = PtzSession(KEY, HANDSHAKE_DID, CAMERA_IP, **kwargs)
    return sess, holder


# ---------------------------------------------------------------------------
# Task 3.2: connect(): discover, adopt dynamic port, mirror, ready
# ---------------------------------------------------------------------------
async def test_connect_completes_against_scripted_camera():
    sess, holder = _make_session()
    await sess.connect(timeout=2.0)
    assert sess.ready is True
    assert sess.peer == (CAMERA_IP, CAMERA_PORT)  # dynamic adopted port


async def test_connect_broadcasts_lan_search_to_32108():
    sess, holder = _make_session()
    await sess.connect(timeout=2.0)
    transport = holder["transport"]

    lan_search = framing.build_control(KEY, 0x30, b"")
    assert lan_search == bytes.fromhex("ed6460f2")  # captured oracle

    bcast_sends = [
        (w, a) for (w, a) in transport.sent
        if a and a[1] == LAN_SEARCH_PORT and w == lan_search
    ]
    dests = {a for _w, a in bcast_sends}
    assert (BROADCAST_IP, LAN_SEARCH_PORT) in dests
    assert ("255.255.255.255", LAN_SEARCH_PORT) in dests
    assert (CAMERA_IP, LAN_SEARCH_PORT) in dests


async def test_connect_still_sprays_legacy_f1_41_fallback_byte_exact():
    sess, holder = _make_session()
    await sess.connect(timeout=2.0)
    transport = holder["transport"]

    legacy_sends = [
        (w, a) for (w, a) in transport.sent
        if a in {(CAMERA_IP, p) for p in PORT_RANGE}
    ]
    assert {a for _w, a in legacy_sends} == {(CAMERA_IP, p) for p in PORT_RANGE}
    for wire, _addr in legacy_sends:
        assert wire == HANDSHAKE_WIRE


async def test_connect_adopts_dynamic_session_port_from_lan_search_reply():
    assert CAMERA_PORT not in PORT_RANGE  # guard: truly dynamic, not hardcoded
    sess, _holder = _make_session()
    await sess.connect(timeout=2.0)
    assert sess.peer == (CAMERA_IP, CAMERA_PORT)  # (10.1.20.152, 41234)
    assert sess.peer[1] not in PORT_RANGE
    assert sess.ready is True


async def test_connect_sends_f1_e0_ready_to_adopted_peer():
    sess, holder = _make_session()
    await sess.connect(timeout=2.0)
    transport = holder["transport"]
    ready_sends = [
        (w, a) for (w, a) in transport.sent
        if _safe_type(w) == CTRL_READY
    ]
    assert len(ready_sends) == 1
    wire, addr = ready_sends[0]
    assert addr == (CAMERA_IP, CAMERA_PORT)
    assert wire == framing.build_control(KEY, CTRL_READY, b"")


async def test_connect_mirrors_f1_41_until_reply_then_sends_f1_e0():
    """The ORDERING gate: mirror f1 41 >= twice before f1 43/f1 44, and the
    client must not send f1 e0 before receiving f1 44."""
    sess, holder = _make_session(mirrors_required=2, ready_reply=True)
    await sess.connect(timeout=2.0)
    transport = holder["transport"]

    assert sess.ready is True

    mirror_sends = [
        (w, a) for (w, a) in transport.sent
        if a == (CAMERA_IP, CAMERA_PORT) and _safe_type(w) == CTRL_SEARCH
    ]
    assert len(mirror_sends) >= 2, "client must mirror f1 41 at least twice"

    first_e0_idx = next(
        i for i, (w, a) in enumerate(transport.sent)
        if a == (CAMERA_IP, CAMERA_PORT) and _safe_type(w) == CTRL_READY
    )
    assert transport._reply_sent is True
    mirrors_before_e0 = [
        1 for (w, a) in transport.sent[:first_e0_idx]
        if a == (CAMERA_IP, CAMERA_PORT) and _safe_type(w) == CTRL_SEARCH
    ]
    assert len(mirrors_before_e0) >= 2


async def test_connect_no_premature_f1_e0_without_f1_43_and_f1_44():
    """If the camera never sends f1 43 / f1 44, the client must NOT send f1 e0
    and must time out."""
    sess, holder = _make_session(mirrors_required=10_000_000, ready_reply=True)
    with pytest.raises(PptzTimeout):
        await sess.connect(timeout=0.3)
    transport = holder["transport"]
    e0_sends = [w for (w, _a) in transport.sent if _safe_type(w) == CTRL_READY]
    assert e0_sends == []  # no premature ready
    assert sess.ready is False


async def test_connect_ignores_datagrams_from_other_hosts():
    """A datagram from a DIFFERENT host must not be adopted as the camera peer."""
    sess, holder = _make_session(mirrors_required=2, ready_reply=True)
    # Pre-seed the proto queue with a datagram from some OTHER host.
    sess._proto.queue.put_nowait((b"\x00\x01\x02garbage", ("10.1.20.7", 9999)))
    await sess.connect(timeout=2.0)
    assert sess.peer == (CAMERA_IP, CAMERA_PORT)


async def test_connect_does_not_adopt_unparseable_datagram_from_camera_ip():
    """A spoofed/garbage datagram from the camera's IP (bogus source port, not
    a valid CS2 control packet) must NOT be latched as the session peer. Source
    IP is forgeable on a LAN, so adoption must require a well-formed reply; the
    real camera reply on its dynamic port is adopted instead."""
    sess, _holder = _make_session(mirrors_required=2, ready_reply=True)
    # Pre-seed a garbage datagram from the camera IP but a bogus source port,
    # racing ahead of the genuine reply.
    sess._proto.queue.put_nowait((b"\xde\xad\xbe\xefgarbage", (CAMERA_IP, 6666)))
    await sess.connect(timeout=2.0)
    assert sess.peer == (CAMERA_IP, CAMERA_PORT)
    assert sess.peer[1] != 6666


async def test_connect_timeout_when_no_reply():
    """A camera that never answers the LAN-search -> connect() times out."""
    sess, _holder = _make_session(SilentCameraTransport)
    with pytest.raises(PptzTimeout):
        await sess.connect(timeout=0.3)
    assert sess.ready is False


async def test_connect_timeout_when_no_ready_reply():
    """Mirror gate satisfied (f1 43 + f1 44) but camera never answers our f1 e0
    -> connect() must time out (expect_ready_reply defaults to True)."""
    sess, _holder = _make_session(mirrors_required=2, ready_reply=False)
    with pytest.raises(PptzTimeout):
        await sess.connect(timeout=0.6)


async def test_connect_expect_ready_reply_false_does_not_wait():
    """With expect_ready_reply=False, a camera that never sends f1 e0 still
    readies once the mirror gate is satisfied and our f1 e0 is sent."""
    sess, holder = _make_session(
        mirrors_required=2, ready_reply=False,
        session_kwargs={"expect_ready_reply": False},
    )
    await sess.connect(timeout=2.0)
    assert sess.ready is True
    transport = holder["transport"]
    e0_sends = [w for (w, _a) in transport.sent if _safe_type(w) == CTRL_READY]
    assert len(e0_sends) == 1


async def test_connect_failure_closes_transport_and_resets_state():
    """A camera that never replies -> connect() raises PptzTimeout AND the
    just-opened transport is closed (no leaked UDP endpoint), with session state
    reset clean for a subsequent connect()."""
    sess, holder = _make_session(SilentCameraTransport)
    with pytest.raises(PptzTimeout):
        await sess.connect(timeout=0.2)
    transport = holder["transport"]
    assert transport.closed is True  # transport.close() was called on failure
    assert sess._transport is None   # reset clean for a retry
    assert sess.ready is False


class SilentCameraTransport:
    """A transport that records sends but never replies (models a dead camera)."""

    def __init__(self, proto):
        self._proto = proto
        self.sent = []
        self.closed = False

    def sendto(self, wire, addr=None):
        self.sent.append((wire, addr))

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed


# ---------------------------------------------------------------------------
# Camera-IP normalisation (numeric literal pass-through; hostname handling)
# ---------------------------------------------------------------------------
async def test_open_transport_binds_concrete_wildcard_not_empty_host(monkeypatch):
    """Regression: _open_transport must bind local_addr=('0.0.0.0', 0), NOT
    ('', 0).

    asyncio's create_datagram_endpoint resolves local_addr via getaddrinfo, and
    an empty host raises 'gaierror: [Errno -2] Name or service not known' on many
    systems (whereas the sync PoC's socket.bind(('', 0)) binds directly with no
    lookup). That empty host broke PTZ on real installs -- and every other
    session test injects a fake transport, bypassing this real code path, so the
    bug shipped undetected. We assert the concrete-wildcard local_addr is used,
    intercepting create_datagram_endpoint so no real socket is opened (the suite
    blocks real sockets via pytest-socket).
    """
    captured = {}

    class _FakeTransport:
        def close(self):
            pass

    async def _fake_create(proto_factory, *, local_addr=None,
                           allow_broadcast=None, **_kw):
        captured["local_addr"] = local_addr
        captured["allow_broadcast"] = allow_broadcast
        return _FakeTransport(), proto_factory()

    sess = PtzSession(
        KEY, HANDSHAKE_DID, "10.1.20.152",
        keepalive_interval=0, port_range=PORT_RANGE,
    )  # no transport_factory -> exercises the real create_datagram_endpoint call
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", _fake_create)
    await sess._open_transport()
    assert captured["local_addr"] == ("0.0.0.0", 0)  # NOT ("", 0)
    assert captured["allow_broadcast"] is True


async def test_resolve_camera_ip_passes_through_numeric_literal():
    """A numeric IPv4 is used as-is -- no DNS, broadcast unchanged."""
    sess = PtzSession(
        KEY, HANDSHAKE_DID, "10.1.20.152",
        keepalive_interval=0, port_range=PORT_RANGE,
    )
    await sess._resolve_camera_ip()
    assert sess.camera_ip == "10.1.20.152"
    assert sess.broadcast_addr == "10.1.20.255"


async def test_connect_unresolvable_host_raises_actionable_error():
    """A non-numeric, unresolvable camera address (e.g. a relay hostname or a
    misconfigured value) fails with a clear, actionable PptzTimeout pointing at
    the options field -- NOT a cryptic '[Errno -2] Name or service not known' --
    and the just-opened transport is closed."""
    holder = {}

    def factory(proto):
        t = SilentCameraTransport(proto)
        holder["t"] = t
        return t

    # The .invalid TLD (RFC 6761) is guaranteed never to resolve.
    sess = PtzSession(
        KEY, HANDSHAKE_DID, "aqara-camera.invalid",
        transport_factory=factory, keepalive_interval=0, port_range=PORT_RANGE,
    )
    with pytest.raises(PptzTimeout, match="set the camera's LAN IP"):
        await sess.connect(timeout=0.5)
    assert holder["t"].closed is True
    assert sess._transport is None
    assert sess.ready is False


# ---------------------------------------------------------------------------
# Task 3.3: send_ioctl + recv_ioctl + keepalive + close
# ---------------------------------------------------------------------------
async def _ready_session(**transport_kwargs):
    """Connect a session against the scripted camera and return (sess, transport)."""
    sess, holder = _make_session(**transport_kwargs)
    await sess.connect(timeout=2.0)
    return sess, holder["transport"]


async def test_send_ioctl_then_recv_reply():
    sess, transport = await _ready_session()
    # Script the camera to answer the next DRW with an AUTH 4097 ok.
    transport.reply_to_next_drw(
        ioctl.build(4097, {"code": 0, "message": "success"}, id=1)
    )
    lumi_id = await sess.send_ioctl(4096, {"app_public_key": "aa"})
    assert lumi_id == 1  # _lumi_id starts at 1
    reply = await sess.recv_ioctl(timeout=1.0)
    assert reply == (4097, 1, {"code": 0, "message": "success"})


async def test_send_ioctl_byte_exact_real_left_packet():
    """With id=12 and drw-seq=0x0A, send_ioctl reproduces the bare-cs2 wire for
    a PTZ 'left' DRW (inner header byte-exact from the capture)."""
    sess, transport = await _ready_session()
    sess._lumi_id = LEFT_IOCTL_ID
    sess._drw_seq = LEFT_DRW_SEQ
    before = len(transport.sent)
    used_id = await sess.send_ioctl(LEFT_IOCTL_TYPE, LEFT_ACTION)

    assert used_id == LEFT_IOCTL_ID
    wire, addr = transport.sent[before]
    assert addr == (CAMERA_IP, CAMERA_PORT)
    assert wire == _expected_left_wire(LEFT_DRW_SEQ, LEFT_IOCTL_ID)
    inner = framing.deframe_drw(KEY, wire)
    assert inner[:5] == bytes.fromhex("f1d00025d1")
    assert inner[8:16] == bytes.fromhex("6c756d692a100000")


async def test_send_ioctl_increments_id_and_drw_seq_independently():
    sess, transport = await _ready_session()
    sess._lumi_id = 100
    sess._drw_seq = 5

    id1 = await sess.send_ioctl(4138, {"action": "up"})
    id2 = await sess.send_ioctl(4138, {"action": "down"})
    assert (id1, id2) == (100, 101)

    def decode(wire):
        inner = framing.deframe_drw(KEY, wire)
        seq, ib = framing.unwrap_ioctl(inner)
        iotype, _id, body = ioctl.parse(ib)
        return seq, iotype, _id, body

    seq1, _t1, lid1, _b1 = decode(transport.sent[-2][0])
    seq2, _t2, lid2, _b2 = decode(transport.sent[-1][0])
    assert (lid1, lid2) == (100, 101)
    assert (seq1, seq2) == (5, 6)


async def test_send_ioctl_before_connect_raises():
    sess, _holder = _make_session()
    with pytest.raises(RuntimeError):
        await sess.send_ioctl(4138, {"action": "left"})


async def test_recv_ioctl_returns_drw_and_skips_control():
    sess, transport = await _ready_session()
    # Queue a keepalive (control, must be skipped) then a real DRW reply.
    ka = framing.build_control(KEY, CTRL_KEEPALIVE, b"")
    auth_ok = ioctl.build(4097, {"code": 0, "message": "success"}, id=1)
    drw = framing.build_ioctl_packet(KEY, 0x000001, auth_ok)
    transport.queue_inbound(ka)
    transport.queue_inbound(drw)

    result = await sess.recv_ioctl(timeout=1.0)
    assert result is not None
    iotype, _id, body = result
    assert iotype == 4097
    assert body == {"code": 0, "message": "success"}


async def test_recv_ioctl_times_out_to_none():
    sess, _transport = await _ready_session()
    assert await sess.recv_ioctl(timeout=0.1) is None


async def test_keepalive_task_sends_f1_30_then_close_cancels_it():
    sess, holder = _make_session(session_kwargs={"keepalive_interval": 0.02})
    await sess.connect(timeout=2.0)
    transport = holder["transport"]
    # Let the keepalive task fire a few times.
    await asyncio.sleep(0.1)
    await sess.close()

    ka_sends = [
        w for (w, _a) in transport.sent if _safe_type(w) == CTRL_KEEPALIVE
    ]
    assert len(ka_sends) >= 1
    assert all(
        w == framing.build_control(KEY, CTRL_KEEPALIVE, b"") for w in ka_sends
    )
    # The keepalive task must be cancelled and cleared after close().
    assert sess._keepalive_task is None
    assert sess.ready is False


async def test_close_marks_not_ready_and_closes_transport():
    sess, transport = await _ready_session()
    assert sess.ready is True
    await sess.close()
    assert sess.ready is False
    assert transport.closed is True
