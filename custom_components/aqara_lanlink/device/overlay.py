"""Local overlay store: per-install extension of the shipped catalogue.

The overlay is a `.storage` JSON file that the deterministic derive
treats identically to the shipped per-model `TRAITS` dicts. Under V3
the merge is override-on-top: overlay entries replace shipped values
at the same wire_path, and a `None` overlay entry drops the catalogue
entry at that wire_path. See `build_descriptors._merge_overlay_into_catalogue`.

Write contract: the overlay is written ONLY by the scan-acceptance flow
in services/scan_device.py. It is never written by the derive, push
handling, or normal setup. The file is immutable across a normal HA
session unless the user explicitly accepts a discovery.

Read contract: read once at integration startup; loaded value is passed
into build_descriptors(model, overlay) for each device.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .traits import TraitSpec

_LOGGER = logging.getLogger(__name__)

_STORAGE_KEY = "aqara_lanlink.overlay"
_STORAGE_VERSION = 1


@dataclass
class _ProvenanceEntry:
    """Internal provenance metadata for an overlay trait.

    `discovered_from` is "cloud_scan" or "bootstrap"; `discovered_at` is
    the timestamp the scan-accept flow wrote the entry. Used by the
    export_overlay service to format PR payloads.
    """
    discovered_from: str
    discovered_at: datetime


@dataclass
class Overlay:
    """In-memory view of the overlay file.

    Two parallel dicts: `_traits[model][pid] -> TraitSpec` for the live
    catalogue-class data, and `_provenance[model][pid] -> _ProvenanceEntry`
    for the export-overlay service. The provenance is kept separate so the
    derive (which only consumes TraitSpecs) does not see it.
    """
    _traits: dict[str, dict[str, TraitSpec]] = field(default_factory=dict)
    _provenance: dict[str, dict[str, _ProvenanceEntry]] = field(default_factory=dict)

    def traits_for_model(self, model: str) -> dict[str, TraitSpec]:
        """Return a fresh dict of `pid -> TraitSpec` for `model`.

        Empty dict if the model has no overlay entries.
        """
        return dict(self._traits.get(model, {}))

    def has_traits_for_model(self, model: str) -> bool:
        return bool(self._traits.get(model))

    def set_trait(
        self,
        *,
        model: str,
        pid: str,
        spec: TraitSpec,
        discovered_from: str,
        discovered_at: datetime,
    ) -> None:
        """Add or replace a trait in the overlay (in-memory only).

        The accept flow calls this then `OverlayStore.async_write(overlay)`
        to persist.
        """
        self._traits.setdefault(model, {})[pid] = spec
        self._provenance.setdefault(model, {})[pid] = _ProvenanceEntry(
            discovered_from=discovered_from,
            discovered_at=discovered_at,
        )

    def all_provenance(self) -> dict[str, dict[str, _ProvenanceEntry]]:
        """Read-only access to provenance metadata. Used by export_overlay."""
        return {m: dict(p) for m, p in self._provenance.items()}


class OverlayStore:
    """Persistence wrapper around the overlay file.

    Wraps HA's `Store` for atomic, versioned, corruption-tolerant
    storage. The class exposes `async_load() -> Overlay` and
    `async_write(overlay)` only - mutation goes through the in-memory
    Overlay object first.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(
            hass, _STORAGE_VERSION, _STORAGE_KEY, atomic_writes=True,
        )

    async def async_load(self) -> Overlay:
        """Read the file, returning an Overlay. Missing/corrupt -> empty."""
        try:
            raw = await self._store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "overlay load failed (%s); starting with empty overlay",
                exc,
            )
            return Overlay()

        if not isinstance(raw, dict):
            if raw is not None:
                _LOGGER.warning(
                    "overlay file has unexpected shape %r; starting empty",
                    type(raw).__name__,
                )
            return Overlay()

        traits_raw = raw.get("traits")
        if not isinstance(traits_raw, dict):
            return Overlay()

        overlay = Overlay()
        for model, model_traits in traits_raw.items():
            if not isinstance(model_traits, dict):
                continue
            for pid, row in model_traits.items():
                if not isinstance(row, dict):
                    continue
                try:
                    spec = self._row_to_traitspec(pid, row)
                except (ValueError, TypeError, RuntimeError) as exc:
                    # TraitSpec.__post_init__ raises RuntimeError for
                    # validation failures (platform mismatch,
                    # auto_clear_seconds with wrong platform, etc.).
                    _LOGGER.warning(
                        "overlay row dropped: model=%s pid=%s: %s",
                        model, pid, exc,
                    )
                    continue
                discovered_at = self._parse_timestamp(row.get("discovered_at"))
                overlay.set_trait(
                    model=model,
                    pid=pid,
                    spec=spec,
                    discovered_from=str(row.get("discovered_from", "")),
                    discovered_at=discovered_at,
                )
        return overlay

    async def async_write(self, overlay: Overlay) -> None:
        """Persist the full overlay to disk atomically."""
        payload = {
            "version": _STORAGE_VERSION,
            "traits": {
                model: {
                    pid: self._traitspec_to_row(spec, model, pid, overlay)
                    for pid, spec in traits.items()
                }
                for model, traits in overlay._traits.items()
            },
        }
        await self._store.async_save(payload)

    @staticmethod
    def _row_to_traitspec(pid: str, row: dict) -> TraitSpec:
        """Construct a TraitSpec from a stored row.

        Raises ValueError / TypeError on argument-shape failures and
        RuntimeError when TraitSpec.__post_init__ rejects the values;
        the caller catches all three.
        """
        enum_values_raw = row.get("enum_values")
        if isinstance(enum_values_raw, dict):
            # Stored shape: {wire_value: label}. Coerce keys/values to
            # str for safety against JSON deserialisation quirks.
            enum_values = {str(k): str(v) for k, v in enum_values_raw.items()}
        else:
            enum_values = None

        return TraitSpec(
            id=pid,
            name=str(row.get("name", "")),
            wire_path=str(row["wire_path"]) if row.get("wire_path") else None,
            data_type=str(row.get("data_type", "unknown")),
            unit=row.get("unit"),
            readable=bool(row.get("readable", True)),
            writable=bool(row.get("writable", True)),
            enum_values=enum_values,
            platform=row.get("platform"),
            device_class=row.get("device_class"),
            auto_clear_seconds=row.get("auto_clear_seconds"),
        )

    @staticmethod
    def _traitspec_to_row(
        spec: TraitSpec, model: str, pid: str, overlay: Overlay,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": spec.name,
            "data_type": spec.data_type,
            "readable": spec.readable,
            "writable": spec.writable,
        }
        if spec.wire_path:
            row["wire_path"] = spec.wire_path
        if spec.unit:
            row["unit"] = spec.unit
        if spec.enum_values:
            # Persist as dict to match TraitSpec.enum_values shape.
            row["enum_values"] = dict(spec.enum_values)
        if spec.platform:
            row["platform"] = spec.platform
        if spec.device_class:
            row["device_class"] = spec.device_class
        if spec.auto_clear_seconds is not None:
            row["auto_clear_seconds"] = spec.auto_clear_seconds
        provenance = overlay._provenance.get(model, {}).get(pid)
        if provenance is not None:
            row["discovered_from"] = provenance.discovered_from
            row["discovered_at"] = provenance.discovered_at.isoformat()
        return row

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime:
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)
