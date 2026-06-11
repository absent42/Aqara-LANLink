"""Entity name disambiguation.

Catalogue traits carry a single `name` per trait_code, so a device that
exposes the same trait on several endpoints (FP2/FP400 occupancy zones,
multi-gang switches, multi-channel lights) would render multiple HA entities
with identical friendly names. This pass runs once over a device's full
descriptor list -- the only point with the whole-device view -- and rewrites
colliding names so each entity is distinguishable.

Rules, applied per group of descriptors sharing an identical `name` (2+):

  - Zone members (endpoint >= 100) get a "(zone N)" suffix, N = endpoint - 100,
    and are disabled by default. Only FP2/FP400 occupancy reach this; their
    catchall presence lives on a low endpoint and is handled below.
  - Low-endpoint members (< 100):
      * a lone one (the catchall sharing a group with zones, e.g. FP2 ep2)
        keeps its bare name and existing enabled state.
      * several (switch gangs, light channels, buttons) get a 1-based index
        suffix and keep their existing enabled state.

Names that don't collide are never touched -- a single high-endpoint sensor
(FP2 HeartRate on ep131) is not a zone.
"""
from __future__ import annotations

import dataclasses

from .descriptors import AnyDescriptor

ZONE_ENDPOINT_BASE = 100


def _endpoint_of(desc: AnyDescriptor) -> int | None:
    """Endpoint id parsed from the descriptor's `auto_<ep>_<fn>_<attr>` key.

    Returns None for keys that don't follow the catalogue convention (e.g.
    hand-authored PTZ entities), which then never count as zones.
    """
    parts = desc.key.split("_")
    if len(parts) >= 2 and parts[0] == "auto" and parts[1].isdigit():
        return int(parts[1])
    return None


def disambiguate_names(descriptors: list[AnyDescriptor]) -> list[AnyDescriptor]:
    """Return a copy of `descriptors` with colliding friendly names rewritten."""
    groups: dict[str, list[int]] = {}
    for i, desc in enumerate(descriptors):
        groups.setdefault(desc.name, []).append(i)

    out = list(descriptors)
    for name, indices in groups.items():
        if len(indices) < 2:
            continue

        zones: list[tuple[int, int]] = []  # (list index, endpoint)
        base: list[tuple[int, int | None]] = []
        for i in indices:
            ep = _endpoint_of(descriptors[i])
            if ep is not None and ep >= ZONE_ENDPOINT_BASE:
                zones.append((i, ep))
            else:
                base.append((i, ep))

        for i, ep in zones:
            out[i] = dataclasses.replace(
                descriptors[i],
                name=f"{name} (zone {ep - ZONE_ENDPOINT_BASE})",
                entity_registry_enabled_default=False,
            )

        if len(base) > 1:
            ordered = sorted(base, key=lambda x: x[1] if x[1] is not None else 0)
            for idx, (i, _ep) in enumerate(ordered, start=1):
                out[i] = dataclasses.replace(descriptors[i], name=f"{name} {idx}")

    return out
