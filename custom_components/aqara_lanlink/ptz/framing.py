"""PPPP / CS2 framing + session-header codec (offline, no networking).

This module owns ALL PPPP framing and the inner session header.  The CS2
stream cipher (cs2) and the lumi IOCTL codec (ioctl) are separate modules
imported here.

On-wire packet forms (validated byte-exactly against real frida captures
in data/frida/ptz-9.txt / ptz-10.txt)
-------------------------------------------------------------------------
Every PPPP packet, once decrypted, has the shape:

    f1 <type:1> <len:2 BE> <payload:len>

1) CONTROL packets (handshake / keepalive: 0x41 search, 0x43 reply,
   0x44 confirm, 0xe0 ready, 0x30 keepalive).  On the wire they are the
   BARE cs2-encrypted PPPP packet — no outer header:

       wire = cs2_encrypt(key, f1 <type> <len:2BE> <payload>)

2) DRW DATA packets (the IOCTL channel; inner type 0xd0).  These are ALSO the
   bare cs2-encrypted PPPP packet — there is NO cleartext outer header.  Proven
   by scanning every captured datagram: 7191 DRW packets decrypt whole-cloth to
   a bare ``f1 d0 ...`` packet; ZERO carry a ``<LEN:2BE> 0x68 <sub:5>`` wrapper.

       wire    = cs2_encrypt(key, inner)
       inner   = f1 d0 <innerlen:2BE> d1 <seq:3 BE> <lumi-IOCTL bytes>
       innerlen= len(d1 + seq3 + lumi) = 4 + len(lumi)

   Captured IOCTL examples (decrypted inner):
       AUTH(4096): f1 d0 00 f8 d1 00 00 00  6c756d69 00100000 ...
       PTZ (4138): f1 d0 00 25 d1 00 00 04  6c756d69 2a100000 ...
   The d1 <seq:3> is a monotonic per-DRW counter (first DRW of a session = 0).

Field widths confirmed from captures
------------------------------------
  Inner f1-packet  : f1(1) + type(1) + len(2 BE)   = 4-byte session header
  IOCTL session    : d1(1) + seq(3 BE)             = 4-byte wrapper before lumi
"""
from __future__ import annotations

from . import cs2

# --- PPPP constants ---------------------------------------------------------
_MAGIC = 0xF1
_DRW_INNER_TYPE = 0xD0  # inner f1-packet type for DRW data
_IOCTL_MARKER = 0xD1    # first byte of the DRW inner payload


# ---------------------------------------------------------------------------
# CONTROL packets (bare cs2-encrypted f1-packet, no outer header)
# ---------------------------------------------------------------------------
def build_control(key: str, type: int, payload: bytes = b"") -> bytes:
    """Build a bare control packet: cs2_encrypt(f1 <type> <len:2BE> <payload>)."""
    inner = (
        bytes([_MAGIC, type & 0xFF])
        + len(payload).to_bytes(2, "big")
        + payload
    )
    return cs2.encrypt(key, inner)


def parse_control(key: str, wire: bytes):
    """Parse a bare control packet -> (type, payload)."""
    dec = cs2.decrypt(key, wire)
    if not dec or dec[0] != _MAGIC:
        raise ValueError("not a PPPP control packet (missing f1 magic)")
    type = dec[1]
    length = int.from_bytes(dec[2:4], "big")
    payload = dec[4:4 + length]
    if len(payload) != length:
        raise ValueError(
            f"truncated control payload: declared {length}, got {len(payload)}"
        )
    return type, payload


# ---------------------------------------------------------------------------
# DRW data packets (8-byte cleartext outer header + cs2-encrypted inner)
# ---------------------------------------------------------------------------
def frame_drw(key: str, inner_pppp_bytes: bytes) -> bytes:
    """Frame an inner f1-packet as a wire DRW datagram.

    The datagram is just ``cs2_encrypt(key, inner)`` -- there is NO cleartext
    outer header.  Proven by scanning every captured datagram (7191 phone/camera
    DRW packets): each one decrypts whole-cloth to a bare ``f1 d0 ...`` packet,
    and ZERO match a ``<LEN:2BE> 0x68 <sub:5>`` outer wrapper.  The channel/seq
    live INSIDE the inner packet (``f1 d0 <len> d1 <seq:3>``), not in an outer
    header.  Captured IOCTL examples:
        AUTH(4096): f1 d0 00 f8 d1 00 00 00  6c756d69 00100000 ...
        PTZ (4138): f1 d0 00 25 d1 00 00 04  6c756d69 2a100000 ...
    """
    return cs2.encrypt(key, inner_pppp_bytes)


def deframe_drw(key: str, wire: bytes) -> bytes:
    """Deframe a wire DRW datagram -> inner f1-packet bytes (cs2-decrypt)."""
    return cs2.decrypt(key, wire)


# ---------------------------------------------------------------------------
# Inner f1 d0 ... session wrapper around a lumi IOCTL
# ---------------------------------------------------------------------------
def wrap_ioctl(seq: int, ioctl_bytes: bytes) -> bytes:
    """Build the inner DRW f1-packet around a lumi IOCTL.

    Returns: f1 d0 <len:2BE> d1 <seq:3BE> <ioctl_bytes>
    where <len> covers (d1 + 3 seq bytes + ioctl_bytes).
    """
    body = bytes([_IOCTL_MARKER]) + (seq & 0xFFFFFF).to_bytes(3, "big") + ioctl_bytes
    return (
        bytes([_MAGIC, _DRW_INNER_TYPE])
        + len(body).to_bytes(2, "big")
        + body
    )


def unwrap_ioctl(inner: bytes):
    """Parse an inner f1 d0 ... packet -> (seq, ioctl_bytes)."""
    if len(inner) < 4 or inner[0] != _MAGIC:
        raise ValueError("not a PPPP inner packet (missing f1 magic)")
    if inner[1] != _DRW_INNER_TYPE:
        raise ValueError(f"not a DRW inner packet (type {inner[1]:#x} != 0xd0)")
    length = int.from_bytes(inner[2:4], "big")
    body = inner[4:4 + length]
    if len(body) != length:
        raise ValueError(
            f"truncated DRW inner: declared {length}, got {len(body)}"
        )
    if len(body) < 4:
        # Need at least the d1 marker (1) + 3-byte seq. Guards body[0] below
        # against an empty body (which would raise IndexError, not ValueError,
        # and so escape the PTZ receive loop's handler).
        raise ValueError(f"DRW inner body too short: {len(body)} bytes")
    if body[0] != _IOCTL_MARKER:
        raise ValueError(f"missing d1 ioctl marker (got {body[0]:#x})")
    seq = int.from_bytes(body[1:4], "big")
    ioctl_bytes = body[4:]
    return seq, ioctl_bytes


# ---------------------------------------------------------------------------
# Convenience: full IOCTL -> wire
# ---------------------------------------------------------------------------
def build_ioctl_packet(key: str, seq: int, ioctl_bytes: bytes) -> bytes:
    """End-to-end: wrap an IOCTL in an f1 d0 inner packet and cs2-encrypt it.

    `seq` is the DRW sequence carried in the inner ``d1 <seq:3>`` field (a
    monotonic per-DRW counter; the first DRW of a session is seq 0).  The wire
    datagram is the bare cs2-encrypted inner packet -- no outer header.
    """
    inner = wrap_ioctl(seq, ioctl_bytes)
    return frame_drw(key, inner)


# ---------------------------------------------------------------------------
# Control packet types (see PTZ_PPPP_HANDSHAKE_NOTES.md)
# ---------------------------------------------------------------------------
CTRL_SEARCH = 0x41   # phone -> cam LAN search / hello (carries DID)
CTRL_REPLY = 0x43    # cam -> phone search reply / punch-ack
CTRL_CONFIRM = 0x44  # cam -> phone punch confirm (WAN trace)
CTRL_CONFIRM_ALT = 0x42  # cam -> phone punch confirm (LAN variant, observed live)
CTRL_LOGIN = 0xF9    # phone -> cam session login / P2P-request (WAN trace)
CTRL_READY = 0xE0    # session-ready handshake (both directions)
CTRL_KEEPALIVE = 0x30  # phone -> cam periodic keepalive / ack
CTRL_LAN_SEARCH = 0x30  # broadcast LAN-search (same type byte; empty payload)

# The camera always LISTENS on this fixed PPPP port for the broadcast LAN-search,
# even though its per-session reply comes FROM a dynamic source port.
LAN_SEARCH_PORT = 32108
