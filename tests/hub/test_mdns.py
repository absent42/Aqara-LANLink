"""Tests for the Aqara mDNS discovery helper.

We don't want real network traffic in tests, so `discover_hubs` is exercised
via a fake AsyncZeroconf that injects a synthetic service into the internal
result dict by way of the `_on_change` handler.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aqara_lanlink.hub import mdns
from custom_components.aqara_lanlink.hub.mdns import (
    AqaraServiceRecord,
    discover_hub_by_did,
    discover_hubs,
)


class _FakeAsyncZeroconf:
    """Stand-in for AsyncZeroconf that records whether it was closed."""

    def __init__(self) -> None:
        self.zeroconf = MagicMock()
        self.closed = False

    async def async_close(self) -> None:
        self.closed = True


class TestDiscoverHubs:
    async def test_returns_empty_on_no_services(self):
        aiozc = _FakeAsyncZeroconf()

        with (
            patch.object(mdns, "AsyncZeroconf", return_value=aiozc),
            patch.object(mdns, "AsyncServiceBrowser") as browser_cls,
            patch.object(mdns, "ha_zeroconf", None),
        ):
            browser = MagicMock()
            browser.async_cancel = AsyncMock()
            browser_cls.return_value = browser

            result = await discover_hubs(hass=None, timeout=0.01)

        assert result == []
        browser_cls.assert_called_once()
        browser.async_cancel.assert_awaited_once()
        assert aiozc.closed  # Standalone mode -> we own and must close it.

    async def test_returns_resolved_record(self):
        """Simulate the AsyncServiceBrowser invoking our on_change handler."""
        aiozc = _FakeAsyncZeroconf()

        captured_handler: list = []

        def browser_factory(zc, service_type, handlers):
            captured_handler.append(handlers[0])
            browser = MagicMock()
            browser.async_cancel = AsyncMock()
            return browser

        async def fake_resolve(zc, service_type, name, results):
            results["lumi1.ABC"] = AqaraServiceRecord(
                host="192.0.2.20", port=12345, did="lumi1.ABC", name="Aqara-Hub",
            )

        with (
            patch.object(mdns, "AsyncZeroconf", return_value=aiozc),
            patch.object(mdns, "AsyncServiceBrowser", side_effect=browser_factory),
            patch.object(mdns, "_resolve", fake_resolve),
            patch.object(mdns, "ha_zeroconf", None),
        ):
            # Kick off the discovery.
            task = asyncio.create_task(discover_hubs(hass=None, timeout=0.05))
            # Let the browser be created before we invoke the handler.
            for _ in range(5):
                if captured_handler:
                    break
                await asyncio.sleep(0)
            assert captured_handler, "handler was never registered"
            from zeroconf import ServiceStateChange
            # Zeroconf invokes handlers with kwargs, not positional args.
            captured_handler[0](
                zeroconf=aiozc.zeroconf,
                service_type="_aqara-setup._tcp.local.",
                name="Aqara-Hub._aqara-setup._tcp.local.",
                state_change=ServiceStateChange.Added,
            )
            result = await task

        assert len(result) == 1
        assert result[0].did == "lumi1.ABC"
        assert result[0].port == 12345

    async def test_discover_hub_by_did_filters(self):
        target = AqaraServiceRecord(
            host="192.0.2.1", port=111, did="lumi1.TARGET", name="a",
        )
        other = AqaraServiceRecord(
            host="192.0.2.2", port=222, did="lumi3.OTHER", name="b",
        )
        with patch.object(mdns, "discover_hubs",
                          AsyncMock(return_value=[other, target])):
            found = await discover_hub_by_did(None, "lumi1.TARGET")
        assert found is target

    async def test_discover_hub_by_did_returns_none_if_missing(self):
        other = AqaraServiceRecord(
            host="192.0.2.2", port=222, did="lumi3.OTHER", name="b",
        )
        with patch.object(mdns, "discover_hubs",
                          AsyncMock(return_value=[other])):
            found = await discover_hub_by_did(None, "lumi1.NOMATCH")
        assert found is None


class TestIsLanAddress:
    @pytest.mark.parametrize(
        "addr",
        [
            "192.168.1.50", "10.0.0.5", "172.16.3.4",  # RFC1918
            "169.254.1.1",   # IPv4 link-local
            "127.0.0.1",     # loopback
            "fe80::1",       # IPv6 link-local
            "fd00::1234",    # IPv6 ULA
            "::1",           # IPv6 loopback
        ],
    )
    def test_accepts_local_addresses(self, addr):
        assert mdns._is_lan_address(addr) is True

    @pytest.mark.parametrize(
        "addr",
        ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "garbage", ""],
    )
    def test_rejects_non_local_and_unparseable(self, addr):
        assert mdns._is_lan_address(addr) is False

    def test_strips_ipv6_scope_id(self):
        assert mdns._is_lan_address("fe80::1%eth0") is True


class TestDecodeProperties:
    def test_bytes_keys_and_values_decoded(self):
        info = MagicMock()
        info.properties = {
            b"id": b"lumi1.abc",
            b"pv": b"true",
            b"cv": None,
        }
        decoded = mdns._decode_properties(info)
        assert decoded == {"id": "lumi1.abc", "pv": "true", "cv": ""}

    def test_missing_properties_returns_empty(self):
        info = MagicMock()
        info.properties = None
        assert mdns._decode_properties(info) == {}
