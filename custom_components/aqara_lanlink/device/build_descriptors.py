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

from homeassistant.const import EntityCategory

from . import catalog
from .attrs import AttrSpec
from .classify_v3 import classify_v3
from .descriptors import (
    AnyDescriptor,
    ButtonDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
    TextDescriptor,
)
from .traits import TraitSpec

if TYPE_CHECKING:
    from .overlay import Overlay

_LOGGER = logging.getLogger(__name__)

# SettingSpec.entity_category is a bare string; descriptors carry HA's enum.
_ENTITY_CATEGORY = {
    "config": EntityCategory.CONFIG,
    "diagnostic": EntityCategory.DIAGNOSTIC,
}


def build_setting_descriptors(model: str) -> list[AnyDescriptor]:
    """Turn `model`'s rid-keyed SettingSpecs into HA entity descriptors.

    Reads `catalog.settings_for_model(model)` and emits one descriptor per
    SettingSpec, dispatched on `platform`. Each descriptor binds to an
    `AttrSpec(name=rid, resource_id=rid)` so `coordinator.async_write` sends
    the bare rid over LANLink instead of a wire path. Unknown platforms are
    skipped with a warning rather than crashing the derive.

    State model for these rid-keyed settings (child lock, indicator, button
    mode, power-off memory, max power, find/restart):

    - Writes are fully local: the 3-part resource ID is sent over LANLink with
      no cloud on the write path.
    - State is cloud-seeded once at load (config-entry setup, via
      `res/query/by/resourceId`) and then set optimistically after each HA
      write. So an entity shows real state at load and after every HA write.
    - LIMITATION: changes made in the Aqara app mid-session are NOT reflected
      until the integration reloads. There is no continuous polling, and these
      settings receive no local push reports (reports are wire-path-keyed,
      settings are rid-keyed, and there is no bridge between the two).
    - Deferral note: a LAN-only install with no cloud configured could mark
      these entities `assumed_state=True`. v1 ships the cloud-seeded path and
      leaves `assumed_state` as-is; the LAN-only refinement is future work.
    """
    out: list[AnyDescriptor] = []
    for rid, spec in catalog.settings_for_model(model).items():
        attr = AttrSpec(name=rid, resource_id=rid)
        category = _ENTITY_CATEGORY.get(spec.entity_category, EntityCategory.CONFIG)
        common = dict(
            key=rid,
            name=spec.name,
            entity_category=category,
            entity_registry_enabled_default=spec.default_enabled,
            attr=attr,
        )
        if spec.platform == "switch":
            out.append(
                SwitchDescriptor(
                    **common, on_value=spec.on_value, off_value=spec.off_value,
                    optimistic=spec.optimistic,
                )
            )
        elif spec.platform == "select":
            options_map = tuple(
                (label, wire)
                for wire, label in (spec.enum_values or {}).items()
            )
            out.append(
                SelectDescriptor(
                    **common, options_map=options_map, optimistic=spec.optimistic,
                )
            )
        elif spec.platform == "number":
            out.append(
                NumberDescriptor(
                    **common,
                    min_value=spec.min,
                    max_value=spec.max,
                    native_unit_of_measurement=spec.unit,
                    optimistic=spec.optimistic,
                )
            )
        elif spec.platform == "text":
            out.append(
                TextDescriptor(**common, optimistic=True)
            )
        elif spec.platform == "button":
            out.append(
                ButtonDescriptor(**common, press_value=spec.press_value or "1")
            )
        else:
            _LOGGER.warning(
                "Skipping setting %s on %s: unknown platform %r",
                rid, model, spec.platform,
            )
    return out


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
    return classify_v3(model, endpoints, merged) + build_setting_descriptors(model)
