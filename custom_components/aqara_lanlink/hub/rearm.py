"""Re-arm relay activation for standalone Wi-Fi devices (the FP2).

A standalone Wi-Fi device is relayed by the hub over LANLink only after a
controller "activates" it (a TLS handshake to its local :443; see
``activation.py``). Proven on hardware: both a hub reboot AND an FP2
power-cycle drop the device from the hub's LANLink topology, and each emits a
topology push with the device absent. After a power-cycle the device is OFFLINE
while booting, so re-activation must RETRY until the device is reachable; and
after a long outage there may be NO topology push when it returns, so a periodic
sweep is needed.

``RearmManager`` owns that logic:

  - ``note_topology(dids)`` is called on every LANLink topology push. For each
    configured standalone DID absent from ``dids`` it schedules a bounded
    retry loop; for any that came back it cancels the in-flight loop.
  - ``sweep()`` is called on a wall-clock interval to cover the no-push case.
  - ``cancel_all()`` is called on unload.

One in-flight task per DID (idempotent). All work is async; no busy-wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from ..const import CONF_ACTIVATION_HOST, CONF_ACTIVATION_PORT
from .activation import ACTIVATION_PORT, activate_relay, validate_aqara_endpoint
from .mdns import discover_hub_by_did

_LOGGER = logging.getLogger(__name__)


class RearmManager:
    """Re-activate configured standalone devices when they fall out of topology.

    Timing values are constructor params (with real defaults) so tests can
    inject fast values.
    """

    def __init__(
        self,
        hass: Any,
        entry: Any,
        *,
        backoffs: tuple[float, ...] = (5, 15, 30, 60),
        settle_timeout: float = 20.0,
        poll_interval: float = 2.0,
        max_rounds: int = 6,
        absence_grace: float = 15.0,
        rearm_cooldown: float = 90.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._backoffs = backoffs
        self._settle_timeout = settle_timeout
        self._poll_interval = poll_interval
        self._max_rounds = max_rounds
        # Debounce: how long a device must stay absent before we re-activate.
        # The relay flaps and often self-recovers within seconds, so reacting
        # instantly just piles redundant :443 handshakes onto a device that's
        # coming back on its own.
        self._absence_grace = absence_grace
        # Cooldown: never re-poke a device we activated within this window.
        # Repeated activations can themselves destabilise the relay, so once
        # we've activated we hold off and let it settle / self-recover.
        self._rearm_cooldown = rearm_cooldown
        self._clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activation: dict[str, float] = {}
        self._last_topology: frozenset[str] = frozenset()

    # -- public API ----------------------------------------------------------

    def note_topology(self, dids: frozenset[str]) -> None:
        """Record the live topology and (re)arm activation as needed.

        For each configured activation DID NOT in ``dids``, ensure a re-arm
        loop is running. For each that IS in ``dids`` and has a running task,
        cancel it -- the device came back on its own.
        """
        self._last_topology = dids
        targets = self._activation_targets()
        absent = [did for did in targets if did not in dids]
        _LOGGER.debug(
            "rearm: note_topology dids=%d targets=%s absent=%s",
            len(dids), sorted(targets), absent,
        )
        for did in targets:
            if did in dids:
                task = self._tasks.pop(did, None)
                if task is not None and not task.done():
                    task.cancel()
            else:
                self._ensure_rearm(did)

    def sweep(self, *_args: Any) -> None:
        """Periodic backstop: re-arm any configured DID still out of topology.

        Signature tolerates the ``datetime`` argument passed by
        ``async_track_time_interval``.
        """
        for did in self._activation_targets():
            if did not in self._last_topology:
                self._ensure_rearm(did)

    def cancel_all(self) -> None:
        """Cancel every in-flight re-arm task (called on unload)."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()

    # -- internals -----------------------------------------------------------

    def _activation_targets(self) -> dict[str, tuple[str, int]]:
        """Map activation DID -> (host, port) for every activated subentry."""
        targets: dict[str, tuple[str, int]] = {}
        for subentry in self._entry.subentries.values():
            data = subentry.data
            host = data.get(CONF_ACTIVATION_HOST)
            did = data.get("did")
            if not host or not did:
                continue
            port = int(data.get(CONF_ACTIVATION_PORT) or ACTIVATION_PORT)
            targets[did] = (host, port)
        return targets

    def _ensure_rearm(self, did: str) -> None:
        """Start a re-arm loop for ``did`` unless one is already live (idempotent)."""
        existing = self._tasks.get(did)
        if existing is not None and not existing.done():
            return
        _LOGGER.info("rearm: %s absent from topology; starting re-arm loop", did)
        self._tasks[did] = self._hass.async_create_task(self._rearm_loop(did))

    async def _rearm_loop(self, did: str) -> None:
        """Bounded retry: activate ``did`` and wait for it to rejoin topology.

        Debounced and rate-limited: the relay flaps and frequently self-recovers,
        so we wait an absence-grace before acting (note_topology cancels us if the
        device returns meanwhile) and never re-activate within the cooldown of a
        prior activation -- repeated :443 handshakes can themselves keep the relay
        unstable.
        """
        targets = self._activation_targets()
        stored_host, stored_port = targets.get(did, ("", ACTIVATION_PORT))
        try:
            # Debounce: let a brief flap resolve itself before we poke anything.
            if self._absence_grace > 0:
                await asyncio.sleep(self._absence_grace)
                if did in self._live_topology():
                    _LOGGER.debug("rearm: %s returned during grace; no activation", did)
                    return

            for round_index in range(self._max_rounds):
                # Cooldown: don't re-poke a device we activated very recently.
                since = self._clock() - self._last_activation.get(did, float("-inf"))
                if since < self._rearm_cooldown:
                    wait = min(self._rearm_cooldown - since, 30.0)
                    _LOGGER.debug(
                        "rearm: %s in cooldown (%.0fs since last activation); waiting %.0fs",
                        did, since, wait,
                    )
                    await asyncio.sleep(wait)
                    if did in self._live_topology():
                        return
                    continue

                rec = await discover_hub_by_did(self._hass, did)
                host = rec.host if rec else stored_host
                port = stored_port

                reachable = await validate_aqara_endpoint(host, port)
                _LOGGER.info(
                    "rearm: %s attempt %d/%d host=%s reachable=%s",
                    did, round_index + 1, self._max_rounds, host, reachable,
                )
                if reachable:
                    self._last_activation[did] = self._clock()
                    await activate_relay(host, did, port)
                    if await self._wait_for_topology(did):
                        _LOGGER.info(
                            "rearm: %s rejoined LANLink topology after activation",
                            did,
                        )
                        return

                # Not reachable, or activated but never settled -- back off.
                delay = self._backoffs[min(round_index, len(self._backoffs) - 1)]
                await asyncio.sleep(delay)

            _LOGGER.info(
                "rearm: gave up re-activating %s for now; sweep will retry", did,
            )
        except asyncio.CancelledError:
            # Device came back (note_topology cancelled us) or unload -- let it
            # propagate so the task is reported as cancelled.
            raise
        except Exception:  # noqa: BLE001 -- a re-arm failure must never crash
            _LOGGER.exception("rearm: unexpected error re-activating %s", did)
        finally:
            # Only clear our own slot; a fresh task may already own it.
            if self._tasks.get(did) is asyncio.current_task():
                del self._tasks[did]

    def _live_topology(self) -> frozenset[str]:
        """The hub's current LANLink topology DID set (empty if unavailable)."""
        return getattr(
            self._entry.runtime_data.hub, "lanlink_topology_dids", frozenset(),
        )

    async def _wait_for_topology(self, did: str) -> bool:
        """Poll the live topology up to settle_timeout for ``did`` to appear."""
        waited = 0.0
        while waited < self._settle_timeout:
            if did in self._live_topology():
                return True
            await asyncio.sleep(self._poll_interval)
            waited += self._poll_interval
        return did in self._live_topology()
