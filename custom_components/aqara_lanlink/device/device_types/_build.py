"""Single descriptor constructor: (TraitSpec, platform) -> descriptor.

The one place that turns a trait plus a platform string into a descriptor and
carries the trait's decoration fields onto it. Two callers:

  - _fallback: infers the platform from data_type/writable, then delegates here.
  - classify_v3's override pre-pass: passes the trait's EXPLICIT spec.platform,
    making a model overrides.py classification authoritative over composers.

Key convention matches _fallback (auto_<wire_path>) so entity_ids are stable
whether a trait is auto-classified or force-classified via an override.
"""
from __future__ import annotations

import logging

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, BinarySensorDescriptor, ButtonDescriptor, EventDescriptor,
    NumberDescriptor, SelectDescriptor, SensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.sensor import SensorDeviceClass

from ._base import _default_precision, _ec

_LOGGER = logging.getLogger(__name__)


def build_descriptor(spec: TraitSpec, platform: str) -> AnyDescriptor | None:
    """Build the descriptor for `spec` at `platform`, carrying decoration.

    Returns None when the platform is unknown or the trait lacks data the
    descriptor requires (e.g. a select with no enum_values); the caller logs
    and skips. The trait's __post_init__ has already rejected illegal field
    combinations (state_class without sensor, auto_clear without binary_sensor,
    etc.), so this builder can trust those invariants.
    """
    # Key + attr binding MUST use the wire_path -- it is the report-routing key
    # and the convention _fallback uses (auto_<wire_path>). spec.id CAN differ
    # from spec.wire_path (propertyId vs wire path); keying off id would silently
    # change entity_ids on upgrade. Catalogue traits set both equal; fall back to
    # id only for hand-authored specs lacking a wire_path.
    wire_path = spec.wire_path or spec.id
    key = f"auto_{wire_path.replace('.', '_')}"
    ec = _ec(spec)
    enabled = spec.default_enabled
    unit = spec.unit_of_measurement or spec.unit

    if platform == "sensor":
        # A read-only enum trait renders as an ENUM sensor whose state is the
        # human label, not the raw wire value. transform_in maps the wire value
        # to its label (unmapped values pass through verbatim so undocumented
        # codes stay visible). ENUM sensors cannot carry unit/state_class/
        # precision/scale, so the enum branch omits them.
        if spec.enum_values:
            labels = dict(spec.enum_values)
            # options is stored as a tuple, not a list: the descriptor is used
            # as a dict key in Device._entities_by_descriptor, so a mutable
            # (unhashable) field would crash entity registration. AqaraSensor
            # converts it back to the list HA expects via _attr_options.
            return SensorDescriptor(
                key=key, name=spec.name, trait=spec,
                device_class=SensorDeviceClass.ENUM,
                options=tuple(labels.values()),
                transform_in=lambda raw, _m=labels: _m.get(raw, raw),
                icon=spec.icon,
                entity_category=ec, entity_registry_enabled_default=enabled,
            )
        precision = (
            spec.suggested_display_precision
            if spec.suggested_display_precision is not None
            else _default_precision(spec.device_class, spec.data_type)
        )
        return SensorDescriptor(
            key=key, name=spec.name, trait=spec,
            native_unit_of_measurement=unit,
            device_class=spec.device_class,
            state_class=spec.state_class,
            suggested_display_precision=precision,
            scale=spec.scale,
            icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "binary_sensor":
        return BinarySensorDescriptor(
            key=key, name=spec.name, trait=spec,
            device_class=spec.device_class,
            icon=spec.icon,
            auto_clear_seconds=spec.auto_clear_seconds,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "event":
        labels = tuple((spec.enum_values or {}).values())
        return EventDescriptor(
            key=key, name=spec.name, trigger_trait=spec,
            event_types=labels or ("triggered",),
            device_class=spec.device_class,
            icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "select":
        if not spec.enum_values:
            _LOGGER.warning(
                "build_descriptor: select override on %s has no enum_values; skipped",
                spec.id,
            )
            return None
        options_map = tuple(
            (label, wire) for wire, label in spec.enum_values.items()
        )
        return SelectDescriptor(
            key=key, name=spec.name, attr=AttrSpec(name=wire_path),
            options_map=options_map, icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "number":
        return NumberDescriptor(
            key=key, name=spec.name, attr=AttrSpec(name=wire_path),
            min_value=spec.min_value if spec.min_value is not None else 0,
            max_value=spec.max_value if spec.max_value is not None else 100,
            step=spec.step or 1,
            native_unit_of_measurement=unit,
            device_class=spec.device_class,
            scale=spec.scale,
            icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "switch":
        return SwitchDescriptor(
            key=key, name=spec.name, attr=AttrSpec(name=wire_path),
            device_class=spec.device_class, icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    if platform == "button":
        return ButtonDescriptor(
            key=key, name=spec.name, attr=AttrSpec(name=wire_path),
            icon=spec.icon,
            entity_category=ec, entity_registry_enabled_default=enabled,
        )
    _LOGGER.warning(
        "build_descriptor: unknown platform=%r for %s; skipped", platform, spec.id,
    )
    return None
