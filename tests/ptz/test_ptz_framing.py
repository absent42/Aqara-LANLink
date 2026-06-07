"""Framing/codec tests for framing.py, validated against REAL captured bytes.

Oracles (extracted from data/frida/*.txt P2P_Proprietary_Encrypt/Decrypt dumps):
  - CONTROL: a 24-byte UDP handshake (type 0x41 search) -- bare cs2-encrypted
    f1-packet, no outer header.
  - DATA: DRW IOCTL packets are ALSO bare cs2-encrypted f1-packets -- there is
    NO cleartext outer header.  Proven by scanning every captured datagram:
    7191 DRW packets decrypt whole-cloth to a bare ``f1 d0 ...`` packet, ZERO
    carry a ``<LEN:2BE> 0x68 <sub:5>`` wrapper.  Captured inner DRW headers:
        AUTH(4096): f1 d0 00 f8 d1 00 00 00  6c756d69 00100000 ...
        PTZ (4138): f1 d0 00 25 d1 00 00 04  6c756d69 2a100000 ...
"""
import pytest

from custom_components.aqara_lanlink.ptz import cs2, framing, ioctl

KEY = "aqarager19kn"

# --- Oracle 1: real 24-byte handshake (CONTROL, bare f1-packet) -------------
# wire == cs2_encrypt(KEY, f1 41 00 14 + 20-byte DID)
HANDSHAKE_WIRE = bytes.fromhex(
    "ed1590f366335c1390a313d122d7a89cfbc36e25b8a356bf"
)
HANDSHAKE_TYPE = 0x41
HANDSHAKE_DID = bytes.fromhex("415141524144450000057c1f5a5558444c000000")

# --- Oracle 2: real PTZ "left" DRW inner header (DATA, bare cs2) ------------
# Captured inner prefix: f1 d0 00 25 d1 00 00 04 6c756d69 2a100000 ...
# len 0x0025 = 37 = d1(1)+seq(3)+lumi(33); lumi = 16 hdr + 17-byte body, so the
# body is exactly {"action":"left"} (17 bytes).  The lumi `id` field is not in
# the truncated dump, so we pin everything except `id` and parametrize `id`.
LEFT_SEQ = 0x000004                    # the captured d1 <seq:3>
LEFT_IOCTL_TYPE = 4138
LEFT_ACTION = {"action": "left"}
# inner header through the lumi iotype, byte-exact from the capture:
LEFT_INNER_PREFIX = bytes.fromhex("f1d00025d1000004" "6c756d692a100000")


# ---------------------------------------------------------------------------
# CONTROL packets
# ---------------------------------------------------------------------------
def test_parse_control_real_handshake():
    typ, payload = framing.parse_control(KEY, HANDSHAKE_WIRE)
    assert typ == HANDSHAKE_TYPE
    assert payload == HANDSHAKE_DID


def test_build_control_reproduces_real_handshake_byte_exact():
    wire = framing.build_control(KEY, HANDSHAKE_TYPE, HANDSHAKE_DID)
    assert wire == HANDSHAKE_WIRE


def test_control_round_trip_empty_payload():
    wire = framing.build_control(KEY, 0x30, b"")
    typ, payload = framing.parse_control(KEY, wire)
    assert typ == 0x30
    assert payload == b""


@pytest.mark.parametrize("typ", [0x41, 0x43, 0x44, 0xE0, 0x30])
@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", b"hello world", bytes(range(256)), b"\xff" * 300],
)
def test_control_round_trip_property(typ, payload):
    wire = framing.build_control(KEY, typ, payload)
    got_typ, got_payload = framing.parse_control(KEY, wire)
    assert got_typ == typ
    assert got_payload == payload


# ---------------------------------------------------------------------------
# DRW data packets (bare cs2-encrypted f1 d0 ... inner; no outer header)
# ---------------------------------------------------------------------------
def test_wrap_ioctl_reproduces_real_ptz_inner_prefix():
    """Our builder must reproduce the captured PTZ inner header byte-exactly."""
    ioctl_bytes = ioctl.build(LEFT_IOCTL_TYPE, LEFT_ACTION, id=64)
    inner = framing.wrap_ioctl(LEFT_SEQ, ioctl_bytes)
    assert inner[: len(LEFT_INNER_PREFIX)] == LEFT_INNER_PREFIX
    # len field covers d1 + seq3 + lumi(=16+17) = 37 = 0x25
    assert int.from_bytes(inner[2:4], "big") == 0x25


def test_wrap_ioctl_layout():
    ioctl_bytes = ioctl.build(LEFT_IOCTL_TYPE, LEFT_ACTION, id=12)
    inner = framing.wrap_ioctl(LEFT_SEQ, ioctl_bytes)
    assert inner[0] == 0xF1
    assert inner[1] == 0xD0
    assert int.from_bytes(inner[2:4], "big") == len(inner) - 4
    assert inner[4] == 0xD1
    assert int.from_bytes(inner[5:8], "big") == LEFT_SEQ
    assert inner[8:] == ioctl_bytes


def test_wrap_unwrap_round_trip():
    ioctl_bytes = ioctl.build(4138, {"action": "right"}, id=99)
    inner = framing.wrap_ioctl(0x010203, ioctl_bytes)
    seq, got = framing.unwrap_ioctl(inner)
    assert seq == 0x010203
    assert got == ioctl_bytes


def test_unwrap_ioctl_empty_body_raises_valueerror_not_indexerror():
    # A DRW inner that declares a zero-length body must raise ValueError, not
    # IndexError (which would escape recv_ioctl's handler and crash the PTZ
    # receive loop) when body[0] is accessed.
    inner = b"\xf1\xd0\x00\x00"  # f1 d0, declared length 0 -> empty body
    with pytest.raises(ValueError):
        framing.unwrap_ioctl(inner)


def test_frame_drw_is_bare_cs2_no_outer_header():
    """The wire datagram is exactly cs2_encrypt(inner) -- no outer header."""
    inner = framing.wrap_ioctl(LEFT_SEQ, ioctl.build(4138, LEFT_ACTION, id=64))
    wire = framing.frame_drw(KEY, inner)
    assert wire == cs2.encrypt(KEY, inner)
    # round trips back to the exact inner
    assert framing.deframe_drw(KEY, wire) == inner


@pytest.mark.parametrize(
    "inner", [b"\xf1\xd0\x00\x01z", b"\xf1\xd0" + b"\x01\x00" + b"\xab" * 256]
)
def test_deframe_drw_round_trip_property(inner):
    wire = framing.frame_drw(KEY, inner)
    assert framing.deframe_drw(KEY, wire) == inner


def test_build_ioctl_packet_round_trip():
    ioctl_bytes = ioctl.build(4138, {"action": "up"}, id=7)
    wire = framing.build_ioctl_packet(KEY, 0x000010, ioctl_bytes)
    inner = framing.deframe_drw(KEY, wire)
    seq, got_ioctl = framing.unwrap_ioctl(inner)
    assert seq == 0x000010
    assert ioctl.parse(got_ioctl) == (4138, 7, {"action": "up"})
