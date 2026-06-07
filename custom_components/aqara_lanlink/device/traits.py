"""Trait specification catalog.

A `TraitSpec` describes a single Aqara device "trait" -- a property addressed
by a dotted numeric ID such as `1.7.85` (property ID) or `2.133.32923`
(path-style ID). Trait IDs cross-cut device models: motion on a G400 and
motion on an FP2 reference the same `TraitSpec` if they share the trait id.

The catalog ships **empty** in source. All entries are runtime
registrations, populated when per-model packages under
`device/models/` are loaded at integration setup. Authored
(hand-curated) knowledge lives in `catalog` and the per-model packages
under `device/models/`, not in this catalog.

Identical re-registration is a no-op (idempotent). Otherwise, the last writer
wins: a new registration overwrites the existing entry.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal
from collections.abc import Callable


def _has_control_char(value: str) -> bool:
    """True if `value` contains a Unicode control character (category Cc).

    Catches newline/CR/tab/NUL etc. while leaving printable Unicode such as
    degree signs and superscripts (e.g. units like "°C", "µg/m³") untouched.
    """
    return any(unicodedata.category(c) == "Cc" for c in value)

# Module-level mutable catalog. Keyed by trait id.
BY_ID: dict[str, "TraitSpec"] = {}

# Optional callback fired after every successful register_trait that mutates
# the catalog. Vestigial post-V3: no production code wires a listener now
# that the derive-result cache is gone; kept so the catalog module retains
# a hook for future cache-style consumers.
_change_listener: Callable[[], None] | None = None

# v3d-7 validation constants. Hoisted to module level so they are not
# re-allocated on every TraitSpec construction (~9000 specs at module load
# across the device-model packages).
_VALID_PLATFORMS: frozenset[str] = frozenset({
    "sensor", "binary_sensor", "switch", "number", "select", "event", "button",
})
_VALID_ENTITY_CATEGORIES: frozenset[str | None] = frozenset({None, "config", "diagnostic"})
_NUMERIC_PLATFORMS: frozenset[str] = frozenset({"sensor", "number"})


@dataclass(frozen=True)
class TraitSpec:
    """A single trait's metadata.

    `id` is the canonical trait identifier (typically the first entry of a
    cloud `propertyId` array). `name` is a stable symbolic name auto-generated
    by the auto-derive pipeline. The remaining fields capture the
    cloud-reported metadata that drives entity construction.
    """

    id: str
    name: str
    description: str = ""
    data_type: str = "unknown"
    unit: str | None = None
    enum_values: dict[str, str] | None = None
    wire_path: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    readable: bool = True
    # V3 convention: traits are NOT writable by default. The V3 generator
    # only writes `writable: true` to data.json when V3's spec says so, so
    # the default must be False -- otherwise read-only sensor traits
    # silently classify as Number/Switch/Select.
    writable: bool = False
    sources: frozenset[str] = frozenset({"observed"})
    default_enabled: bool = True
    # Global Aqara master-spec trait identifier (aqaralinkspec.json key,
    # e.g. 32989 == CurrentIlluminance). Distinct from `id`, which is the
    # dotted-numeric pid (e.g. "0.3.85"). None when the plugin-extraction
    # pipeline cannot confidently correlate via name matching.
    trait_id: int | None = None
    # Runtime provenance marker set by descriptor builders.
    #   None         -> catalogue-authored; not yet realised as a runtime descriptor.
    #   "cloud_trait"-> built by auto_derive's cloud-trait pass (warm propertyId).
    #   "panel_synth"-> built by auto_derive's panel-synthesis pass (cold).
    # Authoring provenance (api/plugin/observed) lives in `sources` and is
    # independent of this field. `Literal` narrows callers to the two valid
    # strings; the existing codebase already mixes `Literal` with
    # `@dataclass(frozen=True)` (see descriptors.py:160-187).
    synthesis_source: Literal["cloud_trait", "panel_synth"] | None = None
    # v3d-7 classification fields (all optional):
    platform: str | None = None
    entity_category: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit_of_measurement: str | None = None
    scale: float | None = None
    suggested_display_precision: int | None = None
    icon: str | None = None
    auto_clear_seconds: float | None = None
    # V3 Open Cloud catalogue fields. Populated by the catalogue generator
    # from spec.query.qlinkmodel.config responses. Optional for back-compat
    # with hand-authored catalogue entries that pre-date the V3 regen.
    function_code: str | None = None   # e.g. "BasicInformation", "Temperature", "OccupancySensing"
    trait_code: str | None = None      # e.g. "Reachable", "CurrentTemperature", "Occupancy"
    subscribable: bool = False         # V3 distinguishes this from readable
    endpoint_id: int | None = None     # first component of wire_path; useful for filtering by endpoint

    def __post_init__(self) -> None:
        """v3d-7 validation. Raises RuntimeError on construction-time misconfig."""
        # Reject control characters in free-form, cloud/device-sourced string
        # fields. These have no place in trait metadata and, left in, would
        # corrupt the generated overrides.py snippet (export_overlay), a
        # second-order source-injection vector. Printable Unicode (e.g. "°C")
        # is allowed -- only control chars (category Cc) are rejected.
        for fld_name in (
            "id", "name", "description", "data_type",
            "unit", "wire_path", "device_class",
        ):
            val = getattr(self, fld_name)
            if isinstance(val, str) and _has_control_char(val):
                raise RuntimeError(
                    f"TraitSpec(id={self.id!r}): field {fld_name!r} contains a"
                    f" control character; refusing"
                )
        if isinstance(self.enum_values, dict):
            for key, label in self.enum_values.items():
                if _has_control_char(str(key)) or _has_control_char(str(label)):
                    raise RuntimeError(
                        f"TraitSpec(id={self.id!r}): enum_values contains a"
                        f" control character; refusing"
                    )
        if self.platform is not None and self.platform not in _VALID_PLATFORMS:
            raise RuntimeError(
                f"TraitSpec(id={self.id!r}): platform={self.platform!r} not in"
                f" {sorted(_VALID_PLATFORMS)}"
            )
        if self.entity_category not in _VALID_ENTITY_CATEGORIES:
            raise RuntimeError(
                f"TraitSpec(id={self.id!r}): entity_category={self.entity_category!r}"
                f" must be None, 'config', or 'diagnostic'"
            )
        # state_class requires explicit platform == "sensor" (no heuristic inference at construction)
        if self.state_class is not None and self.platform != "sensor":
            raise RuntimeError(
                f"TraitSpec(id={self.id!r}): state_class={self.state_class!r}"
                f" requires explicit platform='sensor' (got platform={self.platform!r})"
            )
        # scale / unit_of_measurement / suggested_display_precision require sensor or number
        for fld_name in ("scale", "unit_of_measurement", "suggested_display_precision"):
            fld_value = getattr(self, fld_name)
            if fld_value is not None and self.platform not in _NUMERIC_PLATFORMS:
                raise RuntimeError(
                    f"TraitSpec(id={self.id!r}): {fld_name}={fld_value!r}"
                    f" requires explicit platform in {sorted(_NUMERIC_PLATFORMS)}"
                    f" (got platform={self.platform!r})"
                )
        # scale must be a non-zero finite float. nan / inf would silently
        # produce garbage state values via apply_value's multiplication;
        # scale=0 silently zeroes every read AND, because the number-write
        # path short-circuits on `scale != 0`, leaves writes un-scaled --
        # asymmetric, silent, undiagnosed. Reject at construction time.
        if self.scale is not None and (
            not math.isfinite(self.scale) or self.scale == 0
        ):
            raise RuntimeError(
                f"TraitSpec(id={self.id!r}): scale={self.scale!r}"
                f" must be a non-zero finite float"
            )
        if self.auto_clear_seconds is not None:
            if self.platform != "binary_sensor":
                raise RuntimeError(
                    f"TraitSpec(id={self.id!r}): auto_clear_seconds="
                    f"{self.auto_clear_seconds!r} requires platform='binary_sensor'"
                    f" (got platform={self.platform!r})"
                )
            if (
                not math.isfinite(self.auto_clear_seconds)
                or self.auto_clear_seconds <= 0
            ):
                raise RuntimeError(
                    f"TraitSpec(id={self.id!r}): auto_clear_seconds="
                    f"{self.auto_clear_seconds!r} must be a positive finite float"
                )

    def __hash__(self) -> int:
        ev = self.enum_values
        ev_hash: tuple[tuple[str, str], ...] | None = (
            tuple(sorted(ev.items())) if ev is not None else None
        )
        return hash((
            self.id,
            self.name,
            self.description,
            self.data_type,
            self.unit,
            ev_hash,
            self.wire_path,
            self.min_value,
            self.max_value,
            self.step,
            self.readable,
            self.writable,
            self.sources,
            self.default_enabled,
            self.trait_id,
            self.synthesis_source,
            # v3d-7
            self.platform,
            self.entity_category,
            self.device_class,
            self.state_class,
            self.unit_of_measurement,
            self.scale,
            self.suggested_display_precision,
            self.icon,
            self.auto_clear_seconds,
            # V3 catalogue fields:
            self.function_code,
            self.trait_code,
            self.subscribable,
            self.endpoint_id,
        ))


def register_trait(spec: TraitSpec) -> TraitSpec:
    """Register or merge `spec` into the catalog.

    Rules:

    - If the id is unknown, store `spec` and return it.
    - If the existing entry equals `spec`, return the existing entry
      (idempotent no-op; no change listener fired).
    - Otherwise replace the existing entry with `spec` and return `spec`
      (last-writer-wins).
    """
    existing = BY_ID.get(spec.id)
    if existing is None:
        BY_ID[spec.id] = spec
        _notify_change()
        return spec
    if existing == spec:
        return existing
    BY_ID[spec.id] = spec
    _notify_change()
    return spec


def get(trait_id: str) -> TraitSpec | None:
    """Return the registered `TraitSpec` for `trait_id`, or None."""
    return BY_ID.get(trait_id)


def all_traits() -> list[TraitSpec]:
    """Snapshot of every registered trait."""
    return list(BY_ID.values())


def reset_for_tests() -> None:
    """Clear the catalog. Tests only -- production code never calls this."""
    BY_ID.clear()
    global _change_listener
    _change_listener = None


def set_change_listener(listener: Callable[[], None] | None) -> None:
    """Install a callback fired after every catalog-mutating registration.

    Set to None to detach. Vestigial post-V3 (no live caller); kept as
    a hook for future cache-style consumers. Not part of the public
    device-model API.
    """
    global _change_listener
    _change_listener = listener


def _notify_change() -> None:
    listener = _change_listener
    if listener is None:
        return
    try:
        listener()
    except Exception:  # noqa: BLE001
        # The listener exists for cache persistence; never let its
        # failure break a registration call.
        pass


# `replace` re-export keeps callers from also importing dataclasses.replace
# when they want to merge metadata into an existing TraitSpec.
__all__ = [
    "BY_ID",
    "TraitSpec",
    "all_traits",
    "get",
    "register_trait",
    "replace",
    "reset_for_tests",
    "set_change_listener",
]
