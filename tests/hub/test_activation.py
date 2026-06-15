import asyncio

import pytest

from custom_components.aqara_lanlink.hub import activation


@pytest.mark.asyncio
async def test_activate_relay_swallows_handshake_failure(monkeypatch):
    calls = {}

    class FakeTunnel:
        def __init__(self, device_id, keepalive_interval=0):
            calls["did"] = device_id

        async def connect(self, host, port, *, ssl=None):
            calls["host"], calls["port"], calls["ssl_set"] = host, port, ssl is not None
            raise asyncio.TimeoutError()  # handshake always fails - that's fine

        async def close(self):
            calls["closed"] = True

    monkeypatch.setattr(activation, "EncryptedTunnel", FakeTunnel)
    await activation.activate_relay("10.0.0.9", "lumi1.test")  # must NOT raise
    assert calls["host"] == "10.0.0.9" and calls["port"] == 443
    assert calls["ssl_set"] is True and calls["closed"] is True
