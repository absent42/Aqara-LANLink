"""Tests for the Aqara LANLink hub config flow.

Covers:
- Task 6.1: the user-facing entry step that runs mDNS hub discovery and
  either offers a selector of discovered hubs or falls back to a manual
  hub IP + DID form.
- Task 6.2: the credentials step that captures Aqara cloud credentials
  (email/password OR pasted user_id/token), validates them with a real
  LANLink checkin against the hub, and advances to the confirm step.
- Task 6.3: the confirm step's cloud device-count preview and the
  resulting ``CREATE_ENTRY`` (or ``ABORT`` for duplicates) outcome.
- Task 6.4: the device subentry flow's ``async_step_pick_device``
  step. Tests instantiate the flow class directly because the test
  environment's HA may not include the ``ConfigSubentryFlow``
  manager (added in HA 2025.2); calling the step methods directly
  exercises the same code path as the production flow manager.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    start_reauth_flow,
)

from custom_components.aqara_lanlink import config_flow as config_flow_module
from custom_components.aqara_lanlink.config_flow import (
    AqaraDeviceSubentryFlow,
    AqaraLanLinkConfigFlow,
)
from custom_components.aqara_lanlink.device.camera.base import CameraDevice
from custom_components.aqara_lanlink.const import (
    CONF_AQARA_ACCOUNT,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_DEVICE_DID,
    CONF_DEVICE_MODEL,
    CONF_HUB_DID,
    CONF_HUB_IP,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
    DOMAIN,
)
from custom_components.aqara_lanlink.hub.cloud_client import (
    AqaraAuthError,
    AqaraTokens,
    EndpointPanel,
)
from custom_components.aqara_lanlink.hub.mdns import AqaraServiceRecord
from custom_components.aqara_lanlink.hub.probe import ProbeResult


# Where the config flow imports `discover_hubs` and `probe_tunnel_host` from.
# The flow imports these function symbols into its own module namespace, so
# patches must target the bindings the flow code actually consults.
_DISCOVER_TARGET = "custom_components.aqara_lanlink.config_flow.discover_hubs"
_PROBE_TARGET = "custom_components.aqara_lanlink.config_flow.probe_tunnel_host"
_CLOUD_CLIENT_TARGET = "custom_components.aqara_lanlink.config_flow.AqaraCloudClient"
_HUB_COORDINATOR_TARGET = (
    "custom_components.aqara_lanlink.config_flow.HubCoordinator"
)
# Tests that drive a reauth flow to completion (or create-entry flows) trigger
# HA's config-entry reload, which calls __init__.py:async_setup_entry. That
# function imports HubCoordinator + AqaraCloudClient under the package
# namespace; tests must patch those bindings too if they want to short-circuit
# the real LANLink session start during reload.
_INIT_HUB_COORDINATOR_TARGET = (
    "custom_components.aqara_lanlink.HubCoordinator"
)
_INIT_CLOUD_CLIENT_TARGET = (
    "custom_components.aqara_lanlink.AqaraCloudClient"
)
# async_get_clientsession spawns aiohttp's pycares resolver thread, which
# trips the test plugin's lingering-thread teardown check. Patching it to
# return a MagicMock keeps __init__.py:async_setup_entry happy without
# actually opening a session.
_INIT_CLIENTSESSION_TARGET = (
    "custom_components.aqara_lanlink.async_get_clientsession"
)


# Stand-in CameraDevice subclasses for tests that need a concrete model class
# to register against. The V1-era model packages (camera_agl005/G100Device,
# camera_agl013/G400Device) were deleted when V3 catalogue dispatch made the
# per-model package shape obsolete; these subclasses preserve the same
# config-flow surface (MODEL + CameraDevice scaffolding) so the flow logic
# tests still exercise the real registry/prefill plumbing.
class G100Device(CameraDevice):
    MODEL = "lumi.camera.agl005"


class G400Device(CameraDevice):
    MODEL = "lumi.camera.agl013"


def _record(
    did: str = "lumi1.ABCDEF",
    host: str = "192.0.2.10",
    port: int = 12345,
    name: str = "Aqara-Hub",
) -> AqaraServiceRecord:
    return AqaraServiceRecord(host=host, port=port, did=did, name=name)


def _make_panel(
    endpoint_id: int,
    paths: tuple[str, ...],
    *,
    device_types: str = "",
) -> EndpointPanel:
    """Build a minimal EndpointPanel for test mocks."""
    return EndpointPanel(
        endpoint_id=endpoint_id,
        endpoint_name="",
        endpoint_icon_id="",
        device_name="",
        device_types=device_types,
        position_name="",
        model_type=0,
        obj_properties=(),
        paths=paths,
    )


# =============================================================================
# mDNS returns hubs -> selector form
# =============================================================================


class TestUserStepWithDiscoveredHubs:
    async def test_two_hubs_renders_selector_with_both_dids(self, hass) -> None:
        records = [
            _record(did="lumi1.AAA", host="192.0.2.10", port=11111, name="Hub-A"),
            _record(did="lumi1.BBB", host="192.0.2.20", port=22222, name="Hub-B"),
        ]
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=records)), \
             patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        # The schema should accept hub_did as a vol.In over the discovered DIDs.
        schema_dict = result["data_schema"].schema
        # Find the key whose name is hub_did
        key = next(k for k in schema_dict if str(k) == CONF_HUB_DID)
        validator = schema_dict[key]
        # vol.In renders as a callable that exposes its container; testing
        # behaviour is the most reliable contract.
        assert validator("lumi1.AAA") == "lumi1.AAA"
        assert validator("lumi1.BBB") == "lumi1.BBB"
        with pytest.raises(vol.Invalid):
            validator("lumi1.UNKNOWN")

    async def test_selector_submission_advances_to_credentials(self, hass) -> None:
        """Picking a discovered DID must advance the flow to credentials."""
        records = [
            _record(did="lumi1.AAA", host="192.0.2.10", port=11111, name="Hub-A"),
        ]
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=records)), \
             patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_HUB_DID: "lumi1.AAA"},
            )

        # The flow advances to the credentials step (which is a stub here).
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"


# =============================================================================
# mDNS returns no hubs -> manual form
# =============================================================================


class TestUserStepWithNoDiscoveredHubs:
    async def test_no_hubs_renders_manual_ip_did_form(self, hass) -> None:
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        schema_dict = result["data_schema"].schema
        keys = {str(k) for k in schema_dict}
        assert CONF_HUB_IP in keys
        assert CONF_HUB_DID in keys

    async def test_manual_submission_advances_to_credentials_with_defaults(
        self, hass,
    ) -> None:
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])), \
             patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)):
            init = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {CONF_HUB_IP: "192.168.1.50", CONF_HUB_DID: "lumi1.MANUAL"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"

    async def test_unsafe_manual_hub_ip_is_rejected_before_probe(
        self, hass,
    ) -> None:
        """A manual hub IP with injection chars must be rejected without ever
        probing (and so without sending credentials to that host)."""
        probe = AsyncMock(return_value=ProbeResult.OK)
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])), \
             patch(_PROBE_TARGET, probe):
            init = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {CONF_HUB_IP: "1.2.3.4 --foo=bar", CONF_HUB_DID: "lumi1.X"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_host"}
        probe.assert_not_called()

    async def test_discovery_error_falls_back_to_manual_form(self, hass) -> None:
        """If mDNS raises, the flow must still render the manual form."""
        with patch(
            _DISCOVER_TARGET,
            AsyncMock(side_effect=RuntimeError("zeroconf exploded")),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        schema_dict = result["data_schema"].schema
        keys = {str(k) for k in schema_dict}
        assert CONF_HUB_IP in keys
        assert CONF_HUB_DID in keys


# =============================================================================
# Credentials step (Task 6.2): real cloud login + LANLink checkin validation
# =============================================================================


class TestCredentialsStepFormRender:
    async def test_credentials_step_renders_a_form(self, hass) -> None:
        """After picking a hub, the credentials form must render with the
        documented fields and an Aqara region default of EU."""
        with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])), \
             patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)):
            init = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"},
            )
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {CONF_HUB_IP: "192.168.1.50", CONF_HUB_DID: "lumi1.X"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"
        schema_dict = result["data_schema"].schema
        keys = {str(k) for k in schema_dict}
        assert CONF_AQARA_ACCOUNT in keys
        assert CONF_AQARA_PASSWORD in keys
        assert CONF_AQARA_REGION in keys
        assert CONF_AQARA_USER_ID in keys
        assert CONF_AQARA_TOKEN in keys


def _stub_coordinator_success(*, did: str = "lumi1.HUB") -> MagicMock:
    """Build a HubCoordinator stand-in whose `start()` resolves
    `wait_connected` immediately so the flow's checkin succeeds.

    Stringy properties (``did``, ``token``) are set explicitly so the
    integration's __init__.py setup path can serialize them into HA's
    device registry without tripping JSON encoder recursion on a default
    MagicMock attribute. ``cloud_client`` defaults to None to match the
    real coordinator's pre-setup state.
    """
    coord = MagicMock()
    coord.start = MagicMock()  # sync method on the real class

    async def _wait_connected(timeout: float | None = None) -> None:
        return None

    coord.wait_connected = AsyncMock(side_effect=_wait_connected)
    coord.stop = AsyncMock()
    coord.did = did
    coord.token = "TOK"
    coord.cloud_client = None
    coord.async_read = AsyncMock(return_value={})
    return coord


def _stub_coordinator_timeout() -> MagicMock:
    """Build a HubCoordinator stand-in whose `wait_connected` raises
    `asyncio.TimeoutError` so the flow surfaces `cannot_connect`."""
    coord = MagicMock()
    coord.start = MagicMock()

    async def _wait_connected(timeout: float | None = None) -> None:
        raise asyncio.TimeoutError

    coord.wait_connected = AsyncMock(side_effect=_wait_connected)
    coord.stop = AsyncMock()
    return coord


async def _advance_to_credentials(hass) -> str:
    """Drive the user step manually so a credentials-step test can submit."""
    with patch(_DISCOVER_TARGET, AsyncMock(return_value=[])), \
         patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"},
        )
        cred = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            {CONF_HUB_IP: "192.168.1.50", CONF_HUB_DID: "lumi1.HUB"},
        )
    assert cred["step_id"] == "credentials"
    return cred["flow_id"]


class TestCredentialsCloudLoginPath:
    async def test_cloud_login_success_advances_to_confirm(self, hass) -> None:
        """Email + password + region: cloud login succeeds, checkin
        succeeds, flow advances to the confirm step.
        """
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="USR123", token="TOK123", raw_result={},
            ),
        )
        # Confirm-step preview must not blow up in this credentials test.
        cloud_client.query_device_list = AsyncMock(return_value=[])
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "hunter2",
                    CONF_AQARA_REGION: "EU",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"
        # The credentials-step contract: ``login`` was awaited exactly
        # once with the user's credentials. We deliberately do NOT assert
        # on cloud_factory.call_count - that couples this test to the
        # confirm step's implementation detail of also constructing a
        # client for the device-count preview.
        cloud_client.login.assert_awaited_once_with("user@example.com", "hunter2")
        coord.start.assert_called_once()
        coord.wait_connected.assert_awaited_once()
        coord.stop.assert_awaited_once()

    async def test_cloud_login_failure_renders_error(self, hass) -> None:
        """AqaraAuthError from `login` must surface `aqara_login_failed`."""
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            side_effect=AqaraAuthError("bad password"),
        )
        cloud_factory = MagicMock(return_value=cloud_client)
        coord_factory = MagicMock(return_value=_stub_coordinator_success())

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "wrong",
                    CONF_AQARA_REGION: "EU",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"
        assert result["errors"] == {"base": "aqara_login_failed"}
        # Coordinator must NOT have been constructed since cloud login failed.
        coord_factory.assert_not_called()


class TestCredentialsManualTokenPath:
    async def test_manual_tokens_skip_cloud_login(self, hass) -> None:
        """Pasted user_id + token must skip cloud login entirely and only
        run the LANLink checkin.

        The cloud factory may be invoked once by the confirm-step
        preview, but ``cloud_client.login`` must never be awaited.
        """
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=[])
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "MANUAL_USR",
                    CONF_AQARA_TOKEN: "MANUAL_TOK",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"
        # The login API must never have been awaited - that's the
        # contract we care about.
        cloud_client.login.assert_not_awaited()
        coord.start.assert_called_once()
        coord.wait_connected.assert_awaited_once()
        coord.stop.assert_awaited_once()
        # Verify the coordinator was constructed with the manual creds.
        ctor_kwargs = coord_factory.call_args.kwargs
        assert ctor_kwargs.get("user_id") == "MANUAL_USR"
        assert ctor_kwargs.get("token") == "MANUAL_TOK"

    async def test_manual_tokens_win_when_both_provided(self, hass) -> None:
        """If both an account and manual tokens are supplied, the manual
        tokens take precedence and cloud login is skipped."""
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock()
        cloud_client.query_device_list = AsyncMock(return_value=[])
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "hunter2",
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "MANUAL_USR",
                    CONF_AQARA_TOKEN: "MANUAL_TOK",
                },
            )

        assert result["step_id"] == "confirm"
        # Cloud login was NOT used: the manual tokens won.
        cloud_client.login.assert_not_awaited()
        ctor_kwargs = coord_factory.call_args.kwargs
        assert ctor_kwargs.get("user_id") == "MANUAL_USR"
        assert ctor_kwargs.get("token") == "MANUAL_TOK"


class TestCredentialsCheckinFailure:
    async def test_checkin_timeout_renders_cannot_connect(self, hass) -> None:
        """A LANLink checkin that never completes within the timeout must
        surface `cannot_connect`."""
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(user_id="U", token="T", raw_result={}),
        )
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_timeout()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "hunter2",
                    CONF_AQARA_REGION: "EU",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"
        assert result["errors"] == {"base": "cannot_connect"}
        # stop() must still be awaited so the half-open coordinator is torn
        # down even though wait_connected raised.
        coord.stop.assert_awaited_once()


class TestCredentialsValidation:
    async def test_no_credentials_at_all_renders_required_error(
        self, hass,
    ) -> None:
        """Submitting with neither cloud creds nor manual tokens must
        surface `aqara_credentials_required` and not invoke either the
        cloud client or the coordinator."""
        flow_id = await _advance_to_credentials(hass)

        cloud_factory = MagicMock()
        coord_factory = MagicMock()
        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {CONF_AQARA_REGION: "EU"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"
        assert result["errors"] == {"base": "aqara_credentials_required"}
        cloud_factory.assert_not_called()
        coord_factory.assert_not_called()

    async def test_partial_manual_tokens_alone_surfaces_required_error(
        self, hass,
    ) -> None:
        """Partial manual tokens with no cloud credentials must surface
        `aqara_credentials_required`. Manual tokens require BOTH user_id
        and token; supplying only one with no account/password leaves no
        viable path to validate, so neither the cloud client nor the
        coordinator is invoked."""
        flow_id = await _advance_to_credentials(hass)

        cloud_factory = MagicMock()
        coord_factory = MagicMock()
        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "MANUAL_USR",
                    # CONF_AQARA_TOKEN intentionally omitted
                },
            )

        assert result["step_id"] == "credentials"
        assert result["errors"] == {"base": "aqara_credentials_required"}
        cloud_factory.assert_not_called()
        coord_factory.assert_not_called()

    async def test_partial_manual_tokens_with_cloud_creds_uses_cloud_path(
        self, hass,
    ) -> None:
        """Partial manual tokens combined with valid cloud credentials
        must fall through to the cloud-login path. This tightens the
        manual-tokens-require-both contract: a half-filled manual pair
        is treated as absent and the account/password are exercised
        instead."""
        flow_id = await _advance_to_credentials(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="CLOUD_USR", token="CLOUD_TOK", raw_result={},
            ),
        )
        cloud_client.query_device_list = AsyncMock(return_value=[])
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id,
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "hunter2",
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "MANUAL_USR",
                    # CONF_AQARA_TOKEN intentionally omitted
                },
            )

        # Cloud path was taken, not the manual path: login was awaited
        # with the supplied credentials. (The factory is invoked twice -
        # once for login, once for the confirm-step preview.)
        cloud_client.login.assert_awaited_once_with("user@example.com", "hunter2")
        # Coordinator was driven by the cloud-issued tokens, not the
        # half-filled manual pair.
        coord.start.assert_called_once()
        coord.wait_connected.assert_awaited_once()
        coord.stop.assert_awaited_once()
        ctor_kwargs = coord_factory.call_args.kwargs
        assert ctor_kwargs.get("user_id") == "CLOUD_USR"
        assert ctor_kwargs.get("token") == "CLOUD_TOK"
        # Flow advanced to confirm.
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"


# =============================================================================
# Confirm step (Task 6.3): cloud device-count preview + entry creation
# =============================================================================


_HUB_DID = "lumi1.HUB"
_HUB_IP = "192.168.1.50"
_HUB_PORT_DEFAULT = 59703


async def _drive_to_confirm(
    hass,
    *,
    cloud_client: MagicMock,
    coord: MagicMock,
    hub_did: str = _HUB_DID,
    hub_ip: str = _HUB_IP,
    account: str = "user@example.com",
    password: str = "hunter2",
    region: str = "EU",
) -> dict:
    """Drive user + credentials steps inside a single patched context.

    The caller supplies a fully-configured cloud_client mock (whose
    ``login`` resolves AND whose ``query_device_list`` is set up to
    return the desired preview behaviour) and a coordinator mock.
    Optional ``account``/``password``/``region`` override the default
    credentials submitted to the credentials step (useful for
    multi-account / multi-region scenarios). Returns the flow result
    for the confirm step's initial render.
    """
    cloud_factory = MagicMock(return_value=cloud_client)
    coord_factory = MagicMock(return_value=coord)

    with (
        patch(_DISCOVER_TARGET, AsyncMock(return_value=[])),
        patch(_PROBE_TARGET, AsyncMock(return_value=ProbeResult.OK)),
        patch(_CLOUD_CLIENT_TARGET, cloud_factory),
        patch(_HUB_COORDINATOR_TARGET, coord_factory),
    ):
        init = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"},
        )
        await hass.config_entries.flow.async_configure(
            init["flow_id"],
            {CONF_HUB_IP: hub_ip, CONF_HUB_DID: hub_did},
        )
        confirm_render = await hass.config_entries.flow.async_configure(
            init["flow_id"],
            {
                CONF_AQARA_ACCOUNT: account,
                CONF_AQARA_PASSWORD: password,
                CONF_AQARA_REGION: region,
            },
        )
    return confirm_render


class TestConfirmStepCloudPreview:
    async def test_happy_path_shows_count_and_creates_entry(self, hass) -> None:
        """Cloud returns the hub + 5 sub-devices. The confirm form's
        description placeholders must reflect 6 (hub + 5). Submitting the
        empty form must create the entry with the cloud-supplied model
        and all credential fields populated."""
        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="USR123", token="TOK123", raw_result={},
            ),
        )
        cloud_client.query_device_list = AsyncMock(
            return_value=[
                {
                    "did": _HUB_DID,
                    "model": "lumi.gateway.agl004",
                    "deviceName": "Aqara Hub M3",
                    "parentDeviceId": "",
                },
                {
                    "did": "lumi1.SUB1", "model": "lumi.sensor_motion",
                    "parentDeviceId": _HUB_DID,
                },
                {
                    "did": "lumi1.SUB2", "model": "lumi.sensor_magnet",
                    "parentDeviceId": _HUB_DID,
                },
                {
                    "did": "lumi1.SUB3", "model": "lumi.switch",
                    "parentDeviceId": _HUB_DID,
                },
                {
                    "did": "lumi1.SUB4", "model": "lumi.plug",
                    "parentDeviceId": _HUB_DID,
                },
                {
                    "did": "lumi1.SUB5", "model": "lumi.curtain",
                    "parentDeviceId": _HUB_DID,
                },
                # An unrelated device on a different hub - must NOT be counted.
                {
                    "did": "lumi1.OTHER", "model": "lumi.foo",
                    "parentDeviceId": "lumi1.OTHERHUB",
                },
            ],
        )
        coord = _stub_coordinator_success()

        confirm_render = await _drive_to_confirm(
            hass, cloud_client=cloud_client, coord=coord,
        )

        assert confirm_render["type"] == FlowResultType.FORM
        assert confirm_render["step_id"] == "confirm"
        placeholders = confirm_render.get("description_placeholders") or {}
        assert placeholders.get("hub_did") == _HUB_DID
        assert placeholders.get("hub_model") == "lumi.gateway.agl004"
        # 5 sub-devices + the hub itself = 6 devices total.
        assert placeholders.get("device_count") == "6"
        cloud_client.query_device_list.assert_awaited_once_with("TOK123")

        # Submit the empty confirm form - entry must be created.
        result = await hass.config_entries.flow.async_configure(
            confirm_render["flow_id"], {},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        # The cloud record carried a human-readable deviceName; the entry
        # title must use it in preference to the model+DID fallback.
        assert result["title"] == "Aqara Hub M3"
        data = result["data"]
        assert data[CONF_HUB_DID] == _HUB_DID
        assert data[CONF_HUB_IP] == _HUB_IP
        assert data[CONF_HUB_PORT] == _HUB_PORT_DEFAULT
        assert data[CONF_HUB_MODEL] == "lumi.gateway.agl004"
        assert data[CONF_AQARA_REGION] == "EU"
        assert data[CONF_AQARA_USER_ID] == "USR123"
        assert data[CONF_AQARA_TOKEN] == "TOK123"
        assert data[CONF_AQARA_ACCOUNT] == "user@example.com"
        # Unique id was set so a duplicate-DID flow would abort.
        assert result["result"].unique_id == _HUB_DID

    async def test_cloud_unreachable_still_creates_entry(self, hass) -> None:
        """If the cloud preview raises, the form must still render with
        an abbreviated description and submission must still create the
        entry using the credential-step fallback model."""
        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="USR123", token="TOK123", raw_result={},
            ),
        )
        cloud_client.query_device_list = AsyncMock(
            side_effect=AqaraAuthError("temporarily unavailable"),
        )
        coord = _stub_coordinator_success()

        confirm_render = await _drive_to_confirm(
            hass, cloud_client=cloud_client, coord=coord,
        )

        assert confirm_render["type"] == FlowResultType.FORM
        assert confirm_render["step_id"] == "confirm"
        placeholders = confirm_render.get("description_placeholders") or {}
        # Description must NOT show a numeric count when cloud failed.
        count_str = placeholders.get("device_count", "")
        assert count_str and not count_str.isdigit()
        assert placeholders.get("hub_did") == _HUB_DID
        # No real model came back from the cloud; the default fallback
        # the credentials step used must be carried forward.
        assert placeholders.get("hub_model") == "lumi.gateway.agl004"

        # Submission still creates the entry using the fallback model.
        result = await hass.config_entries.flow.async_configure(
            confirm_render["flow_id"], {},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        data = result["data"]
        assert data[CONF_HUB_DID] == _HUB_DID
        assert data[CONF_HUB_MODEL] == "lumi.gateway.agl004"
        assert data[CONF_AQARA_USER_ID] == "USR123"
        assert data[CONF_AQARA_TOKEN] == "TOK123"

    async def test_duplicate_did_aborts_already_configured(self, hass) -> None:
        """A second flow for the same hub_did must abort with reason
        ``already_configured`` after `_abort_if_unique_id_configured`."""
        # First flow: complete it end to end so the entry is registered
        # against unique_id == _HUB_DID.
        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="USR123", token="TOK123", raw_result={},
            ),
        )
        cloud_client.query_device_list = AsyncMock(
            return_value=[
                {
                    "did": _HUB_DID, "model": "lumi.gateway.agl004",
                    "parentDeviceId": "",
                },
            ],
        )
        coord = _stub_coordinator_success()

        confirm_render = await _drive_to_confirm(
            hass, cloud_client=cloud_client, coord=coord,
        )
        first = await hass.config_entries.flow.async_configure(
            confirm_render["flow_id"], {},
        )
        assert first["type"] == FlowResultType.CREATE_ENTRY

        # Second flow with the same DID: drive it up to the confirm
        # submission. The abort happens at submit time when we call
        # async_set_unique_id + _abort_if_unique_id_configured.
        cloud_client_2 = MagicMock()
        cloud_client_2.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="USR999", token="TOK999", raw_result={},
            ),
        )
        cloud_client_2.query_device_list = AsyncMock(return_value=[])
        coord_2 = _stub_coordinator_success()

        confirm_render_2 = await _drive_to_confirm(
            hass, cloud_client=cloud_client_2, coord=coord_2,
        )
        result = await hass.config_entries.flow.async_configure(
            confirm_render_2["flow_id"], {},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"


# =============================================================================
# Subentry flow (Task 6.4): pick-device step
# =============================================================================


def _hub_entry(hass, *, hub_did: str = "lumi1.HUB") -> MockConfigEntry:
    """Build + register a hub config entry with the data the subentry flow reads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=hub_did,
        title="Test Hub",
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


def _build_subentry_flow(
    hass,
    entry: MockConfigEntry,
    *,
    existing_subentries: dict | None = None,
    runtime_data: object | None = None,
) -> AqaraDeviceSubentryFlow:
    """Instantiate the subentry flow with the wiring HA's flow manager
    would otherwise set up.

    HA 2025.2+'s flow manager seeds ``handler``, ``hass``, ``flow_id``,
    and ``context`` on the flow instance. We replicate that minimal
    surface so tests can drive the flow without depending on the
    subentry flow manager (which isn't present on every HA the test
    plugin pins to).
    """
    if existing_subentries is not None:
        # Attach a synthetic ``subentries`` mapping to the entry. The
        # production code reads it via ``getattr(entry, "subentries", None)``,
        # so any mapping shape works at test time.
        object.__setattr__(entry, "subentries", existing_subentries)
    if runtime_data is not None:
        object.__setattr__(entry, "runtime_data", runtime_data)

    flow = AqaraDeviceSubentryFlow()
    flow.hass = hass
    flow.handler = (entry.entry_id, "device")
    flow.flow_id = "test_flow_id"
    flow.context = {"source": "user"}
    return flow


def _device_record(
    *,
    did: str,
    model: str,
    name: str,
    parent: str = "lumi1.HUB",
) -> dict:
    return {
        "did": did,
        "model": model,
        "deviceName": name,
        "parentDeviceId": parent,
    }


# A trait list that auto-derive will turn into at least one descriptor.
# The shape mirrors what `query_device_traits` returns: each trait has
# `path`, `propertyId`, `value`, and supplementary fields. A simple
# boolean-valued trait under a non-internal path is enough to produce
# a switch or binary_sensor descriptor.
_TRAIT_BOOLEAN = [
    {
        "path": "1.power.test",
        "propertyId": ["test_power"],
        "value": "0",
        "valueType": "boolean",
        "enums": "",
        "unit": "",
        "min": "",
        "max": "",
        "step": "",
        "defaultValue": "0",
        "permission": "rw",
    },
]


class TestSubentryFlowPickDevice:
    async def test_five_devices_one_virtual_one_existing_renders_picker(
        self, hass,
    ) -> None:
        """Cloud returns the hub plus four sub-devices: one is already
        added (excluded with info-text), one is virtual (excluded with
        info-text), one has a registered override class (always
        eligible), one auto-derive candidate yields descriptors, and one
        has no descriptors (now classified as supported_bootstrap and
        included in the picker). The form must list the three eligible
        ones in the multi-select; only the virtual and already-added
        devices surface in the info text.
        """
        entry = _hub_entry(hass)

        sub_existing = SimpleNamespace(
            subentry_id="existing-sub",
            data={CONF_DEVICE_DID: "lumi1.SUB1", CONF_DEVICE_MODEL: "lumi.foo"},
        )
        flow = _build_subentry_flow(
            hass, entry, existing_subentries={sub_existing.subentry_id: sub_existing},
        )

        device_list = [
            # The hub itself - must NOT appear in the picker.
            _device_record(
                did="lumi1.HUB", model="lumi.gateway.agl004",
                name="Aqara Hub M3", parent="",
            ),
            # Already-added device - must surface in the info text.
            _device_record(
                did="lumi1.SUB1", model="lumi.foo", name="Existing Sensor",
            ),
            # Virtual soft-sensor - must surface in the info text.
            _device_record(
                did="virtual.softA", model="virtual.soft",
                name="Soft Temperature",
            ),
            # Registered-override device. The override class doesn't
            # have to actually exist for this flow to label it
            # "supported_override" - the test patches the registry so
            # the lookup returns a marker class.
            _device_record(
                did="lumi1.SUB2", model="lumi.override.x",
                name="Override Switch",
            ),
            # Auto-derive candidate - traits produce a descriptor.
            _device_record(
                did="lumi1.SUB3", model="lumi.auto.good",
                name="Auto Sensor",
            ),
            # Auto-derive candidate - traits return empty descriptors.
            _device_record(
                did="lumi1.SUB4", model="lumi.auto.empty",
                name="Auto Empty",
            ),
            # Cross-hub device - must not be considered at all.
            _device_record(
                did="lumi1.OTHER", model="lumi.foreign",
                name="Other Hub Device", parent="lumi1.OTHERHUB",
            ),
        ]

        # Override-class lookup: only the lumi.override.x model has one.
        # Returning a tiny class without ``EXTRA_CONFIG_SCHEMA`` keeps
        # the submit path on the no-extras branch (verified by a
        # follow-on test below).
        class _OverrideMarker:
            EXTRA_CONFIG_SCHEMA = None

        def _fake_get_class(model):
            return _OverrideMarker if model == "lumi.override.x" else None

        async def _async_devices(self, region, token):
            return device_list

        # Catalogue-first: build_descriptors is stubbed so lumi.auto.good
        # returns one descriptor (supported) and lumi.auto.empty returns
        # none (unsupported). No cloud call is made.
        _stub_desc = object()

        def _stub_build(model, overlay):
            return [_stub_desc] if model == "lumi.auto.good" else []

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                side_effect=_fake_get_class,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
            patch(
                "custom_components.aqara_lanlink.device.build_descriptors.build_descriptors",
                side_effect=_stub_build,
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        # The supported set must be exactly {SUB2 (override), SUB3 (auto-good),
        # SUB4 (auto-empty, bootstrap)}. SUB1 (existing) is excluded.
        # virtual.softA is unsupported. The hub itself is filtered out.
        # Task 9: uncatalogued models (no descriptors, no override class) now
        # surface in the picker as supported_bootstrap instead of unsupported.
        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        selector = schema[key]
        # SelectSelector exposes its serialised options on ``.config``.
        options = selector.config["options"]
        choices = {opt["value"]: opt["label"] for opt in options}
        assert set(choices.keys()) == {"lumi1.SUB2", "lumi1.SUB3", "lumi1.SUB4"}
        # Labels include the friendly name and the model.
        assert "Override Switch" in choices["lumi1.SUB2"]
        assert "lumi.override.x" in choices["lumi1.SUB2"]

        # Description placeholders include the supported count and
        # info text mentioning only the virtual and already-added devices.
        # Auto Empty is now supported (bootstrap), so it must NOT appear in
        # the unsupported info block.
        placeholders = result.get("description_placeholders") or {}
        assert placeholders["supported_count"] == "3"
        info = placeholders["unsupported_info"]
        assert "Existing Sensor" in info
        assert "Soft Temperature" in info  # virtual
        assert "Auto Empty" not in info  # now eligible via bootstrap path
        # The hub itself must NOT appear in either set.
        assert "Aqara Hub M3" not in info
        assert "lumi1.HUB" not in info

    async def test_supported_with_extras_advances_to_extras_step(
        self, hass,
    ) -> None:
        """A picked supported device whose override class declares an
        ``EXTRA_CONFIG_SCHEMA`` must advance the flow to the
        ``device_extras`` step. The extras step renders the device
        class's schema as the form; the actual schema content varies
        per device."""
        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            _device_record(
                did="lumi1.HUB", model="lumi.gateway.agl004",
                name="Hub", parent="",
            ),
            _device_record(
                did="lumi1.G400", model="lumi.camera.g400",
                name="G400 Doorbell",
            ),
        ]

        class _G400WithExtras:
            EXTRA_CONFIG_SCHEMA = vol.Schema(
                {vol.Required("rtsp_path"): str},
            )

        def _fake_get_class(model):
            return _G400WithExtras if model == "lumi.camera.g400" else None

        async def _async_devices(self, region, token):
            return device_list

        # The G400 has an override class, so _classify returns SUPPORTED_OVERRIDE
        # without consulting build_descriptors. No cloud call needed.
        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                side_effect=_fake_get_class,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
        ):
            # Initial render so the flow caches device records.
            await flow.async_step_user()
            # Submit picking the G400 (single-select shape).
            result = await flow.async_step_pick_device(
                {"device": "lumi1.G400"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "device_extras"
        # The form's schema must be the device class's
        # EXTRA_CONFIG_SCHEMA, exposing the per-device fields.
        schema_keys = {str(k) for k in result["data_schema"].schema}
        assert "rtsp_path" in schema_keys

    async def test_no_extras_submission_creates_subentry_directly(
        self, hass,
    ) -> None:
        """A picked supported device with NO override-class extras must
        create the subentry directly. The created entry's data must
        carry the device DID, model, and the cloud metadata + cached
        traits so setup-time auto-derive doesn't have to refetch."""
        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            _device_record(
                did="lumi1.HUB", model="lumi.gateway.agl004",
                name="Hub", parent="",
            ),
            _device_record(
                did="lumi1.AUTO", model="lumi.auto.good",
                name="Auto Sensor",
            ),
        ]

        async def _async_devices(self, region, token):
            return device_list

        # Catalogue-first: stub build_descriptors to return a descriptor
        # for lumi.auto.good so _classify returns SUPPORTED_AUTO_DERIVE_UNVERIFIED.
        def _fake_get_class(model):
            return None

        _stub_desc = object()

        def _stub_build(model, overlay):
            return [_stub_desc] if model == "lumi.auto.good" else []

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                side_effect=_fake_get_class,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
            patch(
                "custom_components.aqara_lanlink.device.build_descriptors.build_descriptors",
                side_effect=_stub_build,
            ),
        ):
            # Render then submit (single-select shape).
            await flow.async_step_user()
            result = await flow.async_step_pick_device(
                {"device": "lumi1.AUTO"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Auto Sensor"
        assert result["data"][CONF_DEVICE_DID] == "lumi1.AUTO"
        assert result["data"][CONF_DEVICE_MODEL] == "lumi.auto.good"
        assert "_cloud_metadata" in result["data"]
        assert result["data"]["_cloud_metadata"]["did"] == "lumi1.AUTO"
        # _cloud_traits is no longer stashed: catalogue-first classify makes
        # no cloud call, so the trait cache is never populated.
        assert "_cloud_traits" not in result["data"]
        # The subentry's ``unique_id`` must be the DID so duplicate-add
        # protection works at the subentry layer.
        assert result["unique_id"] == "lumi1.AUTO"

    async def test_cloud_unreachable_shows_cannot_connect_error(
        self, hass,
    ) -> None:
        """If the cloud device-list call fails, the form must re-render
        with a ``cannot_connect`` error so the user can retry without
        losing the wizard state."""
        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry)

        async def _failing_devices(self, region, token):
            raise AqaraAuthError("temporarily unavailable")

        with patch.object(
            AqaraDeviceSubentryFlow, "_fetch_device_list", _failing_devices,
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_topology_mismatch_logs_warning_but_proceeds(
        self, hass, caplog,
    ) -> None:
        """If the LANLink coordinator reports DIDs the cloud does not,
        a WARNING must be logged but the flow must still render the
        picker form. The mismatch is informational only."""
        # Coordinator stand-in: the production code only reads
        # ``runtime_data.hub.lanlink_topology_dids`` (and ``hub.did``).
        hub_stub = SimpleNamespace(
            did="lumi1.HUB",
            lanlink_topology_dids=frozenset({"lumi1.GHOST", "lumi1.SUB1"}),
        )
        runtime_stub = SimpleNamespace(hub=hub_stub)

        entry = _hub_entry(hass)
        flow = _build_subentry_flow(
            hass, entry, runtime_data=runtime_stub,
        )

        device_list = [
            _device_record(
                did="lumi1.HUB", model="lumi.gateway.agl004",
                name="Hub", parent="",
            ),
            _device_record(
                did="lumi1.SUB1", model="lumi.auto.good",
                name="Visible Sensor",
            ),
            # lumi1.GHOST is what the LANLink push frame reports but
            # the cloud does NOT list - the warn-only branch.
        ]

        async def _async_devices(self, region, token):
            return device_list

        def _fake_get_class(model):
            return None

        # Catalogue-first: stub build_descriptors so lumi.auto.good returns
        # a descriptor and thus classifies as supported.
        _stub_desc = object()

        def _stub_build(model, overlay):
            return [_stub_desc] if model == "lumi.auto.good" else []

        with caplog.at_level(
            logging.WARNING, logger="custom_components.aqara_lanlink.config_flow",
        ):
            with (
                patch.object(
                    config_flow_module.registry,
                    "get_device_class",
                    side_effect=_fake_get_class,
                ),
                patch.object(
                    AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
                ),
                patch(
                    "custom_components.aqara_lanlink.device.build_descriptors.build_descriptors",
                    side_effect=_stub_build,
                ),
            ):
                result = await flow.async_step_user()

        # Form rendered as normal: SUB1 is in the picker.
        assert result["type"] == FlowResultType.FORM
        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        choices = {opt["value"] for opt in options}
        assert "lumi1.SUB1" in choices

        # Warning was emitted for the missing DID.
        warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING"
            and "LANLink topology reports DIDs the cloud does not list"
            in r.getMessage()
        ]
        assert warnings, (
            "expected a topology-mismatch WARNING; got %r"
            % [r.getMessage() for r in caplog.records]
        )
        assert "lumi1.GHOST" in warnings[0].getMessage()

    async def test_main_flow_advertises_device_subentry_type(
        self, hass,
    ) -> None:
        """The hub config flow must expose the device subentry type via
        ``async_get_supported_subentry_types``. HA's UI consults this
        to render the entry's "Add device" action."""
        from custom_components.aqara_lanlink.config_flow import (
            AqaraLanLinkConfigFlow,
        )

        entry = _hub_entry(hass)
        types = AqaraLanLinkConfigFlow.async_get_supported_subentry_types(entry)
        assert set(types.keys()) == {"device"}
        assert types["device"] is AqaraDeviceSubentryFlow


# =============================================================================
# Subentry flow (Task 6.5): device_extras step
# =============================================================================


async def _drive_picker_to_extras(
    hass,
    *,
    device_record: dict,
    device_class: type | None,
    traits: list[dict] | None = None,
) -> tuple[AqaraDeviceSubentryFlow, dict]:
    """Drive the picker up to the point where it dispatches to
    ``device_extras``.

    Returns the flow instance (so the caller can submit further steps
    against it) and the result of the pick_device submission. The
    parent config entry is registered, the cloud device-list returns
    only the hub plus ``device_record``, and ``registry.get_device_class``
    is patched to return ``device_class`` for the device's model.

    Catalogue-first: _classify no longer makes cloud calls. When
    device_class is None, build_descriptors is stubbed to return a
    single descriptor so the device classifies as supported.
    """
    entry = _hub_entry(hass)
    flow = _build_subentry_flow(hass, entry)

    device_list = [
        _device_record(
            did="lumi1.HUB", model="lumi.gateway.agl004",
            name="Hub", parent="",
        ),
        device_record,
    ]

    target_model = device_record.get("model", "")

    def _fake_get_class(model):
        return device_class if model == target_model else None

    async def _async_devices(self, region, token):
        return device_list

    # Stub build_descriptors to return a single descriptor for the
    # target model so _classify returns _SUPPORTED_AUTO_DERIVE.
    # Override classes are handled by _fake_get_class -> _SUPPORTED_OVERRIDE.
    _stub_descriptor = object()

    def _stub_build(model, overlay):
        if model == target_model and device_class is None:
            return [_stub_descriptor]
        return []

    with (
        patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=_fake_get_class,
        ),
        patch.object(
            AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
        ),
        patch(
            "custom_components.aqara_lanlink.device.build_descriptors.build_descriptors",
            side_effect=_stub_build,
        ),
    ):
        await flow.async_step_user()
        result = await flow.async_step_pick_device(
            {"device": device_record["did"]},
        )
    return flow, result


class TestSubentryFlowDeviceExtras:
    async def test_no_override_class_skips_form_and_creates_subentry(
        self, hass,
    ) -> None:
        """An auto-derive device (no override class) must skip the
        extras form entirely - the picker dispatches to
        ``device_extras`` and the subentry is created immediately with
        an empty ``extras`` payload (data carries cloud metadata + traits
        only)."""
        device_record = _device_record(
            did="lumi1.BULB", model="lumi.bulb.basic",
            name="Lounge Bulb",
        )
        _, result = await _drive_picker_to_extras(
            hass,
            device_record=device_record,
            device_class=None,
            traits=list(_TRAIT_BOOLEAN),
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Lounge Bulb"
        data = result["data"]
        assert data[CONF_DEVICE_DID] == "lumi1.BULB"
        assert data[CONF_DEVICE_MODEL] == "lumi.bulb.basic"
        assert data["_cloud_metadata"]["did"] == "lumi1.BULB"
        # _cloud_traits is no longer stashed: catalogue-first classify makes
        # no cloud call, so the trait cache is never populated.
        assert "_cloud_traits" not in data
        assert result["unique_id"] == "lumi1.BULB"

    async def test_override_with_extras_renders_form_then_creates_subentry(
        self, hass,
    ) -> None:
        """An override class declaring ``EXTRA_CONFIG_SCHEMA`` must
        render the schema as the extras form on first dispatch, and
        then create the subentry with the user's extras merged into the
        data dict on submission."""

        class _G400Override:
            EXTRA_CONFIG_SCHEMA = vol.Schema(
                {
                    vol.Required("rtsp_path"): str,
                    vol.Optional("rtsp_port", default=554): int,
                },
            )

        device_record = _device_record(
            did="lumi1.G400", model="lumi.camera.g400",
            name="G400 Doorbell",
        )
        flow, render_result = await _drive_picker_to_extras(
            hass,
            device_record=device_record,
            device_class=_G400Override,
            traits=[],
        )

        assert render_result["type"] == FlowResultType.FORM
        assert render_result["step_id"] == "device_extras"
        # The form renders the device class's schema verbatim.
        schema_keys = {str(k) for k in render_result["data_schema"].schema}
        assert "rtsp_path" in schema_keys
        assert "rtsp_port" in schema_keys

        # Submit the extras form. The class declares no
        # ``async_validate_extras`` hook so creation must succeed.
        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                _G400Override if model == "lumi.camera.g400" else None
            ),
        ):
            result = await flow.async_step_device_extras(
                {"rtsp_path": "/live/stream1", "rtsp_port": 555},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        data = result["data"]
        assert data[CONF_DEVICE_DID] == "lumi1.G400"
        assert data[CONF_DEVICE_MODEL] == "lumi.camera.g400"
        # The extras must be merged on top of the standard subentry data.
        assert data["rtsp_path"] == "/live/stream1"
        assert data["rtsp_port"] == 555
        # Reserved keys still carry the cloud-derived values.
        assert data["_cloud_metadata"]["did"] == "lumi1.G400"
        assert result["unique_id"] == "lumi1.G400"

    async def test_async_validate_extras_error_re_renders_form(
        self, hass,
    ) -> None:
        """If the override class's ``async_validate_extras`` returns an
        error key, the form must re-render with that key under
        ``errors['base']`` and no subentry must be created."""

        class _CameraOverride:
            EXTRA_CONFIG_SCHEMA = vol.Schema(
                {vol.Required("rtsp_path"): str},
            )

            @staticmethod
            async def async_validate_extras(hass, data):
                # Pretend the TCP probe rejected the supplied path.
                return "rtsp_unreachable"

        device_record = _device_record(
            did="lumi1.CAM", model="lumi.camera.foo",
            name="Test Camera",
        )
        flow, _ = await _drive_picker_to_extras(
            hass,
            device_record=device_record,
            device_class=_CameraOverride,
            traits=[],
        )

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                _CameraOverride if model == "lumi.camera.foo" else None
            ),
        ):
            result = await flow.async_step_device_extras(
                {"rtsp_path": "/bogus"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "device_extras"
        assert result["errors"] == {"base": "rtsp_unreachable"}

    async def test_async_validate_extras_success_creates_subentry(
        self, hass,
    ) -> None:
        """If ``async_validate_extras`` returns ``None``, validation
        passes and the subentry is created with the supplied extras
        merged into its data dict."""

        validate_calls: list[dict] = []

        class _CameraOverride:
            EXTRA_CONFIG_SCHEMA = vol.Schema(
                {vol.Required("rtsp_path"): str},
            )

            @staticmethod
            async def async_validate_extras(hass, data):
                validate_calls.append(dict(data))
                return None

        device_record = _device_record(
            did="lumi1.CAM2", model="lumi.camera.bar",
            name="Test Camera 2",
        )
        flow, _ = await _drive_picker_to_extras(
            hass,
            device_record=device_record,
            device_class=_CameraOverride,
            traits=[],
        )

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                _CameraOverride if model == "lumi.camera.bar" else None
            ),
        ):
            result = await flow.async_step_device_extras(
                {"rtsp_path": "/live/main"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["rtsp_path"] == "/live/main"
        assert result["data"][CONF_DEVICE_DID] == "lumi1.CAM2"
        # The hook was awaited once with the user's input.
        assert validate_calls == [{"rtsp_path": "/live/main"}]

    async def test_extras_with_reserved_key_does_not_clobber_identity(
        self, hass,
    ) -> None:
        """A buggy override class that names an extras field after a
        reserved subentry key (``did``, ``model``, ``_cloud_metadata``,
        ``_cloud_traits``) must NOT corrupt the subentry's identity:
        the reserved key keeps its cloud-derived value and the extras
        value is dropped with a warning."""

        class _BadOverride:
            EXTRA_CONFIG_SCHEMA = vol.Schema(
                {vol.Optional("did", default=""): str},
            )

        device_record = _device_record(
            did="lumi1.GOOD", model="lumi.bad.shape",
            name="Stubborn Device",
        )
        flow, _ = await _drive_picker_to_extras(
            hass,
            device_record=device_record,
            device_class=_BadOverride,
            traits=[],
        )

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                _BadOverride if model == "lumi.bad.shape" else None
            ),
        ):
            result = await flow.async_step_device_extras(
                {"did": "lumi1.HIJACKED"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        # The reserved key was NOT clobbered.
        assert result["data"][CONF_DEVICE_DID] == "lumi1.GOOD"
        assert result["unique_id"] == "lumi1.GOOD"


# =============================================================================
# Subentry flow (Task 15): device_extras pre-population from async_prefill_extras
# =============================================================================


class TestSubentryFlowExtrasPreFill:
    async def test_device_extras_prefills_form_on_successful_read(
        self, hass, monkeypatch,
    ) -> None:
        """When async_prefill_extras returns values, the extras form is rendered
        with those values as suggested defaults."""
        # async_prefill_extras reads cls.RTSP_URL_TRAIT_PATH from the
        # G400Device class attribute; patch it there so the hook sees the
        # test path.
        test_path = "1.99.20368"
        monkeypatch.setattr(G400Device, "RTSP_URL_TRAIT_PATH", test_path)
        # Prefill also filters candidates against the catalogue's known
        # paths (so cameras whose V3 spec lacks CameraRTSPURL skip the
        # cloud query entirely); stub catalog so the test path is known.
        from custom_components.aqara_lanlink.device import catalog as _catalog
        monkeypatch.setattr(
            _catalog, "all_traits_for_model", lambda model: {test_path: None},
        )
        monkeypatch.setattr(
            _catalog, "dropped_paths_for_model", lambda model: frozenset(),
        )
        monkeypatch.setattr(
            _catalog, "endpoints_for_model", lambda model: {},
        )

        # Hub coordinator stub whose cloud client returns a bare RTSP URL.
        # Prefill switched from LANLink relay-read to cloud trait-read after
        # observing LANLink reads time out for CameraRTSPURL on real hubs.
        rtsp_url = "rtsp://admin:secret@10.1.20.150:8554/ch1"

        hub_stub = MagicMock()
        hub_stub.token = "test-token"
        hub_stub.cloud_client = MagicMock()
        hub_stub.cloud_client.query_device_traits = AsyncMock(
            return_value=[{"path": test_path, "value": rtsp_url}],
        )
        runtime_stub = SimpleNamespace(hub=hub_stub)

        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry, runtime_data=runtime_stub)

        g400_record = _device_record(
            did="lumi1.G400CAM",
            model="lumi.camera.agl013",
            name="G400 Doorbell",
        )
        flow._picked_device = g400_record

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                G400Device if model == "lumi.camera.agl013" else None
            ),
        ), patch.object(
            AqaraDeviceSubentryFlow,
            "add_suggested_values_to_schema",
            wraps=flow.add_suggested_values_to_schema,
        ) as mock_add_suggested:
            result = await flow.async_step_device_extras(None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "device_extras"
        mock_add_suggested.assert_called_once()
        call_args = mock_add_suggested.call_args
        # add_suggested_values_to_schema(schema, suggested_values) -- positional args
        suggested_values = call_args[0][1]
        assert suggested_values.get("camera_ip") == "10.1.20.150"
        assert suggested_values.get("rtsp_username") == "admin"
        assert suggested_values.get("rtsp_password") == "secret"

    async def test_device_extras_renders_blank_form_on_prefill_failure(
        self, hass,
    ) -> None:
        """When async_prefill_extras returns None (relayed read fails),
        a blank extras form is rendered without raising."""
        # Simulate a prefill failure by making the hub's relayed read raise.
        # async_prefill_extras catches the exception and returns None, so the
        # flow must fall back to a blank manual form.
        hub_stub = MagicMock()
        hub_stub.async_read = AsyncMock(
            side_effect=RuntimeError("relayed read failed"),
        )
        runtime_stub = SimpleNamespace(hub=hub_stub)

        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry, runtime_data=runtime_stub)

        g400_record = _device_record(
            did="lumi1.G400CAM2",
            model="lumi.camera.agl013",
            name="G400 Doorbell 2",
        )
        flow._picked_device = g400_record

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                G400Device if model == "lumi.camera.agl013" else None
            ),
        ), patch.object(
            AqaraDeviceSubentryFlow,
            "add_suggested_values_to_schema",
            wraps=flow.add_suggested_values_to_schema,
        ) as mock_add_suggested:
            result = await flow.async_step_device_extras(None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "device_extras"
        # Prefill returned None (relayed read raised), so
        # add_suggested_values_to_schema must NOT have been called.
        mock_add_suggested.assert_not_called()

    async def test_device_extras_renders_form_when_prefill_hook_raises(
        self, hass, monkeypatch,
    ) -> None:
        """A device class whose async_prefill_extras raises must not break the
        flow -- the extras form still renders."""
        # Make the hook raise; the flow's try/except must absorb the error.
        monkeypatch.setattr(
            G400Device,
            "async_prefill_extras",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        hub_stub = MagicMock()
        hub_stub.async_read = AsyncMock(return_value={})
        runtime_stub = SimpleNamespace(hub=hub_stub)

        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry, runtime_data=runtime_stub)

        g400_record = _device_record(
            did="lumi1.G400CAM3",
            model="lumi.camera.agl013",
            name="G400 Doorbell 3",
        )
        flow._picked_device = g400_record

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: (
                G400Device if model == "lumi.camera.agl013" else None
            ),
        ):
            result = await flow.async_step_device_extras(None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "device_extras"

    async def test_uncatalogued_model_runs_bootstrap_review(self, hass) -> None:
        """An uncatalogued model (not in catalogue, not in overlay, no
        override class) must:

        1. Enter ``async_step_bootstrap_review`` (not finish as
           ``device_extras``).
        2. Render a FORM with step_id ``bootstrap_review``.
        3. On submission with ``selected=[pid]``, write that pid to the
           overlay and create the subentry.
        """
        from custom_components.aqara_lanlink.device.overlay import Overlay, OverlayStore

        bootstrap_record = _device_record(
            did="lumi1.UNKNOWN", model="lumi.unknown.bootstrap",
            name="Mystery Device",
        )
        entry = _hub_entry(hass)
        flow = _build_subentry_flow(hass, entry)

        # Directly stash the picked device so we can call async_step_device_extras
        # without going through the full picker flow.
        flow._picked_device = bootstrap_record

        # Panels returned by the cloud: one endpoint with two paths.
        panels = {
            1: _make_panel(1, ("4.143.32952",)),
        }
        # Traits response: one novel trait (not in any catalogue).
        traits_response = [
            {
                "path": "4.143.32952",
                "propertyId": ["0.1.85"],
                "unit": "C",
                "enums": None,
                "value": "23.5",
                "valueType": "number",
                "min": "", "max": "", "step": "",
                "defaultValue": "23.5",
                "permission": "r",
            },
        ]

        # Overlay starts empty so the bootstrap branch triggers.
        captured_overlay: list[Overlay] = []

        mock_store = MagicMock(spec=OverlayStore)
        mock_store.async_load = AsyncMock(return_value=Overlay())
        mock_store.async_write = AsyncMock(
            side_effect=lambda ov: captured_overlay.append(ov),
        )

        mock_cloud = MagicMock()
        mock_cloud.query_collection_panels = AsyncMock(return_value=panels)
        mock_cloud.query_device_traits = AsyncMock(return_value=traits_response)

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                return_value=None,
            ),
            # Stub build_descriptors to return [] so the bootstrap branch
            # triggers (same logic _classify uses for uncatalogued models).
            patch(
                "custom_components.aqara_lanlink.device.build_descriptors.build_descriptors",
                return_value=[],
            ),
            # Patch _get_overlay to return an empty overlay (uncatalogued).
            patch.object(
                AqaraDeviceSubentryFlow,
                "_get_overlay",
                AsyncMock(return_value=Overlay()),
            ),
            # Patch AqaraCloudClient so we don't need real HTTP.
            # The symbol is already imported at module scope in config_flow.py.
            patch(
                "custom_components.aqara_lanlink.config_flow.AqaraCloudClient",
                return_value=mock_cloud,
            ),
            # _write_bootstrap_acceptance imports OverlayStore locally, so
            # patch at the source module for both the render and submit calls.
            patch(
                "custom_components.aqara_lanlink.device.overlay.OverlayStore",
                return_value=mock_store,
            ),
        ):
            # Initial call - must render the bootstrap_review form.
            result = await flow.async_step_device_extras(None)

            assert result["type"] == FlowResultType.FORM, (
                f"expected FORM, got {result['type']} (step={result.get('step_id')})"
            )
            assert result["step_id"] == "bootstrap_review"
            placeholders = result.get("description_placeholders") or {}
            assert placeholders.get("model") == "lumi.unknown.bootstrap"
            assert placeholders.get("did") == "lumi1.UNKNOWN"
            assert placeholders.get("count") == "1"

            # V3: gap entries are keyed by wire_path, not by propertyId.
            # Submit with the discovered wire_path selected.
            result2 = await flow.async_step_bootstrap_review(
                user_input={"selected": ["4.143.32952"]},
            )

        # The subentry must be created.
        assert result2["type"] == FlowResultType.CREATE_ENTRY
        assert result2["data"][CONF_DEVICE_DID] == "lumi1.UNKNOWN"
        assert result2["data"][CONF_DEVICE_MODEL] == "lumi.unknown.bootstrap"
        assert result2["unique_id"] == "lumi1.UNKNOWN"

        # The overlay store must have been written with the accepted
        # wire_path (V3 contract: overlay key == TraitSpec.id == wire_path).
        mock_store.async_write.assert_awaited_once()
        assert len(captured_overlay) == 1
        saved = captured_overlay[0]
        assert "4.143.32952" in saved.traits_for_model("lumi.unknown.bootstrap")

        # Both query methods must have been called exactly once across the
        # entire bootstrap flow (form render + submit), proving the report is
        # cached and not re-fetched on submission.
        assert mock_cloud.query_collection_panels.await_count == 1
        assert mock_cloud.query_device_traits.await_count == 1


# =============================================================================
# Re-auth flow (Task 6.7): refresh expired/rejected Aqara cloud tokens
# =============================================================================


class TestReauthFlow:
    """Cover the re-auth flow that runs when the stored token is rejected.

    HA invokes this flow via ``entry.async_start_reauth(hass)`` (typically
    from a coordinator that has seen persistent ``hub_rejected_checkin``
    failures). The flow re-uses the credentials-step shape but pre-fills
    the existing account email and, on success, updates the entry's data
    in place rather than creating a new entry.
    """

    async def test_reauth_pre_fills_account(self, hass) -> None:
        """The initial render must include the existing account email in
        the description placeholders so the user can see which entry is
        being re-authenticated."""
        entry = _hub_entry(hass)

        result = await start_reauth_flow(hass, entry)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        placeholders = result.get("description_placeholders") or {}
        assert placeholders.get("account") == "user@example.com"
        # The pre-populated form keeps the same fields the credentials
        # step uses; the user only types the password (or new tokens).
        schema_dict = result["data_schema"].schema
        keys = {str(k) for k in schema_dict}
        assert CONF_AQARA_ACCOUNT in keys
        assert CONF_AQARA_PASSWORD in keys
        assert CONF_AQARA_REGION in keys
        assert CONF_AQARA_USER_ID in keys
        assert CONF_AQARA_TOKEN in keys

    async def test_reauth_cloud_login_success_updates_entry(self, hass) -> None:
        """A successful cloud login + checkin must update the existing
        entry's user_id/token in place and abort with reason
        ``reauth_successful``. No new entry is created."""
        entry = _hub_entry(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="NEW_USR", token="NEW_TOK", raw_result={},
            ),
        )
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_INIT_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLIENTSESSION_TARGET, MagicMock(return_value=MagicMock())),
        ):
            init = await start_reauth_flow(hass, entry)
            assert init["step_id"] == "reauth_confirm"
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "newpassword",
                    CONF_AQARA_REGION: "EU",
                },
            )
            # Drain the post-reauth entry-reload task while patches are
            # still active so __init__.py:async_setup_entry sees the
            # stub coordinator + cloud client.
            await hass.async_block_till_done()

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        # Entry was updated in place, not duplicated.
        assert entry.data[CONF_AQARA_USER_ID] == "NEW_USR"
        assert entry.data[CONF_AQARA_TOKEN] == "NEW_TOK"
        # The hub coordinates are unchanged across a re-auth.
        assert entry.data[CONF_HUB_DID] == "lumi1.HUB"
        assert entry.data[CONF_HUB_IP] == "192.0.2.10"
        cloud_client.login.assert_awaited_once_with(
            "user@example.com", "newpassword",
        )
        # Coordinator was constructed with the new token, not the stale
        # one that's still on the entry data at validation time.
        ctor_kwargs = coord_factory.call_args.kwargs
        assert ctor_kwargs.get("user_id") == "NEW_USR"
        assert ctor_kwargs.get("token") == "NEW_TOK"
        coord.stop.assert_awaited_once()

    async def test_reauth_manual_tokens_success_updates_entry(self, hass) -> None:
        """Pasting a fresh user_id + token must skip cloud login and,
        on a successful checkin, update the existing entry in place."""
        entry = _hub_entry(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock()
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_INIT_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLIENTSESSION_TARGET, MagicMock(return_value=MagicMock())),
        ):
            init = await start_reauth_flow(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {
                    # Empty account exercises the preservation fallback:
                    # the entry's existing account email must be retained
                    # when the manual-token submission leaves it blank.
                    CONF_AQARA_ACCOUNT: "",
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "MANUAL_NEW_USR",
                    CONF_AQARA_TOKEN: "MANUAL_NEW_TOK",
                },
            )
            # Drain the post-reauth entry-reload task while patches are
            # still active so __init__.py:async_setup_entry sees the
            # stub coordinator + cloud client.
            await hass.async_block_till_done()

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        # Cloud login must NOT have been attempted on the manual path.
        cloud_client.login.assert_not_awaited()
        # Entry data updated in place.
        assert entry.data[CONF_AQARA_USER_ID] == "MANUAL_NEW_USR"
        assert entry.data[CONF_AQARA_TOKEN] == "MANUAL_NEW_TOK"
        # Existing account is preserved when manual-token submission has
        # an empty account field (locks in the `validated["account"] or
        # existing_account` fallback contract in the reauth handler).
        assert entry.data[CONF_AQARA_ACCOUNT] == "user@example.com"
        ctor_kwargs = coord_factory.call_args.kwargs
        assert ctor_kwargs.get("user_id") == "MANUAL_NEW_USR"
        assert ctor_kwargs.get("token") == "MANUAL_NEW_TOK"

    async def test_reauth_login_failure_renders_error(self, hass) -> None:
        """An ``AqaraAuthError`` from the cloud login must surface as
        ``aqara_login_failed`` and re-render the re-auth form. The
        existing entry data must not have been mutated."""
        entry = _hub_entry(hass)
        original_token = entry.data[CONF_AQARA_TOKEN]

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            side_effect=AqaraAuthError("bad password"),
        )
        cloud_factory = MagicMock(return_value=cloud_client)
        coord_factory = MagicMock(return_value=_stub_coordinator_success())

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            init = await start_reauth_flow(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "wrongpassword",
                    CONF_AQARA_REGION: "EU",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "aqara_login_failed"}
        # Coordinator must NOT have been constructed for a failed login.
        coord_factory.assert_not_called()
        # Entry data must remain untouched.
        assert entry.data[CONF_AQARA_TOKEN] == original_token

    async def test_reauth_checkin_failure_renders_error(self, hass) -> None:
        """A LANLink checkin that times out must surface as
        ``cannot_connect`` and re-render the re-auth form. The existing
        entry data must not have been mutated."""
        entry = _hub_entry(hass)
        original_token = entry.data[CONF_AQARA_TOKEN]

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="NEW_USR", token="NEW_TOK", raw_result={},
            ),
        )
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_timeout()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
        ):
            init = await start_reauth_flow(hass, entry)
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_PASSWORD: "newpassword",
                    CONF_AQARA_REGION: "EU",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "cannot_connect"}
        # Cleanup must still happen even on a failed checkin.
        coord.stop.assert_awaited_once()
        # Entry data must remain untouched.
        assert entry.data[CONF_AQARA_TOKEN] == original_token


# =============================================================================
# Reconfigure flow: user-initiated credential update outside auth-failure
# =============================================================================


class TestReconfigureFlow:
    """Cover the per-entry Reconfigure menu item.

    Triggered when the user picks "Reconfigure" from the entry's three-dot
    menu (HA exposes this when ``async_step_reconfigure`` is defined on
    the ConfigFlow). Used for:
      - the "dismissed the auth-failure Repair card and now needs another
        way in" edge case
      - pre-emptive credential rotation before the current token expires
      - switching the Aqara account on a hub without delete+re-add

    Per-entry by construction: HA scopes the flow to the entry whose
    menu was clicked, so a Reconfigure on Hub A does not touch Hub B.
    """

    async def test_reconfigure_pre_fills_account(self, hass) -> None:
        """Initial render pre-fills the existing account/region so the
        user only has to type the password (or paste new tokens)."""
        from custom_components.aqara_lanlink.const import DOMAIN
        entry = _hub_entry(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        placeholders = result.get("description_placeholders") or {}
        assert placeholders.get("account") == "user@example.com"
        schema_dict = result["data_schema"].schema
        keys = {str(k) for k in schema_dict}
        # Same form shape as the credentials step / reauth confirm.
        assert CONF_AQARA_ACCOUNT in keys
        assert CONF_AQARA_PASSWORD in keys
        assert CONF_AQARA_REGION in keys
        assert CONF_AQARA_USER_ID in keys
        assert CONF_AQARA_TOKEN in keys

    async def test_reconfigure_manual_tokens_updates_entry(self, hass) -> None:
        """Pasting fresh user_id + token via the Reconfigure menu must
        update the entry data in place and abort with
        ``reconfigure_successful``. Hub coordinates stay untouched."""
        from custom_components.aqara_lanlink.const import DOMAIN
        entry = _hub_entry(hass)

        cloud_client = MagicMock()
        cloud_client.login = AsyncMock()
        cloud_factory = MagicMock(return_value=cloud_client)
        coord = _stub_coordinator_success()
        coord_factory = MagicMock(return_value=coord)

        with (
            patch(_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLOUD_CLIENT_TARGET, cloud_factory),
            patch(_INIT_HUB_COORDINATOR_TARGET, coord_factory),
            patch(_INIT_CLIENTSESSION_TARGET, MagicMock(return_value=MagicMock())),
        ):
            init = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reconfigure", "entry_id": entry.entry_id},
            )
            assert init["step_id"] == "reconfigure"
            result = await hass.config_entries.flow.async_configure(
                init["flow_id"],
                {
                    CONF_AQARA_ACCOUNT: "user@example.com",
                    CONF_AQARA_REGION: "EU",
                    CONF_AQARA_USER_ID: "RECONFIG_USR",
                    CONF_AQARA_TOKEN: "RECONFIG_TOK",
                },
            )
            await hass.async_block_till_done()

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_AQARA_USER_ID] == "RECONFIG_USR"
        assert entry.data[CONF_AQARA_TOKEN] == "RECONFIG_TOK"
        # Hub coordinates must not be touched by Reconfigure.
        assert entry.data[CONF_HUB_DID] == "lumi1.HUB"
        assert entry.data[CONF_HUB_IP] == "192.0.2.10"
        # Manual-token path: cloud login must NOT have been attempted.
        cloud_client.login.assert_not_awaited()


# =============================================================================
# Multi-region / multi-account sanity (Task 6.8)
# =============================================================================


class TestMultiRegionMultiAccount:
    """Two independent hubs on two different accounts/regions must coexist.

    A user may own one hub on an EU Aqara account and another on a US
    Aqara account. Running the hub config flow once per hub must produce
    two independent ``ConfigEntry`` rows, each carrying its own
    region/account/user_id/token and keyed by its own hub DID.
    """

    async def test_two_hubs_on_two_accounts_coexist_as_independent_entries(
        self, hass,
    ) -> None:
        """Run the hub flow twice with different (did, region, account); both coexist."""
        eu_did = "lumi1.HUB_EU"
        us_did = "lumi1.HUB_US"

        # --- Flow 1: EU account + EU hub ---------------------------------
        eu_client = MagicMock()
        eu_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="EU_USR", token="EU_TOK", raw_result={},
            ),
        )
        eu_client.query_device_list = AsyncMock(
            return_value=[
                {
                    "did": eu_did,
                    "model": "lumi.gateway.agl004",
                    "deviceName": "Aqara Hub EU",
                    "parentDeviceId": "",
                },
                {
                    "did": "lumi1.EU_SUB1",
                    "model": "lumi.sensor_motion",
                    "parentDeviceId": eu_did,
                },
            ],
        )
        eu_coord = _stub_coordinator_success()

        eu_confirm = await _drive_to_confirm(
            hass,
            cloud_client=eu_client,
            coord=eu_coord,
            hub_did=eu_did,
            hub_ip="192.168.1.51",
            account="eu@example.com",
            password="eu-pass",
            region="EU",
        )
        with (
            patch(_INIT_CLOUD_CLIENT_TARGET, MagicMock(return_value=eu_client)),
            patch(_INIT_HUB_COORDINATOR_TARGET, MagicMock(return_value=eu_coord)),
            patch(_INIT_CLIENTSESSION_TARGET, MagicMock(return_value=MagicMock())),
        ):
            eu_result = await hass.config_entries.flow.async_configure(
                eu_confirm["flow_id"], {},
            )
            await hass.async_block_till_done()
        assert eu_result["type"] == FlowResultType.CREATE_ENTRY

        # --- Flow 2: US account + US hub ---------------------------------
        us_client = MagicMock()
        us_client.login = AsyncMock(
            return_value=AqaraTokens(
                user_id="US_USR", token="US_TOK", raw_result={},
            ),
        )
        us_client.query_device_list = AsyncMock(
            return_value=[
                {
                    "did": us_did,
                    "model": "lumi.gateway.agl004",
                    "deviceName": "Aqara Hub US",
                    "parentDeviceId": "",
                },
            ],
        )
        us_coord = _stub_coordinator_success()

        us_confirm = await _drive_to_confirm(
            hass,
            cloud_client=us_client,
            coord=us_coord,
            hub_did=us_did,
            hub_ip="192.168.1.52",
            account="us@example.com",
            password="us-pass",
            region="US",
        )
        with (
            patch(_INIT_CLOUD_CLIENT_TARGET, MagicMock(return_value=us_client)),
            patch(_INIT_HUB_COORDINATOR_TARGET, MagicMock(return_value=us_coord)),
            patch(_INIT_CLIENTSESSION_TARGET, MagicMock(return_value=MagicMock())),
        ):
            us_result = await hass.config_entries.flow.async_configure(
                us_confirm["flow_id"], {},
            )
            await hass.async_block_till_done()
        assert us_result["type"] == FlowResultType.CREATE_ENTRY

        # --- Both entries coexist ----------------------------------------
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 2
        by_did = {e.data[CONF_HUB_DID]: e for e in entries}
        assert set(by_did) == {eu_did, us_did}

        eu_entry = by_did[eu_did]
        us_entry = by_did[us_did]

        # EU entry data
        assert eu_entry.data[CONF_AQARA_REGION] == "EU"
        assert eu_entry.data[CONF_AQARA_ACCOUNT] == "eu@example.com"
        assert eu_entry.data[CONF_AQARA_USER_ID] == "EU_USR"
        assert eu_entry.data[CONF_AQARA_TOKEN] == "EU_TOK"
        assert eu_entry.unique_id == eu_did

        # US entry data
        assert us_entry.data[CONF_AQARA_REGION] == "US"
        assert us_entry.data[CONF_AQARA_ACCOUNT] == "us@example.com"
        assert us_entry.data[CONF_AQARA_USER_ID] == "US_USR"
        assert us_entry.data[CONF_AQARA_TOKEN] == "US_TOK"
        assert us_entry.unique_id == us_did

        # The two entries are genuinely distinct.
        assert eu_entry.unique_id != us_entry.unique_id
        assert eu_entry.data[CONF_AQARA_USER_ID] != us_entry.data[CONF_AQARA_USER_ID]
        assert eu_entry.data[CONF_AQARA_TOKEN] != us_entry.data[CONF_AQARA_TOKEN]
        assert eu_entry.data[CONF_AQARA_REGION] != us_entry.data[CONF_AQARA_REGION]
        assert eu_entry.data[CONF_AQARA_ACCOUNT] != us_entry.data[CONF_AQARA_ACCOUNT]



# =============================================================================
# Options flow (Task 9): entry-hub camera options
# =============================================================================


def test_async_get_options_flow_exists():
    assert hasattr(AqaraLanLinkConfigFlow, "async_get_options_flow")


def _camera_hub_entry(hass) -> MockConfigEntry:
    """Build and register a hub config entry whose hub model is a camera (G100)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="lumi1.G100HUB",
        title="Test G100 Hub",
        data={
            CONF_HUB_IP: "192.0.2.20",
            CONF_HUB_PORT: 59703,
            CONF_HUB_DID: "lumi1.G100HUB",
            CONF_HUB_MODEL: "lumi.camera.agl005",
            CONF_AQARA_ACCOUNT: "user@example.com",
            CONF_AQARA_REGION: "EU",
            CONF_AQARA_USER_ID: "USR",
            CONF_AQARA_TOKEN: "TOK",
        },
    )
    entry.add_to_hass(hass)
    return entry


class TestOptionsFlow:
    async def test_camera_hub_renders_four_field_form(self, hass) -> None:
        """For a camera hub, async_step_init must render a form with the
        four camera configuration fields."""
        entry = _camera_hub_entry(hass)

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: G100Device if model == "lumi.camera.agl005" else None,
        ), patch.object(
            G100Device,
            "async_prefill_extras",
            AsyncMock(return_value=None),
        ), patch(
            "custom_components.aqara_lanlink.config_flow.is_camera_model",
            side_effect=lambda model: model == "lumi.camera.agl005",
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"
        schema_keys = {str(k) for k in result["data_schema"].schema}
        assert "camera_ip" in schema_keys
        assert "rtsp_username" in schema_keys
        assert "rtsp_password" in schema_keys
        assert "backchannel_channel" in schema_keys

    async def test_camera_hub_rejects_unsafe_camera_ip(self, hass) -> None:
        """An unsafe camera_ip (whitespace/metacharacters) must re-render the
        form with an error rather than persist a poisoned value that later
        flows into the go2rtc exec source."""
        entry = _camera_hub_entry(hass)

        def _gdc(model):
            return G100Device if model == "lumi.camera.agl005" else None

        def _icm(model):
            return model == "lumi.camera.agl005"

        with patch.object(
            config_flow_module.registry, "get_device_class", side_effect=_gdc,
        ), patch.object(
            G100Device, "async_prefill_extras", AsyncMock(return_value=None),
        ), patch(
            "custom_components.aqara_lanlink.config_flow.is_camera_model",
            side_effect=_icm,
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    "camera_ip": "1.2.3.4 --foo=bar",
                    "rtsp_username": "admin",
                    "rtsp_password": "s3cret",
                    "backchannel_channel": 1,
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"camera_ip": "invalid_host"}
        assert entry.options.get("camera_ip") != "1.2.3.4 --foo=bar"

    async def test_non_camera_hub_aborts_with_not_a_camera(self, hass) -> None:
        """For a non-camera hub model, async_step_init must abort with
        reason 'not_a_camera'."""
        entry = _hub_entry(hass)

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            return_value=None,
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "not_a_camera"

    async def test_camera_hub_submit_creates_entry_with_options(self, hass) -> None:
        """Submitting the options form for a camera hub must produce a
        CREATE_ENTRY result and persist the four camera fields in entry.options."""
        entry = _camera_hub_entry(hass)

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: G100Device if model == "lumi.camera.agl005" else None,
        ), patch.object(
            G100Device,
            "async_prefill_extras",
            AsyncMock(return_value=None),
        ), patch(
            "custom_components.aqara_lanlink.config_flow.is_camera_model",
            side_effect=lambda model: model == "lumi.camera.agl005",
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)

        assert result["type"] == FlowResultType.FORM

        with patch.object(
            config_flow_module.registry,
            "get_device_class",
            side_effect=lambda model: G100Device if model == "lumi.camera.agl005" else None,
        ), patch(
            "custom_components.aqara_lanlink.config_flow.is_camera_model",
            side_effect=lambda model: model == "lumi.camera.agl005",
        ):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    "camera_ip": "192.0.2.55",
                    "rtsp_username": "admin",
                    "rtsp_password": "s3cret",
                    "backchannel_channel": 2,
                },
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options["camera_ip"] == "192.0.2.55"
        assert entry.options["rtsp_username"] == "admin"
        assert entry.options["rtsp_password"] == "s3cret"
        assert entry.options["backchannel_channel"] == 2
