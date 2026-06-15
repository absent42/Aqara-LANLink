"""Manual IP-entry path probes the host and surfaces distinguished errors.

Covers Task 3: when the user manually enters a hub IP and DID, the flow
probes the host before advancing to credentials. Different probe results
surface distinct, actionable error keys rather than a generic timeout.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.aqara_lanlink import config_flow
from custom_components.aqara_lanlink.const import (
    CONF_HUB_DID,
    CONF_HUB_IP,
    DOMAIN,
)
from custom_components.aqara_lanlink.hub.probe import ProbeResult

_PROBE_TARGET = "custom_components.aqara_lanlink.config_flow.probe_tunnel_host"
_DISCOVER_TARGET = "custom_components.aqara_lanlink.config_flow.discover_hubs"

_MANUAL_IP = "192.0.2.50"
_MANUAL_DID = "lumi1.ABCDEF123456"


async def _init_manual_form(hass):
    """Drive the user step initial render (no discovered hubs) and return
    the flow_id of the rendered manual IP+DID form."""
    with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


class TestManualEntryProbe:
    async def test_refused_surfaces_not_tunnel_host_error(self, hass) -> None:
        """ProbeResult.REFUSED on manual entry must re-render the user step
        with error key not_tunnel_host."""
        flow_id = await _init_manual_form(hass)

        with patch.object(
            config_flow, "probe_tunnel_host",
            new=AsyncMock(return_value=ProbeResult.REFUSED),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {CONF_HUB_IP: _MANUAL_IP, CONF_HUB_DID: _MANUAL_DID},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "not_tunnel_host"}

    async def test_timeout_surfaces_cannot_connect_error(self, hass) -> None:
        """ProbeResult.TIMEOUT on manual entry must re-render with cannot_connect."""
        flow_id = await _init_manual_form(hass)

        with patch.object(
            config_flow, "probe_tunnel_host",
            new=AsyncMock(return_value=ProbeResult.TIMEOUT),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {CONF_HUB_IP: _MANUAL_IP, CONF_HUB_DID: _MANUAL_DID},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_not_lanlink_surfaces_not_lanlink_error(self, hass) -> None:
        """ProbeResult.NOT_LANLINK on manual entry must re-render with not_lanlink."""
        flow_id = await _init_manual_form(hass)

        with patch.object(
            config_flow, "probe_tunnel_host",
            new=AsyncMock(return_value=ProbeResult.NOT_LANLINK),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {CONF_HUB_IP: _MANUAL_IP, CONF_HUB_DID: _MANUAL_DID},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "not_lanlink"}

    async def test_ok_advances_to_credentials(self, hass) -> None:
        """ProbeResult.OK on manual entry must advance to the credentials step."""
        flow_id = await _init_manual_form(hass)

        with patch.object(
            config_flow, "probe_tunnel_host",
            new=AsyncMock(return_value=ProbeResult.OK),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {CONF_HUB_IP: _MANUAL_IP, CONF_HUB_DID: _MANUAL_DID},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"


# ---------------------------------------------------------------------------
# Manual "Pick + IP" subentry activation fallback.
#
# When auto-discovery misses a standalone Wi-Fi device, the picker offers a
# sentinel option that routes to a manual step. The user picks the device
# from the cloud list and types its LAN IP; the flow validates the IP and
# the Aqara endpoint, then funnels into the SAME activate-on-add path used by
# the auto-discovery picker.
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.aqara_lanlink import config_flow as config_flow_module  # noqa: E402
from custom_components.aqara_lanlink.config_flow import (  # noqa: E402
    AqaraDeviceSubentryFlow,
    _MANUAL_ACTIVATE_SENTINEL,
)
from custom_components.aqara_lanlink.const import (  # noqa: E402
    CONF_ACTIVATION_HOST,
    CONF_ACTIVATION_PORT,
    CONF_AQARA_ACCOUNT,
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
)

_STANDALONE_DID = "lumi1.fp2manual"
_STANDALONE_MODEL = "lumi.motion.agl001"
_STANDALONE_HOST = "10.1.20.160"


def _manual_hub_entry(hass, *, hub_did: str = "lumi1.M3HUB") -> MockConfigEntry:
    """Build + register a minimal hub config entry for the manual flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=hub_did,
        title="Hub M3",
        data={
            CONF_HUB_IP: "192.0.2.10",
            CONF_HUB_PORT: 59703,
            CONF_HUB_DID: hub_did,
            CONF_HUB_MODEL: "lumi.gateway.agl004",
            CONF_AQARA_ACCOUNT: "user@example.com",
            CONF_AQARA_REGION: "EU",
            CONF_AQARA_USER_ID: "USR",
            CONF_AQARA_TOKEN: "TOK",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _build_manual_subentry_flow(hass, entry) -> AqaraDeviceSubentryFlow:
    """Wire a subentry flow instance the same way HA's flow manager would."""
    flow = AqaraDeviceSubentryFlow()
    flow.hass = hass
    flow.handler = (entry.entry_id, "device")
    flow.flow_id = "test_flow_id"
    flow.context = {"source": "user"}
    return flow


def _manual_device_list(hub_did: str) -> list[dict]:
    """Cloud list: the connected hub + a standalone device (devicetype 8)."""
    return [
        {
            "did": hub_did,
            "model": "lumi.gateway.agl004",
            "deviceName": "Hub M3",
            "parentDeviceId": "",
            "devicetype": 1,
        },
        {
            "did": _STANDALONE_DID,
            "model": _STANDALONE_MODEL,
            "deviceName": "Aqara FP2",
            "parentDeviceId": "",
            "devicetype": 8,
        },
    ]


def _device_stub(device_list):
    """Return an async stub for _fetch_device_list returning device_list."""
    async def _async_devices(self, region, token):
        return device_list
    return _async_devices


def _picker_options(result):
    """Pull the picker option values out of a rendered pick_device form."""
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == "device")
    return [opt["value"] for opt in schema[key].config["options"]]


class TestPickerManualSentinel:
    """The picker always offers a manual-activate sentinel option."""

    async def test_picker_offers_manual_sentinel(self, hass) -> None:
        """The sentinel appears both when devices exist and when none do."""
        hub_did = "lumi1.M3HUB"

        # Case 1: a standalone device IS offered (not in topology, but the
        # cloud list has a child too so the supported set is non-empty via
        # the normal path). We only assert the sentinel is present.
        entry = _manual_hub_entry(hass, hub_did=hub_did)
        flow = _build_manual_subentry_flow(hass, entry)
        device_list = [
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            {
                "did": "lumi.CHILD",
                "model": "lumi.light.agl003",
                "deviceName": "LED Bulb",
                "parentDeviceId": hub_did,
                "devicetype": 2,
            },
        ]
        with (
            patch.object(
                config_flow_module.registry, "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list",
                _device_stub(device_list),
            ),
        ):
            result = await flow.async_step_user()
        assert _MANUAL_ACTIVATE_SENTINEL in _picker_options(result)

        # Case 2: no eligible devices at all -- only the hub. The sentinel
        # must still be reachable (the empty branch renders a real picker).
        entry2 = _manual_hub_entry(hass, hub_did="lumi1.OTHERHUB")
        flow2 = _build_manual_subentry_flow(hass, entry2)
        empty_list = [
            {
                "did": "lumi1.OTHERHUB",
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
        ]
        with (
            patch.object(
                config_flow_module.registry, "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list",
                _device_stub(empty_list),
            ),
        ):
            result2 = await flow2.async_step_user()
        assert _MANUAL_ACTIVATE_SENTINEL in _picker_options(result2)

    async def test_pick_sentinel_routes_to_manual_step(self, hass) -> None:
        """Submitting the sentinel routes to the manual_activate step."""
        hub_did = "lumi1.M3HUB"
        entry = _manual_hub_entry(hass, hub_did=hub_did)
        flow = _build_manual_subentry_flow(hass, entry)

        with patch.object(
            AqaraDeviceSubentryFlow, "_fetch_device_list",
            _device_stub(_manual_device_list(hub_did)),
        ):
            # Initial render so flow state is warm, then submit the sentinel.
            await flow.async_step_user()
            result = await flow.async_step_pick_device(
                {"device": _MANUAL_ACTIVATE_SENTINEL},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_activate"


class TestManualActivateStep:
    """The manual_activate step validates the IP format/safety, then activates.

    It deliberately does NOT probe :443 before poking: a bare-TLS connection
    moments before the activation poke poisons the device's activation window so
    the hub never adopts it (proven via controlled A/B). Reachability is left to
    the best-effort poke itself.
    """

    @pytest.mark.parametrize("bad_ip", ["999.999.999.999", "10.1.20", "abc"])
    async def test_manual_rejects_malformed_ip(self, hass, bad_ip) -> None:
        """A malformed IP re-renders manual_activate with invalid_ip."""
        hub_did = "lumi1.M3HUB"
        entry = _manual_hub_entry(hass, hub_did=hub_did)
        flow = _build_manual_subentry_flow(hass, entry)

        with patch.object(
            AqaraDeviceSubentryFlow, "_fetch_device_list",
            _device_stub(_manual_device_list(hub_did)),
        ):
            await flow.async_step_manual_activate()  # populate _manual_records
            result = await flow.async_step_manual_activate(
                {"device": _STANDALONE_DID, "host": bad_ip},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "manual_activate"
        assert result["errors"] == {"host": "invalid_ip"}

    async def test_manual_success_activates_and_persists(self, hass) -> None:
        """A valid IP + standalone record activates and persists the host."""
        hub_did = "lumi1.M3HUB"
        entry = _manual_hub_entry(hass, hub_did=hub_did)
        # During the wait loop the device never appears in topology; the
        # loop exits after the patched (fast) sleeps.
        stub_hub = SimpleNamespace(
            lanlink_topology_dids=frozenset({hub_did}),
        )
        entry.runtime_data = SimpleNamespace(hub=stub_hub)
        flow = _build_manual_subentry_flow(hass, entry)

        activate_mock = AsyncMock()

        with (
            patch.object(
                config_flow_module.registry, "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list",
                _device_stub(_manual_device_list(hub_did)),
            ),
            patch.object(
                config_flow_module, "activate_relay", activate_mock,
            ),
            patch.object(
                config_flow_module.asyncio, "sleep", AsyncMock(),
            ),
        ):
            await flow.async_step_manual_activate()
            result = await flow.async_step_manual_activate(
                {"device": _STANDALONE_DID, "host": _STANDALONE_HOST},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        activate_mock.assert_awaited_once_with(
            _STANDALONE_HOST, _STANDALONE_DID, 443,
        )
        data = result["data"]
        assert data[CONF_ACTIVATION_HOST] == _STANDALONE_HOST
        assert data[CONF_ACTIVATION_PORT] == 443
