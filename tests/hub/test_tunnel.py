"""Tests for EncryptedTunnel keepalive-loss (half-open) detection."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.hub import tunnel as tmod
from custom_components.aqara_lanlink.hub.crypto import aes_cbc_encrypt

# Synthetic verify frames in the structure validated against a real M3 hub
# (commit 23125981): 00 10 <enc1:16> 00 20 <enc2:32>, where
# enc1 = E(session_key, <4-byte challenge>) and enc2 = E(session_key, enc1).
# Built from arbitrary keys so no live-capture material lives in the repo; the
# real-hardware provenance is preserved in git history.
_SK_A = bytes.fromhex("00112233445566778899aabbccddeeff")
_SK_B = bytes.fromhex("ffeeddccbbaa99887766554433221100")


def _build_verify_frame(session_key: bytes, challenge: bytes) -> bytes:
    enc1 = aes_cbc_encrypt(session_key, challenge)
    enc2 = aes_cbc_encrypt(session_key, enc1)
    return b"\x00\x10" + enc1 + b"\x00\x20" + enc2


@pytest.mark.parametrize(
    "challenge", [b"\x3d\xa7\xd5\xae", b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"]
)
def test_verify_server_proof_accepts_valid_frame(challenge):
    frame = _build_verify_frame(_SK_A, challenge)
    assert tmod.EncryptedTunnel._verify_server_proof(_SK_A, frame) is True


def test_verify_server_proof_rejects_wrong_session_key():
    # A valid frame under a DIFFERENT session key: enc2 no longer decrypts to
    # enc1. This is the replay / wrong-peer rejection (the fail-closed teeth).
    frame = _build_verify_frame(_SK_A, b"\x3d\xa7\xd5\xae")
    assert tmod.EncryptedTunnel._verify_server_proof(_SK_B, frame) is False


def test_verify_server_proof_rejects_corrupted_proof_block():
    frame = bytearray(_build_verify_frame(_SK_A, b"\x3d\xa7\xd5\xae"))
    frame[-1] ^= 0xFF  # corrupt the last byte of enc2
    assert tmod.EncryptedTunnel._verify_server_proof(_SK_A, bytes(frame)) is False


def test_verify_server_proof_accepts_unrecognised_structure():
    # Fail open: a frame we don't model must not block a hub with another layout.
    assert tmod.EncryptedTunnel._verify_server_proof(_SK_A, b"\x99short") is True





def test_keepalive_acks_lost_thresholds():
    # Silence strictly beyond interval*factor counts as lost.
    assert tmod._keepalive_acks_lost(
        last_recv=0.0, now=31.0, interval=10.0, factor=3,
    ) is True
    assert tmod._keepalive_acks_lost(
        last_recv=0.0, now=30.0, interval=10.0, factor=3,
    ) is False
    assert tmod._keepalive_acks_lost(
        last_recv=0.0, now=25.0, interval=10.0, factor=3,
    ) is False


@pytest.mark.asyncio
async def test_connect_passes_ssl_to_open_connection(monkeypatch):
    import ssl as _ssl
    captured = {}
    async def fake_open(host, port, **kw):
        captured.update(host=host, port=port, **kw)
        return object(), object()  # reader, writer stubs
    monkeypatch.setattr("asyncio.open_connection", fake_open)
    from custom_components.aqara_lanlink.hub.tunnel import EncryptedTunnel
    t = EncryptedTunnel(device_id="lumi1.test", keepalive_interval=0)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    try:
        await t.connect("10.0.0.9", 443, ssl=ctx)   # handshake fails later; we assert open args
    except Exception:
        pass
    assert captured["ssl"] is ctx
    assert captured["server_hostname"] == "10.0.0.9"


@pytest.mark.asyncio
async def test_keepalive_loop_forces_reconnect_when_silent():
    t = tmod.EncryptedTunnel(device_id="d", keepalive_interval=0.01)
    t._session_key = b"k"
    closed: list[bool] = []
    writer = MagicMock()
    writer.close = lambda: closed.append(True)
    t._writer = writer
    # Last receive a long time ago (in real monotonic terms) -> half-open.
    t._last_recv_monotonic = time.monotonic() - 1000.0

    await asyncio.wait_for(t._keepalive_loop(), timeout=1.0)
    assert closed  # writer closed to force the read side to unblock + reconnect
