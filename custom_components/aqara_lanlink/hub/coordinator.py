"""LANLink session coordinator.

Owns the long-running loop that keeps a LANLink session alive against an
Aqara hub:
    1. open EncryptedTunnel
    2. send checkin
    3. listen for reports, dispatching each to handlers registered
       via ``register_report_handler`` keyed by sub-device DID
    4. on any error/close, backoff and reconnect

Designed to be started once from `async_setup_entry` and stopped on unload.
The hub's actual idle timeout is generous once the cloud user/token pair has
been verified, so this just needs to be robust against transient TCP issues,
hub restarts, and token/credential rotation.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Any, Protocol
from collections.abc import Callable

from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from .cloud_client import AqaraCloudClient
    from ..device.observed_path_cache import ObservedPathCache

from . import topology
from .session import LANLinkClient
from .protocol import (
    G400_MODEL,
    LANLINK_CMD_KEEPALIVE,
    LANLINK_CMD_KEEPALIVE_DONE,
    LANLINK_CMD_TOPOLOGY,
    LANLINK_TYPE_DEVICE,
    LANReport,
    build_read,
    build_write,
)
from .topology import TopologyDevice
from .tunnel import EncryptedTunnel, TunnelError
from .mdns import discover_hub_by_did

_LOGGER = logging.getLogger(__name__)


ReportHandler = Callable[[LANReport], None]

# Reports can arrive in the gap between session-up and a device's handler being
# registered at setup. Briefly buffer per-DID so the first post-reboot report is
# replayed on registration instead of dropped. Bounded so a chatty/unknown DID
# can't grow memory without limit.
_REPORT_BUFFER_MAXLEN = 10
_REPORT_BUFFER_MAX_DIDS = 64

# Outbound-write srcs are remembered briefly so the hub's echo of our own write
# can be recognised and suppressed in _dispatch_report (see self-echo handling).
_SELF_SRC_TTL = 30.0   # seconds to keep a src (echoes arrive in ~1s; generous slack)
_SELF_SRC_MAX = 64     # hard cap (guards against echoes that never arrive)


def _apply_jitter(delay: float) -> float:
    """Randomly spread a reconnect delay to [0.5x, 1.5x] so multiple clients
    that a hub dropped at once don't all reconnect on the same beat."""
    return delay * random.uniform(0.5, 1.5)


def _to_wire_path(path: str) -> str:
    """Append the LANLink resource-instance suffix to a 3-part trait path.

    Cloud trait metadata (and our descriptors) store 3-part canonical
    paths (``<endpoint>.<group>.<resource>``). Live LANLink push reports
    AND the form the device's firmware accepts on writes use 4-part
    keys with a trailing instance index (``2.132.32920.1``). Inbound
    reports also arrive as 4-part -- see ``Device._canonicalize_report_key``
    which strips the suffix at receive time so the descriptor index
    matches.

    Verified against the Aqara Home app's outbound writes captured via
    Frida on 2026-05-09: the app's `write_done` response listed
    ``result: ["2.132.32920.1", "2.132.32922.0"]`` (4-part), and the
    same paths show up on every push report we receive. Without the
    suffix the cluster delivers the write to the parent hub but the
    device firmware silently rejects it (returns ``code=0, result=null``).

    Already-4-part paths (e.g. authored TraitSpecs that explicitly
    encode a non-default channel) pass through unchanged. Single-
    channel devices like a single-bulb almost universally use ``.1``;
    multi-channel devices that need per-channel suffixes will require
    a per-descriptor override slot once we have one.
    """
    if path.count(".") == 2:
        return f"{path}.1"
    return path


class _HasName(Protocol):
    """Structural type for AttrSpec-like objects.

    Task 3.2 ships before Chunk 3 (which lands the real AttrSpec dataclass).
    The coordinator only needs the ``.name`` attribute for building wire
    messages and dict-keyability for writes, so we accept anything that
    looks like that. When AttrSpec arrives as a frozen kw_only dataclass
    it automatically satisfies this protocol.
    """

    name: str

    def __hash__(self) -> int: ...


class HubCoordinator:
    """Maintain a LANLink session and dispatch incoming reports.

    Parameters
    ----------
    host, port:
        TCP endpoint of the Aqara hub (mDNS-discovered or user-supplied).
    device_id:
        Hub DID (e.g. `lumi1.TESTHUB00001`). Used both for device-key
        derivation in the tunnel and as the checkin `did` field.
    user_id, token:
        Aqara cloud credentials (user ID + session token) obtained via
        `AqaraCloudClient`.
    model:
        Device model string sent in read/write requests. Defaults to the
        G400 model; non-G400 integrations can override.
    reconnect_min_delay / reconnect_max_delay:
        Exponential backoff bounds, in seconds, for the reconnect loop.

    Report handlers are registered per sub-device DID via
    ``register_report_handler``. The coordinator routes each incoming
    report to the handlers registered under ``report.sdid`` (falling back
    to ``report.did`` when ``sdid`` is empty). Handlers are synchronous;
    async work should be scheduled with ``asyncio.create_task`` from
    within the handler.
    """

    # Per-entry ObservedPathCache; assigned by async_setup_entry after
    # construction. Tracks wire paths seen via LANLink push but not yet
    # bound to a (model, pid). None until async_setup_entry assigns it.
    observed_path_cache: "ObservedPathCache | None" = None
    # Optional callback invoked after each topology push, with the updated
    # DID frozenset. Assigned by async_setup_entry after construction so
    # the coordinator does not reach into HA config-entry internals.
    # A None value means no callback is installed.
    on_topology_changed: "Callable[[frozenset[str]], None] | None" = None
    # Invoked (no args) each time a tunnel session reaches the connected state
    # after a successful checkin. The hub's push subscription is per-connection,
    # so the integration uses this to re-arm subscribe+seed on every reconnect.
    on_session_up: "Callable[[], None] | None" = None
    # Invoked (no args) on each keepalive ack from the hub. The integration uses
    # this ~10s cadence to run the push-liveness watchdog without a wall-clock
    # timer. A None value means no callback is installed.
    on_keepalive: "Callable[[], None] | None" = None

    def __init__(
        self,
        host: str,
        port: int,
        device_id: str,
        user_id: str,
        token: str,
        model: str = G400_MODEL,
        reconnect_min_delay: float = 2.0,
        reconnect_max_delay: float = 60.0,
        hass: object | None = None,
        on_endpoint_change: Callable[[str, int], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._device_id = device_id
        self._user_id = user_id
        self._token = token
        # Public alias for entities (e.g. Light platform's dynamic-effect cloud
        # call). Same value as _token; the alias exists so non-hub callers
        # don't poke at a private attribute. Long-lived session: the
        # integration's async_setup_entry constructs AqaraCloudClient with HA's
        # async_get_clientsession(hass) so the entity's effect-activation path
        # doesn't pay TLS handshake + connection-pool warmup per click.
        self.token: str = token
        self.cloud_client: "AqaraCloudClient | None" = None  # set by async_setup_entry
        self._model = model
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._hass = hass
        self._on_endpoint_change = on_endpoint_change

        self._report_handlers: dict[str, list[ReportHandler]] = {}
        # Reports received for a DID before its handler is registered (startup
        # race); replayed when register_report_handler is called for that DID.
        self._pending_reports: dict[str, deque[LANReport]] = {}
        self._tunnel: EncryptedTunnel | None = None
        self._client: LANLinkClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._connected_event = asyncio.Event()
        # Most-recent LANLink `cmd=topology` push frame's DID set. Updated
        # by ``_dispatch_async`` on each topology push; consumed by
        # ``async_get_topology`` for cloud-vs-LANLink cross-checking.
        self._lanlink_topology_dids: frozenset[str] = frozenset()
        # Monotonic timestamp of the most recent inbound report (any report,
        # including the hub's own 1.176.20009.1 heartbeat). The push-liveness
        # watchdog uses ``seconds_since_last_report`` to detect a hub that is
        # connected with a populated topology yet has silently stopped
        # forwarding. Seeded to construction time so we don't fire immediately.
        self._last_report_monotonic = time.monotonic()
        # Union of every DID ever seen in a topology push. Lets us tell a
        # LAN-direct device that has gone offline (was present, now dropped)
        # apart from a Zigbee sub-device that is never in the LAN-direct
        # topology at all.
        self._ever_seen_topology_dids: set[str] = set()
        # Per-DID monotonic timestamp of the most recent subscribe arm, and the
        # set of DIDs that have already produced their first report since that
        # arm. Together they yield the arm->first-report latency diagnostic.
        self._armed_at: dict[str, float] = {}
        self._reported_since_arm: set[str] = set()

        # src strings stamped on our own writes (build_write src="3,,<ms>,,"),
        # remembered briefly so the hub's echo of the write can be suppressed.
        # OrderedDict[src -> monotonic insert time]; bounded by _SELF_SRC_TTL/_MAX.
        self._recent_self_src: OrderedDict[str, float] = OrderedDict()

    def _remember_self_src(self, src: str) -> None:
        """Remember an outbound write's ``src`` so its echo can be suppressed.

        Bounded on insert by TTL (``_SELF_SRC_TTL``) and size (``_SELF_SRC_MAX``).
        """
        reg = self._recent_self_src
        now = time.monotonic()
        cutoff = now - _SELF_SRC_TTL
        # Drop expired entries (oldest first; OrderedDict preserves insert order).
        while reg:
            _, ts = next(iter(reg.items()))
            if ts >= cutoff:
                break
            reg.popitem(last=False)
        # Same-ms writes collapse to one src entry (re-insert refreshes it); both
        # echoes are then suppressed - acceptable, optimistic state is already set.
        reg[src] = now
        reg.move_to_end(src)             # ensure it is the newest even if it existed
        while len(reg) > _SELF_SRC_MAX:  # cap size, evict oldest
            reg.popitem(last=False)

    def note_subscription_armed(self, did: str) -> None:
        """Record that a subscribe was (re-)armed for ``did`` so the next report
        for it can be timed (arm -> first-report latency)."""
        self._armed_at[did] = time.monotonic()
        self._reported_since_arm.discard(did)

    def seconds_since_last_report(self) -> float:
        """Seconds since the most recent inbound LANLink report."""
        return time.monotonic() - self._last_report_monotonic

    def _record_topology(self, dids: frozenset[str]) -> None:
        """Adopt a new LAN-direct topology DID set and remember every DID seen."""
        self._lanlink_topology_dids = dids
        self._ever_seen_topology_dids |= dids

    def is_lan_direct_offline(self, did: str) -> bool:
        """True when ``did`` is a LAN-direct device (seen in a prior topology
        push) that has since dropped out of the current topology -- i.e. it has
        gone offline. The hub itself and devices never seen as LAN-direct
        (Zigbee sub-devices) are never reported offline by this check.
        """
        if did == self._device_id:
            return False
        return (
            did in self._ever_seen_topology_dids
            and did not in self._lanlink_topology_dids
        )

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background session loop."""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="lanlink-coordinator")

    async def stop(self) -> None:
        """Cancel the session loop and close the tunnel."""
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._close_tunnel()

    @property
    def is_connected(self) -> bool:
        """True when the session is handshake-complete and checked in."""
        return self._connected_event.is_set()

    @property
    def connected(self) -> bool:
        """Spec-named alias for `is_connected`.

        The platform / entity layer uses `hub.connected`; this alias keeps
        the existing `is_connected` callers unchanged.
        """
        return self._connected_event.is_set()

    @property
    def did(self) -> str:
        """Hub DID. Used by entities for `via_device` linkage."""
        return self._device_id

    @property
    def hass(self) -> Any:
        """The HA core handle, or None if the coordinator was constructed
        outside HA (standalone tooling).

        Devices' `async_setup_camera` / `async_setup` hooks need this for
        executor-job dispatch and config-dir lookup; surfacing it here lets
        them stay decoupled from the platform-level setup wiring.
        """
        return self._hass

    @property
    def lanlink_topology_dids(self) -> frozenset[str]:
        """DIDs from the most-recent LANLink `cmd=topology` push frame.

        Returns ``frozenset()`` until a topology push has been observed.
        Used by config-flow code to cross-check cloud enumeration against
        what the hub reports as LAN-direct.
        """
        return self._lanlink_topology_dids

    async def wait_connected(self, timeout: float | None = None) -> None:
        """Wait for the first successful checkin, or raise TimeoutError."""
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

    # -------------------------------------------------------------------------
    # Attribute IO (AttrSpec-based)
    # -------------------------------------------------------------------------

    async def _send_awaiting(self, client, msg, *, op: str) -> dict:
        """Send a framed message and await its response.

        Translates transport failures into ``HomeAssistantError`` (using ``op``
        as the verb) so a service call fails cleanly instead of logging an
        unexpected-exception traceback for a recoverable network event -- the
        coordinator's reconnect loop is already handling it.
        """
        try:
            return await client.async_send_and_await(msg)
        except ConnectionError as exc:
            raise HomeAssistantError(f"hub disconnected during {op}: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise HomeAssistantError(f"hub {op} timed out") from exc

    def _resolve_write_framing(
        self, did: str, parent_did: str | None,
    ) -> tuple[str, str | None]:
        """Resolve the wire ``(did, sdid)`` framing for a write.

        - ``parent_did`` non-empty -> route via that parent (a Zigbee sub-device
          of a peer hub): ``wire_did = parent_did``, ``sdid = did``.
        - ``parent_did == ""`` (the own-parent sentinel a standalone Wi-Fi node
          carries -- camera, FP2) OR ``did == self._device_id`` (the connected
          hub itself) -> address the target DIRECTLY by its own DID:
          ``wire_did = did``, ``sdid = None`` (so ``data.did == data.sdid``).
          PROVEN LIVE 2026-06-30: a relayed write with ``did == sdid == G350``
          toggled the camera's OSD timestamp overlay over the M3 tunnel, while
          ``did = hub, sdid = G350`` did nothing -- the M3 routes the relay by
          ``did`` (did=device => finds the device's tunnel; did=hub => looks for
          a local child it doesn't own => no-op).
        - ``parent_did is None`` (legacy callers that don't know the parent) ->
          assume the connected hub is the parent.

        Returns ``(wire_did, sdid)`` where ``sdid`` is None for a direct write.
        """
        if parent_did:
            wire_did = parent_did
        elif parent_did == "" or did == self._device_id:
            # Own-parent node (standalone Wi-Fi device) or the connected hub
            # itself: address the target directly by its own DID.
            wire_did = did
        else:
            wire_did = self._device_id  # legacy (parent_did is None)
        sdid = None if wire_did == did else did
        return wire_did, sdid

    async def async_read(
        self,
        did: str,
        model: str,
        attrs: list[_HasName],
    ) -> dict[str, Any]:
        """Read one or more attributes from a device under this hub.

        Accepts AttrSpec-like objects (anything exposing ``.name``); the
        coordinator unwraps ``.name`` for the wire message so device code
        can stay declarative. Returns the ``result`` mapping from the
        ``read_done`` response (real hub firmware uses ``data.result``;
        ``data.value`` is tolerated as a defensive fallback).

        Two framing modes are selected automatically:

        - When ``did`` equals this coordinator's hub DID, the read is
          hub-scoped: the wire message uses ``did == sdid == hub_did``
          and ``model`` as supplied.
        - When ``did`` differs from the hub DID, the read targets a
          sub-device and gateway-relay framing is used:
          ``data.did = hub_did``, ``data.sdid = did``,
          ``data.model = hub_model``. Without this framing the hub
          silently times out.

        Raises HomeAssistantError if the hub session is not active.
        """
        client = self._client
        if client is None or not self._connected_event.is_set():
            raise HomeAssistantError("hub not connected")
        names = [a.name for a in attrs]
        seq = client.next_seq()
        if did == self._device_id:
            # Hub-scoped read: direct framing, no gateway indirection.
            msg = build_read(seq, did, model, names)
        else:
            # Sub-device read: gateway-relay framing through the hub.
            msg = build_read(
                seq, self._device_id, self._model, names, sdid=did,
            )
        response = await self._send_awaiting(client, msg, op="read")
        data = response.get("data") or {}
        # Real hub firmware returns the attr map under ``result``. Older
        # captures and some test fixtures use ``value`` -- tolerate both.
        result = data.get("result")
        if isinstance(result, dict):
            return result
        value = data.get("value")
        if isinstance(value, dict):
            return value
        return {}

    async def async_write(
        self,
        did: str,
        model: str,
        attrs: dict[_HasName, Any],
        *,
        parent_did: str | None = None,
    ) -> None:
        """Write one or more attributes to a device addressable from this hub.

        Accepts a mapping of AttrSpec-like -> value; the coordinator
        unwraps ``.name`` so device code can stay declarative. Awaits the
        ``write_done`` response and raises HomeAssistantError if the hub
        reports a non-zero code.

        Wire framing is determined by ``parent_did``, the device's actual
        parent hub in the cluster (which is NOT necessarily the hub we
        connected to -- a child of a peer hub still routes through our
        connection because the cluster leader fans commands out via the
        cluster mesh):

        - Hub-scoped (parent_did == did, or omitted with did == hub we
          connected to): ``data.did == data.sdid == did``,
          ``data.model = model``. Used for writing to the hub itself.
        - Cross-device / cluster (parent_did != did): ``data.did =
          parent_did``, ``data.sdid = did``, ``data.model = model``.
          Critically, this is what the Aqara mobile app sends -- the
          target device's parent is the routing key, not the hub we're
          connected to. (Verified against the Aqara Home Frida capture
          on 2026-05-09: phone writes with did=M100, sdid=bulb,
          model=bulb_model when bulb's parent is M100, and these writes
          actuate; without the correct parent_did the hub returns
          ``code=0, result=null`` and the device silently no-ops.)

        Backwards compatibility: when ``parent_did`` is ``None`` (the
        default), the coordinator falls back to the prior behaviour --
        treating ``did == hub_we_connected_to`` as hub-scoped and any
        other ``did`` as a child of the connected hub. Callers that know
        the device's actual parent in the cluster should pass
        ``parent_did`` explicitly so cross-cluster routing works.

        Raises HomeAssistantError if the hub session is not active.
        """
        client = self._client
        if client is None or not self._connected_event.is_set():
            raise HomeAssistantError("hub not connected")
        # rid-keyed settings carry a resource_id; send it bare (the hub's
        # al2bc actuates the bare rid). Wire-path traits get _to_wire_path's
        # `.1` suffix. See docs/dev/LOCAL_RID_WRITE_FINDINGS.md.
        payload = {
            (getattr(spec, "resource_id", None) or _to_wire_path(spec.name)): value
            for spec, value in attrs.items()
        }
        _LOGGER.debug(
            "async_write: did=%s parent_did=%s model=%s payload=%s",
            did, parent_did, model, payload,
        )
        seq = client.next_seq()
        wire_did, sdid = self._resolve_write_framing(did, parent_did)
        # Stamp the src ourselves and remember it, so the hub's echo of this
        # write is recognised and suppressed in _dispatch_report (self-echo).
        src = f"3,,{int(time.time() * 1000)},,"
        self._remember_self_src(src)
        # Wire `model` is always the target's model. sdid=None is a hub-scoped
        # write (did==sdid); a set sdid is a cluster-routed sub-device write.
        msg = build_write(seq, wire_did, model, payload, sdid=sdid, src=src)
        response = await self._send_awaiting(client, msg, op="write")
        data = response.get("data") or {}
        # Only `code` signals failure. `result` is intentionally NOT read:
        # rid writes ack with result:null even when they actuate (false
        # negative). See docs/dev/LOCAL_RID_WRITE_FINDINGS.md.
        code = data.get("code", 0)
        _LOGGER.debug(
            "async_write: did=%s response code=%s data=%s",
            did, code, {k: v for k, v in data.items() if k != "value"},
        )
        if code != 0:
            message = data.get("message") or f"hub write failed (code={code})"
            raise HomeAssistantError(message)

    # -------------------------------------------------------------------------
    # Internal: run loop with reconnect
    # -------------------------------------------------------------------------

    async def _run(self) -> None:
        backoff = self._reconnect_min_delay
        while not self._stopping:
            try:
                await self._run_session_once()
                backoff = self._reconnect_min_delay  # reset on clean session end
            except asyncio.CancelledError:
                return
            except ConnectionError as exc:
                # Most likely the hub rotated its port after a reboot; a
                # targeted mDNS lookup will tell us the new one. Best-effort
                # -- if mDNS fails we just retry the old port.
                _LOGGER.warning(
                    "LANLink session error (%s); reconnecting in %.1fs",
                    exc, backoff,
                )
                if await self._refresh_endpoint_via_mdns():
                    # The hub moved to a new port; retry it immediately
                    # rather than waiting out the (now-irrelevant) backoff.
                    backoff = self._reconnect_min_delay
                    continue
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "LANLink session error (%s); reconnecting in %.1fs",
                    exc, backoff,
                )
            if self._stopping:
                return
            try:
                await self._reconnect_sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, self._reconnect_max_delay)

    async def _reconnect_sleep(self, backoff: float) -> None:
        """Jittered backoff between reconnect attempts (patch point in tests)."""
        await asyncio.sleep(_apply_jitter(backoff))

    async def _refresh_endpoint_via_mdns(self) -> bool:
        """Query mDNS for the hub's current (host, port) and adopt it.

        Returns True when the endpoint actually changed, so the caller can
        retry immediately instead of waiting out the reconnect backoff.
        """
        try:
            record = await discover_hub_by_did(
                self._hass, self._device_id, timeout=3.0,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("mDNS discovery failed: %s", exc)
            return False
        if record is None:
            _LOGGER.debug(
                "mDNS found no hub matching did=%s; keeping %s:%d",
                self._device_id, self._host, self._port,
            )
            return False
        if (record.host, record.port) == (self._host, self._port):
            return False
        _LOGGER.info(
            "LANLink endpoint updated via mDNS: %s:%d -> %s:%d (did=%s)",
            self._host, self._port, record.host, record.port, self._device_id,
        )
        self._host = record.host
        self._port = record.port
        if self._on_endpoint_change is not None:
            try:
                self._on_endpoint_change(record.host, record.port)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("on_endpoint_change callback raised")
        return True

    async def _run_session_once(self) -> None:
        """Open, listen, checkin, keep listening until tunnel closes.

        The new seq-keyed-futures dispatch requires ``listen()`` to be
        running before ``checkin()`` can resolve, so we spawn the listen
        task immediately after a successful ``tunnel.connect``. Any async
        report that races in during checkin is routed through listen to
        ``_dispatch_report`` (already wired by the time listen starts).
        """
        tunnel = EncryptedTunnel(device_id=self._device_id)
        self._tunnel = tunnel
        try:
            await tunnel.connect(self._host, self._port)
        except (OSError, TunnelError) as exc:
            raise ConnectionError(f"tunnel connect failed: {exc}") from exc

        client = LANLinkClient(
            tunnel=tunnel,
            device_id=self._device_id,
            user_id=self._user_id,
            model=self._model,
            token=self._token,
        )
        client.on_report = self._dispatch_report
        client.on_async = self._dispatch_async
        self._client = client

        listen_task = asyncio.create_task(
            client.listen(), name="lanlink-listen",
        )
        try:
            try:
                accepted = await client.checkin()
            except (ConnectionError, asyncio.TimeoutError) as exc:
                raise ConnectionError(f"checkin failed: {exc}") from exc
            if not accepted:
                raise ConnectionError("hub rejected checkin (bad user/token)")

            self._connected_event.set()
            _LOGGER.info(
                "LANLink session up (hub=%s did=%s)",
                self._host, self._device_id,
            )
            if self.on_session_up is not None:
                try:
                    self.on_session_up()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("on_session_up callback raised")
            # listen_task owns tunnel.receive for the session lifetime;
            # awaiting it blocks until the tunnel closes or errors.
            await listen_task
        finally:
            self._connected_event.clear()
            self._client = None
            if not listen_task.done():
                listen_task.cancel()
                try:
                    await listen_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            _LOGGER.info("LANLink session ended (hub=%s)", self._host)
            await self._close_tunnel()

    async def _close_tunnel(self) -> None:
        tunnel = self._tunnel
        self._tunnel = None
        if tunnel is not None:
            try:
                await tunnel.close()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("tunnel close error", exc_info=True)

    # -------------------------------------------------------------------------
    # Report handler registry
    # -------------------------------------------------------------------------

    def register_report_handler(
        self, did: str, handler: ReportHandler,
    ) -> Callable[[], None]:
        """Register ``handler`` to receive reports for sub-device ``did``.

        Returns an unregister callable. Calling the returned callable once
        removes this specific handler from the registry; calling it more
        than once is a no-op (idempotent).

        Multiple handlers can be registered for the same DID; they are
        invoked in registration order. Exceptions raised by one handler
        do not prevent subsequent handlers from being called.
        """
        self._report_handlers.setdefault(did, []).append(handler)

        # Replay any reports that arrived for this DID before it had a handler
        # (the startup session-up vs handler-registration race).
        buffered = self._pending_reports.pop(did, None)
        if buffered:
            _LOGGER.debug(
                "replaying %d buffered report(s) for did=%s on registration",
                len(buffered), did,
            )
            for report in buffered:
                try:
                    handler(report)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "buffered report handler raised for did=%s", did,
                    )

        def _unregister() -> None:
            handlers = self._report_handlers.get(did)
            if handlers is None:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                return
            if not handlers:
                self._report_handlers.pop(did, None)

        return _unregister

    def _buffer_report(self, did: str, report: LANReport) -> None:
        """Hold a report whose DID has no handler yet, for replay on register."""
        buf = self._pending_reports.get(did)
        if buf is None:
            if len(self._pending_reports) >= _REPORT_BUFFER_MAX_DIDS:
                _LOGGER.debug(
                    "report buffer full (%d DIDs); dropping report for did=%s",
                    len(self._pending_reports), did,
                )
                return
            buf = self._pending_reports[did] = deque(maxlen=_REPORT_BUFFER_MAXLEN)
        buf.append(report)
        _LOGGER.debug(
            "buffered report for did=%s (no handler yet); buffered=%d",
            did, len(buf),
        )

    def _dispatch_report(self, report: LANReport) -> None:
        """Route a report to handlers registered for its sub-device DID.

        Uses ``report.sdid`` as the primary key, falling back to
        ``report.did`` when ``sdid`` is an empty string (e.g. the hub
        itself emitting a report for its own state). Handlers are
        invoked synchronously in registration order; a raising handler
        is logged and the remaining handlers are still called.
        """
        self._last_report_monotonic = time.monotonic()
        _LOGGER.debug(
            "LANLink report did=%s sdid=%s values=%s",
            report.did, report.sdid, report.values,
        )
        if report.src and report.src in self._recent_self_src:
            _LOGGER.debug(
                "suppressed self-echo did=%s sdid=%s src=%s",
                report.did, report.sdid, report.src,
            )
            return
        key = report.sdid or report.did
        armed = self._armed_at.get(key)
        if armed is not None and key not in self._reported_since_arm:
            self._reported_since_arm.add(key)
            _LOGGER.debug(
                "LANLink first report after subscribe arm for did=%s latency=%.2fs",
                key, time.monotonic() - armed,
            )
        handlers = self._report_handlers.get(key)
        if not handlers:
            self._buffer_report(key, report)
            return
        # Iterate a copy so a handler unregistering during dispatch
        # cannot mutate the list we're walking.
        for handler in list(handlers):
            try:
                handler(report)
            except Exception:
                _LOGGER.exception(
                    "report handler raised for did=%s", key,
                )

    # -------------------------------------------------------------------------
    # Async non-report dispatch (topology pushes, etc.)
    # -------------------------------------------------------------------------

    def _dispatch_async(self, raw: dict[str, Any]) -> None:
        """Capture spontaneous non-report frames forwarded from the session.

        Currently recognises ``cmd=topology, type=device`` push frames
        and stores their parsed DID set on the coordinator for later
        cloud-vs-LANLink cross-checking. Unrecognised frames are logged
        at INFO so hub-level signals (errors, kick-outs, future commands)
        are not silently dropped.
        """
        cmd = raw.get("cmd")
        msg_type = raw.get("type")
        if cmd == LANLINK_CMD_TOPOLOGY and msg_type == LANLINK_TYPE_DEVICE:
            self._record_topology(
                topology.parse_lanlink_topology_push(raw.get("data")),
            )
            _LOGGER.info(
                "LANLink topology push captured: %d DIDs (hub=%s)",
                len(self._lanlink_topology_dids), self._device_id,
            )
            if self.on_topology_changed is not None:
                try:
                    self.on_topology_changed(self._lanlink_topology_dids)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "on_topology_changed callback raised (hub=%s)",
                        self._device_id,
                    )
            return
        if cmd in (LANLINK_CMD_KEEPALIVE_DONE, LANLINK_CMD_KEEPALIVE):
            # Routine heartbeat ack -- expected on every interval. Keep it out
            # of INFO so it doesn't drown real signals; DEBUG is plenty.
            _LOGGER.debug(
                "LANLink keepalive ack (hub=%s seq=%s)",
                self._device_id, raw.get("seq"),
            )
            if self.on_keepalive is not None:
                try:
                    self.on_keepalive()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("on_keepalive callback raised")
            return
        _LOGGER.info(
            "Coordinator received unrecognized async LANLink message "
            "cmd=%s type=%s seq=%s data=%s",
            cmd, msg_type, raw.get("seq"), raw.get("data"),
        )

    # -------------------------------------------------------------------------
    # Cloud-primary topology enumeration
    # -------------------------------------------------------------------------

    async def async_get_topology(
        self, cloud_client: Any,
    ) -> list[TopologyDevice]:
        """Return all devices belonging to this hub via cloud-primary enumeration.

        Calls ``cloud_client.query_device_list`` using the coordinator's
        active token (``self._token``, the same token used for LANLink
        checkin), filters to entries whose ``parentDeviceId`` matches
        this hub's DID (sub-devices) plus the hub's own record, and
        returns parsed ``TopologyDevice`` records. ``virtual.*`` DIDs
        (cloud-only Soft Sensors with no LANLink path) are skipped.

        Cross-checks against the most-recent LANLink topology push frame
        (``self._lanlink_topology_dids``) and logs a warning for any DID
        the hub announced over LAN that the cloud response did not
        include.
        """
        records = await cloud_client.query_device_list(self._token)
        devices: list[TopologyDevice] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            did = record.get("did", "")
            if not isinstance(did, str) or not did:
                continue
            if did.startswith("virtual."):
                continue
            parent_did = record.get("parentDeviceId", "")
            if did == self._device_id or parent_did == self._device_id:
                devices.append(topology.parse_cloud_device(record))

        cloud_dids = {d.did for d in devices}
        missing = self._lanlink_topology_dids - cloud_dids
        if missing:
            _LOGGER.warning(
                "LANLink topology reports DIDs the cloud does not list "
                "(hub=%s missing=%s)",
                self._device_id, sorted(missing),
            )
        return devices
