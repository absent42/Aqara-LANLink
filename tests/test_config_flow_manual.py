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
