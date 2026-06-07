"""ObservedPathCache: persisted set of wire paths seen via LANLink push
that the shipped catalogue and the local overlay do not cover.

Each LANLink push event carries a path-shaped key. When the path is in
neither catalogue nor overlay (Task 8 gates this at handle_report), the
PATH ITSELF is information the integration should remember.
ObservedPathCache captures these candidate paths, scoped by (model, did)
so the Repair notification can identify the specific paired device that
emitted each path rather than collapsing multi-device-same-model
installs into a single entry.

A Repair issue (`candidate_paths_<entry>`) prompts the user to run
`aqara_lanlink.scan_device` which produces a review-and-select gap
report; accepted entries are written to the overlay and the candidate
paths are discarded from this cache.

Storage shape (v2):
    {<model>: {<did>: [<path>, ...]}}

v1 -> v2 migration: v1 entries (no DID, just `{model: [paths]}`) are
discarded on load; the integration's runtime gate (catalog
dropped_paths_for_model) means most legacy v1 entries were administrative
chatter anyway, and any genuinely-novel paths will be re-recorded on
the next push.

Storage is in HA's standard .storage/aqara_lanlink.observed_paths file
via homeassistant.helpers.storage.Store with async_delay_save debouncing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

_STORAGE_KEY = "aqara_lanlink.observed_paths"
_STORAGE_VERSION = 2
_SAVE_DELAY = 10  # seconds; debounces multiple records within the window


class ObservedPathCache:
    """In-memory + on-disk cache of wire paths observed via LANLink push.

    Paths are bucketed by (model, did): the model identifies the device
    type (shared trait surface across all instances) and the did
    identifies the specific paired device (needed so the candidate-paths
    Repair issue can name the actual device the user should scan).

    The `_new_since_setup` in-memory mirror tracks paths recorded AFTER
    async_load completes; it resets to empty on every load and powers
    the Repair-flow notification callback (see `on_new_paths_callback`
    arg).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        on_new_paths_callback: Callable[[], None] | None = None,
    ) -> None:
        self._hass = hass
        self._store: Store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        # {model: {did: set[path]}}
        self._data: dict[str, dict[str, set[str]]] = {}
        # Mirror of paths added since async_load, same shape.
        self._new_since_setup: dict[str, dict[str, set[str]]] = {}
        self._on_new_paths_callback = on_new_paths_callback

    async def async_load(self) -> None:
        """Load from disk. Treat missing or corrupted storage as empty.

        v1 storage (no DIDs) is discarded -- the DID isn't recoverable
        from path alone, and the runtime trait_policy gate added in
        commit `2004893` means most v1 entries were administrative
        chatter that no longer gets recorded anyway.

        Resets _new_since_setup to empty so any paths recorded BEFORE
        load completes are not counted as "new since setup" (the load
        IS the setup boundary for notification purposes).
        """
        try:
            raw = await self._store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "observed_paths storage load failed (%s); starting empty", exc,
            )
            raw = None
        self._data = {}
        if isinstance(raw, dict):
            for model, by_did in raw.items():
                # v2 shape: {model: {did: [paths]}}
                if isinstance(by_did, dict):
                    bucket = self._data.setdefault(model, {})
                    for did, paths in by_did.items():
                        if isinstance(paths, list):
                            bucket[did] = {p for p in paths if isinstance(p, str)}
                    continue
                # v1 shape: {model: [paths]} -- discard; DID unrecoverable.
                if isinstance(by_did, list):
                    _LOGGER.info(
                        "observed_paths: dropping legacy v1 entry for model %s "
                        "(no DID recoverable). Genuinely-novel paths will be "
                        "re-recorded on next push.", model,
                    )
                    continue
        elif raw is not None:
            _LOGGER.warning(
                "observed_paths storage has unexpected shape %r; "
                "starting empty", type(raw).__name__,
            )
        self._new_since_setup = {}

    def prune_dropped_paths(
        self, lookup: Callable[[str], frozenset[str]],
    ) -> int:
        """Remove paths that the trait_policy now drops.

        Walks every (model, did, path) and discards entries whose path
        is in `lookup(model)`. Useful right after async_load to clean
        out paths recorded before trait_policy added them to the DROP
        set (so users don't see a Repair notification listing what's
        actually intentionally-filtered admin chatter).

        Returns the number of paths discarded. Schedules a save if any
        change occurred.
        """
        removed = 0
        for model in list(self._data):
            dropped = lookup(model)
            if not dropped:
                continue
            for did in list(self._data[model]):
                paths = self._data[model][did]
                stale = paths & dropped
                if stale:
                    paths -= stale
                    removed += len(stale)
                if not paths:
                    del self._data[model][did]
            if not self._data[model]:
                del self._data[model]
        if removed:
            self._store.async_delay_save(self._snapshot, _SAVE_DELAY)
        return removed

    def get_paths(self, model: str) -> frozenset[str]:
        """Return a fresh frozenset of paths for the given model, across
        every did. Kept for the candidate-list union used elsewhere."""
        bucket = self._data.get(model, {})
        out: set[str] = set()
        for paths in bucket.values():
            out |= paths
        return frozenset(out)

    def models(self) -> list[str]:
        """Return the list of model codes the cache has entries for."""
        return list(self._data.keys())

    def record(self, did: str, model: str, path: str) -> None:
        """Record a single observed path for a (did, model) pair.

        Idempotent: re-observing a known path is a no-op. When the
        path is genuinely new since async_load, fires the
        on_new_paths_callback. The callback fires for EVERY new path
        (not just the 0 -> >=1 transition) so the Repair-issue
        ``{count}`` placeholder refreshes when more paths arrive
        between push events and the user clicking Fix. HA's issue
        registry handles same-issue_id ``async_create_issue`` calls
        as updates, so the additional invocations don't duplicate
        notifications -- they just refresh the count.
        """
        if not did or not model or not path:
            return
        by_did = self._data.setdefault(model, {})
        bucket = by_did.setdefault(did, set())
        if path in bucket:
            return
        bucket.add(path)
        new_by_did = self._new_since_setup.setdefault(model, {})
        new_by_did.setdefault(did, set()).add(path)
        self._store.async_delay_save(self._snapshot, _SAVE_DELAY)
        if self._on_new_paths_callback is not None:
            try:
                self._on_new_paths_callback()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "ObservedPathCache on_new_paths_callback raised",
                )

    def new_paths_count(self) -> int:
        """Return the total number of paths recorded since async_load."""
        return sum(
            len(paths)
            for by_did in self._new_since_setup.values()
            for paths in by_did.values()
        )

    def new_paths_by_device(self) -> list[dict]:
        """Return paths recorded since async_load, one entry per (did, model).

        Each entry: {"did": str, "model": str, "paths": frozenset[str]}.
        Empty (model, did) tuples (paths fully discarded) are omitted.
        Sorted by (model, did) for deterministic notification rendering.
        """
        out: list[dict] = []
        for model in sorted(self._new_since_setup):
            for did in sorted(self._new_since_setup[model]):
                paths = self._new_since_setup[model][did]
                if not paths:
                    continue
                out.append({
                    "did": did,
                    "model": model,
                    "paths": frozenset(paths),
                })
        return out

    def _snapshot(self) -> dict[str, dict[str, list[str]]]:
        """Return a JSON-serializable snapshot for Store.async_save.

        Paths are sorted so the on-disk file is deterministic across runs --
        helps with diagnostics, snapshot tests, and user-side git-tracked
        .storage backups.
        """
        return {
            model: {did: sorted(paths) for did, paths in by_did.items()}
            for model, by_did in self._data.items()
        }


__all__ = ["ObservedPathCache"]
