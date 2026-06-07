"""Async PPPP/CS2 P2P session - asyncio port of tools/ptz_poc/pppp.py.

Same wire state machine as the validated PoC: broadcast discovery to :32108,
adopt the camera's dynamic reply port, mirror f1 41 until f1 43 (+f1 42/f1 44 or
f1 e0 flood), send f1 e0 ready, then AUTH(4096)->4097. Datagrams are bare cs2.

Only the I/O model differs from the sync PoC: the blocking socket + keepalive
thread become an asyncio DatagramProtocol + asyncio tasks. The wire bytes and
state transitions are identical.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket

from . import framing, ioctl

_LOGGER = logging.getLogger(__name__)

# Control packet types re-exported from framing for readability.
CTRL_SEARCH = framing.CTRL_SEARCH
CTRL_REPLY = framing.CTRL_REPLY
CTRL_CONFIRM = framing.CTRL_CONFIRM
CTRL_CONFIRM_ALT = framing.CTRL_CONFIRM_ALT
CTRL_LOGIN = framing.CTRL_LOGIN
CTRL_READY = framing.CTRL_READY
CTRL_KEEPALIVE = framing.CTRL_KEEPALIVE
CTRL_LAN_SEARCH = framing.CTRL_LAN_SEARCH
LAN_SEARCH_PORT = framing.LAN_SEARCH_PORT

_MAGIC = 0xF1
_DRW_INNER_TYPE = 0xD0

_DEFAULT_PORT_RANGE = range(28151, 28158)  # legacy secondary fallback only
_RESEND_INTERVAL = 0.3  # seconds between discovery / hello re-sends
_RECV_SLICE = 0.3       # per-iteration receive timeout during handshake


def _subnet_broadcast_24(ip: str) -> str:
    """Derive the /24 subnet broadcast for an IPv4 address (x.y.z.w -> x.y.z.255)."""
    parts = ip.split(".")
    if len(parts) != 4:
        return "255.255.255.255"
    return ".".join([*parts[:3], "255"])


class PptzTimeout(Exception):
    """Raised when the camera never replies or the session never reaches ready."""


class _RxProtocol(asyncio.DatagramProtocol):
    """Pushes every received datagram onto an asyncio.Queue of (data, addr)."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait((data, addr))

    def error_received(self, exc) -> None:  # pragma: no cover - transport noise
        _LOGGER.debug("ptz session transport error: %s", exc)


class PtzSession:
    """LAN-only async PPPP/CS2 session driver for an Aqara camera.

    Asyncio port of tools/ptz_poc/pppp.py PPPPSession. Owns the UDP datagram
    endpoint (an _RxProtocol pushing onto a queue) and the two independent
    sequence counters; reuses the framing helpers for every packet. The
    handshake byte layouts and state transitions are identical to the sync PoC;
    only the I/O model differs (asyncio.wait_for(queue.get()) instead of a
    blocking recvfrom with a socket timeout, and an asyncio keepalive task
    instead of a thread).

    Handshake sequence reproduced (LAN-only):

        phone -> cam : f1 30 LAN-search       broadcast to :32108
        cam  -> phone: f1 41 (our DID)        FROM the dynamic session port
        phone -> cam : f1 41 mirror           until f1 43 + f1 44 (or f1 e0)
        cam  -> phone: f1 43 / f1 44          reply / confirm
        phone <-> cam: f1 e0                  session-ready handshake
        phone -> cam : f1 30 keepalive        periodic, on a background task
        [ session ready -> DRW IOCTLs flow via send_ioctl / recv_ioctl ]
    """

    def __init__(
        self,
        key: str,
        did_blob: bytes,
        camera_ip: str,
        *,
        port_range=_DEFAULT_PORT_RANGE,
        broadcast_addr: str | None = None,
        send_login: bool = False,
        login_payload: bytes | None = None,
        expect_ready_reply: bool = True,
        keepalive_interval: float = 1.0,
        transport_factory=None,
    ) -> None:
        self.key = key
        self.did_blob = bytes(did_blob)
        self.camera_ip = camera_ip
        self.port_range = port_range
        self.broadcast_addr = (
            broadcast_addr if broadcast_addr is not None
            else _subnet_broadcast_24(camera_ip)
        )
        self.send_login = send_login
        self.login_payload = self.did_blob if login_payload is None else login_payload
        self.expect_ready_reply = expect_ready_reply
        self.keepalive_interval = keepalive_interval
        # transport_factory(proto) -> transport; lets tests inject a scripted
        # fake transport. Defaults to a real broadcast-capable UDP endpoint.
        self._transport_factory = transport_factory

        # Adopted session peer (ip, port); set during connect().
        self.peer: tuple[str, int] | None = None
        self.ready = False
        # Trailing session bytes from the f1 43 reply (after the 20-byte DID).
        self.session_info: bytes = b""

        # Two counters:
        #   _lumi_id -> lumi IOCTL `id` field (build_ioctl); starts at 1.
        #   _drw_seq -> DRW sequence in the inner d1 <seq:3> header; the first
        #               DRW of a session is seq 0 (captured: AUTH=0, then PTZ).
        self._lumi_id = 1
        self._drw_seq = 0

        self._proto = _RxProtocol()
        self._transport = None
        self._keepalive_task: asyncio.Task | None = None

    # -- raw datagram I/O ---------------------------------------------------
    async def _open_transport(self) -> None:
        """Create the datagram endpoint (or the injected fake transport)."""
        if self._transport_factory is not None:
            self._transport = self._transport_factory(self._proto)
            return
        loop = asyncio.get_running_loop()
        # Bind to the concrete IPv4 wildcard, NOT ("", 0): asyncio's
        # create_datagram_endpoint resolves local_addr via getaddrinfo, and an
        # empty host fails with "[Errno -2] Name or service not known" on many
        # systems (whereas the sync PoC's socket.bind(("", 0)) binds directly
        # with no lookup). Using 0.0.0.0 binds the wildcard without a DNS call.
        transport, _proto = await loop.create_datagram_endpoint(
            lambda: self._proto,
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )
        self._transport = transport

    async def _recv_slice(self, remaining: float):
        """Await the next datagram, or return None on a per-slice timeout.

        Mirrors the sync PoC's `settimeout(min(_RECV_SLICE, remaining))` +
        recvfrom raising socket.timeout: we wait at most one slice, returning
        None so the caller can re-broadcast and keep waiting until the deadline.
        """
        try:
            return await asyncio.wait_for(
                self._proto.queue.get(), min(_RECV_SLICE, remaining)
            )
        except (asyncio.TimeoutError, TimeoutError):
            return None

    async def _resolve_camera_ip(self) -> None:
        """Ensure ``self.camera_ip`` is a numeric IPv4 (resolve a hostname).

        Discovery broadcasts to the /24 subnet of ``camera_ip`` AND adopts the
        peer by comparing each reply's source address to ``camera_ip``, so a
        hostname (or a cloud-relay name) can never work -- the camera's numeric
        LAN IP is required. Accept an IPv4 literal as-is; resolve a hostname via
        getaddrinfo; otherwise raise a clear, actionable error rather than the
        cryptic ``[Errno -2] Name or service not known`` from a later sendto.
        """
        host = self.camera_ip
        try:
            ipaddress.IPv4Address(host)
            return  # already a numeric IPv4 literal -- nothing to do
        except (ipaddress.AddressValueError, ValueError):
            pass
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(
                host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM,
            )
        except OSError as exc:
            raise PptzTimeout(
                f"camera address {host!r} is not a LAN IP and could not be "
                f"resolved ({exc}); set the camera's LAN IP in the integration "
                f"options"
            ) from exc
        if not infos:
            raise PptzTimeout(
                f"camera address {host!r} did not resolve to an IPv4 address; "
                f"set the camera's LAN IP in the integration options"
            )
        resolved = infos[0][4][0]
        _LOGGER.debug("PTZ resolved camera host %r -> %s", host, resolved)
        self.camera_ip = resolved
        # The constructor derived broadcast_addr from the unresolved host;
        # recompute it from the resolved numeric IP.
        self.broadcast_addr = _subnet_broadcast_24(resolved)

    # -- connect / handshake ------------------------------------------------
    async def connect(self, timeout: float = 5.0) -> None:
        """Discover the camera via a broadcast LAN-search, adopt its dynamic
        session port, then drive the f1 e0 ready exchange.

        Raises PptzTimeout only if NO datagram from the camera arrives within
        `timeout`, or if the f1 e0 ready exchange is not completed in time.
        """
        await self._open_transport()

        # From here on the transport is open; if discovery/handshake/ready
        # raises (e.g. PptzTimeout on an offline camera), close the just-opened
        # transport before propagating so we never leak the UDP endpoint. No
        # keepalive task exists yet, so a plain close + reset is correct here.
        try:
            # Normalise the camera address to a numeric IPv4 first (the
            # broadcast-subnet derivation and peer-adoption both require it).
            await self._resolve_camera_ip()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout

            # Discover + adopt the camera's dynamic session port.
            self.peer = await self._discover_peer(deadline)
            _LOGGER.debug("PTZ session: adopted peer %s", self.peer)

            # Drive the handshake loop: mirror our f1 41 hello, ack other
            # devices' hellos, collect f1 43 (reply) + f1 44 (confirm) (or an
            # f1 e0 flood), then do the f1 e0 ready exchange.
            await self._do_handshake(deadline)
        except BaseException:
            self._close_transport()
            raise

        self.ready = True
        _LOGGER.debug("PTZ session: ready (peer=%s)", self.peer)
        self._start_keepalive()

    async def _discover_peer(self, deadline: float) -> tuple[str, int]:
        """Broadcast the LAN-search until the camera replies; adopt (ip, src_port).

        Each iteration: send the LAN-search to the three :32108 targets, spray
        the legacy f1 41 across port_range (secondary fallback), then await a
        datagram with a short slice timeout. The FIRST datagram from camera_ip
        (ANY source port) is adopted as the session peer.
        """
        loop = asyncio.get_running_loop()
        lan_search = framing.build_control(self.key, CTRL_LAN_SEARCH, b"")
        legacy_search = framing.build_control(self.key, CTRL_SEARCH, self.did_blob)
        bcast_targets = [
            (self.broadcast_addr, LAN_SEARCH_PORT),
            ("255.255.255.255", LAN_SEARCH_PORT),
            (self.camera_ip, LAN_SEARCH_PORT),
        ]

        next_send = 0.0  # send immediately on the first iteration
        while True:
            now = loop.time()
            remaining = deadline - now
            if remaining <= 0:
                raise PptzTimeout("no camera reply to LAN-search broadcast")

            if now >= next_send:
                for target in bcast_targets:
                    self._transport.sendto(lan_search, target)
                # Secondary fallback: legacy unicast f1 41 spray.
                for port in self.port_range:
                    self._transport.sendto(legacy_search, (self.camera_ip, port))
                next_send = now + _RESEND_INTERVAL

            got = await self._recv_slice(remaining)
            if got is None:
                continue  # nothing this slice; re-broadcast and keep waiting
            _wire, addr = got
            # Adopt the camera's reply as the session peer, but only if the
            # datagram both comes from the camera IP AND decrypts to a
            # well-formed CS2 control packet. Source IP/port are forgeable on a
            # LAN, so a bare IP match would let a spoofed or garbage datagram
            # racing the real reply be latched as the peer -- and then receive
            # our auth IOCTL. Requiring parse_control to succeed is the same
            # gate the handshake loop applies. The camera answers from its
            # dynamic session port, so the validated (ip, port) IS the peer.
            if not addr or addr[0] != self.camera_ip:
                continue  # different host -> ignore and keep searching
            try:
                framing.parse_control(self.key, _wire)
            except ValueError:
                continue  # not a valid control reply -> keep searching
            return addr

    async def _do_handshake(self, deadline: float) -> None:
        """Run the post-adoption handshake until ready or the deadline.

        Reacts to inbound control packets by type (decrypting with
        parse_control; ignoring undecryptable / non-f1 datagrams):

          - IN f1 41 carrying OUR did_blob -> mirror our f1 41 hello.
          - IN f1 41 for another device (or stray) -> best-effort f1 30 ack.
          - IN f1 43 -> got_reply=True (stash trailing session bytes).
          - IN f1 44 / f1 42 -> got_confirm=True.
          - IN f1 e0 -> cam_ready=True (camera floods f1 e0).

        Once got_reply AND (got_confirm OR cam_ready), transition to ready.
        Re-sends our mirror f1 41 every ~_RESEND_INTERVAL.
        """
        loop = asyncio.get_running_loop()
        hello = framing.build_control(self.key, CTRL_SEARCH, self.did_blob)
        ack = framing.build_control(self.key, CTRL_KEEPALIVE, b"")

        got_reply = False
        got_confirm = False
        cam_ready = False
        self.session_info = b""
        next_hello = 0.0  # send a hello immediately on the first iteration

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PptzTimeout(
                    "session never reached ready "
                    f"(got_reply={got_reply}, got_confirm={got_confirm})"
                )

            now = loop.time()
            if now >= next_hello:
                self._transport.sendto(hello, self.peer)
                next_hello = now + _RESEND_INTERVAL

            got = await self._recv_slice(remaining)
            if got is None:
                continue
            wire, _addr = got
            try:
                typ, payload = framing.parse_control(self.key, wire)
            except ValueError:
                continue  # undecryptable / non-f1 -> ignore

            r, c, ready = self._apply_control_packet(typ, payload, hello, ack)
            got_reply = got_reply or r
            got_confirm = got_confirm or c
            cam_ready = cam_ready or ready

            if got_reply and (got_confirm or cam_ready):
                await self._do_ready(deadline, cam_ready)
                return

    def _apply_control_packet(
        self, typ: int, payload: bytes, hello: bytes, ack: bytes,
    ) -> tuple[bool, bool, bool]:
        """React to one decrypted handshake control packet.

        Sends the mirror hello / best-effort ack and stashes trailing session
        bytes as side effects; returns ``(got_reply, got_confirm, cam_ready)``
        flag deltas to OR into the handshake loop's state.
        """
        if typ == CTRL_SEARCH:
            if payload == self.did_blob:
                # Our own hello echoed back -> mirror it to keep progressing.
                self._transport.sendto(hello, self.peer)
            else:
                # Some other device's hello -> best-effort ack (low prio).
                self._transport.sendto(ack, self.peer)
            return False, False, False
        if typ == CTRL_REPLY:
            # Stash any trailing session bytes after the 20-byte DID.
            if len(payload) > len(self.did_blob):
                self.session_info = payload[len(self.did_blob):]
            return True, False, False
        if typ in (CTRL_CONFIRM, CTRL_CONFIRM_ALT):
            return False, True, False
        if typ == CTRL_READY:
            # Camera announces readiness directly (it floods f1 e0).
            return False, False, True
        return False, False, False

    async def _do_ready(self, deadline: float, cam_ready: bool = False) -> None:
        """Send login (if enabled), our f1 e0, and optionally await an f1 e0.

        If the camera already announced readiness (`cam_ready`), we consider the
        session ready as soon as we send ours -- no need to wait.
        """
        loop = asyncio.get_running_loop()
        if self.send_login:
            # The real f1 f9 (88B) login is nested-encrypted and not yet
            # constructable; send placeholder hellos so the ordering is right.
            _LOGGER.warning(
                "ptz login is a STUB (f1 f9 not built); sending f1 41 placeholders"
            )
            self._transport.sendto(
                framing.build_control(self.key, CTRL_SEARCH, self.did_blob), self.peer
            )

        self._transport.sendto(framing.build_control(self.key, CTRL_READY, b""), self.peer)

        if not self.expect_ready_reply or cam_ready:
            return
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PptzTimeout("session never reached ready (no f1 e0 reply)")
            got = await self._recv_slice(remaining)
            if got is None:
                continue
            wire, _addr = got
            try:
                typ, _payload = framing.parse_control(self.key, wire)
            except ValueError:
                continue
            if typ == CTRL_READY:
                return
            # f1 43/f1 44 retransmits or keepalives -> keep waiting.

    # -- keepalive ----------------------------------------------------------
    def _start_keepalive(self) -> None:
        if self.keepalive_interval <= 0:
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        ka = framing.build_control(self.key, CTRL_KEEPALIVE, b"")
        try:
            while True:
                await asyncio.sleep(self.keepalive_interval)
                if self.peer is None or self._transport is None:
                    continue
                self._transport.sendto(ka, self.peer)
        except asyncio.CancelledError:
            raise
        except OSError:  # pragma: no cover - transport closed underneath us
            return

    # -- IOCTL data ---------------------------------------------------------
    async def send_ioctl(self, iotype: int, body) -> int:
        """Build a lumi IOCTL, wrap as a DRW, cs2-encrypt, send to the peer.

        Increments the lumi `id` and the DRW seq independently; the DRW seq is a
        monotonic per-DRW counter starting at 0. Returns the lumi `id` used.
        """
        if self.peer is None:
            raise RuntimeError("send_ioctl before connect(): no peer adopted")
        lumi_id = self._lumi_id
        drw_seq = self._drw_seq
        self._lumi_id += 1
        self._drw_seq += 1

        ioctl_bytes = ioctl.build(iotype, body, id=lumi_id)
        wire = framing.build_ioctl_packet(self.key, drw_seq, ioctl_bytes)
        self._transport.sendto(wire, self.peer)
        _LOGGER.debug("PTZ tx: iotype=%s id=%s drw_seq=%s -> %s",
                      iotype, lumi_id, drw_seq, self.peer)
        return lumi_id

    async def recv_ioctl(self, timeout: float):
        """Return the next DRW IOCTL as (iotype, id, body), or None on timeout.

        Control packets (ack/keepalive/ready/search) are consumed silently; only
        a real DRW (f1 d0) IOCTL is returned.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                got = await asyncio.wait_for(self._proto.queue.get(), remaining)
            except (asyncio.TimeoutError, TimeoutError):
                return None
            wire, _addr = got
            try:
                inner = framing.deframe_drw(self.key, wire)
            except (ValueError, AssertionError):
                _LOGGER.debug("PTZ rx: undecryptable datagram from %s (%dB)",
                              _addr, len(wire))
                continue
            if len(inner) >= 2 and inner[0] == _MAGIC and inner[1] == _DRW_INNER_TYPE:
                try:
                    _seq, ioctl_bytes = framing.unwrap_ioctl(inner)
                    iotype, _id, body = ioctl.parse(ioctl_bytes)
                except (ValueError, AssertionError):
                    _LOGGER.debug("PTZ rx: DRW (non-IOCTL) inner=%s from %s",
                                  inner[:8].hex(), _addr)
                    continue
                return iotype, _id, body
            # Otherwise it's a bare control packet (f1 e0/e1/f0/d1/30/...) ->
            # log its type (a camera-side session close shows up as f1 f0) and
            # keep reading.
            _LOGGER.debug("PTZ rx control: %s from %s", inner[:2].hex(), _addr)
            continue

    # -- teardown -----------------------------------------------------------
    def _close_transport(self) -> None:
        """Close the UDP transport (if any) and reset transport/proto state.

        Synchronous: closes the datagram endpoint without touching the
        keepalive task, so it is safe to call from connect()'s failure path
        (no keepalive started yet) and from close() (after the task is gone).
        """
        if self._transport is not None:
            with contextlib.suppress(OSError):  # pragma: no cover
                self._transport.close()
        self._transport = None
        # Fresh protocol/queue so a subsequent connect() starts clean (no stale
        # datagrams) and _open_transport() has a valid proto to bind.
        self._proto = _RxProtocol()

    async def close(self) -> None:
        """Cancel the keepalive task and close the transport."""
        self.ready = False
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
            self._keepalive_task = None
        self._close_transport()
