"""Aqara LANLink integration.

End-to-end runtime setup: load the persistent traits/attrs catalog,
construct + start the HubCoordinator, build a Device per subentry (using
either a registered override class or AutoDerivedDevice with cloud-derived
descriptors), and forward platforms.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_HUB_DID,
    CONF_HUB_IP,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
    CONF_PHONE_ID,
    DEFAULT_HUB_MODEL,
    DEFAULT_HUB_PORT,
    DOMAIN,
    PLATFORMS,
    PUSH_STALL_TTL_SECONDS,
)
from .device import registry
from .device import catalog as device_catalog
from .device.base import AutoDerivedDevice, Device, SelfDeviceContext
from .device.camera.base import AutoDerivedCameraDevice
from .device.build_descriptors import build_descriptors
from .device.catalog import ptz_features_for_model
from .device.observed_path_cache import ObservedPathCache
from .device.overlay import Overlay, OverlayStore
from .hub.cloud_client import (
    AqaraCloudAuthError,
    AqaraCloudClient,
    enrich_light_effects,
)
from .hub.coordinator import HubCoordinator
from .hub.rearm import RearmManager
from .hub.topology import classify_tunnel_host
from .ptz.controller import PtzController

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AqaraLanLinkRuntimeData:
    """Per-config-entry runtime data.

    `hub` is the long-lived coordinator owning the LANLink session for this
    config entry. `devices` is keyed by `subentry_id` (one per device
    subentry); each value is a `Device` instance scanned for descriptors at
    setup time. `self_device` is the tunnel host's own device built from the
    hub DID/model traits; None when the hub's own trait resolution fails or
    returns nothing. `overlay` is the per-install extension of the shipped
    catalogue, loaded once at setup time and passed into build_descriptors.
    """

    hub: HubCoordinator
    devices: dict[str, Device]
    self_device: Device | None = None
    # Derived from the LANLink topology push after connection. 'hub' means the
    # tunnel host reported at least one child sub-device DID. 'standalone' is
    # the conservative default when no topology push has arrived yet (the push
    # is asynchronous and may lag setup by seconds). Informational only in
    # Phase 2 -- no behaviour branches on this value.
    host_kind: str = "standalone"
    overlay: Overlay = field(default_factory=Overlay)
    overlay_store: OverlayStore | None = None
    cloud_client: "AqaraCloudClient | None" = None
    cloud_token: str = ""
    cloud_region: str = ""
    # Per-device (device, merged_traits, did, model) captured at setup so the
    # cloud push subscription can be re-armed on every tunnel session-up /
    # topology growth (the hub-side subscription is per-connection).
    subscription_targets: list[tuple[Device, Mapping[str, Any], str, str]] = field(
        default_factory=list,
    )
    # Monotonic timestamp of the last watchdog-triggered re-arm; used to
    # rate-limit re-arms to at most once per stall TTL.
    watchdog_last_rearm: float = 0.0
    # True while a re-arm pass is running, so overlapping triggers (session-up +
    # topology-growth on a fresh connect, or the watchdog) coalesce into one.
    rearm_in_flight: bool = False
    # Consecutive watchdog re-arms that have not yet produced a report; once it
    # reaches the threshold a "push_stalled" Repair is raised. Reset to 0 (and
    # the Repair cleared) as soon as reports resume.
    stall_rearm_count: int = 0
    # Snapshot of entry.options taken at setup. The update listener compares
    # against it so a data-only write (e.g. persisting the rediscovered LANLink
    # endpoint) does not trigger a full reload -- only an actual options change
    # does.
    last_options: dict[str, Any] = field(default_factory=dict)
    # One PtzController per PTZ-capable camera device (and the self-device when
    # the hub is itself a PTZ camera), keyed by DID. Built at setup BEFORE the
    # camera/button/number/select platforms are forwarded so those platforms
    # can look up their device's controller. The local PTZ P2P plane is
    # separate from the LANLink tunnel; controllers connect lazily on first
    # command and are torn down on unload.
    ptz_controllers: dict[str, "PtzController"] = field(default_factory=dict)


# Type alias used by platform modules: `entry.runtime_data` is typed as
# `AqaraLanLinkRuntimeData` when the entry was set up via this integration.
AqaraLanLinkConfigEntry = ConfigEntry[AqaraLanLinkRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """No YAML setup; config entries only."""
    return True


async def _options_update_listener(
    hass: HomeAssistant, entry: ConfigEntry,
) -> None:
    """Reload the entry when its options change.

    The listener also fires on data-only writes (we persist the rediscovered
    LANLink endpoint to entry.data at runtime). Reloading on those would tear
    down and rebuild the live tunnel in a loop, so reload only when the
    options actually changed.
    """
    data = getattr(entry, "runtime_data", None)
    if isinstance(data, AqaraLanLinkRuntimeData):
        new_options = dict(entry.options)
        if new_options == data.last_options:
            return
        data.last_options = new_options
    await hass.config_entries.async_reload(entry.entry_id)


def _resolve_device_label(hass: HomeAssistant, did: str) -> str:
    """Return the user-facing label for `did`: HA device-registry name
    when available, else the bare DID. Falls back gracefully so the
    notification still renders if the device entry hasn't been created
    yet (e.g. the report arrived before async_setup_entry finished
    registering the device)."""
    try:
        dev_reg = dr.async_get(hass)
        entry = dev_reg.async_get_device(identifiers={(DOMAIN, did)})
    except Exception:  # noqa: BLE001 -- best-effort label lookup
        return did
    if entry is None:
        return did
    return entry.name_by_user or entry.name or did


def _format_candidates(
    hass: HomeAssistant, by_device: list[dict],
) -> str:
    """Render newly observed paths as a per-device Markdown section.

    `by_device` is the list returned by `ObservedPathCache.new_paths_by_device`:
    one entry per (did, model) pair. Each section names the HA device
    (via the device registry) plus the model + did so the user can pick
    the exact device to scan, then lists the bullet paths.
    """
    sections: list[str] = []
    for entry in by_device:
        did = entry["did"]
        model = entry["model"]
        paths = sorted(entry["paths"])
        if not paths:
            continue
        label = _resolve_device_label(hass, did)
        header = f"**{label}** (`{did}` -- `{model}`)"
        bullets = "\n".join(f"- `{path}`" for path in paths)
        sections.append(f"{header}\n{bullets}")
    return "\n\n".join(sections)


def _register_candidate_paths_issue(
    hass: HomeAssistant, entry_id: str, cache: ObservedPathCache,
) -> None:
    """Create or refresh the Repair issue for unrecognised observed paths.

    The Fix button invokes aqara_lanlink.scan_device for the affected
    device, routing into the review-and-select flow.
    """
    by_device = cache.new_paths_by_device()
    if not by_device:
        return
    count = str(cache.new_paths_count())
    ir.async_create_issue(
        hass, DOMAIN, f"candidate_paths_{entry_id}",
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="candidate_paths",
        translation_placeholders={
            "count": count,
            "details": _format_candidates(hass, by_device),
        },
        data={"entry_id": entry_id},
    )


async def _run_cloud_call_with_reauth(
    coro,
    *,
    hass: HomeAssistant,
    entry: ConfigEntry,
    cloud_auth_failed: bool,
    op_label: str,
) -> bool:
    """Run a cloud-call coroutine; on auth failure trigger HA's re-auth flow.

    Returns the updated cloud_auth_failed flag. The first auth failure in
    a setup pass triggers `entry.async_start_reauth(hass)` (which adds a
    Repair card the user can act on); subsequent calls in the same pass
    short-circuit so we don't re-trigger the flow per device.

    Non-auth cloud failures propagate to the caller, which can log and
    continue with graceful degradation.
    """
    if cloud_auth_failed:
        coro.close()  # release the un-awaited coroutine cleanly
        return cloud_auth_failed
    try:
        await coro
    except AqaraCloudAuthError as exc:
        _LOGGER.warning(
            "Aqara cloud rejected %s (%s); triggering re-auth flow",
            op_label, exc,
        )
        entry.async_start_reauth(hass)
        return True
    return False


def _ensure_phone_id(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the entry's stable cloud PhoneId, generating+persisting if absent.

    Aqara namespaces push-subscription state by (user, PhoneId). A PhoneId that
    changes per request/reload orphans every subscription on the hub and lets
    dead ``policy_resset`` entries accumulate. Persisting one value per install
    in ``entry.data`` keeps the subscription identity stable across reloads.
    Runs at setup so both new and pre-existing entries are covered uniformly.
    """
    existing = entry.data.get(CONF_PHONE_ID)
    if existing:
        return existing
    phone_id = str(uuid.uuid4()).upper()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PHONE_ID: phone_id},
    )
    return phone_id


def _topology_grew(prev: Any, new: Any) -> bool:
    """True when ``new`` carries a DID not present in ``prev`` (hub became ready
    / a device rejoined). A steady-state or shrinking topology returns False so
    we only re-arm when there is genuinely something new to subscribe for.
    Defensive against non-iterable inputs (e.g. uninitialised mocks)."""
    try:
        return bool(set(new) - set(prev))
    except TypeError:
        return False


async def _rearm_subscriptions(
    hass: HomeAssistant, entry: "AqaraLanLinkConfigEntry",
) -> None:
    """Re-run subscribe+seed for every device on the current cloud session.

    The hub-side push subscription is per-tunnel-connection, so it must be
    re-armed whenever the tunnel reconnects or the hub's topology becomes
    ready. Best-effort: per-device cloud errors are logged and skipped; an auth
    failure triggers the re-auth flow once.
    """
    data = getattr(entry, "runtime_data", None)
    if data is None:
        return
    cloud = data.cloud_client
    token = data.cloud_token
    if cloud is None or not token or not data.subscription_targets:
        return
    # Coalesce overlapping triggers: a re-arm re-subscribes the same fixed
    # target set, so a second concurrent pass would only duplicate cloud calls.
    if data.rearm_in_flight:
        return
    data.rearm_in_flight = True
    try:
        cloud_auth_failed = False
        for device, merged_traits, did, model in data.subscription_targets:
            cloud_auth_failed = await _run_cloud_call_with_reauth(
                _subscribe_and_seed_traits(
                    cloud=cloud, token=token, device=device,
                    merged_traits=merged_traits, did=did, model=model,
                ),
                hass=hass, entry=entry,
                cloud_auth_failed=cloud_auth_failed,
                op_label=f"re-arm subscribe+seed for did={did}",
            )
    finally:
        data.rearm_in_flight = False


# Number of consecutive failed watchdog re-arms before raising a Repair telling
# the user the hub likely needs attention (reboot / LAN Control). At one re-arm
# per TTL this is ~15 minutes of sustained silence.
STALL_REARM_REPAIR_THRESHOLD = 3


def _push_appears_stalled(
    *,
    connected: bool,
    topology_size: int,
    seconds_since_report: float,
    ttl: float,
) -> bool:
    """True when the tunnel is up and the hub claims a ready topology, yet no
    report has arrived within ``ttl`` -- i.e. forwarding is silently wedged.
    Disconnected / empty-topology states are owned by the reconnect and
    topology-growth re-arm paths, so they are not treated as stalled here.
    """
    return bool(connected) and topology_size > 0 and seconds_since_report > ttl


async def _watchdog_tick(
    hass: HomeAssistant, entry: "AqaraLanLinkConfigEntry",
) -> None:
    """One watchdog pass: re-arm the subscription if pushes appear stalled."""
    data = getattr(entry, "runtime_data", None)
    if data is None:
        return
    coordinator = data.hub
    issue_id = f"push_stalled_{entry.entry_id}"
    seconds_since = coordinator.seconds_since_last_report()
    if not _push_appears_stalled(
        connected=coordinator.connected,
        topology_size=len(coordinator.lanlink_topology_dids),
        seconds_since_report=seconds_since,
        ttl=PUSH_STALL_TTL_SECONDS,
    ):
        # Healthy (or recovered): clear any stall escalation.
        if data.stall_rearm_count:
            data.stall_rearm_count = 0
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    # Cooldown: a single re-arm needs time to take effect (and produce a report);
    # don't hammer the cloud every keepalive while a hub stays wedged.
    now = time.monotonic()
    if now - data.watchdog_last_rearm < PUSH_STALL_TTL_SECONDS:
        return
    data.watchdog_last_rearm = now
    data.stall_rearm_count += 1
    _LOGGER.warning(
        "LANLink pushes appear stalled (no report in %.0fs, topology=%d DIDs); "
        "re-arming push subscription (attempt %d)",
        seconds_since, len(coordinator.lanlink_topology_dids),
        data.stall_rearm_count,
    )
    await _rearm_subscriptions(hass, entry)
    # Re-arming repeatedly without recovery means the hub itself needs attention
    # (reboot / LAN Control off) -- escalate to a Repair.
    if data.stall_rearm_count >= STALL_REARM_REPAIR_THRESHOLD:
        ir.async_create_issue(
            hass, DOMAIN, issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="push_stalled",
        )


async def _build_device(
    hass: HomeAssistant,
    entry: AqaraLanLinkConfigEntry,
    *,
    coordinator: Any,
    cloud: Any,
    token: str,
    overlay: Any,
    did: str,
    model: str,
    subentry: Any,
    cloud_auth_failed: bool,
) -> tuple[Device, dict, bool]:
    """Build one Device through the shared setup steps 3a-3e.

    Covers: descriptor build, cloud light-effects enrichment, override-class
    selection, ``async_setup``, the known-wire-paths gate, report-handler
    registration, and the cloud subscribe+seed. Used by both the per-subentry
    loop and the tunnel-host self-device path (which previously kept a
    hand-maintained copy of these steps in sync).

    Returns ``(device, merged_traits, cloud_auth_failed)``; the updated
    ``cloud_auth_failed`` flag is threaded back so the caller keeps tracking
    whether a re-auth was already triggered this setup pass.
    """
    # 3a: descriptors from the shipped catalogue + overlay (pure, no cloud).
    derived = build_descriptors(model, overlay)
    # 3a-bis: enrich LightDescriptors with cloud-fetched scenes (best-effort).
    cloud_auth_failed = await _run_cloud_call_with_reauth(
        enrich_light_effects(
            cloud, token,
            device_model=model, user_device_id=did, descriptors=derived,
        ),
        hass=hass, entry=entry, cloud_auth_failed=cloud_auth_failed,
        op_label=f"effects enrichment for did={did}",
    )
    # 3b: override class, else auto-derived (camera vs generic).
    cls = registry.get_device_class(model)
    if cls is None:
        cls = (
            AutoDerivedCameraDevice
            if device_catalog.is_camera_model(model)
            else AutoDerivedDevice
        )
    device = cls(coordinator=coordinator, subentry=subentry, derived=derived)
    await device.async_setup(coordinator, subentry)
    # 3c: known-paths gate (catalogue + overlay + policy-dropped paths).
    merged_traits: dict = dict(overlay.traits_for_model(model))
    merged_traits.update(device_catalog.all_traits_for_model(model))
    device._known_wire_paths = (
        frozenset(
            spec.wire_path
            for spec in merged_traits.values()
            if spec.wire_path
        )
        | device_catalog.dropped_paths_for_model(model)
    )
    # 3d: route incoming LANLink reports for this DID into the device.
    device._unregister_report_handler = (
        coordinator.register_report_handler(did, device.handle_report)
    )
    # 3e: subscribe + seed initial trait values via the cloud (best-effort).
    cloud_auth_failed = await _run_cloud_call_with_reauth(
        _subscribe_and_seed_traits(
            cloud=cloud, token=token, device=device,
            merged_traits=merged_traits, did=did, model=model,
        ),
        hass=hass, entry=entry, cloud_auth_failed=cloud_auth_failed,
        op_label=f"trait subscribe+seed for did={did}",
    )
    return device, merged_traits, cloud_auth_failed


async def async_setup_entry(
    hass: HomeAssistant, entry: AqaraLanLinkConfigEntry,
) -> bool:
    """Set up an Aqara LANLink tunnel-host config entry.

    Steps:
        0. Load the local overlay and pre-warm the model registry.
        1. Construct + start the HubCoordinator; wait for first checkin.
        1a. Wire AqaraCloudClient onto the coordinator for entity-level
            cloud calls (e.g. AqaraLight dynamic-effect run_sequence).
        2. Register the hub in HA's device registry.
        3. For each subentry, resolve descriptors from the catalogue, pick
           an override class (or AutoDerivedDevice), instantiate, seed
           initial values, register reconnect-driven re-reads.
        4. Stash AqaraLanLinkRuntimeData on entry.runtime_data.
        5. Forward platforms.
    """
    # Step 0: Load the local overlay (per-install trait extensions accepted
    # via the scan service). Loaded once here; passed into build_descriptors
    # for each device. Returns an empty Overlay when no file exists yet.
    overlay_store = OverlayStore(hass)
    overlay = await overlay_store.async_load()

    # Step 0a: Pre-warm the per-model registry so subsequent sync
    # `registry.get_device_class` / `catalog.get_*` calls during the
    # subentry config flow do not block the event loop on import +
    # filesystem walk. Idempotent across multiple entries.
    await registry.async_ensure_discovered(hass)

    # Step 1: Start the LANLink session coordinator.
    def _persist_endpoint(host: str, port: int) -> None:
        """Persist the rediscovered LANLink endpoint to entry.data.

        The hub rotates its LANLink port across reboots; mDNS recovers the new
        one at runtime. Storing it back means the next HA start dials the
        last-known-good port first instead of the stale config-flow port. This
        is a data-only write -- the update listener ignores it (no reload)."""
        if (
            entry.data.get(CONF_HUB_IP) == host
            and entry.data.get(CONF_HUB_PORT) == port
        ):
            return
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HUB_IP: host, CONF_HUB_PORT: port},
        )

    coordinator = HubCoordinator(
        host=entry.data[CONF_HUB_IP],
        port=entry.data.get(CONF_HUB_PORT, DEFAULT_HUB_PORT),
        device_id=entry.data[CONF_HUB_DID],
        user_id=entry.data[CONF_AQARA_USER_ID],
        token=entry.data[CONF_AQARA_TOKEN],
        model=entry.data.get(CONF_HUB_MODEL, DEFAULT_HUB_MODEL),
        hass=hass,
        on_endpoint_change=_persist_endpoint,
    )
    # ObservedPathCache tracks wire paths seen via LANLink push but not
    # yet bound to a (model, pid). The callback registers a HA Repair
    # issue prompting the user to reload the integration.
    coordinator.observed_path_cache = ObservedPathCache(
        hass, on_new_paths_callback=lambda: _register_candidate_paths_issue(
            hass, entry.entry_id, coordinator.observed_path_cache,
        ),
    )
    await coordinator.observed_path_cache.async_load()
    # Prune cached paths that the trait_policy now drops. The runtime gate
    # at handle_report won't record these going forward (Device's
    # _known_wire_paths unions in dropped_paths_for_model), but any entries
    # recorded BEFORE that gate landed are stale -- typically the
    # ZigbeeNetworkDiagnostics / BasicInformation administrative chatter
    # that the policy now drops at generator time. Clean them on every load
    # so the user doesn't see a stale candidate-paths notification listing
    # what's intentionally filtered.
    pruned = coordinator.observed_path_cache.prune_dropped_paths(
        device_catalog.dropped_paths_for_model,
    )
    if pruned:
        _LOGGER.info(
            "observed_paths: pruned %d stale entries now classified as drop "
            "by trait_policy", pruned,
        )
    # Successful setup clears any stale candidate-paths notification from a
    # previous run. Delete any lingering Repair issue for this entry.
    ir.async_delete_issue(
        hass, DOMAIN, f"candidate_paths_{entry.entry_id}",
    )
    coordinator.start()
    try:
        await coordinator.wait_connected(timeout=30.0)
    except asyncio.TimeoutError as exc:
        await coordinator.stop()
        raise ConfigEntryNotReady(
            "Hub did not respond within 30s",
        ) from exc

    # Wrap remaining setup so partial failures don't leak the coordinator's
    # background reader/writer task. HA retries async_setup_entry on
    # ConfigEntryNotReady; without this, every retry would spawn another
    # coordinator instance while leaving the previous one running.
    try:
        # Step 1a: Build the cloud client (long-lived session) and attach
        # it to the coordinator so entities can access it without
        # round-tripping through the integration module.
        region = entry.data[CONF_AQARA_REGION]
        cloud = AqaraCloudClient(
            region=region,
            session=async_get_clientsession(hass),
            phone_id=_ensure_phone_id(hass, entry),
        )
        coordinator.cloud_client = cloud

        # Step 2: Register the hub in HA's device registry.
        dev_reg = dr.async_get(hass)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, coordinator.did)},
            manufacturer="Aqara",
            model=entry.data.get(CONF_HUB_MODEL, DEFAULT_HUB_MODEL),
            name=entry.title,
        )

        # Step 3: Build a Device per subentry.
        token = entry.data[CONF_AQARA_TOKEN]
        devices: dict[str, Device] = {}
        # Captured per device for re-arming the push subscription on reconnect.
        subscription_targets: list[tuple[Device, Mapping[str, Any], str, str]] = []
        # Tracks whether a cloud auth failure has already triggered the
        # re-auth flow this setup pass. Subsequent cloud calls in the same
        # pass short-circuit so we don't fire async_start_reauth per device.
        cloud_auth_failed = False
        subentries = getattr(entry, "subentries", None) or {}
        for subentry_id, subentry in subentries.items():
            try:
                model = subentry.data["model"]
                did = subentry.data["did"]
            except (AttributeError, KeyError) as exc:
                _LOGGER.warning(
                    "Skipping subentry %s: missing did/model (%s)",
                    subentry_id, exc,
                )
                continue

            # Steps 3a-3e (descriptors, effects, class, setup, known-paths
            # gate, report handler, subscribe+seed) are shared with the
            # self-device path via _build_device.
            device, merged_traits, cloud_auth_failed = await _build_device(
                hass, entry,
                coordinator=coordinator, cloud=cloud, token=token,
                overlay=overlay, did=did, model=model, subentry=subentry,
                cloud_auth_failed=cloud_auth_failed,
            )
            subscription_targets.append((device, merged_traits, did, model))

            # Diagnostic: dump the stashed cloud traits and the resolved
            # descriptor identities so we can compare the descriptor paths
            # to live LANLink push paths during early-bringup debugging.
            _log_path_diagnostic(
                subentry_id, did, subentry, device,
                coordinator=coordinator,
            )

            devices[subentry_id] = device

        entry.runtime_data = AqaraLanLinkRuntimeData(
            hub=coordinator,
            devices=devices,
            overlay=overlay,
            overlay_store=overlay_store,
            cloud_client=cloud,
            cloud_token=token,
            cloud_region=region,
            subscription_targets=subscription_targets,
            last_options=dict(entry.options),
        )

        # Step 3f-pre: Classify the tunnel host from the LANLink topology push.
        # The topology push is asynchronous and typically arrives after setup;
        # coordinator.lanlink_topology_dids will be frozenset() unless the push
        # has already been processed. classify_tunnel_host conservatively returns
        # 'standalone' for an empty set, so the label is always valid but may
        # not yet reflect the true topology. Informational only in Phase 2.
        hub_did = entry.data[CONF_HUB_DID]
        # No `await` between the initial classify below and the callback
        # wiring further down: a topology push processed in that window would
        # be missed by both. Keep these two blocks adjacent and await-free.
        host_kind = classify_tunnel_host(
            coordinator.lanlink_topology_dids, hub_did,
        )
        entry.runtime_data.host_kind = host_kind
        _LOGGER.info(
            "Aqara tunnel host %s classified as %s",
            hub_did, host_kind,
        )

        # Wire the topology-change callback so host_kind stays correct when
        # the LANLink topology push arrives after setup completes. It also
        # re-arms the push subscription when the topology GROWS: the hub forwards
        # nothing until its mesh is ready, and the inline subscribe at setup may
        # have run while topology was still empty (the 0-DID cold-start case).
        try:
            prev_topology: Any = frozenset(coordinator.lanlink_topology_dids)
        except TypeError:
            prev_topology = frozenset()

        # Re-arm relay activation for configured standalone Wi-Fi devices (the
        # FP2). A hub reboot or device power-cycle drops the device from the
        # LANLink topology; RearmManager re-activates it (TLS handshake to its
        # :443) when it falls out of topology, retrying while the device is
        # still booting. A periodic sweep covers the case where the device
        # returns without emitting a topology push.
        rearm = RearmManager(hass, entry)
        entry.async_on_unload(rearm.cancel_all)
        entry.async_on_unload(
            async_track_time_interval(
                hass, rearm.sweep, timedelta(minutes=5),
                cancel_on_shutdown=True,
            )
        )

        def _on_topology_changed(dids: frozenset[str]) -> None:
            nonlocal prev_topology
            # Sticky upgrade only: once we've seen children and classified the
            # host as a hub, a later transient 0-DID push (hub mesh momentarily
            # empty) must not downgrade it back to 'standalone'.
            new_kind = classify_tunnel_host(dids, hub_did)
            if new_kind == "hub" and entry.runtime_data.host_kind != "hub":
                _LOGGER.info(
                    "Aqara tunnel host %s reclassified as hub (was %s)",
                    hub_did, entry.runtime_data.host_kind,
                )
                entry.runtime_data.host_kind = "hub"
            grew = _topology_grew(prev_topology, dids)
            prev_topology = dids
            if grew:
                _LOGGER.info(
                    "LANLink topology grew (%d DIDs); re-arming push subscription",
                    len(dids),
                )
                hass.async_create_task(_rearm_subscriptions(hass, entry))
            # Re-activate any configured standalone device absent from the new
            # topology; cancel a pending re-arm for one that just came back.
            rearm.note_topology(dids)

        coordinator.on_topology_changed = _on_topology_changed

        # Re-arm the push subscription on every tunnel session-up. The hub-side
        # subscription is per-connection, so a reconnect (even one that returns
        # the same topology, which _on_topology_changed would not treat as
        # growth) leaves pushes disarmed until we re-subscribe.
        def _on_session_up() -> None:
            hass.async_create_task(_rearm_subscriptions(hass, entry))

        coordinator.on_session_up = _on_session_up

        # Push-liveness watchdog, driven off the keepalive cycle (every ~10s):
        # re-arm if a connected, topology-ready hub stops forwarding past the
        # stall TTL -- a case neither session-up nor topology-growth re-arm would
        # catch. _watchdog_tick self-rate-limits via the re-arm cooldown.
        def _on_keepalive() -> None:
            hass.async_create_task(_watchdog_tick(hass, entry))

        coordinator.on_keepalive = _on_keepalive

        # Step 3f: Build the self-device for the tunnel host's own traits.
        # Mirrors the per-subentry setup (steps 3a-3e); keep the two in sync.
        # This is a graceful best-effort step: any failure (cloud error,
        # empty descriptor set, unexpected exception) leaves self_device as
        # None, logs a warning, and never blocks setup or platform forwarding.
        # A hub whose qlink/trait/read returns nothing is a normal case.
        hub_model = entry.data.get(CONF_HUB_MODEL, DEFAULT_HUB_MODEL)
        try:
            self_ctx = SelfDeviceContext(
                did=hub_did,
                model=hub_model,
                camera_ip=entry.options.get("camera_ip", ""),
                rtsp_username=entry.options.get("rtsp_username", ""),
                rtsp_password=entry.options.get("rtsp_password", ""),
                backchannel_channel=int(entry.options.get("backchannel_channel", 1)),
            )
            # Same steps 3a-3e as the per-subentry devices, via _build_device.
            self_device: Device | None
            self_device, _self_merged_traits, cloud_auth_failed = await _build_device(
                hass, entry,
                coordinator=coordinator, cloud=cloud, token=token,
                overlay=overlay, did=hub_did, model=hub_model, subentry=self_ctx,
                cloud_auth_failed=cloud_auth_failed,
            )
            entry.runtime_data.self_device = self_device
        except Exception:  # noqa: BLE001 -- never block setup on self-device failure
            _LOGGER.warning(
                "Self-device build failed for hub did=%s model=%s; "
                "hub entities will not be available until next reload.",
                hub_did, hub_model,
                exc_info=True,
            )

        # Build a PtzController per PTZ-capable camera (per-subentry devices and
        # the self-device) BEFORE forwarding platforms. The camera/button/number/
        # select platforms read entry.runtime_data.ptz_controllers during their
        # own setup, and platform setup order is not guaranteed, so the dict must
        # be fully populated first. The IP is resolved lazily on first command via
        # the device's async_resolve_camera_ip coroutine.
        def _build_ptz_controller(
            device: Device, subentry: Any, model: str,
        ) -> None:
            features = ptz_features_for_model(model)
            if not features:
                return

            async def _ip_provider() -> str | None:
                return await device.async_resolve_camera_ip(coordinator, subentry)

            entry.runtime_data.ptz_controllers[device.did] = PtzController(
                cloud_client=cloud,
                token=token,
                user_id=entry.data[CONF_AQARA_USER_ID],
                did=device.did,
                camera_ip_provider=_ip_provider,
                features=features,
            )

        for subentry_id, device in devices.items():
            _build_ptz_controller(device, subentries[subentry_id], device.MODEL)
        if entry.runtime_data.self_device is not None:
            self_dev = entry.runtime_data.self_device
            _build_ptz_controller(self_dev, self_dev.subentry, self_dev.MODEL)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        from .services import register_services
        entry.async_on_unload(register_services(hass, entry))
        entry.async_on_unload(
            entry.add_update_listener(_options_update_listener)
        )
    except Exception:
        # Any failure after wait_connected leaks the coordinator's background
        # task unless we tear it down explicitly. Stop, then re-raise so HA
        # surfaces the original error (typically ConfigEntryNotReady -> retry).
        await coordinator.stop()
        raise

    return True


# Per-call cap on the cloud trait-read batch. Aqara's open API accepts up to
# ~16 paths per qlink/trait/read; over-large batches return malformed frames
# that close the tunnel. Keep this conservative.
_TRAIT_READ_BATCH_SIZE = 16


async def _subscribe_and_seed_traits(
    *,
    cloud: AqaraCloudClient,
    token: str,
    device: Device,
    merged_traits: Mapping[str, Any],
    did: str,
    model: str,
) -> None:
    """Cloud-subscribe to a device's traits and seed initial values.

    Posts qlink/trait/read with needSubscribe=true for each catalogued
    wire path. The response yields the current value per path; seed it
    into the device via seed_initial_value so entities show state on
    first registration. Subscription is per-tunnel-connection on the
    hub side, so this must re-run on every setup / reload.

    Never raises -- per-path errors are logged and skipped.
    Catalogue traits with no wire_path are excluded.
    Empty trait list is a no-op.
    """
    paths: list[str] = sorted({
        spec.wire_path
        for spec in merged_traits.values()
        if getattr(spec, "wire_path", None)
    })
    if not paths:
        return
    seeded = 0
    failed_batches = 0
    for i in range(0, len(paths), _TRAIT_READ_BATCH_SIZE):
        batch = paths[i:i + _TRAIT_READ_BATCH_SIZE]
        try:
            traits = await cloud.query_device_traits(token, did, batch)
        except AqaraCloudAuthError:
            # Token expired/invalid -- propagate so the caller can trigger
            # HA's re-auth flow once per setup pass. Without this, every
            # batch would log a generic WARNING and the user would never
            # see an actionable Repair card.
            raise
        except Exception as exc:  # noqa: BLE001 -- log and continue
            failed_batches += 1
            _LOGGER.warning(
                "subscribe+seed: cloud read failed for did=%s model=%s "
                "batch=%s (%s); pushes for those paths will be inactive "
                "until next reload.",
                did, model, batch, exc,
            )
            continue
        for entry in traits:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if not path:
                continue
            value = entry.get("value")
            # Skip None AND empty string. Aqara returns "" for traits that
            # haven't produced a reading yet (e.g. p100 tilt angles before
            # first measurement); seeding "" lands on numeric sensors and
            # makes HA reject the entity with a "non-numeric value" error.
            if value is None or value == "":
                continue
            device.seed_initial_value(path, str(value))
            seeded += 1
    _LOGGER.info(
        "subscribe+seed: did=%s model=%s paths=%d seeded=%d failed_batches=%d",
        did, model, len(paths), seeded, failed_batches,
    )
    # Record the arm so the coordinator can time the arm -> first-report latency
    # (a diagnostic for whether arming actually causes pushes, and how fast).
    note = getattr(getattr(device, "coordinator", None), "note_subscription_armed", None)
    if callable(note):
        note(did)


def _log_path_diagnostic(
    subentry_id: str,
    did: str,
    subentry: Any,
    device: Device,
    *,
    coordinator: Any = None,
) -> None:
    """Dump path/pid info to compare descriptor paths vs live LANLink keys.

    Logs diagnostic DEBUG lines per subentry on every reload (device values
    and DIDs are verbose and semi-sensitive, so they stay off INFO):
      - cloud traits: every (path, propertyId) we got back from the cloud
      - descriptor ids: the trait.id / attr.name lookup keys this device
        will use to dispatch incoming reports and write outbound values

    Compared against ``LANLink report ... values=...`` lines from the
    coordinator, this surfaces the 3-part vs 4-part path mismatch (and
    any first-match-wins selection bug for propertyIds with multiple
    paths) without needing to re-add the device.
    """
    try:
        stashed = subentry.data.get("_cloud_traits") or []
    except AttributeError:
        stashed = []
    cloud_paths_by_pid: dict[str, list[tuple[str, Any]]] = {}
    for trait in stashed:
        path = trait.get("path") or ""
        pids = trait.get("propertyId") or []
        pid = pids[0] if pids else None
        if pid:
            cloud_paths_by_pid.setdefault(pid, []).append(
                (path, trait.get("value")),
            )
    _LOGGER.debug(
        "diag subentry=%s did=%s cloud_paths_by_pid=%s",
        subentry_id, did, cloud_paths_by_pid,
    )
    _LOGGER.debug(
        "diag subentry=%s did=%s by_trait_id=%s by_attr_name=%s",
        subentry_id, did,
        sorted(device._by_trait_id.keys()),
        sorted(device._by_attr_name.keys()),
    )
    if coordinator is not None:
        observed_cache = getattr(coordinator, "observed_path_cache", None)
        if observed_cache is not None:
            observed_for_model = sorted(
                observed_cache.get_paths(device.MODEL)
            )
            _LOGGER.debug(
                "diag subentry=%s did=%s observed_paths_for_model=%s "
                "new_since_setup_count=%d",
                subentry_id, did, observed_for_model,
                observed_cache.new_paths_count(),
            )



async def async_unload_entry(
    hass: HomeAssistant, entry: AqaraLanLinkConfigEntry,
) -> bool:
    """Unload an Aqara LANLink hub config entry."""
    data: AqaraLanLinkRuntimeData = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Tear down PTZ controllers (close any warm P2P session / cancel idle
        # timers). Best-effort: a per-controller shutdown failure must not block
        # the rest of unload.
        if data.ptz_controllers:
            await asyncio.gather(
                *(c.async_shutdown() for c in data.ptz_controllers.values()),
                return_exceptions=True,
            )
            data.ptz_controllers.clear()
        for device in data.devices.values():
            unregister = getattr(device, "_unregister_report_handler", None)
            if unregister is not None:
                try:
                    unregister()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "report handler unregister raised for %s",
                        getattr(device, "did", "?"),
                    )
                device._unregister_report_handler = None
            try:
                await device.async_unload()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "device.async_unload raised for %s", getattr(device, "did", "?"),
                )
        # Symmetric teardown for the self-device. Mirrors the per-subentry
        # loop above.
        self_device = data.self_device
        if self_device is not None:
            unregister = getattr(self_device, "_unregister_report_handler", None)
            if unregister is not None:
                try:
                    unregister()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "report handler unregister raised for self-device %s",
                        getattr(self_device, "did", "?"),
                    )
                self_device._unregister_report_handler = None
            try:
                await self_device.async_unload()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "device.async_unload raised for self-device %s",
                    getattr(self_device, "did", "?"),
                )
        await data.hub.stop()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: AqaraLanLinkConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Handle a device deleted from the device page.

    Each sub-device is backed by its own config subentry, so deleting the
    device means deleting that subentry: ``async_remove_subentry`` clears the
    device and all its entities (sensors, etc.) from the registries and stops
    setup re-creating them, after which we reload the entry so the live tunnel
    drops the device's report handler, PTZ controller and push subscription.
    (Removing the subentry alone does not reload -- the options-update listener
    ignores subentry-only changes -- hence the explicit schedule.)

    The hub device itself is associated with the config entry directly (config
    subentry id ``None``); it is refused here and removed by deleting the whole
    integration. A device whose subentry has already gone (a stale leftover) is
    allowed through so Home Assistant can purge the registry entry.
    """
    entry_id = config_entry.entry_id
    subentry_ids = device_entry.config_entries_subentries.get(entry_id, set())
    live_subentry_ids = [
        subentry_id
        for subentry_id in subentry_ids
        if subentry_id is not None and subentry_id in config_entry.subentries
    ]
    if live_subentry_ids:
        for subentry_id in live_subentry_ids:
            hass.config_entries.async_remove_subentry(config_entry, subentry_id)
        hass.config_entries.async_schedule_reload(entry_id)
        return True
    if None in subentry_ids:
        # Hub / self-device: not deletable from the device page.
        return False
    # No live subentry backs this device -- let HA purge the stale entry.
    return True
