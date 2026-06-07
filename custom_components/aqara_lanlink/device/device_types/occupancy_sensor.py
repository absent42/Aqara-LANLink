"""Composers for the OccupancySensor and MotionSensor deviceTypes.

Both emit a BinarySensorDescriptor for the OccupancySensing.Occupancy (or
.MotionDetected) trait; other traits on the endpoint (OccupancySensorType,
sensitivity, etc.) delegate to _fallback so config knobs surface too.

The deviceType disambiguates what Occupancy *means*:

  - OccupancySensor (FP-series presence sensors, multi-zone) -> the Occupancy
    trait is genuine presence -> device_class=occupancy.
  - MotionSensor (cameras' motion endpoint, standalone PIR motion sensors) ->
    the Occupancy trait IS motion detection (labelled "Motion" in the Aqara
    app; HumanRecognition is the occupancy-like signal on cameras) ->
    device_class=motion, name "Motion".

The catalogue already encodes the distinction in the endpoint deviceType; this
module just honours it.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, BinarySensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from . import _fallback
from ._base import DETECTOR_AUTO_CLEAR_SECONDS, ComposeContext, _ec

# The MotionSensor variant reuses the shared detector auto-clear window. Camera
# motion (and PIR motion) is a momentary pulse: the device fires "1" but does
# not reliably send "0", so the binary sensor needs a self-clear. auto_clear
# also marks the descriptor momentary, so it skips the stale-"on" restore on
# restart and -- on cameras -- the per-camera Detection clear delay number tunes
# it via resolve_auto_clear_seconds.


def _compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
    *,
    occupancy_device_class,
    occupancy_name: str | None,
    occupancy_auto_clear: float | None,
) -> list[AnyDescriptor]:
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        if spec.function_code == "OccupancySensing" and spec.trait_code == "Occupancy":
            out.append(_make_binary_sensor(
                spec, occupancy_device_class,
                name=occupancy_name, auto_clear_seconds=occupancy_auto_clear,
            ))
        elif spec.function_code == "OccupancySensing" and spec.trait_code == "MotionDetected":
            out.append(_make_binary_sensor(spec, BinarySensorDeviceClass.MOTION))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    """OccupancySensor deviceType: Occupancy -> device_class=occupancy (stateful)."""
    return _compose(
        endpoint_id, traits, context,
        occupancy_device_class=BinarySensorDeviceClass.OCCUPANCY,
        occupancy_name=None,
        occupancy_auto_clear=None,
    )


def motion_compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    """MotionSensor deviceType: Occupancy is really motion -> device_class=motion,
    name 'Motion', momentary with auto-clear."""
    return _compose(
        endpoint_id, traits, context,
        occupancy_device_class=BinarySensorDeviceClass.MOTION,
        occupancy_name="Motion",
        occupancy_auto_clear=DETECTOR_AUTO_CLEAR_SECONDS,
    )


def _make_binary_sensor(
    spec: TraitSpec, device_class, name: str | None = None,
    auto_clear_seconds: float | None = None,
) -> BinarySensorDescriptor:
    return BinarySensorDescriptor(
        key=f"auto_{spec.id.replace('.', '_')}",
        name=name or spec.name,
        trait=spec,
        device_class=device_class,
        on_values=frozenset({"1"}),
        auto_clear_seconds=auto_clear_seconds,
        entity_category=_ec(spec),
        entity_registry_enabled_default=spec.default_enabled,
    )
