"""V3-first classifier orchestrator.

Replaces auto_derive._classify. Groups traits by endpoint, looks up each
endpoint's deviceType, dispatches to the matching per-deviceType composer.

The orchestrator is the only place that knows about endpoints; per-deviceType
composers receive the trait dict already filtered to one endpoint.

Dynamic-endpoint filtering: callers can pass `active_endpoints` (the
runtime EndpointArrayDynamic value) to skip endpoints the device has not
provisioned. None means "all endpoints active".
"""
from __future__ import annotations

import logging
from collections import defaultdict

from .descriptors import AnyDescriptor
from .device_types import _base, _COMPOSERS, _fallback, get_composer
from .device_types._build import build_descriptor
from .traits import TraitSpec

_LOGGER = logging.getLogger(__name__)

# deviceTypes whose composer MERGES MULTIPLE traits into one entity -- e.g. Light
# fuses Output.OnOff + LevelControl + ColorControl into a single LightDescriptor.
# Pulling one of those traits out to honor a per-trait platform override would
# break the composite, so overrides on these endpoints are ignored (scope-1 limit).
# NOTE: single-trait-absorbing composers (camera detectors, Doorbell) and pure
# passthroughs (VideoDoorbell -> camera) are NOT fused -- their other traits
# already passthrough, so a per-trait override on them is safe and is honored.
_FUSED_DEVICE_TYPES: frozenset[str] = frozenset({"Light"})


def classify_v3(
    model: str,
    endpoints: dict[int, dict],
    traits: dict[str, TraitSpec],
    active_endpoints: frozenset[int] | None = None,
) -> list[AnyDescriptor]:
    """Classify a V3-shaped catalogue (endpoints + wire-path-keyed traits) into HA descriptors."""
    # Group traits by endpoint_id. Traits with endpoint_id=None go to
    # endpoint 0 by convention (matches the V3 spec -- endpoint 0 is "Root").
    by_endpoint: dict[int, dict[str, TraitSpec]] = defaultdict(dict)
    for wp, spec in traits.items():
        ep = spec.endpoint_id if spec.endpoint_id is not None else 0
        by_endpoint[ep][wp] = spec

    out: list[AnyDescriptor] = []

    # Loop-invariant: only `model` is read by composers.
    context = _base.ComposeContext(model=model)

    # Iterate every endpoint that has traits OR appears in the endpoints map.
    all_endpoint_ids = set(by_endpoint.keys()) | set(endpoints.keys())
    for ep_id in sorted(all_endpoint_ids):
        if active_endpoints is not None and ep_id not in active_endpoints:
            continue
        ep_traits = by_endpoint.get(ep_id, {})
        if not ep_traits:
            continue
        device_type = endpoints.get(ep_id, {}).get("deviceType")
        composer = _select_composer(device_type, model, ep_id)
        # Forced per-trait platform overrides become descriptors directly; the
        # rest of the endpoint's traits pass through to the composer.
        forced_descriptors, ep_traits = _apply_platform_overrides(
            ep_traits, device_type, model, ep_id,
        )
        out.extend(forced_descriptors)
        out.extend(composer(endpoint_id=ep_id, traits=ep_traits, context=context))
    return out


def _select_composer(device_type: str | None, model: str, ep_id: int) -> _base.Composer:
    """Pick the composer for an endpoint's deviceType.

    Unknown (uncatalogued) deviceTypes log a warning and fall back to per-trait
    classification; endpoints with no deviceType also use _fallback.
    """
    if device_type and device_type not in _COMPOSERS:
        _LOGGER.warning(
            "classify_v3: model=%s endpoint=%s has unknown deviceType=%r;"
            " falling back to per-trait classification.",
            model, ep_id, device_type,
        )
        return _fallback.compose
    if device_type:
        return get_composer(device_type)
    return _fallback.compose


def _apply_platform_overrides(
    ep_traits: dict[str, TraitSpec],
    device_type: str | None,
    model: str,
    ep_id: int,
) -> tuple[list[AnyDescriptor], dict[str, TraitSpec]]:
    """Resolve per-trait platform overrides for one endpoint.

    Returns ``(forced_descriptors, passthrough_traits)``. For a fused deviceType
    the overrides are ignored (with a warning) and every trait passes through;
    otherwise traits carrying a ``platform`` override are built into descriptors
    here and removed from the passthrough set handed to the composer.
    """
    if device_type in _FUSED_DEVICE_TYPES:
        overridden = [s.id for s in ep_traits.values() if s.platform is not None]
        if overridden:
            _LOGGER.warning(
                "classify_v3: model=%s endpoint=%s deviceType=%s fuses its "
                "traits; platform override(s) on %s ignored.",
                model, ep_id, device_type, overridden,
            )
        return [], ep_traits

    forced = {wp: s for wp, s in ep_traits.items() if s.platform is not None}
    if not forced:
        return [], ep_traits

    descriptors: list[AnyDescriptor] = []
    for s in forced.values():
        desc = build_descriptor(s, s.platform)
        if desc is not None:
            descriptors.append(desc)
        else:
            _LOGGER.warning(
                "classify_v3: model=%s endpoint=%s: platform override "
                "%r on %s produced no descriptor (missing required data?);"
                " trait dropped.",
                model, ep_id, s.platform, s.id,
            )
    passthrough = {wp: s for wp, s in ep_traits.items() if s.platform is None}
    return descriptors, passthrough
