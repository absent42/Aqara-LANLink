"""Tests for the credential-free tunnel-host capability probe."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.aqara_lanlink.hub.probe import (
    ProbeResult,
    probe_tunnel_host,
)
from custom_components.aqara_lanlink.hub.tunnel import TunnelError


async def _run(connect_side_effect):
    tunnel = AsyncMock()
    tunnel.connect.side_effect = connect_side_effect
    with patch(
        "custom_components.aqara_lanlink.hub.probe.EncryptedTunnel",
        return_value=tunnel,
    ):
        return await probe_tunnel_host(
            "10.0.0.1", 1234, "lumi1.abc", timeout=0.5,
        )


async def test_probe_ok_when_handshake_completes():
    assert await _run(None) is ProbeResult.OK


async def test_probe_refused_maps_to_not_tunnel_host():
    assert await _run(ConnectionRefusedError(111, "refused")) is (
        ProbeResult.REFUSED
    )


async def test_probe_timeout_maps_to_offline():
    """wait_for expiry surfaces as asyncio.TimeoutError -> TIMEOUT."""
    assert await _run(asyncio.TimeoutError()) is ProbeResult.TIMEOUT


async def test_probe_other_oserror_maps_to_offline():
    assert await _run(OSError(113, "no route to host")) is (
        ProbeResult.TIMEOUT
    )


async def test_probe_handshake_failure_maps_to_not_lanlink():
    assert await _run(TunnelError("bad ECDH header")) is (
        ProbeResult.NOT_LANLINK
    )


@pytest.mark.parametrize("side_effect", [
    None,                                      # OK path
    ConnectionRefusedError(111, "refused"),    # REFUSED path
    TunnelError("bad ECDH"),                   # NOT_LANLINK path
])
async def test_probe_always_closes_the_tunnel(side_effect):
    """close() is awaited on every outcome -- the finally block."""
    tunnel = AsyncMock()
    tunnel.connect.side_effect = side_effect
    with patch(
        "custom_components.aqara_lanlink.hub.probe.EncryptedTunnel",
        return_value=tunnel,
    ):
        await probe_tunnel_host("10.0.0.1", 1234, "lumi1.abc", timeout=0.5)
    tunnel.close.assert_awaited()
