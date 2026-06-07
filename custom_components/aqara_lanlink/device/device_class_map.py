"""Static lookup: Aqara cloud-side device-type -> HA platform + device_class.

The cloud `collection/panels` response carries a `deviceTypes` string for
each endpoint (e.g. "OccupancySensor", "TemperatureSensor"). When the
synth pass produces a descriptor on that endpoint AND the descriptor's
kind matches the HA platform the cloud-side type implies, this module
returns the appropriate HA device_class string.

The mapping is deliberately conservative: only Aqara types observed in
captured traffic are listed. Unknown types return None (no device_class,
descriptor still synthesises).

VibrationSensor maps to event (per Z2M convention): vibration sensors
fire knock/drop/tilt events as their primary interaction, not a boolean
on/off state.

Hub is intentionally omitted: hubs are the integration's parent device,
not synthesisable entities.
"""
from __future__ import annotations


AQARA_DEVICE_TYPE_TO_HA: dict[str, tuple[str, str | None]] = {
    "OccupancySensor":   ("binary_sensor", "occupancy"),
    "MotionSensor":      ("binary_sensor", "motion"),
    "TemperatureSensor": ("sensor", "temperature"),
    "HumiditySensor":    ("sensor", "humidity"),
    "IlluminanceSensor": ("sensor", "illuminance"),
    "VibrationSensor":   ("event", None),
    "Button":            ("event", None),
    "Light":             ("light", None),
    "Camera":            ("camera", None),
}


def device_class_for(aqara_type: str, master_kind: str) -> str | None:
    """Return the HA device_class string, or None.

    Returns None when:
      - aqara_type is not in AQARA_DEVICE_TYPE_TO_HA, or
      - master_kind disagrees with the platform the table specifies, or
      - the table entry's device_class is None (entries with no
        applicable HA class -- Button/Light/Camera/VibrationSensor).

    Defensive by design: never claims a device_class for a descriptor
    kind that won't accept it.
    """
    entry = AQARA_DEVICE_TYPE_TO_HA.get(aqara_type)
    if entry is None:
        return None
    platform, device_class = entry
    if master_kind != platform:
        return None
    return device_class


__all__ = ["AQARA_DEVICE_TYPE_TO_HA", "device_class_for"]
