"""The deterministic catalogue-first derive.

`build_descriptors(model, overlay) -> list[AnyDescriptor]` is a pure
function that produces the entity-descriptor set for one device from
two shipped data sources (the per-model `TRAITS` dicts and the master
spec) and one per-install source (the local overlay).

There is no cloud, no coordinator, no traits_response, no probe list.
The function is idempotent: identical inputs produce identical output
(same set, same order, same field values). This is the keystone of the
catalogue-first design.

Naming, classification, and unit are resolved before each descriptor is
constructed - no post-construction rename pass. Display names are baked
into data.json at generator time (PascalCase `OnOff` becomes `On off`
via `humanize_name`); composers and entities consume them as-is. The
V3-spec identifier (`trait_code`) is preserved separately for composer
dispatch and diagnostics.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import catalog
from .classify_v3 import classify_v3
from .descriptors import AnyDescriptor
from .traits import TraitSpec

if TYPE_CHECKING:
    from .overlay import Overlay

_LOGGER = logging.getLogger(__name__)


def _merge_overlay_into_catalogue(
    catalogue: dict[str, TraitSpec],
    overlay: dict[str, TraitSpec | None],
) -> dict[str, TraitSpec]:
    """Merge overlay onto catalogue with override-on-top semantics.

    - overlay entry with TraitSpec REPLACES the catalogue entry at the same
      wire_path (or adds a new one).
    - overlay entry with None REMOVES the catalogue entry at that wire_path.
    - catalogue entries with no overlay counterpart pass through unchanged.

    BREAKING CHANGE vs pre-V3: the old merge was additive-only (catalogue
    won on conflict). V3 inverts this so per-install overlay corrections
    can fix shipped-catalogue mistakes without a release.
    """
    merged: dict[str, TraitSpec] = dict(catalogue)
    for wp, spec in overlay.items():
        if spec is None:
            merged.pop(wp, None)
        else:
            merged[wp] = spec
    return merged


def build_descriptors(model: str, overlay: Overlay) -> list[AnyDescriptor]:
    """Build the descriptor list for `model` deterministically.

    Consumes the shipped catalogue (`catalog.all_traits_for_model`,
    `catalog.endpoints_for_model`) and the overlay
    (`overlay.traits_for_model`), merges them with override-on-top
    semantics, then hands the result to `classify_v3` for endpoint-aware
    composition.
    """
    catalogue = catalog.all_traits_for_model(model)
    overlay_traits = overlay.traits_for_model(model)
    merged = _merge_overlay_into_catalogue(catalogue, overlay_traits)
    endpoints = catalog.endpoints_for_model(model)
    return classify_v3(model, endpoints, merged)
