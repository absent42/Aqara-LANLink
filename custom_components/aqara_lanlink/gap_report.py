"""Pure gap-report builder (V3 shape).

Given a flattened view of what the device pushed (a `{wire_path: value}`
map) plus the union of what the catalogue and overlay already know, return
a list of `GapEntry` rows for every wire path the device exposes but is
NOT yet covered. Each row carries a proposed V3-shaped `TraitSpec` ready
to be written to the overlay on user accept.

The function is pure and stateless; consumers (the scan service and the
config-flow bootstrap) provide all inputs. Used in two places:
- services/scan_device.py - manual / observation-prompted scans.
- config_flow.py - the uncatalogued-model bootstrap at device add time.

V3 contract: proposed `TraitSpec.id == wire_path`. `function_code` and
`trait_code` are derived from the live catalogue when the function_id
(second wire-path segment) is recognised; otherwise they stay None and
the proposal is marked `unrecognised_wire_path` in `sources` so the
bootstrap flow can flag it for maintainer review.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .device import catalog
from .device.traits import TraitSpec

if TYPE_CHECKING:
    from .device.overlay import Overlay
    from .hub.cloud_client import EndpointPanel

_LOGGER = logging.getLogger(__name__)


@dataclass
class GapEntry:
    """One row of a gap report: a trait the catalogue/overlay does not cover.

    `proposed_spec` is what the accept flow would write to the overlay if
    the user picks this entry. `cloud_value` / `cloud_unit` are
    informational (shown in the review UI) and are NOT written to the
    overlay.

    In V3 `pid` and `wire_path` carry the same string; the field is kept
    distinct for downstream consumers that key by `pid`.
    """
    pid: str
    wire_path: str
    proposed_spec: TraitSpec
    cloud_value: Any | None
    cloud_unit: str | None


@dataclass
class GapReport:
    """A scan's gap report.

    `entries` is empty when the catalogue + overlay fully cover what
    the cloud returned. Iteration order is stable: sorted by wire_path.
    """
    model: str
    entries: list[GapEntry]


# Cache of {function_id -> function_code} derived from the live catalogue.
# Populated lazily on first lookup so module import doesn't trigger model
# package discovery (and so tests that inject synthetic TraitSpecs into
# the registry after import can refresh via `_rebuild_fn_id_to_code()`).
_FN_ID_TO_CODE_CACHE: dict[int, str] | None = None


def _build_fn_id_to_code() -> dict[int, str]:
    """Derive `{function_id: function_code}` from every shipped V3 model package.

    Aqara's V3 cloud spec is not strictly consistent: a given function_id
    can be assigned different function_codes on different devices. The
    known example is fn_id=133 which is `LevelControl` on every dimmable
    light but `Output` on lumi.plug.acn005 endpoint 3 (the cloud reuses
    the OnOff trait code 32920 there under a different fn_id). The
    collision lives in Aqara's spec, not in our data -- a regen would
    reintroduce it.

    For the gap-report's "propose a TraitSpec for an observed wire path"
    heuristic we need ONE function_code per function_id, so we pick the
    most-common assignment across the catalogue. Ties broken
    alphabetically for determinism. The downstream Repair flow lets the
    maintainer override the proposed function_code anyway, so an
    imperfect best-guess for the rare collision is acceptable.

    Collisions log at DEBUG (informational, no action required); they
    are NOT a catalogue defect.
    """
    tallies: dict[int, Counter[str]] = {}
    for spec in catalog.iter_all_traits():
        if not spec.wire_path or not spec.function_code:
            continue
        parts = spec.wire_path.split(".")
        if len(parts) < 2:
            continue
        try:
            fn_id = int(parts[1])
        except ValueError:
            continue
        tallies.setdefault(fn_id, Counter())[spec.function_code] += 1
    out: dict[int, str] = {}
    for fn_id, counter in tallies.items():
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        winner = ranked[0][0]
        out[fn_id] = winner
        if len(ranked) > 1:
            _LOGGER.debug(
                "gap_report: function_id %d ambiguous in catalogue %s; "
                "using %r (majority winner).",
                fn_id, dict(counter), winner,
            )
    return out


def _fn_id_to_code() -> dict[int, str]:
    """Return the cached function_id -> function_code map, building if absent."""
    global _FN_ID_TO_CODE_CACHE
    if _FN_ID_TO_CODE_CACHE is None:
        _FN_ID_TO_CODE_CACHE = _build_fn_id_to_code()
    return _FN_ID_TO_CODE_CACHE


def _rebuild_fn_id_to_code() -> None:
    """Force the next `_fn_id_to_code()` call to re-derive the map.

    Public so test fixtures can refresh after injecting synthetic TraitSpec
    rows into `registry._TRAIT_INDEX`. Production code never calls this.
    """
    global _FN_ID_TO_CODE_CACHE
    _FN_ID_TO_CODE_CACHE = None


def build_gap_report(
    pushed: dict[str, Any],
    known: dict[str, TraitSpec],
    *,
    model: str,
) -> GapReport:
    """Build the gap report for one device (V3 shape).

    Args:
        pushed: `{wire_path: value}` map of every trait the device
            exposed in the cloud probe. Values are the raw stringified
            cloud-returned values; they are used only for data-type
            inference, never persisted to the overlay.
        known: `{wire_path: TraitSpec}` map of catalogue + overlay traits
            that already cover the model. Wire paths in this dict are
            filtered out of the report.
        model: Aqara model string; used by name-inference helpers.

    Returns:
        `GapReport` with one `GapEntry` per uncovered wire path, sorted
        by wire_path.
    """
    entries: list[GapEntry] = []
    for wire_path, value in pushed.items():
        if not wire_path:
            continue
        if wire_path in known:
            continue  # already covered by catalogue / overlay
        proposed = _propose_trait_spec(
            wire_path=wire_path, value=_stringify_value(value), model=model,
        )
        if proposed is None:
            continue  # could not synthesize anything useful
        entries.append(GapEntry(
            pid=wire_path,
            wire_path=wire_path,
            proposed_spec=proposed,
            cloud_value=value,
            cloud_unit=None,
        ))

    entries.sort(key=lambda e: e.wire_path)
    return GapReport(model=model, entries=entries)


def build_gap_report_from_cloud(
    *,
    panels: dict[int, "EndpointPanel"],
    traits_response: list[dict],
    catalogue: dict[str, TraitSpec],
    overlay: "Overlay",
    model: str,
) -> GapReport:
    """Adapter that converts a cloud probe response into the V3 inputs.

    Bridges the existing scan_device / config_flow callers (which hold a
    cloud `traits_response` and a pid-keyed catalogue) to the new V3
    `build_gap_report` signature. `panels` is currently unused (V1 used
    it for endpoint-name resolution); kept in the signature so callers
    do not need to drop the argument at the call site.
    """
    _ = panels  # reserved for future use; V3 inference does not need it
    pushed: dict[str, Any] = {}
    for trait in traits_response:
        path = trait.get("path", "")
        if not path:
            continue
        pushed[path] = trait.get("value")
    known: dict[str, TraitSpec] = {}
    for spec in catalogue.values():
        if spec.wire_path:
            known[spec.wire_path] = spec
    for spec in overlay.traits_for_model(model).values():
        if spec.wire_path:
            known[spec.wire_path] = spec
    # Trait-policy-dropped paths are intentionally excluded from the
    # catalogue but the cloud still reports them on probe. Mark them
    # as known (with a sentinel None spec) so build_gap_report excludes
    # them from the "novel paths" set -- otherwise the scan-device
    # service would propose them as accept-candidates and a user
    # accepting one would defeat the policy filter at runtime.
    from .device import catalog as _catalog
    for dropped_path in _catalog.dropped_paths_for_model(model):
        known.setdefault(dropped_path, None)  # type: ignore[arg-type]
    return build_gap_report(pushed, known, model=model)


def _stringify_value(value: Any) -> str:
    """Coerce a cloud-returned value to a string for data-type inference."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _propose_trait_spec(
    *,
    wire_path: str,
    value: str,
    model: str,
) -> TraitSpec | None:
    """Build a V3-shaped TraitSpec the user could accept into the overlay.

    `wire_path` is the dotted numeric path the device pushed. `value` is
    the cloud-returned value, used only for data-type inference.
    `function_code` / `trait_code` are looked up from a catalogue-derived
    map; when the function_id is not present in ANY shipped model
    package, `function_code` stays None and the proposal carries an
    "unrecognised_wire_path" marker in `sources` so the bootstrap flow
    flags it for maintainer review.

    Platform inference is deliberately omitted: in V3 the per-deviceType
    composer sets `platform`. Returns None only when the TraitSpec
    constructor rejects the proposal.
    """
    parts = wire_path.split(".")
    try:
        ep_id = int(parts[0]) if parts and parts[0] else 0
    except ValueError:
        ep_id = 0
    fn_id: int | None
    if len(parts) >= 2:
        try:
            fn_id = int(parts[1])
        except ValueError:
            fn_id = None
    else:
        fn_id = None
    function_code = _fn_id_to_code().get(fn_id) if fn_id is not None else None
    data_type = _infer_data_type(value)
    sources_set: set[str] = {"observed"}
    if function_code is None:
        sources_set.add("unrecognised_wire_path")
    sources = frozenset(sources_set)
    name = _propose_name(wire_path=wire_path, function_code=function_code)
    try:
        return TraitSpec(
            id=wire_path,
            wire_path=wire_path,
            endpoint_id=ep_id,
            function_code=function_code,
            trait_code=None,  # cannot derive trait_code from wire path alone
            name=name,
            data_type=data_type,
            readable=True,
            subscribable=True,
            sources=sources,
        )
    except RuntimeError as exc:
        _LOGGER.warning(
            "gap_report: TraitSpec rejected proposal for wire_path=%s: %s",
            wire_path, exc,
        )
        return None


# PascalCase -> Sentence-case helper lives in `device.humanize`; this
# module just re-exports under the local private name so the existing
# call sites read the same.
from .device.humanize import humanize_name as _humanize_pascal_case  # noqa: E402


def _propose_name(*, wire_path: str, function_code: str | None) -> str:
    """Pick a human-ish display name for the proposed trait.

    Precedence:
        1) the V3 function_code (if resolvable), humanised;
        2) a stable fallback that includes the wire_path.

    Wire paths whose function_id is in no shipped model leave function_code
    None and fall to the auto_ fallback; they already carry the
    `unrecognised_wire_path` marker for maintainer review.
    """
    if function_code:
        humanised = _humanize_pascal_case(function_code)
        if humanised:
            return humanised
    return f"auto_{wire_path}"


def _infer_data_type(value: str) -> str:
    """Infer a `TraitSpec.data_type` string from a stringified cloud value.

    V3 drops platform inference (composers handle that). Returns the
    coarse-grained type label TraitSpec expects:

        "bool"   - "0"/"1"/"true"/"false"
        "number" - parses as int/float
        "string" - anything else (including the empty string)
    """
    if value is None:
        return "string"
    s = value.strip()
    lower = s.lower()
    if lower in ("true", "false", "0", "1"):
        return "bool"
    # Try numeric coerce; treat "0" / "1" specially above so binary
    # toggles classify as bool rather than number.
    try:
        int(s)
        return "number"
    except ValueError:
        pass
    try:
        float(s)
        return "number"
    except ValueError:
        pass
    return "string"
