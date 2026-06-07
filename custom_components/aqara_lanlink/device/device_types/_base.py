"""Shared types and helpers for V3 per-deviceType composers.

Each composer module exports a single function:

    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        ...

Composers do not import each other; cross-endpoint composition is handled
by classify_v3, not by composers calling siblings.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from custom_components.aqara_lanlink.device.descriptors import AnyDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec

# Default momentary auto-clear window (seconds) for detection/recognition
# binary sensors: marks the descriptor momentary so a stale "on" self-recovers
# if the firmware stops reporting. Shared by the occupancy/motion composer and
# the camera-detector family.
DETECTOR_AUTO_CLEAR_SECONDS: float = 60.0


@dataclass(frozen=True)
class ComposeContext:
    """Read-only context passed to every per-deviceType composer.

    Only ``model`` is consumed by composers today (e.g. the light.py warning);
    kept as a dataclass so per-composer context can grow again without having
    to touch every composer signature.
    """

    model: str


Composer = Callable[
    [int, dict[str, TraitSpec], ComposeContext],
    list[AnyDescriptor],
]


def find_trait(
    traits: dict[str, TraitSpec],
    function_code: str,
    trait_code: str,
) -> TraitSpec | None:
    """Locate a TraitSpec by (function_code, trait_code) within an endpoint's
    trait dict. Returns None if not present."""
    for spec in traits.values():
        if spec.function_code == function_code and spec.trait_code == trait_code:
            return spec
    return None


def _ec(spec: TraitSpec):
    """Map TraitSpec.entity_category ("diagnostic" or None) to the HA enum.

    Hoisted here from _fallback.py because every per-deviceType composer (not
    just _fallback) needs to convert the string field to the HA enum value
    when constructing descriptors.
    """
    if spec.entity_category == "diagnostic":
        from homeassistant.helpers.entity import EntityCategory
        return EntityCategory.DIAGNOSTIC
    return None


# `suggested_display_precision` defaults per device_class. HA renders the
# entity's numeric state with this many decimals by default; users can
# still override per-entity via Settings -> Devices -> entity -> Configure.
# Values reflect Aqara firmware reporting conventions and common HA usage
# (humidity/temperature show 1 dp, battery shows whole percent, energy
# captures sub-Wh resolution at 3 dp).
_PRECISION_BY_DEVICE_CLASS: dict[str, int] = {
    "atmospheric_pressure": 1,
    "battery": 0,
    "carbon_dioxide": 0,
    "current": 2,
    "energy": 3,
    "humidity": 1,
    "illuminance": 0,
    "power": 1,
    "pressure": 1,
    "signal_strength": 0,
    "temperature": 1,
    "voltage": 1,
    "volatile_organic_compounds": 0,
}


def _default_precision(
    device_class, data_type: str | None = None,
) -> int | None:
    """Pick `suggested_display_precision` for the given device_class.

    Falls back to 0 for integer-typed traits (so a whole-valued int doesn't
    render as ``87.0``), and to None for floats with no device_class match
    -- HA then renders the float at its native precision.

    ``device_class`` accepts an HA enum member (``SensorDeviceClass.HUMIDITY``)
    or a bare string (``"humidity"``); enums are unwrapped via ``.value``.
    """
    if device_class is not None:
        key = getattr(device_class, "value", device_class)
        if isinstance(key, str) and key in _PRECISION_BY_DEVICE_CLASS:
            return _PRECISION_BY_DEVICE_CLASS[key]
    if data_type == "int":
        return 0
    return None


def make_single_trait_composer(
    *,
    function_code: str,
    trait_code: str,
    descriptor_factory: Callable[[TraitSpec], AnyDescriptor],
) -> Composer:
    """Return a composer that folds the single (function_code, trait_code) trait
    into a descriptor built by ``descriptor_factory`` and delegates every other
    trait on the endpoint to _fallback.

    The simple single-trait deviceType composers (temperature, humidity,
    pressure, illuminance, ...) differ only in the match key and the descriptor
    they emit, so they are all built from this factory.
    """

    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        # Local import: _fallback imports _base, so importing it at module
        # scope would create a cycle.
        from . import _fallback

        out: list[AnyDescriptor] = []
        others: dict[str, TraitSpec] = {}
        for wp, spec in traits.items():
            if spec.function_code == function_code and spec.trait_code == trait_code:
                out.append(descriptor_factory(spec))
            else:
                others[wp] = spec
        out.extend(_fallback.compose(endpoint_id, others, context))
        return out

    return compose


def measurement_sensor(
    spec: TraitSpec,
    device_class,
    default_unit: str,
    *,
    use_spec_unit: bool = True,
) -> AnyDescriptor:
    """Build a MEASUREMENT SensorDescriptor for a single measured trait.

    ``use_spec_unit=False`` forces ``default_unit`` even when the trait declares
    its own unit (illuminance is always lux).
    """
    from homeassistant.components.sensor import SensorStateClass

    from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor

    unit = (spec.unit or default_unit) if use_spec_unit else default_unit
    return SensorDescriptor(
        key=f"auto_{spec.id.replace('.', '_')}",
        name=spec.name,
        trait=spec,
        device_class=device_class,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=unit,
        suggested_display_precision=_default_precision(device_class, spec.data_type),
        entity_category=_ec(spec),
        entity_registry_enabled_default=spec.default_enabled,
    )
