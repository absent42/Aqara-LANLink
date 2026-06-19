"""Device-model registry with auto-discovery of `device/models/*` packages.

Per-device override classes register themselves at module-import time via
the `@register_device(*models)` decorator. The registry triggers a one-time
import of every package under `device/models/` on first access; subsequent
calls are no-ops.

A `None` return from `get_device_class` is normal: it means "no override
class is registered, the caller should fall back to `AutoDerivedDevice`."

During discovery, each model package's module-level attributes are indexed:
  MODELS        - tuple of model strings this package owns
  MANUFACTURER  - manufacturer display name (default "Aqara")
  DISPLAY_NAME  - human-readable device name
  WHITELABELS   - dict mapping model -> alternative brand name (optional)
  TRAITS        - dict mapping trait_id -> TraitSpec (optional)

These are populated into the per-model indices (_TRAIT_INDEX, _TRAITS_BY_MODEL,
_DISPLAY_INDEX) which catalog.py reads via thin shim functions. wire_path and
enum_values are now fields on the individual TraitSpec entries; see
`device/traits.py`.

Note: the empty-package check applies only to immediate subpackages of
device/models/ (e.g. device/models/camera_agl013/, device/models/light_agl003/).
The device/models/__init__.py itself is not a subpackage and is never
visited by pkgutil.iter_modules against device/models/__path__.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from .base import Device

if TYPE_CHECKING:
    from .settings import SettingSpec
    from .traits import TraitSpec

_LOGGER = logging.getLogger(__name__)

# Module-level catalog. Keyed by model string; multiple keys can map to
# the same class for devices with model variants.
_BY_MODEL: dict[str, type[Device]] = {}
_discovered: bool = False

# Per-model indices populated during discovery. catalog.py reads these.
# Maps (model, trait_id) -> TraitSpec.
_TRAIT_INDEX: dict[tuple[str, str], "TraitSpec"] = {}
# Maps model -> {trait_id: TraitSpec}. Same data as _TRAIT_INDEX, pre-grouped
# by model so per-model lookups (all_traits_for_model) are O(traits-for-model)
# instead of scanning the whole flat _TRAIT_INDEX once per device at setup.
_TRAITS_BY_MODEL: dict[str, dict[str, "TraitSpec"]] = {}
# Maps model -> DisplayMetadata (imported lazily to avoid circular import).
_DISPLAY_INDEX: dict[str, object] = {}
# Maps model -> tuple of trait IDs authorized for pidless synthesis.
_ALLOW_UNAUTHORED_INDEX: dict[str, tuple[int, ...]] = {}
# Maps model -> frozenset of trait IDs opted-in past the v3d-6 noise filter.
_NOISE_OVERRIDE_INDEX: dict[str, frozenset[int]] = {}
# Maps model -> {endpoint_id: endpoint_metadata_dict} from V3 model packages.
_ENDPOINTS_INDEX: dict[str, dict[int, dict]] = {}
# Maps model -> tuple of device type strings from V3 model packages.
_DEVICE_TYPES_INDEX: dict[str, tuple[str, ...]] = {}
# Maps model -> dict of maintainer-declared capabilities (e.g. {"ptz": ...}).
# PTZ is not in the V3 trait catalogue (separate P2P plane); declared by hand
# in each model package's overrides.py CAPABILITIES map.
_CAPABILITIES_INDEX: dict[str, dict] = {}
# Maps model -> frozenset of wire paths the trait_policy excluded from the
# catalogue. Consumed by ObservedPathCache + gap_report so dropped paths
# don't surface as new-trait candidates the user would be asked to accept.
_DROPPED_PATHS_INDEX: dict[str, frozenset[str]] = {}
# Maps model -> {rid: reason} of resource ids the settings generator excluded
# (sensor/event/diagnostic/dual). Mirrors _DROPPED_PATHS_INDEX but is a dict so
# the drop reason is carried for the scan-device consumer.
_DROPPED_RIDS_INDEX: dict[str, dict[str, str]] = {}
# Maps model -> {rid: SettingSpec}. Rid-keyed device settings written bare
# over LANLink (no wire_path); separate from the trait pipeline.
_SETTINGS_INDEX: dict[str, dict[str, "SettingSpec"]] = {}
# Maps model -> power_class string ("mains"/"battery"/...) or None.
_POWER_CLASS_INDEX: dict[str, str | None] = {}


def _put_trait(model: str, trait_id: str, spec: "TraitSpec") -> None:
    """Insert a trait into both the flat and per-model indices in sync.

    ``_TRAIT_INDEX`` (flat (model, trait_id) lookup) and ``_TRAITS_BY_MODEL``
    (pre-grouped per-model lookup) must always agree; routing every insert
    through here keeps them from drifting. Tests that seed traits directly
    should call this rather than assigning to ``_TRAIT_INDEX``.
    """
    _TRAIT_INDEX[(model, trait_id)] = spec
    _TRAITS_BY_MODEL.setdefault(model, {})[trait_id] = spec


def register_device(*models: str) -> Callable[[type[Device]], type[Device]]:
    """Class decorator. Register the class as the handler for one or more models.

    Idempotent: re-registering the same model -> same class is a no-op.
    Re-registering the same model -> a *different* class raises
    `RuntimeError`.
    """
    if not models:
        raise ValueError("register_device requires at least one model string")

    def _decorator(cls: type[Device]) -> type[Device]:
        if not isinstance(cls, type) or not issubclass(cls, Device):
            raise TypeError(
                f"register_device target must subclass Device; got {cls!r}",
            )
        for model in models:
            existing = _BY_MODEL.get(model)
            if existing is None:
                _BY_MODEL[model] = cls
                continue
            if existing is cls:
                continue
            raise RuntimeError(
                f"model {model!r} is already registered to "
                f"{existing.__name__}; cannot also register {cls.__name__}",
            )
        return cls

    return _decorator


def get_device_class(model: str) -> type[Device] | None:
    """Return the registered class for `model`, or None if none is registered.

    A None return is normal -- it signals the caller should use
    `AutoDerivedDevice` (the stock subclass) for this model.

    Calling this from async code without first awaiting
    ``async_ensure_discovered`` will block the event loop on the first call
    of a session (it walks `device/models/` and imports each subpackage).
    Production callers should pre-warm via ``async_ensure_discovered`` in
    their ``async_setup_entry``; subsequent calls are pure dict lookups.
    """
    _ensure_discovered()
    return _BY_MODEL.get(model)


async def async_ensure_discovered(hass: Any) -> None:
    """Async-safe pre-warm of the per-model index.

    Runs the synchronous filesystem walk and module imports on the
    executor so the event loop stays responsive. Idempotent: subsequent
    calls (and any later sync ``get_device_class`` / ``catalog.get_*``
    calls) are no-ops because ``_discovered`` is True after the first
    successful run.

    Call this once at integration setup time, before any code path that
    invokes ``get_device_class`` from inside the event loop.
    """
    if _discovered:
        return
    await hass.async_add_executor_job(_ensure_discovered)


def known_models() -> list[str]:
    """Return every model string with a registered override class."""
    _ensure_discovered()
    return sorted(_BY_MODEL.keys())


def noise_override_for_model(model: str) -> frozenset[int]:
    """Return the per-model ALLOW_NOISE_TRAITS opt-in set, or empty.

    Used by v3d-6 trait_noise_filter to honour package-level overrides
    of the universal noise denylist. Empty return = no override; the
    universal filter applies. Unknown model returns empty (no crash).
    """
    _ensure_discovered()
    return _NOISE_OVERRIDE_INDEX.get(model, frozenset())


def reset_for_tests() -> None:
    """Clear discovery state. Tests only -- production never calls this."""
    global _discovered
    _BY_MODEL.clear()
    _TRAIT_INDEX.clear()
    _TRAITS_BY_MODEL.clear()
    _DISPLAY_INDEX.clear()
    _ALLOW_UNAUTHORED_INDEX.clear()
    _NOISE_OVERRIDE_INDEX.clear()
    _ENDPOINTS_INDEX.clear()
    _DEVICE_TYPES_INDEX.clear()
    _CAPABILITIES_INDEX.clear()
    _DROPPED_PATHS_INDEX.clear()
    _DROPPED_RIDS_INDEX.clear()
    _SETTINGS_INDEX.clear()
    _POWER_CLASS_INDEX.clear()
    _discovered = False


def _ensure_discovered() -> None:
    """Walk `device/models/` once and import every subpackage.

    Sets _discovered = True only after the loop completes successfully.
    If a validation error escapes (RuntimeError from _index_package), the
    surviving index dicts (_TRAIT_INDEX, _TRAITS_BY_MODEL, _DISPLAY_INDEX,
    _ALLOW_UNAUTHORED_INDEX) are cleared before re-raising so that a
    subsequent call starts from a completely clean state rather than
    partially-populated indices. This prevents the cross-package "model
    already claimed" check from tripping on leftover data from a failed
    first pass.

    Validation errors raise RuntimeError, which propagates to the caller.
    Generic import failures for individual modules are caught, logged, and
    skipped so a single broken module does not abort the rest.
    """
    global _discovered
    if _discovered:
        return
    try:
        models_pkg = importlib.import_module(
            "custom_components.aqara_lanlink.device.models",
        )
    except ImportError:
        _LOGGER.debug("device.models package missing; skipping discovery")
        _discovered = True
        return
    pkg_paths = getattr(models_pkg, "__path__", None)
    if not pkg_paths:
        _discovered = True
        return
    try:
        for module_info in pkgutil.iter_modules(pkg_paths):
            if module_info.name.startswith("_"):
                # Private helpers like _loader.py live alongside model packages
                # but are not themselves model packages; skip them.
                continue
            full_name = (
                f"custom_components.aqara_lanlink.device.models.{module_info.name}"
            )
            pkg_name = full_name
            mod = None
            try:
                mod = importlib.import_module(full_name)
            except Exception:  # noqa: BLE001
                # An individual model module failing to import shouldn't
                # break discovery for the rest. Log + continue.
                _LOGGER.exception("failed to import %s", full_name)
                continue
            # _index_package performs validation and raises RuntimeError for
            # misconfigured packages. These errors propagate to the outer
            # try/except which clears the indices before re-raising.
            _index_package(mod, pkg_name)
    except Exception:
        _TRAIT_INDEX.clear()
        _TRAITS_BY_MODEL.clear()
        _DISPLAY_INDEX.clear()
        _ALLOW_UNAUTHORED_INDEX.clear()
        _NOISE_OVERRIDE_INDEX.clear()
        raise
    _discovered = True


def _index_package(mod: object, pkg_name: str) -> None:
    """Validate and index a successfully imported model package.

    Delegates to _validate_models (raises RuntimeError on misconfiguration,
    returns None when the package declares nothing to index), then populates the
    display and per-model indices: _TRAIT_INDEX, _TRAITS_BY_MODEL, _DISPLAY_INDEX,
    _ALLOW_UNAUTHORED_INDEX, _NOISE_OVERRIDE_INDEX, and the endpoint/device-type/
    capabilities/dropped-paths indices.
    """
    models = _validate_models(mod, pkg_name)
    if models is None:
        return
    _populate_display(mod, pkg_name, models)
    _populate_model_indices(mod, pkg_name, models)


def _validate_models(mod: object, pkg_name: str) -> tuple[str, ...] | None:
    """Validate a model package and return its MODELS tuple.

    Returns None when the package declares nothing to index (no MODELS and no
    data attributes is a hard error; data without MODELS is a hard error; an
    override-class-only or genuinely empty MODELS-less package returns None).
    Raises RuntimeError for any misconfiguration.
    """
    models: tuple[str, ...] | None = getattr(mod, "MODELS", None)
    traits: dict | None = getattr(mod, "TRAITS", None)
    whitelabels: dict | None = getattr(mod, "WHITELABELS", None)

    has_override_class = any(
        issubclass(cls, Device)
        for _name, cls in vars(mod).items()
        if (
            isinstance(cls, type)
            and cls is not Device
            and issubclass(cls, Device)
            and cls.__module__ == getattr(mod, "__name__", None)
        )
    )
    has_data = (
        models is not None
        or traits is not None
        or whitelabels is not None
    )

    # Empty package check: no override class and no data attributes.
    if not has_override_class and not has_data:
        raise RuntimeError(
            f"package {pkg_name}: empty package (no override class and no data"
            " attributes); every model subpackage must declare MODELS or an"
            " override class"
        )

    # Any data attribute (TRAITS, WHITELABELS) without MODELS is a contributor
    # mistake: data with no model claim is unreachable.
    if models is None and (
        traits is not None
        or whitelabels is not None
    ):
        offenders = []
        if traits is not None:
            offenders.append("TRAITS")
        if whitelabels is not None:
            offenders.append("WHITELABELS")
        raise RuntimeError(
            f"package {pkg_name}: {', '.join(offenders)} declared without"
            " MODELS"
        )

    # No further validation needed if no MODELS declared.
    if models is None:
        return None

    # Empty MODELS tuple.
    if len(models) == 0:
        raise RuntimeError(f"package {pkg_name}: empty MODELS tuple")

    # Duplicate entries within a single MODELS tuple.
    seen: set[str] = set()
    for m in models:
        if m in seen:
            raise RuntimeError(
                f"package {pkg_name}: duplicate model {m!r} in MODELS tuple"
            )
        seen.add(m)

    # Two packages claiming the same model string.
    for m in models:
        if m in _DISPLAY_INDEX:
            raise RuntimeError(
                f"package {pkg_name}: model {m!r} already claimed by another"
                " package"
            )

    # Override class registered for a model not in MODELS.
    models_set = set(models)
    if has_override_class:
        for registered_model in list(_BY_MODEL):
            cls = _BY_MODEL[registered_model]
            if (
                getattr(cls, "__module__", None) == getattr(mod, "__name__", None)
                and registered_model not in models_set
            ):
                raise RuntimeError(
                    f"package {pkg_name}: override class"
                    f" {cls.__name__!r} registered for model"
                    f" {registered_model!r} which is not in MODELS"
                )

    # WHITELABELS key not in MODELS.
    if whitelabels is not None:
        for wl_key in whitelabels:
            if wl_key not in models_set:
                raise RuntimeError(
                    f"package {pkg_name}: WHITELABELS key {wl_key!r} not in"
                    " MODELS"
                )

    return models


def _populate_display(
    mod: object, pkg_name: str, models: tuple[str, ...],
) -> None:
    """Populate _DISPLAY_INDEX: one entry per model, white-label aware."""
    from .catalog import DisplayMetadata

    manufacturer: str = getattr(mod, "MANUFACTURER", "Aqara")
    display_name: str | None = getattr(mod, "DISPLAY_NAME", None)
    whitelabels: dict | None = getattr(mod, "WHITELABELS", None)
    display_name_val = display_name or pkg_name.rsplit(".", 1)[-1]
    for m in models:
        model_display_name = display_name_val
        if whitelabels is not None and m in whitelabels:
            model_display_name = whitelabels[m]
        _DISPLAY_INDEX[m] = DisplayMetadata(
            manufacturer=manufacturer, display_name=model_display_name,
        )


def _populate_model_indices(
    mod: object, pkg_name: str, models: tuple[str, ...],
) -> None:
    """Populate the per-model noise/trait/allow-unauthored/endpoints/
    device-types/capabilities/dropped-paths indices for the package."""
    # v3d-6: per-package ALLOW_NOISE_TRAITS opt-in for trait noise filter.
    allow_noise_raw = getattr(mod, "ALLOW_NOISE_TRAITS", ())
    try:
        allow_noise_set = frozenset(int(t) for t in allow_noise_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"package {pkg_name}: ALLOW_NOISE_TRAITS must be an iterable"
            f" of ints (got {type(allow_noise_raw).__name__})"
        ) from exc
    for m in models:
        _NOISE_OVERRIDE_INDEX[m] = allow_noise_set

    # Populate both trait indices (flat + per-model), kept in sync by _put_trait.
    traits: dict | None = getattr(mod, "TRAITS", None)
    if traits is not None:
        for trait_id, spec in traits.items():
            for m in models:
                _put_trait(m, trait_id, spec)

    # ALLOW_UNAUTHORED_TRAITS: validated tuple[int, ...] for pidless synthesis.
    allow_unauthored: object = getattr(mod, "ALLOW_UNAUTHORED_TRAITS", None)
    if allow_unauthored is not None:
        if not isinstance(allow_unauthored, tuple):
            raise RuntimeError(
                f"package {pkg_name}: ALLOW_UNAUTHORED_TRAITS must be a tuple "
                f"(got {type(allow_unauthored).__name__})"
            )
        for trait_id in allow_unauthored:
            if not isinstance(trait_id, int):
                raise RuntimeError(
                    f"package {pkg_name}: ALLOW_UNAUTHORED_TRAITS contains "
                    f"non-int entry {trait_id!r}"
                )
        for m in models:
            _ALLOW_UNAUTHORED_INDEX[m] = allow_unauthored
    else:
        for m in models:
            _ALLOW_UNAUTHORED_INDEX[m] = ()

    # ENDPOINTS / DEVICE_TYPES / CAPABILITIES / DROPPED_PATHS: simple per-model
    # copies; a missing attribute leaves no entry (catalog returns the default).
    endpoints: object = getattr(mod, "ENDPOINTS", None)
    if endpoints is not None:
        for m in models:
            _ENDPOINTS_INDEX[m] = endpoints  # type: ignore[assignment]
    device_types: object = getattr(mod, "DEVICE_TYPES", None)
    if device_types is not None:
        for m in models:
            _DEVICE_TYPES_INDEX[m] = tuple(device_types)  # type: ignore[arg-type]
    capabilities: object = getattr(mod, "CAPABILITIES", None)
    if capabilities is not None:
        for m in models:
            _CAPABILITIES_INDEX[m] = dict(capabilities)  # type: ignore[arg-type]
    dropped_paths: object = getattr(mod, "DROPPED_PATHS", None)
    if dropped_paths is not None:
        for m in models:
            _DROPPED_PATHS_INDEX[m] = frozenset(dropped_paths)  # type: ignore[arg-type]
    dropped_rids: object = getattr(mod, "DROPPED_RIDS", None)
    if dropped_rids is not None:
        for m in models:
            _DROPPED_RIDS_INDEX[m] = dict(dropped_rids)  # type: ignore[arg-type]

    # SETTINGS / POWER_CLASS: rid-keyed device settings + power source. Per-model
    # copies; a missing attribute leaves no entry (catalog returns the default).
    settings: object = getattr(mod, "SETTINGS", None)
    if settings is not None:
        for m in models:
            _SETTINGS_INDEX[m] = dict(settings)  # type: ignore[arg-type]
    power_class: object = getattr(mod, "POWER_CLASS", None)
    if power_class is not None:
        for m in models:
            _POWER_CLASS_INDEX[m] = power_class  # type: ignore[assignment]

__all__ = [
    "_ALLOW_UNAUTHORED_INDEX",
    "_CAPABILITIES_INDEX",
    "_DEVICE_TYPES_INDEX",
    "_DISPLAY_INDEX",
    "_ENDPOINTS_INDEX",
    "_NOISE_OVERRIDE_INDEX",
    "_POWER_CLASS_INDEX",
    "_SETTINGS_INDEX",
    "_TRAITS_BY_MODEL",
    "_TRAIT_INDEX",
    "get_device_class",
    "known_models",
    "noise_override_for_model",
    "register_device",
    "reset_for_tests",
]
