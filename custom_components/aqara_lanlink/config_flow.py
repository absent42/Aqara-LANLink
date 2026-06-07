"""Config flow for the Aqara LANLink integration.

This is the user-facing entry point for adding a tunnel host (hub or
standalone LANLink device) to Home Assistant.
The flow is multi-step:

    user        -> mDNS-driven tunnel host picker (or manual IP+DID fallback)
    credentials -> Aqara cloud login OR manual user_id/token paste, then
                   a real LANLink checkin against the tunnel host to validate
                   that the supplied credentials work
    confirm     -> entry creation with cloud device-count preview

The credentials step accepts either of two paths on the same form:

1. Aqara email + password + region. The flow calls
   ``AqaraCloudClient(region).login(email, password)`` to exchange the
   credentials for a (user_id, token) pair.
2. Pasted user_id + token directly. Cloud login is skipped entirely.

Whichever path produces the (user_id, token) pair, the flow then
validates the pair by spinning up a one-shot ``HubCoordinator`` and
waiting for a successful LANLink checkin (10s budget). On success the
flow advances to the confirm step with the credentials stashed on
``self`` for the confirm step to consume.

After the hub entry is created, devices are added through the
``AqaraDeviceSubentryFlow`` (one subentry per device). The subentry
flow's ``async_step_pick_device`` queries the cloud for the user's
device list, filters to devices belonging to this hub, classifies each
as supported-via-override, supported-via-auto-derive, or unsupported,
renders a multi-select form for the supported set, and lists the
unsupported set in the description text.

Known limitation: the LANLink protocol rejects bad tokens at the
checkin stage by closing the session immediately, which from the
coordinator's vantage point looks identical to a hub we simply can't
reach. We deliberately keep both lumped under ``cannot_connect`` even
on reauth, because the LANLink layer doesn't surface enough signal to
disambiguate "token rejected by hub" from "hub unreachable" -- the
reauth form uses the same shared validation helper as the initial
credentials step and inherits the same coarse error bucket.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

# ConfigSubentryFlow and its ``_get_entry()`` helper are guaranteed by the
# manifest's ``min_ha_version`` (2025.4.0; ``_get_entry`` landed in 2025.4.0),
# so the production class is always available -- no compatibility shim needed.
from homeassistant.config_entries import ConfigSubentryFlow

from .const import (
    AQARA_REGIONS,
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
    DEFAULT_HUB_MODEL,
    DEFAULT_HUB_PORT,
    DOMAIN,
)
from .device import registry
from .device.catalog import is_camera_model
from .host_validation import is_safe_host
from .hub.cloud_client import (
    AqaraAuthError,
    AqaraCloudClient,
)
from .hub.coordinator import HubCoordinator
from .hub.mdns import AqaraServiceRecord, discover_hubs
from .hub.probe import ProbeResult, probe_tunnel_host

_LOGGER = logging.getLogger(__name__)

_DISCOVERY_TIMEOUT = 3.0

# Cloud device/query "devicetype" field values.
_CLOUD_DEVICETYPE_HUB = 1   # 1 = hub, 2 = sub-device, 8 = camera


def _cloud_devicetype(record: dict) -> int | None:
    """Return a record's cloud devicetype as an int, or None if absent/unparseable."""
    raw = record.get("devicetype")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _topology_dids_for_entry(entry) -> frozenset[str]:
    """Return the connected hub's LANLink topology DID set for a config entry.

    Safe against an entry with no runtime_data, a None hub, or a hub that
    has not yet received a topology push -- all yield an empty frozenset.
    """
    runtime = getattr(entry, "runtime_data", None)
    hub = getattr(runtime, "hub", None) if runtime is not None else None
    return getattr(hub, "lanlink_topology_dids", frozenset()) if hub is not None else frozenset()


# How long we give the LANLink coordinator to complete its first checkin
# against the hub before declaring the credentials un-validatable. 10s
# is a comfortable budget: the tunnel handshake + checkin round trip
# typically completes in well under a second on a healthy LAN.
_CHECKIN_TIMEOUT = 10.0


async def _discover_tunnel_hosts(hass: HomeAssistant) -> dict[str, AqaraServiceRecord]:
    """Discover tunnel hosts over mDNS and filter to probe-verified ones.

    Runs mDNS discovery to get all advertising records, then probes every
    candidate concurrently. Returns only the records whose probe result is
    ProbeResult.OK, keyed by DID. Network or zeroconf errors from discover_hubs
    are swallowed and an empty dict is returned so callers fall back to the
    manual form.
    """
    try:
        records = await discover_hubs(hass, timeout=_DISCOVERY_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("mDNS discovery failed in config flow: %s", exc)
        return {}

    if not records:
        return {}

    results = await asyncio.gather(
        *(probe_tunnel_host(r.host, r.port, r.did) for r in records),
    )
    return {
        r.did: r
        for r, result in zip(records, results)
        if result is ProbeResult.OK
    }


class AqaraLanLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for adding an Aqara LANLink tunnel host."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow scratch state.

        Each ConfigFlow instance is one wizard run, so storing fields on
        ``self`` is the documented way to carry data between steps.
        """
        super().__init__()
        self._discovered: dict[str, AqaraServiceRecord] = {}
        self._hub_did: str | None = None
        self._hub_ip: str | None = None
        self._hub_port: int = DEFAULT_HUB_PORT
        # Filled in by `async_step_credentials` after a successful
        # validation; consumed by Task 6.3's confirm step when it
        # creates the config entry.
        self._aqara_user_id: str | None = None
        self._aqara_token: str | None = None
        self._aqara_region: str | None = None
        # Account email used for cloud login (empty string if the user
        # took the manual-token path). Carried into the entry data so a
        # future re-auth flow can pre-fill the email field. Filled in by
        # the credentials step.
        self._aqara_account: str = ""
        # Filled in by `async_step_confirm` on initial render. The model
        # comes from the cloud device-list response if it returns the hub
        # itself; otherwise we keep the default fallback. Carried into
        # the description placeholders and into the created entry data.
        self._hub_model: str = DEFAULT_HUB_MODEL
        # Human-readable hub name (e.g. "Aqara Hub M3") sourced from the
        # cloud device-list response. Empty string if the cloud preview
        # didn't return the hub's own record. Used as the preferred
        # entry title; falls back to model+DID when empty.
        self._hub_name: str = ""
        # ``None`` means "cloud unreachable OR not yet queried"; an int
        # means "preview succeeded with this many devices (hub + subs)".
        # The confirm form's description text branches on this value.
        self._device_count: int | None = None
        # Re-auth handle: populated by ``async_step_reauth`` from the
        # existing config entry whose token expired or was rejected.
        # ``None`` outside of a re-auth flow run.
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Entry step: discover tunnel hosts over mDNS, fall back to manual entry.

        On the first call (``user_input is None``) we run discovery once
        and render the appropriate form. On submission we capture the
        tunnel host coordinates, stash them on the flow, and advance to the
        credentials step.
        """
        if user_input is None:
            # Initial render: run discovery once, decide which form to show.
            self._discovered = await _discover_tunnel_hosts(self.hass)
            return self.async_show_form(
                step_id="user",
                data_schema=self._build_user_schema(),
            )

        # Submission: which form was shown? (If `_discovered` is non-empty
        # we showed the selector; otherwise we showed the manual form.)
        if self._discovered:
            chosen_did = user_input[CONF_HUB_DID]
            record = self._discovered[chosen_did]  # vol.In rejected unknown values
            self._hub_did = record.did
            self._hub_ip = record.host
            self._hub_port = record.port
        else:
            hub_did = user_input[CONF_HUB_DID]
            hub_ip = user_input[CONF_HUB_IP]
            if not is_safe_host(hub_ip):
                # Reject before probing: the hub IP receives the LANLink
                # credentials at checkin, so an unvalidated host is an SSRF /
                # credential-disclosure risk.
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_user_schema(),
                    errors={"base": "invalid_host"},
                )
            probe_result = await probe_tunnel_host(hub_ip, DEFAULT_HUB_PORT, hub_did)
            if probe_result is ProbeResult.OK:
                self._hub_did = hub_did
                self._hub_ip = hub_ip
                self._hub_port = DEFAULT_HUB_PORT
            else:
                # REFUSED/NOT_LANLINK get specific hints; TIMEOUT and any other
                # outcome fall through to the generic cannot_connect.
                error_key = {
                    ProbeResult.REFUSED: "not_tunnel_host",
                    ProbeResult.NOT_LANLINK: "not_lanlink",
                }.get(probe_result, "cannot_connect")
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_user_schema(),
                    errors={"base": error_key},
                )

        return await self.async_step_credentials()

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Capture Aqara cloud credentials and validate them with a checkin.

        Two credential paths share the same form. Manual tokens win:
        if the user supplied BOTH a complete (user_id, token) pair AND
        an account/password, the manual tokens are used and cloud login
        is skipped. The cloud-login path is only taken when manual
        tokens are absent or incomplete.

        Validation is a one-shot ``HubCoordinator`` checkin against the
        hub captured by the user step. We start the coordinator, wait up
        to ``_CHECKIN_TIMEOUT`` seconds for the first successful checkin,
        and then stop it cleanly regardless of outcome. A successful
        checkin advances the flow to the confirm step (Task 6.3); any
        failure re-renders this form with an error.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="credentials",
                data_schema=self._build_credentials_schema(),
            )

        validated, error_key = await self._validate_credentials_input(user_input)
        if error_key is not None:
            return self.async_show_form(
                step_id="credentials",
                data_schema=self._build_credentials_schema(
                    defaults=user_input,
                ),
                errors={"base": error_key},
            )

        assert validated is not None
        # Success: stash everything Task 6.3 needs to create the entry,
        # then advance. ``account`` is whatever the user typed into the
        # email field, even if they took the manual-token path - in that
        # case it's just an empty string, which is fine: the entry data
        # carries it as a hint for re-auth and nothing else.
        self._aqara_user_id = validated["user_id"]
        self._aqara_token = validated["token"]
        self._aqara_region = validated["region"]
        self._aqara_account = validated["account"]
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Final confirmation step with cloud device-count preview.

        Two passes:

        1. Initial render (``user_input is None``): query the cloud for
           the device list, count devices belonging to this hub, and
           render the form with a description placeholder summarising
           what the user is about to add. If the cloud call fails, the
           form is still rendered but the description omits the count;
           the user can still confirm and create the entry, and retry
           the device-add flow later when the cloud is reachable again.
        2. Submission (``user_input is not None``): set the unique id
           to the hub's DID, abort if a duplicate exists, otherwise
           create the entry.

        The hub model used for the entry data is sourced from the cloud
        device-list response (the entry whose ``did`` matches our
        ``_hub_did``). If the cloud response doesn't include the hub
        itself, or the cloud call failed, we keep the default model the
        credentials step used for its validation checkin. Tightening
        this is a Task 6.7 concern; the default is good enough to bring
        the entry up.
        """
        assert self._hub_did is not None and self._hub_ip is not None
        assert self._aqara_user_id is not None and self._aqara_token is not None
        assert self._aqara_region is not None

        if user_input is None:
            # Initial render: try a one-shot cloud preview. Failures here
            # are non-fatal - the user can still create the entry and
            # retry the device-add flow later.
            await self._populate_device_preview()
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders=self._confirm_placeholders(),
            )

        # Submission: enforce uniqueness on the hub DID, then create.
        await self.async_set_unique_id(self._hub_did)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=self._hub_name or f"{self._hub_model} ({self._hub_did})",
            data={
                CONF_HUB_IP: self._hub_ip,
                CONF_HUB_PORT: self._hub_port,
                CONF_HUB_DID: self._hub_did,
                CONF_HUB_MODEL: self._hub_model,
                CONF_AQARA_ACCOUNT: self._aqara_account,
                CONF_AQARA_REGION: self._aqara_region,
                CONF_AQARA_USER_ID: self._aqara_user_id,
                CONF_AQARA_TOKEN: self._aqara_token,
            },
        )

    # =========================================================================
    # Re-auth flow (Task 6.7)
    # =========================================================================

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Entry point when HA starts a re-auth flow.

        HA invokes this when an entry's coordinator (or any other code
        path with access to the entry) calls ``entry.async_start_reauth``
        because the stored token was rejected. We capture the existing
        config entry so the credentials form can pre-fill the account
        email and target the right hub for validation, then forward to
        the confirm step where the user supplies fresh credentials.
        """
        del entry_data  # The entry data is read off ``self._reauth_entry`` directly.
        self._reauth_entry = self._get_reauth_entry()
        # Carry the existing hub coordinates onto ``self`` so the shared
        # validation helper can target the right hub without re-asking.
        entry = self._reauth_entry
        self._hub_did = entry.data.get(CONF_HUB_DID)
        self._hub_ip = entry.data.get(CONF_HUB_IP)
        self._hub_port = entry.data.get(CONF_HUB_PORT) or DEFAULT_HUB_PORT
        self._hub_model = entry.data.get(CONF_HUB_MODEL) or DEFAULT_HUB_MODEL
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Render the re-auth credentials form and process submission.

        The form schema mirrors the credentials step (account + password
        + region + manual user_id + manual token). The description
        placeholder includes the existing account email so the user can
        confirm which entry is being re-authenticated.

        On submission the same shared validation helper is used as the
        credentials step (cloud login OR manual token + LANLink checkin
        against the existing hub). On success we update ``entry.data``
        with the new credentials in place and trigger a reload via
        ``async_update_reload_and_abort``.
        """
        assert self._reauth_entry is not None
        existing_account = (
            self._reauth_entry.data.get(CONF_AQARA_ACCOUNT) or ""
        )
        existing_region = self._reauth_entry.data.get(CONF_AQARA_REGION) or "EU"
        return await self._handle_credential_update(
            self._reauth_entry, "reauth_confirm", user_input,
            existing_account, existing_region,
        )

    # =========================================================================
    # Reconfigure flow (user-initiated credential update outside auth-failure)
    # =========================================================================

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Entry point when the user picks "Reconfigure" from the entry menu.

        Mirrors the re-auth confirm step (same credentials form, same
        validation, same data update on success) but is triggered by the
        user rather than by a token-rejection event. Useful for the
        "dismissed the Repair card and now needs another way in" case,
        for changing the Aqara account on a hub, or for a pre-emptive
        token refresh before the current one expires.

        Per-entry by construction: HA invokes this scoped to the entry
        whose Reconfigure menu item was clicked, so reconfiguring Hub A
        does not touch Hub B.
        """
        entry = self._get_reconfigure_entry()
        # Carry the existing hub coordinates onto ``self`` so the shared
        # validation helper can target the right hub.
        self._hub_did = entry.data.get(CONF_HUB_DID)
        self._hub_ip = entry.data.get(CONF_HUB_IP)
        self._hub_port = entry.data.get(CONF_HUB_PORT) or DEFAULT_HUB_PORT
        self._hub_model = entry.data.get(CONF_HUB_MODEL) or DEFAULT_HUB_MODEL

        existing_account = entry.data.get(CONF_AQARA_ACCOUNT) or ""
        existing_region = entry.data.get(CONF_AQARA_REGION) or "EU"
        return await self._handle_credential_update(
            entry, "reconfigure", user_input, existing_account, existing_region,
        )

    async def _handle_credential_update(
        self,
        entry: ConfigEntry,
        step_id: str,
        user_input: dict[str, Any] | None,
        existing_account: str,
        existing_region: str,
    ) -> ConfigFlowResult:
        """Shared credentials form + validation + entry update.

        Backs both ``async_step_reauth_confirm`` and ``async_step_reconfigure``:
        renders the credentials form (account/region pre-filled), validates via
        the same cloud-login-or-manual-token helper, and on success updates only
        the credential-bearing fields, reloading the entry. Everything else
        (hub IP/DID/model) is unchanged.
        """
        if user_input is None:
            # Pre-fill the account + region so the user only needs to type the
            # password (or paste new tokens) on the cloud-login path.
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._build_credentials_schema(
                    defaults={
                        CONF_AQARA_ACCOUNT: existing_account,
                        CONF_AQARA_REGION: existing_region,
                    },
                ),
                description_placeholders={"account": existing_account},
            )
        validated, error_key = await self._validate_credentials_input(user_input)
        if error_key is not None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._build_credentials_schema(defaults=user_input),
                errors={"base": error_key},
                description_placeholders={"account": existing_account},
            )
        assert validated is not None
        return self.async_update_reload_and_abort(
            entry,
            data_updates={
                CONF_AQARA_USER_ID: validated["user_id"],
                CONF_AQARA_TOKEN: validated["token"],
                CONF_AQARA_REGION: validated["region"],
                CONF_AQARA_ACCOUNT: validated["account"] or existing_account,
            },
        )

    # =========================================================================
    # Options flow registration
    # =========================================================================

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "AqaraLanLinkOptionsFlow":
        """Return the options flow for editing the entry-hub camera settings."""
        return AqaraLanLinkOptionsFlow()

    # =========================================================================
    # Subentry flow registration
    # =========================================================================

    @staticmethod
    @callback
    def async_get_supported_subentry_types(
        config_entry: ConfigEntry,
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Register the device subentry flow for hub entries.

        HA calls this when rendering the entry's "Add device" action.
        Returning ``{"device": AqaraDeviceSubentryFlow}`` exposes a
        single subentry type that the user can launch from the
        integration page.
        """
        del config_entry  # unused; required by the HA ABI
        return {"device": AqaraDeviceSubentryFlow}

    # =========================================================================
    # Internals
    # =========================================================================

    def _build_user_schema(self) -> vol.Schema:
        """Selector schema if hubs were found, manual IP+DID schema otherwise."""
        if self._discovered:
            choices = {
                r.did: f"{r.name} ({r.host})"
                for r in self._discovered.values()
            }
            return vol.Schema(
                {vol.Required(CONF_HUB_DID): vol.In(choices)},
            )
        return vol.Schema(
            {
                vol.Required(CONF_HUB_IP): str,
                vol.Required(CONF_HUB_DID): str,
            },
        )

    def _build_credentials_schema(
        self, defaults: dict[str, Any] | None = None,
    ) -> vol.Schema:
        """Credentials form schema.

        Account/password and user_id/token are all optional at the
        schema layer because the user only supplies one path; the
        handler enforces the actual at-least-one-path-must-be-complete
        rule in Python so we can produce a helpful error key rather
        than a raw schema-validation message.

        ``defaults`` lets a re-rendered form preserve what the user
        already typed, minus the password (we never echo a password
        back). The region default falls back to ``"EU"`` because the
        majority of hubs we expect to onboard live there.
        """
        defaults = defaults or {}
        region_default = defaults.get(CONF_AQARA_REGION) or "EU"
        account_default = defaults.get(CONF_AQARA_ACCOUNT) or ""
        user_id_default = defaults.get(CONF_AQARA_USER_ID) or ""
        token_default = defaults.get(CONF_AQARA_TOKEN) or ""
        return vol.Schema(
            {
                vol.Optional(
                    CONF_AQARA_ACCOUNT, default=account_default,
                ): str,
                vol.Optional(CONF_AQARA_PASSWORD, default=""): str,
                vol.Required(
                    CONF_AQARA_REGION, default=region_default,
                ): vol.In(AQARA_REGIONS),
                vol.Optional(
                    CONF_AQARA_USER_ID, default=user_id_default,
                ): str,
                vol.Optional(
                    CONF_AQARA_TOKEN, default=token_default,
                ): str,
            },
        )

    async def _cloud_login(
        self, region: str, account: str, password: str,
    ):
        """Run a one-shot cloud login.

        Reuses HA's shared aiohttp session so the TLS connection is pooled
        rather than handshaked-then-torn-down per request."""
        client = AqaraCloudClient(region=region, hass=self.hass)
        return await client.login(account, password)

    async def _validate_credentials_input(
        self, user_input: Mapping[str, Any],
    ) -> tuple[dict[str, str] | None, str | None]:
        """Shared credential-validation helper used by both the
        ``credentials`` step and the ``reauth_confirm`` step.

        Encodes the dual-path contract (cloud login OR manual tokens,
        manual wins on conflict, partial manual falls through to cloud)
        plus the LANLink checkin against the hub coordinates already
        stashed on ``self``. Returns one of:

        * ``({"user_id": ..., "token": ..., "region": ..., "account": ...}, None)``
          on success: the caller decides what to do with the validated
          tuple (advance the wizard, update an existing entry, etc.).
        * ``(None, error_key)`` on validation failure: the caller
          re-renders the form with the supplied error key, which is one
          of ``aqara_login_failed``, ``aqara_credentials_required`` or
          ``cannot_connect``.

        The helper assumes ``self._hub_ip``/``self._hub_did``/
        ``self._hub_port`` are populated; both call sites do this
        upfront (the user step for the initial flow, the reauth step
        from the existing entry data).
        """
        region = user_input.get(CONF_AQARA_REGION) or "EU"
        account = (user_input.get(CONF_AQARA_ACCOUNT) or "").strip()
        password = user_input.get(CONF_AQARA_PASSWORD) or ""
        manual_user_id = (user_input.get(CONF_AQARA_USER_ID) or "").strip()
        manual_token = (user_input.get(CONF_AQARA_TOKEN) or "").strip()

        user_id: str | None = None
        token: str | None = None

        if manual_user_id and manual_token:
            # Path 2: pasted tokens. No cloud round-trip.
            user_id = manual_user_id
            token = manual_token
        elif account and password:
            # Path 1: cloud login.
            try:
                tokens = await self._cloud_login(region, account, password)
            except AqaraAuthError as exc:
                _LOGGER.debug("Aqara cloud login rejected: %s", exc)
                return None, "aqara_login_failed"
            except Exception as exc:  # noqa: BLE001
                # Network/transport errors from aiohttp surface here.
                # Treat them as a generic auth failure: the user can't
                # proceed without valid creds, and we don't have a more
                # specific bucket on this form.
                _LOGGER.warning("Aqara cloud login failed: %s", exc)
                return None, "aqara_login_failed"
            user_id = tokens.user_id
            token = tokens.token
        else:
            # Neither path supplied enough information to proceed.
            return None, "aqara_credentials_required"

        assert user_id is not None and token is not None
        checkin_ok = await self._validate_with_checkin(user_id, token)
        if not checkin_ok:
            return None, "cannot_connect"

        return (
            {
                "user_id": user_id,
                "token": token,
                "region": region,
                "account": account,
            },
            None,
        )

    async def _validate_with_checkin(
        self, user_id: str, token: str,
    ) -> bool:
        """Spin up a one-shot HubCoordinator and wait for a successful checkin.

        Returns True iff the coordinator reports a connected session
        within ``_CHECKIN_TIMEOUT`` seconds. Returns False on timeout
        or any other error. The coordinator is always stopped cleanly,
        even on failure, so the test harness (and a real HA instance)
        doesn't see a leftover background task.

        Both timeouts and bad-token rejections currently surface here
        as False; see the module docstring for the reasoning.
        """
        assert self._hub_ip is not None and self._hub_did is not None
        coordinator = HubCoordinator(
            host=self._hub_ip,
            port=self._hub_port,
            device_id=self._hub_did,
            user_id=user_id,
            token=token,
            model=DEFAULT_HUB_MODEL,
            hass=self.hass,
        )
        try:
            try:
                coordinator.start()
            except Exception:  # noqa: BLE001 - any startup failure is "cannot_connect"
                _LOGGER.exception(
                    "Coordinator failed to start during validation for hub %s (did=%s)",
                    self._hub_ip, self._hub_did,
                )
                return False
            try:
                await coordinator.wait_connected(timeout=_CHECKIN_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "LANLink checkin timed out for hub %s (did=%s)",
                    self._hub_ip, self._hub_did,
                )
                return False
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "LANLink checkin failed for hub %s (did=%s): %s",
                    self._hub_ip, self._hub_did, exc,
                )
                return False
            return True
        finally:
            try:
                await coordinator.stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Stopping validation coordinator raised", exc_info=True,
                )

    async def _populate_device_preview(self) -> None:
        """Fetch the cloud device list and update preview state.

        Counts the hub itself plus any device whose ``parentDeviceId``
        matches ``self._hub_did``. If the cloud entry includes the hub's
        own record, also captures its ``model`` so the entry title and
        ``data`` dict carry the real model rather than the validation
        default. ``self._device_count`` stays ``None`` on any failure
        (cloud unreachable OR not yet queried), so the confirm
        description shows the abbreviated form; the entry can still be
        created.
        """
        assert (
            self._aqara_region is not None and self._aqara_token is not None
        )
        client = AqaraCloudClient(region=self._aqara_region, hass=self.hass)
        try:
            devices = await client.query_device_list(self._aqara_token)
        except AqaraAuthError as exc:
            _LOGGER.debug(
                "Aqara cloud preview rejected for hub %s: %s",
                self._hub_did, exc,
            )
            self._device_count = None
            return
        except Exception as exc:  # noqa: BLE001
            # Network errors from aiohttp surface here. Treat as a soft
            # failure so the user can still create the entry; the
            # device subentry flow will retry the cloud call later.
            _LOGGER.warning(
                "Aqara cloud preview failed for hub %s: %s",
                self._hub_did, exc,
            )
            self._device_count = None
            return

        sub_count = 0
        for entry in devices:
            if not isinstance(entry, dict):
                continue
            did = entry.get("did")
            if did == self._hub_did:
                # Update model from the authoritative cloud record if
                # present; ignore an empty/missing field.
                model = entry.get("model")
                if isinstance(model, str) and model:
                    self._hub_model = model
                # Capture the human-readable name for the entry title.
                self._hub_name = entry.get("deviceName") or ""
                continue
            if entry.get("parentDeviceId") == self._hub_did:
                sub_count += 1

        # Total = sub-devices + the hub itself. We always count the hub
        # so the user sees a non-zero total even on a freshly-paired hub
        # with no children yet.
        self._device_count = sub_count + 1

    def _confirm_placeholders(self) -> dict[str, str]:
        """Build the description-placeholder dict for the confirm form.

        ``device_count`` is always a string so the strings.json template
        can render it unconditionally. When the cloud preview failed we
        substitute a human-readable phrase rather than an empty string,
        because most translation systems don't gracefully cope with
        substituted-empty placeholders inside a sentence.
        """
        if self._device_count is None:
            count_str = "unknown (cloud unreachable)"
        else:
            count_str = str(self._device_count)
        return {
            "hub_model": self._hub_model,
            "hub_did": self._hub_did or "",
            "device_count": count_str,
        }


# =============================================================================
# Options flow (Task 9): entry-hub camera settings
# =============================================================================


class AqaraLanLinkOptionsFlow(OptionsFlow):
    """Options flow: camera IP and RTSP credentials for the entry hub.

    Only meaningful when the entry hub is itself a camera. For a non-camera
    hub the flow aborts - there is nothing to configure.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        entry = self.config_entry
        hub_model = entry.data.get(CONF_HUB_MODEL, "")
        device_class = registry.get_device_class(hub_model)
        if not is_camera_model(hub_model):
            return self.async_abort(reason="not_a_camera")

        if user_input is not None:
            camera_ip = (user_input.get("camera_ip") or "").strip()
            if camera_ip and not is_safe_host(camera_ip):
                # camera_ip flows into the go2rtc exec source and RTSP URL;
                # reject anything that is not a literal IP or strict hostname
                # so it cannot inject into those sinks. Re-show with the user's
                # values preserved.
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._camera_options_schema(user_input),
                    errors={"camera_ip": "invalid_host"},
                )
            return self.async_create_entry(title="", data=user_input)

        # Defaults: existing options override a best-effort live RTSP read.
        # async_prefill_extras is reused here; it reads the hub's own RTSP
        # trait through the hub coordinator using the hub model. The read is
        # best-effort: if it does not return credentials the form falls back
        # to blank fields.
        prefill: dict[str, Any] = {}
        runtime = getattr(entry, "runtime_data", None)
        hub_coordinator = getattr(runtime, "hub", None)
        try:
            prefill = await device_class.async_prefill_extras(
                self.hass,
                {
                    "did": entry.data.get(CONF_HUB_DID, ""),
                    "model": entry.data.get(CONF_HUB_MODEL, ""),
                },
                hub_coordinator,
            ) or {}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("options-flow prefill raised: %s", exc)

        def _default(key: str, fallback: Any) -> Any:
            return entry.options.get(key, prefill.get(key, fallback))

        defaults = {
            "camera_ip": _default("camera_ip", ""),
            "rtsp_username": _default("rtsp_username", ""),
            "rtsp_password": _default("rtsp_password", ""),
            "backchannel_channel": _default("backchannel_channel", 1),
        }
        return self.async_show_form(
            step_id="init", data_schema=self._camera_options_schema(defaults),
        )

    @staticmethod
    def _camera_options_schema(defaults: dict[str, Any]) -> vol.Schema:
        """Build the camera options schema with the given field defaults."""
        return vol.Schema(
            {
                vol.Optional(
                    "camera_ip", default=defaults.get("camera_ip", ""),
                ): str,
                vol.Optional(
                    "rtsp_username", default=defaults.get("rtsp_username", ""),
                ): str,
                vol.Optional(
                    "rtsp_password", default=defaults.get("rtsp_password", ""),
                ): str,
                vol.Optional(
                    "backchannel_channel",
                    default=defaults.get("backchannel_channel", 1),
                ): vol.All(vol.Coerce(int), vol.In((1, 2, 3))),
            }
        )


# =============================================================================
# Device subentry flow (Task 6.4)
# =============================================================================


# Classification labels produced by ``_classify``. ``supported_*`` values
# mark a device as eligible for the multi-select picker; ``unsupported_*``
# values exclude it from the picker and surface in the description text.
_SUPPORTED_OVERRIDE = "supported_override"
_SUPPORTED_AUTO_DERIVE = "supported_auto_derive"
# Bootstrap: model is not in the shipped catalogue or any overlay yet.
# The device appears in the picker but ``async_step_device_extras``
# routes it through the mandatory scan + review step before commit.
_SUPPORTED_BOOTSTRAP = "supported_bootstrap"
_UNSUPPORTED_VIRTUAL = "unsupported_virtual"
_UNSUPPORTED_NO_DESCRIPTORS = "unsupported_no_descriptors"

_SUPPORTED_LABELS = frozenset(
    {
        _SUPPORTED_OVERRIDE,
        _SUPPORTED_AUTO_DERIVE,
        _SUPPORTED_BOOTSTRAP,
    },
)

# Subentry data keys that the subentry flow controls itself and must
# never be overwritten by user-supplied extras from a device class's
# ``EXTRA_CONFIG_SCHEMA``. ``did`` and ``model`` define the subentry's
# identity; ``_cloud_metadata`` carries the cloud device record. A buggy
# override class that named an extras field after one of these keys would
# otherwise corrupt subentry identity.
_RESERVED_SUBENTRY_KEYS: frozenset[str] = frozenset(
    {
        "did",
        "model",
        "_cloud_metadata",
    },
)


class AqaraDeviceSubentryFlow(ConfigSubentryFlow):
    """Subentry flow for adding individual Aqara devices to a hub entry.

    A single subentry maps to one Aqara device. The flow's ``user``
    step delegates to ``pick_device``, which:

    1. Queries the cloud for the user's full device list.
    2. Filters to devices whose ``parentDeviceId`` equals the hub's DID.
    3. Excludes devices already configured (their DIDs already have a
       matching subentry).
    4. Classifies each remaining device as supported (override class
       OR auto-derive yields at least one descriptor) or unsupported.
    5. Renders a multi-select form for the supported set, with the
       unsupported set listed in the description text.
    6. On submit, advances to the extras step (Task 6.5) for any picked
       device whose override class declares ``EXTRA_CONFIG_SCHEMA``;
       otherwise creates the subentry directly.
    """

    def __init__(self) -> None:
        super().__init__()
        # Overlay loaded lazily on first classify call, cached for the
        # lifetime of this flow instance.
        self._overlay_cache: Any = None
        # Devices indexed by DID for the submission handler. Populated
        # by the initial render so the submit code path can look up the
        # full record without repeating the cloud call.
        self._devices_by_did: dict[str, dict[str, Any]] = {}
        # The device record the user selected in pick_device. Stashed
        # so async_step_device_extras can render the per-model
        # EXTRA_CONFIG_SCHEMA form and create the subentry on submit.
        # Single-device-per-flow: HA's ConfigSubentryFlow contract is
        # one async_create_entry per flow run, so the picker takes one
        # device per pass and the user reruns the flow for additional
        # devices.
        self._picked_device: dict[str, Any] | None = None
        # Gap report produced by the bootstrap cloud scan. Cached after
        # the first (form-render) call so the submission pass can reuse
        # it without issuing a second network round-trip. Cleared after
        # a successful subentry creation so a subsequent uncatalogued
        # device in the same wizard run gets its own fresh scan.
        self._bootstrap_report: Any = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Subentry flow entry point - delegate to pick_device.

        HA's subentry flow manager seeds ``init_step`` from the
        ``source`` context key, so a fresh subentry-add flow lands
        here. Forwarding to ``pick_device`` keeps the actual logic in
        one place and keeps the URL-visible step name stable across
        wizard revisions.
        """
        return await self.async_step_pick_device(user_input)

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Pick which device to add as a subentry.

        Two passes:

        1. Initial render (``user_input is None``): query the cloud,
           classify each device, render the picker with the supported
           set and an info string listing unsupported devices. If the
           cloud is unreachable, render an error and let the user retry.
        2. Submission (``user_input is not None``): look up the picked
           device record, stash it on ``self._picked_device``, and hand
           off to ``async_step_device_extras`` to either render the
           per-model EXTRA_CONFIG_SCHEMA or create the subentry directly
           when no schema applies.

        Single-device-per-flow: HA's ConfigSubentryFlow contract is one
        ``async_create_entry`` call per flow run. The picker accepts one
        device per pass; users rerun the flow to add additional devices.
        """
        # In production HA, ``_get_entry()`` raises ``UnknownEntry`` if the
        # parent entry has been removed; the flow manager then aborts the
        # subentry flow before reaching this code. The shim returns
        # ``None`` instead, but on real installs we never see that case,
        # so we don't surface a user-visible abort here.
        entry = self._get_entry()

        # Pre-warm the per-model registry on the executor. Normally
        # ``async_setup_entry`` has already done this when the parent hub
        # entry was loaded; calling again is a no-op once `_discovered`
        # is True. We re-call here as defence-in-depth so this flow
        # doesn't block the event loop when ``_classify`` calls
        # ``registry.get_device_class`` for each candidate device.
        await registry.async_ensure_discovered(self.hass)

        if user_input is None:
            return await self._render_pick_device_form(entry)

        # Submission. Re-validate against the cached state from the
        # previous render: if the user somehow picked a DID we don't
        # know about (shouldn't be possible through the UI), abort.
        picked = user_input.get("device")
        if not picked:
            # Empty selection: re-render with a soft error so the user
            # can pick something or back out.
            return await self._render_pick_device_form(
                entry, errors={"base": "no_devices_picked"},
            )

        device_record = self._devices_by_did.get(picked)
        if device_record is None:
            return self.async_abort(reason="unknown_device")

        # Stash the picked record on ``self`` and hand off to the
        # extras step. The extras step decides whether to render a
        # per-model form or create the subentry directly based on the
        # device class's ``EXTRA_CONFIG_SCHEMA``.
        self._picked_device = device_record
        return await self.async_step_device_extras()

    async def async_step_device_extras(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Render per-device EXTRA_CONFIG_SCHEMA, or create the subentry.

        Two passes, gated on whether the picked device's override class
        declares an ``EXTRA_CONFIG_SCHEMA``:

        - No override class, or override declares ``EXTRA_CONFIG_SCHEMA
          is None``: skip the form and create the subentry immediately
          with no extras applied. This is the auto-derive path; the
          device's runtime entities come from the cloud-supplied trait
          metadata at setup time.
        - Override class declares an ``EXTRA_CONFIG_SCHEMA``: on initial
          entry render the schema as a voluptuous form. On submission,
          optionally call the override's ``async_validate_extras`` hook;
          on error re-render the form with the returned error key, on
          success create the subentry merging the user-supplied extras
          into the subentry's data dict.

        The optional ``async_validate_extras`` hook lets a device class
        run a real-world probe (e.g. a TCP connect to a camera's RTSP
        port) before the subentry is committed. It returns ``None`` on
        success or an error key string on failure; the error key must
        have a matching entry under ``config_subentries.device.error``
        in ``strings.json`` so HA renders a localised message.
        """
        if self._picked_device is None:
            # Defensive: should never happen because pick_device sets
            # the field before dispatching here. Surface as an abort so
            # the user can rerun the flow rather than seeing a crash.
            return self.async_abort(reason="unknown_device")

        device_record = self._picked_device
        model = device_record.get("model", "")
        device_class = registry.get_device_class(model)

        # Bootstrap-scan branch: model is uncatalogued.
        # Mirror the same check _classify uses: if build_descriptors returns
        # nothing and there is no override class, the deterministic derive
        # would produce zero entities. Route through the mandatory scan +
        # review step so the user can seed the overlay before the subentry
        # is committed.
        if device_class is None:
            from .device.build_descriptors import build_descriptors
            _overlay = await self._get_overlay()
            if not build_descriptors(model, _overlay):
                return await self.async_step_bootstrap_review()

        # V3 camera models have no @register_device class, so device_class
        # is None for them. Fall back to AutoDerivedCameraDevice so the
        # CameraDevice EXTRA_CONFIG_SCHEMA (camera IP + RTSP credentials)
        # still renders and async_prefill_extras still pre-fills.
        effective_class = device_class
        if effective_class is None and is_camera_model(model):
            from .device.camera.base import AutoDerivedCameraDevice
            effective_class = AutoDerivedCameraDevice

        extras_schema = (
            getattr(effective_class, "EXTRA_CONFIG_SCHEMA", None)
            if effective_class is not None
            else None
        )

        if extras_schema is None:
            # No override class or no schema: create the subentry
            # directly with no extras. Auto-derive runs at HA-setup
            # time using the cached trait metadata stashed on the
            # subentry's data dict by ``_create_device_subentry``.
            return self._create_device_subentry(device_record, extras={})

        if user_input is None:
            # Initial render. Give the device class a chance to pre-fill
            # the form (G400: a relayed read of the camera's RTSP trait).
            data_schema = extras_schema
            prefill_hook = getattr(effective_class, "async_prefill_extras", None)
            if callable(prefill_hook):
                entry = self._get_entry()
                runtime = getattr(entry, "runtime_data", None)
                hub_coordinator = getattr(runtime, "hub", None)
                try:
                    prefill = await prefill_hook(
                        self.hass, device_record, hub_coordinator,
                    )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "async_prefill_extras raised for model=%s: %s",
                        model, exc,
                    )
                    prefill = None
                if prefill:
                    data_schema = self.add_suggested_values_to_schema(
                        extras_schema, prefill,
                    )
            return self.async_show_form(
                step_id="device_extras",
                data_schema=data_schema,
            )

        # Submission: optionally let the device class validate the
        # supplied extras. The hook is forward-compatible: no shipping
        # override class uses it yet (G400 lands in a later chunk), so
        # absence is the common case.
        validate = getattr(device_class, "async_validate_extras", None)
        if callable(validate):
            try:
                error_key = await validate(self.hass, user_input)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "async_validate_extras raised for model=%s: %s",
                    model, exc,
                )
                error_key = "extras_validation_failed"
            if error_key:
                return self.async_show_form(
                    step_id="device_extras",
                    data_schema=extras_schema,
                    errors={"base": error_key},
                )

        return self._create_device_subentry(device_record, extras=user_input)

    async def async_step_bootstrap_review(
        self, user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Bootstrap-scan an uncatalogued device before creating its subentry.

        Runs the same gap-report builder the scan service uses, then
        presents a multi-select form. Accepted entries are written to
        the overlay before the subentry is created; the device's
        deterministic derive then produces entities on first load.
        """
        device_record = self._picked_device
        if device_record is None:
            return self.async_abort(reason="unknown_device")
        did = device_record.get("did", "")
        model = device_record.get("model", "")

        if self._bootstrap_report is None:
            entry = self._get_entry()
            token = entry.data.get(CONF_AQARA_TOKEN, "") if entry is not None else ""
            region = entry.data.get(CONF_AQARA_REGION, "") if entry is not None else ""
            try:
                # Post-V3 role: bootstrap review fires on first-add of any
                # model; for catalogued models the gap report is empty and
                # we short-circuit without a Repair issue. For the ~37
                # uncatalogued models this is the primary path to populate
                # the per-install overlay. See
                # docs/dev/audits/2026-05-24-cloud-surface.md.
                cloud = AqaraCloudClient(region=region, hass=self.hass)
                panels = await cloud.query_collection_panels(token, did)
                paths = list({p for panel in panels.values() for p in panel.paths})
                if not paths:
                    return self._create_device_subentry(device_record, extras={})
                traits_response = await cloud.query_device_traits(
                    token, did, paths=paths,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "bootstrap-scan failed for did=%s model=%s: %s",
                    did, model, exc,
                )
                return self.async_abort(reason="bootstrap_cloud_failed")

            from .gap_report import build_gap_report_from_cloud
            report = build_gap_report_from_cloud(
                panels=panels, traits_response=traits_response,
                catalogue={}, overlay=await self._get_overlay(),
                model=model,
            )

            if not report.entries:
                return self._create_device_subentry(device_record, extras={})

            self._bootstrap_report = report

        report = self._bootstrap_report

        if user_input is None:
            options = [
                SelectOptionDict(
                    value=row.pid,
                    label=(
                        f"{row.pid} / {row.proposed_spec.name} / "
                        f"{row.proposed_spec.function_code or '?'}"
                    ),
                )
                for row in report.entries
            ]
            # Default-selects all entries to reduce clicks; user can untick any.
            schema = vol.Schema({
                vol.Optional(
                    "selected",
                    default=[row.pid for row in report.entries],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    ),
                ),
            })
            return self.async_show_form(
                step_id="bootstrap_review",
                data_schema=schema,
                description_placeholders={
                    "model": model,
                    "did": did,
                    "count": str(len(report.entries)),
                },
            )

        await self._write_bootstrap_acceptance(
            model=model,
            selected_pids=user_input.get("selected") or [],
            report=report,
        )
        self._bootstrap_report = None
        return self._create_device_subentry(device_record, extras={})

    async def _write_bootstrap_acceptance(
        self, *, model: str, selected_pids: list[str], report: Any,
    ) -> None:
        """Write accepted bootstrap traits to the overlay store."""
        from datetime import datetime, timezone
        from .device.overlay import OverlayStore

        # Load fresh state for the write path (not self._get_overlay()'s cache)
        # in case another flow has modified the persisted overlay during this
        # wizard run.
        store = OverlayStore(self.hass)
        overlay = await store.async_load()
        report_by_pid = {e.pid: e for e in report.entries}
        now = datetime.now(timezone.utc)
        for pid in selected_pids:
            entry = report_by_pid.get(pid)
            if entry is None:
                continue
            spec = entry.proposed_spec
            overlay.set_trait(
                model=model, pid=pid, spec=spec,
                discovered_from="bootstrap", discovered_at=now,
            )
        await store.async_write(overlay)

    # ---------------------------------------------------------------------
    # Form rendering + classification
    # ---------------------------------------------------------------------

    async def _render_pick_device_form(
        self,
        entry: ConfigEntry,
        *,
        errors: dict[str, str] | None = None,
    ) -> Any:
        """Build the pick-device form and return the show_form result.

        Performs the cloud query, the topology cross-check (warn-only),
        and the per-device classification. Caches state on ``self`` so
        the submission handler doesn't have to repeat the cloud call.
        On a cloud failure, re-renders the same step with a
        ``cannot_connect`` error so the user can retry.
        """
        hub_did = entry.data[CONF_HUB_DID]
        token = entry.data[CONF_AQARA_TOKEN]
        region = entry.data[CONF_AQARA_REGION]

        try:
            all_devices = await self._fetch_device_list(region, token)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Aqara cloud device list unavailable (hub=%s): %s",
                hub_did, exc,
            )
            merged_errors = dict(errors or {})
            merged_errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="pick_device",
                data_schema=vol.Schema({}),
                errors=merged_errors,
                description_placeholders={
                    "supported_count": "0",
                    "unsupported_info": "",
                },
            )

        candidates, cloud_dids = self._build_candidate_records(
            all_devices, hub_did, entry,
        )
        # Topology cross-validation (warn-only; skipped if coordinator isn't
        # running yet, e.g. first add-device on a fresh entry).
        self._maybe_log_topology_mismatch(entry, cloud_dids)

        existing_dids = self._collect_existing_subentry_dids(entry)
        (
            supported_choices,
            unsupported_records,
            already_added_records,
            devices_by_did,
        ) = await self._classify_candidates(
            candidates, existing_dids, hub_did,
        )
        self._devices_by_did = devices_by_did

        unsupported_info = self._format_unsupported_info(
            unsupported_records, already_added_records,
        )

        if not supported_choices:
            # Nothing to pick. Render an empty schema with the info text so the
            # user can see what was found and bow out.
            return self.async_show_form(
                step_id="pick_device",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "supported_count": "0",
                    "unsupported_info": unsupported_info,
                },
            )

        # HA's SelectSelector renders as a list on the frontend. Single-select:
        # the subentry flow contract creates exactly one subentry per
        # async_create_entry call; users rerun the flow per additional device.
        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=did, label=label)
                            for did, label in supported_choices.items()
                        ],
                        multiple=False,
                        mode=SelectSelectorMode.LIST,
                    ),
                ),
            },
        )

        return self.async_show_form(
            step_id="pick_device",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "supported_count": str(len(supported_choices)),
                "unsupported_info": unsupported_info,
            },
        )

    @staticmethod
    def _build_candidate_records(
        all_devices: list, hub_did: str, entry: ConfigEntry,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Build the subentry-candidate device list from the cloud device list.

        Returns ``(candidates, cloud_dids)``. Three passes:
          1. identify every account hub (records with no parentDeviceId),
          2. accept sub-devices of any account hub plus cluster-member hubs
             that are not the connected tunnel-host,
          3. extend with devices announced in the hub's LANLink topology push
             (e.g. relayed cameras with no parentDeviceId).
        """
        # Pass 1: hubs on the account + the full DID set.
        cloud_dids: set[str] = set()
        hub_dids: set[str] = set()
        for record in all_devices:
            if not isinstance(record, dict):
                continue
            did = record.get("did")
            if not isinstance(did, str):
                continue
            cloud_dids.add(did)
            if not record.get("parentDeviceId"):
                hub_dids.add(did)

        # Pass 2: sub-devices of any account hub + cluster-member hubs.
        candidates: list[dict[str, Any]] = []
        candidate_dids: set[str] = set()
        for record in all_devices:
            if not isinstance(record, dict):
                continue
            did = record.get("did")
            if not isinstance(did, str) or did == hub_did:
                continue
            is_child_of_hub = record.get("parentDeviceId") in hub_dids
            is_cluster_hub = _cloud_devicetype(record) == _CLOUD_DEVICETYPE_HUB
            if not is_child_of_hub and not is_cluster_hub:
                continue
            if did not in candidate_dids:
                candidate_dids.add(did)
                candidates.append(record)

        # Pass 3: topology-announced devices (the only path that makes cameras
        # addable as subentries). Defensive: empty topology adds nothing.
        records_by_did: dict[str, dict[str, Any]] = {
            r["did"]: r
            for r in all_devices
            if isinstance(r, dict) and isinstance(r.get("did"), str)
        }
        for topo_did in _topology_dids_for_entry(entry):
            if topo_did == hub_did or topo_did in candidate_dids:
                continue
            record = records_by_did.get(topo_did)
            if record is None:
                continue
            candidate_dids.add(topo_did)
            candidates.append(record)

        return candidates, cloud_dids

    async def _classify_candidates(
        self,
        candidates: list[dict[str, Any]],
        existing_dids: set[str],
        hub_did: str,
    ) -> tuple[dict[str, str], list, list, dict]:
        """Sort candidates into supported / unsupported / already-added.

        Returns ``(supported_choices, unsupported_records,
        already_added_records, devices_by_did)``.
        """
        unsupported_records: list[tuple[dict[str, Any], str]] = []
        supported_choices: dict[str, str] = {}
        already_added_records: list[dict[str, Any]] = []
        devices_by_did: dict[str, dict[str, Any]] = {}

        _LOGGER.info(
            "pick_device: hub=%s cloud returned %d candidates "
            "(%d already added)",
            hub_did, len(candidates), len(existing_dids),
        )
        for record in candidates:
            did = record["did"]
            devices_by_did[did] = record
            if did in existing_dids:
                already_added_records.append(record)
                continue
            classification = await self._classify(record)
            _LOGGER.debug(
                "pick_device: did=%s model=%s -> %s",
                did, record.get("model", "?"), classification,
            )
            if classification in _SUPPORTED_LABELS:
                supported_choices[did] = self._device_label(record)
            else:
                unsupported_records.append((record, classification))
        return (
            supported_choices,
            unsupported_records,
            already_added_records,
            devices_by_did,
        )

    async def _classify(self, device_record: dict[str, Any]) -> str:
        """Classify a single device for picker eligibility.

        Catalogue-first: descriptor count comes from build_descriptors.
        No cloud call for catalogued models. Uncatalogued models return
        the bootstrap-needed marker; the bootstrap flow handles them.
        """
        from .device.build_descriptors import build_descriptors

        did = device_record.get("did", "")
        model = device_record.get("model", "")

        # Virtual DIDs are cloud-only soft sensors with no LANLink path.
        if isinstance(did, str) and did.startswith("virtual."):
            return _UNSUPPORTED_VIRTUAL

        if registry.get_device_class(model) is not None:
            return _SUPPORTED_OVERRIDE

        overlay = await self._get_overlay()
        descriptors = build_descriptors(model, overlay)
        if descriptors:
            return _SUPPORTED_AUTO_DERIVE
        # Model has no override class and no descriptors from the catalogue
        # or overlay. Surface it in the picker with a bootstrap label so
        # the user can still add it; ``async_step_device_extras`` will
        # route it through the mandatory scan + review step.
        return _SUPPORTED_BOOTSTRAP

    async def _get_overlay(self) -> Any:
        """Lazy-load the overlay once per wizard run."""
        if self._overlay_cache is None:
            from .device.overlay import OverlayStore
            self._overlay_cache = await OverlayStore(self.hass).async_load()
        return self._overlay_cache

    # ---------------------------------------------------------------------
    # Helpers (cloud, topology, subentry creation, formatting)
    # ---------------------------------------------------------------------

    async def _fetch_device_list(
        self, region: str, token: str,
    ) -> list[dict[str, Any]]:
        """Cloud device-list fetch wrapper.

        Pulled into a method so tests can monkey-patch this directly
        without standing up a full ``AqaraCloudClient`` mock chain.
        """
        client = AqaraCloudClient(region=region, hass=self.hass)
        return await client.query_device_list(token)

    @staticmethod
    def _collect_existing_subentry_dids(entry: ConfigEntry) -> frozenset[str]:
        """Return the set of DIDs already configured under this hub.

        Walks ``entry.subentries`` defensively because the attribute is
        only present on HA versions that support config subentries
        (2025.2+). Older HAs return an empty set, which is the right
        no-op behaviour for tests running against an older harness.
        """
        subentries = getattr(entry, "subentries", None) or {}
        dids: set[str] = set()
        for sub in subentries.values():
            data = getattr(sub, "data", None) or {}
            did = data.get(CONF_DEVICE_DID) or data.get("did")
            if isinstance(did, str):
                dids.add(did)
        return frozenset(dids)

    @staticmethod
    def _maybe_log_topology_mismatch(
        entry: ConfigEntry, cloud_dids: set[str],
    ) -> None:
        """Cross-check the LANLink topology push against the cloud.

        If the coordinator is running and reports DIDs the cloud does
        not, log a WARNING. We never block the flow on a mismatch:
        the cloud is the authoritative source for picker enumeration,
        and the LANLink push frame can be stale or partially populated
        on a fresh boot.
        """
        lanlink_dids = _topology_dids_for_entry(entry)
        if not lanlink_dids:
            return
        missing = lanlink_dids - cloud_dids
        if missing:
            hub = getattr(getattr(entry, "runtime_data", None), "hub", None)
            _LOGGER.warning(
                "LANLink topology reports DIDs the cloud does not list "
                "for hub %s: %s",
                getattr(hub, "did", "?"),
                sorted(missing),
            )

    @staticmethod
    def _device_label(record: dict[str, Any]) -> str:
        """Render a device's picker label."""
        name = record.get("deviceName") or record.get("model") or record.get("did")
        model = record.get("model", "")
        if model and name != model:
            return f"{name} ({model})"
        return str(name)

    @staticmethod
    def _format_unsupported_info(
        unsupported_records: list[tuple[dict[str, Any], str]],
        already_added_records: list[dict[str, Any]],
    ) -> str:
        """Build the description-text fragment listing non-pickable devices.

        Two sub-lists, separated by a blank line:

        * Already-added devices (excluded so the user doesn't try to
          re-add them; surfaced so they can see they're recognised).
        * Unsupported devices on this hub.

        Returned as plain text suitable for embedding in a strings.json
        description placeholder. The boilerplate "contributions welcome"
        sentence lives in the strings.json template, so it stays
        localisable.
        """
        lines: list[str] = []

        if already_added_records:
            joined = ", ".join(
                AqaraDeviceSubentryFlow._device_label(r)
                for r in already_added_records
            )
            lines.append(f"Already added: {joined}.")

        if unsupported_records:
            joined = ", ".join(
                AqaraDeviceSubentryFlow._device_label(r)
                for r, _label in unsupported_records
            )
            lines.append(f"Unsupported devices on this hub: {joined}.")

        return "\n\n".join(lines)

    def _create_device_subentry(
        self,
        device_record: dict[str, Any],
        *,
        extras: dict[str, Any] | None = None,
    ) -> Any:
        """Create the subentry from a picked device record.

        The whole cloud device record is stashed under ``_cloud_metadata``
        so device setup has access to the fields it may consult.

        ``extras`` carries any user-supplied per-device configuration
        captured by ``async_step_device_extras`` (for example a G400's
        RTSP path). Extras are merged on top of the standard subentry
        data dict and never override the reserved keys (``did``,
        ``model``, ``_cloud_metadata``); a buggy device class that names
        an extras field after a reserved key would otherwise corrupt the
        subentry's identity.
        """
        did = device_record["did"]
        model = device_record.get("model", "")
        title = device_record.get("deviceName") or model or did
        data: dict[str, Any] = {
            CONF_DEVICE_DID: did,
            CONF_DEVICE_MODEL: model,
            "_cloud_metadata": dict(device_record),
        }
        if extras:
            for key, value in extras.items():
                if key in _RESERVED_SUBENTRY_KEYS:
                    _LOGGER.warning(
                        "Ignoring reserved extras key %r from device class "
                        "for model=%s",
                        key, model,
                    )
                    continue
                data[key] = value
        return self.async_create_entry(
            title=str(title),
            data=data,
            unique_id=did,
        )

