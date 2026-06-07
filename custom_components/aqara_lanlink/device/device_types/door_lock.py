"""Composer for the DoorLock deviceType.

LockState -> ENUM sensor (full state) + BinarySensor(device_class=LOCK)
DoorState -> ENUM sensor (full state) + BinarySensor(device_class=DOOR)
RemoteUnlock -> Switch
StateOfLowBat -> BinarySensor(device_class=BATTERY, diagnostic)
Other DoorLock traits delegate to _fallback.

LockState and DoorState are multi-value enums (lock: NotFullyLocked/Locked/
Unlocked; door: Open/Closed/Jammed/ForcedOpen/Error/Ajar). A single binary
sensor both inverts polarity (default on_values={"1"} mismatched the LOCK/DOOR
device-class semantics) and discards the security-relevant states. So each emits
TWO entities: a full-fidelity ENUM sensor (the authoritative state, all values
preserved) plus a correctly-polarized convenience binary for simple automations.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, BinarySensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from . import _fallback
from ._base import ComposeContext, _ec
from ._build import build_descriptor

# trait_code -> (binary device_class, binary entity name, ON wire values).
# Keyed on the standardized DoorLock-cluster wire codes (stable across models;
# only the label *text* varies, e.g. "Unlocked" vs "UnLocked"), not on labels.
#   LOCK device_class -> on = unlocked / not-secure: NotFullyLocked(0) + Unlocked(2)
#     are ON; Locked(1) is OFF.
#   DOOR device_class -> on = open: Open(0) + ForcedOpen(3) + Ajar(5) are ON;
#     Closed(1) + Jammed(2) + UnspecifiedError(4) are OFF.
_DUAL_STATE_TRAITS: dict[str, tuple[BinarySensorDeviceClass, str, frozenset[str]]] = {
    "LockState": (BinarySensorDeviceClass.LOCK, "Lock", frozenset({"0", "2"})),
    "DoorState": (BinarySensorDeviceClass.DOOR, "Door", frozenset({"0", "3", "5"})),
}


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        if spec.function_code != "DoorLock":
            others[wp] = spec
            continue
        tc = spec.trait_code
        key = f"auto_{spec.id.replace('.', '_')}"
        if tc in _DUAL_STATE_TRAITS:
            device_class, binary_name, on_values = _DUAL_STATE_TRAITS[tc]
            # Full-fidelity ENUM sensor via the shared builder (device_class=ENUM,
            # options, wire->label transform). Keeps the clean auto_<wire_path> key.
            enum_sensor = build_descriptor(spec, "sensor")
            if enum_sensor is not None:
                out.append(enum_sensor)
            # Correctly-polarized convenience binary, suffixed key so its
            # unique_id differs from the enum sensor's.
            out.append(BinarySensorDescriptor(
                key=f"{key}_binary", name=binary_name, trait=spec,
                device_class=device_class, on_values=on_values,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        elif tc == "StateOfLowBat":
            out.append(BinarySensorDescriptor(
                key=key, name=spec.name, trait=spec,
                device_class=BinarySensorDeviceClass.BATTERY,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False,
            ))
        elif tc == "RemoteUnlock":
            out.append(SwitchDescriptor(
                key=key, name=spec.name,
                attr=AttrSpec(name=spec.id),
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
