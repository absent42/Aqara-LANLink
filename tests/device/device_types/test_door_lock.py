"""Tests for the DoorLock deviceType composer."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.aqara_lanlink.device.device_types import (
    _base, door_lock, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor, SensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="aqara.lock.akr011")


def _lock_state() -> TraitSpec:
    # Real catalogue wire semantics: 0=NotFullyLocked, 1=Locked, 2=Unlocked.
    return TraitSpec(
        id="2.165.33020", wire_path="2.165.33020",
        function_code="DoorLock", trait_code="LockState",
        name="Lock state", data_type="enum",
        enum_values={"0": "NotFullyLocked", "1": "Locked", "2": "Unlocked"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def _door_state() -> TraitSpec:
    return TraitSpec(
        id="2.165.33021", wire_path="2.165.33021",
        function_code="DoorLock", trait_code="DoorState",
        name="Door state", data_type="enum",
        enum_values={
            "0": "DoorOpen", "1": "DoorClosed", "2": "DoorJammed",
            "3": "DoorForcedOpen", "4": "DoorUnspecifiedError", "5": "DoorAjar",
        },
        readable=True, subscribable=True, endpoint_id=2,
    )


def _remote_unlock() -> TraitSpec:
    return TraitSpec(
        id="2.165.33030", wire_path="2.165.33030",
        function_code="DoorLock", trait_code="RemoteUnlock",
        name="RemoteUnlock", data_type="bool",
        readable=False, writable=True, endpoint_id=2,
    )


def _low_bat() -> TraitSpec:
    return TraitSpec(
        id="2.165.33099", wire_path="2.165.33099",
        function_code="DoorLock", trait_code="StateOfLowBat",
        name="StateOfLowBat", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _split(descs):
    """Partition descriptors by type for assertions."""
    return (
        [d for d in descs if isinstance(d, SensorDescriptor)],
        [d for d in descs if isinstance(d, BinarySensorDescriptor)],
        [d for d in descs if isinstance(d, SwitchDescriptor)],
    )


def test_lock_state_emits_enum_sensor_and_lock_binary():
    descs = door_lock.compose(endpoint_id=2, traits={"2.165.33020": _lock_state()}, context=_ctx())
    sensors, binaries, _ = _split(descs)
    assert len(sensors) == 1 and len(binaries) == 1

    # Full-fidelity enum sensor: shows the actual state text, no loss.
    s = sensors[0]
    assert s.device_class == SensorDeviceClass.ENUM
    assert s.options == ("NotFullyLocked", "Locked", "Unlocked")
    assert s.transform_in("1") == "Locked"

    # Correctly-polarized convenience binary (LOCK: on = unlocked/not-secure).
    b = binaries[0]
    assert b.device_class == BinarySensorDeviceClass.LOCK
    assert b.on_values == frozenset({"0", "2"})  # Unlocked + NotFullyLocked
    assert "1" not in b.on_values               # Locked must read OFF (secure)
    assert b.key != s.key                        # distinct unique_ids


def test_door_state_emits_enum_sensor_and_door_binary():
    descs = door_lock.compose(endpoint_id=2, traits={"2.165.33021": _door_state()}, context=_ctx())
    sensors, binaries, _ = _split(descs)
    assert len(sensors) == 1 and len(binaries) == 1

    s = sensors[0]
    assert s.device_class == SensorDeviceClass.ENUM
    assert s.transform_in("2") == "DoorJammed"   # security states preserved
    assert s.transform_in("5") == "DoorAjar"

    b = binaries[0]
    assert b.device_class == BinarySensorDeviceClass.DOOR
    # DOOR: on = open. Open(0)/ForcedOpen(3)/Ajar(5) -> on; Closed(1)/Jammed(2)/Error(4) -> off.
    assert b.on_values == frozenset({"0", "3", "5"})
    assert "1" not in b.on_values                # Closed must read OFF


def test_remote_unlock_becomes_switch():
    descs = door_lock.compose(endpoint_id=2, traits={"2.165.33030": _remote_unlock()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SwitchDescriptor)


def test_low_bat_is_diagnostic_battery_sensor():
    descs = door_lock.compose(endpoint_id=2, traits={"2.165.33099": _low_bat()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, BinarySensorDescriptor)
    assert d.device_class == BinarySensorDeviceClass.BATTERY
    assert d.entity_category == EntityCategory.DIAGNOSTIC


def test_full_lock_set():
    traits = {t.id: t for t in (_lock_state(), _door_state(), _remote_unlock(), _low_bat())}
    descs = door_lock.compose(endpoint_id=2, traits=traits, context=_ctx())
    # LockState (sensor+binary) + DoorState (sensor+binary) + RemoteUnlock (switch)
    # + StateOfLowBat (binary) = 6
    assert len(descs) == 6


def test_door_lock_composer_registered():
    assert get_composer("DoorLock") is door_lock.compose
