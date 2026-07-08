"""Model-scoped lookup API for trait specifications and display metadata.

This module provides lookups for authored device knowledge (traits, enum labels,
display metadata) keyed by Aqara model string. The authoritative data lives in
per-model packages under `device/models/<package>/`, declared as module-level
attributes (TRAITS, DISPLAY_NAME, MANUFACTURER, etc.). Per-trait fields like
wire_path and enum_values are stored on the individual TraitSpec entries inside
TRAITS.

The discovery walk in `device/registry` indexes these attributes into per-model
indices that back these lookup functions. The indices live on `registry` so that
the discovery walk owns population; this module's lookup functions are thin shims
that delegate to `registry._ensure_discovered()` and the index dicts via `_reg()`.
"""

from __future__ import annotations

from typing import NamedTuple
from collections.abc import Iterator

from custom_components.aqara_lanlink.device.settings import SettingSpec
from custom_components.aqara_lanlink.device.traits import TraitSpec


class DisplayMetadata(NamedTuple):
    """Display metadata for a device model.

    Fields:
        manufacturer: Manufacturer name (typically "Aqara").
        display_name: Human-readable name for the device model.
    """

    manufacturer: str
    display_name: str


def _reg():
    """Return the registry module with package discovery guaranteed to have run.

    The import is local to avoid a catalog<->registry module-load cycle; after
    the first discovery the ``_ensure_discovered`` call is just an idempotent
    flag check, so every accessor can route through here cheaply.
    """
    from custom_components.aqara_lanlink.device import registry

    registry._ensure_discovered()
    return registry


def get_trait(model: str, trait_id: str) -> TraitSpec | None:
    """Return the authored TraitSpec for a model and trait id, or None.

    Args:
        model: Aqara model string (e.g., "lumi.light.agl003").
        trait_id: Trait identifier (e.g., "1.7.85").

    Returns:
        TraitSpec if the model+trait_id is authored, None otherwise.
    """
    return _reg()._TRAIT_INDEX.get((model, trait_id))


def get_enum_labels(model: str, trait_id: str) -> dict[str, str] | None:
    """Return the authored enum labels for a model and trait id, or None.

    Reads TraitSpec.enum_values; signature preserved from v3a.
    """
    ts = _reg()._TRAIT_INDEX.get((model, trait_id))
    return ts.enum_values if ts is not None else None


def get_display_metadata(model: str) -> DisplayMetadata | None:
    """Return the display metadata for a model, or None.

    Args:
        model: Aqara model string (e.g., "lumi.light.agl003").

    Returns:
        DisplayMetadata (manufacturer, display_name) if the model is known,
        None otherwise.
    """
    return _reg()._DISPLAY_INDEX.get(model)  # type: ignore[return-value]


def iter_all_traits() -> Iterator[TraitSpec]:
    """Flatten every catalogued TraitSpec across every model.

    Order is arbitrary; callers must not depend on it. Triggers package
    discovery on first call (same as every other lookup in this module).
    A single TraitSpec instance may be yielded multiple times if more than
    one model package references it via shared imports.
    """
    yield from _reg()._TRAIT_INDEX.values()


def all_traits_for_model(model: str) -> dict[str, "TraitSpec"]:
    """Return a fresh dict of {trait_id: TraitSpec} declared for `model`.

    Returns a copy, not a live view; mutating the result does not affect
    the underlying index. Empty dict when the package has no TRAITS entries
    or the model is unknown.

    Used by the v3a synth pass to resolve catalogue traits for auto-derived
    models that do not have an override class registered in _BY_MODEL.
    """
    return dict(_reg()._TRAITS_BY_MODEL.get(model, {}))


def settings_for_model(model: str) -> dict[str, "SettingSpec"]:
    """Return a fresh dict of {rid: SettingSpec} declared for `model`.

    Returns a copy, not a live view; mutating the result does not affect the
    underlying index. Empty dict when the package has no SETTINGS entries or
    the model is unknown.

    Backs a later task's build_setting_descriptors(model).
    """
    return dict(_reg()._SETTINGS_INDEX.get(model, {}))


def composites_for_model(model: str) -> dict[str, dict]:
    """Return a fresh dict of {rid: {"codec": ..., "name": ...}} for `model`.

    Returns a copy, not a live view; mutating the result does not affect the
    underlying index. Empty dict when the package has no composites block or
    the model is unknown.

    Backs the entry-level CompositeController construction in __init__.py.
    """
    return dict(_reg()._COMPOSITES_INDEX.get(model, {}))


def power_class_for_model(model: str) -> str | None:
    """Return the model's power_class ("mains"/"battery"/...), or None.

    None when the model is unknown or its package did not declare POWER_CLASS.
    """
    return _reg()._POWER_CLASS_INDEX.get(model)


def allow_unauthored(model: str) -> tuple[int, ...]:
    """Return the per-model ALLOW_UNAUTHORED_TRAITS tuple, or () if absent.

    Models that opt traits into pidless synth (synthesis from master spec
    alone, without a matching catalogue pid) declare a module-level
    ``ALLOW_UNAUTHORED_TRAITS: tuple[int, ...]`` constant. v3a's synth pass
    reads it via this accessor so the field's name is referenced in one
    place only.

    Args:
        model: Aqara model string (e.g., "lumi.light.agl003").

    Returns:
        Tuple of trait IDs (int) authorized for pidless synthesis, or empty
        tuple if the model is unknown or has no ALLOW_UNAUTHORED_TRAITS.
    """
    return _reg()._ALLOW_UNAUTHORED_INDEX.get(model, ())


def endpoints_for_model(model: str) -> dict[int, dict]:
    """Per-endpoint metadata (deviceType etc.) for `model`. {} if uncatalogued."""
    return dict(_reg()._ENDPOINTS_INDEX.get(model, {}))


def device_types_for_model(model: str) -> tuple[str, ...]:
    """Top-level deviceTypes tuple for `model`, e.g. ('OccupancySensor', ...)."""
    return _reg()._DEVICE_TYPES_INDEX.get(model, ())


def ptz_features_for_model(model: str) -> frozenset[str]:
    """PTZ sub-features for `model` (pan_tilt/zoom/presets), or empty frozenset.

    Sourced from the model package's overrides.py CAPABILITIES["ptz"]; PTZ is
    not in the V3 trait catalogue (it is a separate P2P plane), so it is
    maintainer-declared per model.
    """
    caps = _reg()._CAPABILITIES_INDEX.get(model, {})
    return frozenset(caps.get("ptz", ()))


def is_camera_model(model: str) -> bool:
    """True when the V3 catalogue marks any endpoint of `model` with deviceType 'Camera'.

    Used by config_flow's options-flow camera-IP form (and any future caller)
    to decide whether the device is a camera, without depending on per-model
    Device subclasses (V3 catalogue ships data-only packages).
    """
    return "Camera" in device_types_for_model(model)


def dropped_paths_for_model(model: str) -> frozenset[str]:
    """V3 wire paths the trait_policy excluded from `model`'s data.json.

    Used by:
      - the Device's _known_wire_paths construction in __init__.py (so a
        pushed report for a dropped path doesn't enter ObservedPathCache
        and fire the candidate-paths Repair issue)
      - gap_report.build_gap_report_from_cloud (so the scan-device service
        doesn't propose dropped paths as new-trait candidates the user
        could accept into the overlay)

    Empty frozenset when the model hasn't been regenerated with the
    dropped_paths field yet (older shipped packages).
    """
    return _reg()._DROPPED_PATHS_INDEX.get(model, frozenset())


def dropped_rids_for_model(model: str) -> dict[str, str]:
    """Resource ids the settings generator excluded from `model`, with reasons.

    A {rid: reason} map (sensor/event/diagnostic/dual). Unlike
    dropped_paths_for_model this returns a dict, not a frozenset - the drop
    reason is carried for the future scan-device consumer.

    Returns a fresh dict; mutating it does not affect the underlying index.
    Empty dict when the model is unknown or its package has no dropped_rids
    (older shipped packages).
    """
    return dict(_reg()._DROPPED_RIDS_INDEX.get(model, {}))


def reset_for_tests() -> None:
    """Clear all per-model indices. Tests only — production never calls this.

    Delegates to registry.reset_for_tests() since the indices now live there.
    """
    from custom_components.aqara_lanlink.device import registry

    registry.reset_for_tests()


__all__ = [
    "DisplayMetadata",
    "all_traits_for_model",
    "allow_unauthored",
    "composites_for_model",
    "device_types_for_model",
    "dropped_paths_for_model",
    "dropped_rids_for_model",
    "endpoints_for_model",
    "get_display_metadata",
    "get_enum_labels",
    "get_trait",
    "is_camera_model",
    "iter_all_traits",
    "power_class_for_model",
    "ptz_features_for_model",
    "reset_for_tests",
    "settings_for_model",
]
