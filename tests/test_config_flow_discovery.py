"""Discovery is filtered to probe-passing tunnel hosts."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from custom_components.aqara_lanlink.hub.mdns import AqaraServiceRecord
from custom_components.aqara_lanlink.hub.probe import ProbeResult
from custom_components.aqara_lanlink import config_flow


async def test_discovery_drops_non_tunnel_hosts(hass):
    records = [
        AqaraServiceRecord(host="10.0.0.1", port=1, did="lumi1.m3",
                           name="Aqara-Hub-M3-1111"),
        AqaraServiceRecord(host="10.0.0.2", port=2, did="lumi1.m100",
                           name="Aqara-Hub-M100-2222"),
    ]
    probe_map = {"lumi1.m3": ProbeResult.OK,
                 "lumi1.m100": ProbeResult.REFUSED}

    async def _fake_probe(host, port, did, **_kw):
        return probe_map[did]

    with patch.object(config_flow, "discover_hubs", new=AsyncMock(return_value=records)), \
         patch.object(config_flow, "probe_tunnel_host", side_effect=_fake_probe):
        kept = await config_flow._discover_tunnel_hosts(hass)

    assert set(kept) == {"lumi1.m3"}
    assert kept["lumi1.m3"].host == "10.0.0.1"


@pytest.mark.parametrize("bad_result", [
    ProbeResult.REFUSED,
    ProbeResult.TIMEOUT,
    ProbeResult.NOT_LANLINK,
])
async def test_discovery_drops_each_non_ok_result(hass, bad_result):
    """Each non-OK probe result causes the host to be dropped."""
    records = [
        AqaraServiceRecord(host="10.0.0.1", port=1, did="ok.did",
                           name="Aqara-Hub-OK"),
        AqaraServiceRecord(host="10.0.0.2", port=2, did="bad.did",
                           name="Aqara-Hub-Bad"),
    ]
    probe_map = {"ok.did": ProbeResult.OK, "bad.did": bad_result}

    async def _fake_probe(host, port, did, **_kw):
        return probe_map[did]

    with patch.object(config_flow, "discover_hubs", new=AsyncMock(return_value=records)), \
         patch.object(config_flow, "probe_tunnel_host", side_effect=_fake_probe):
        kept = await config_flow._discover_tunnel_hosts(hass)

    assert set(kept) == {"ok.did"}, f"expected only ok.did kept, got {set(kept)}"
    assert "bad.did" not in kept


async def test_discovery_returns_empty_when_mdns_fails(hass):
    with patch.object(config_flow, "discover_hubs",
                      new=AsyncMock(side_effect=OSError("mdns boom"))):
        kept = await config_flow._discover_tunnel_hosts(hass)
    assert kept == {}
