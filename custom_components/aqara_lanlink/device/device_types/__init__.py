"""Per-deviceType dispatcher.

Routes a V3 deviceType string to the corresponding composer module's
compose() function. Unknown deviceTypes route to _fallback.compose.
"""
from __future__ import annotations

from . import _detectors, _fallback, button, contact_sensor, cube, door_lock, doorbell, electrical_sensor, humidity_sensor, illuminance_sensor, knob, light, occupancy_sensor, pressure_sensor, speaker, switch, temperature_sensor, thermostat, vibration_sensor, window_covering
from ._base import Composer, ComposeContext


_COMPOSERS: dict[str, Composer] = {
    "Button": button.compose,
    # Camera streaming/snapshot is handled by the dedicated camera entity setup,
    # not the descriptor classifier; V3 Camera endpoints carry only meta/
    # diagnostic traits, so delegate them straight to _fallback.
    "Camera": _fallback.compose,
    "ContactSensor": contact_sensor.compose,
    "Cube": cube.compose,
    "DoorLock": door_lock.compose,
    "Doorbell": doorbell.compose,
    "ElectricalSensor": electrical_sensor.compose,
    # Hub endpoint 1: surviving traits are LQI + GeneralDiagnostics (diagnostic);
    # _fallback's data_type dispatch produces the right output.
    "Hub": _fallback.compose,
    "HumiditySensor": humidity_sensor.compose,
    "IlluminanceSensor": illuminance_sensor.compose,
    "Knob": knob.compose,
    # NOTE: fuses multiple traits into one entity -> listed in classify_v3._FUSED_DEVICE_TYPES
    "Light": light.compose,
    "OccupancySensor": occupancy_sensor.compose,
    # Catalogue uses "AtmosphericPressureSensor"; "PressureSensor" kept as
    # alias in case future devices declare the shorter name.
    "AtmosphericPressureSensor": pressure_sensor.compose,
    "PressureSensor": pressure_sensor.compose,
    # Public (endpoint 1 of nearly every device): cross-cutting endpoint
    # metadata (Identify, PowerSource, network diagnostics). No Public-specific
    # logic; _fallback dispatches each trait on data_type.
    "Public": _fallback.compose,
    # Root (endpoint 0): diagnostic metadata (FirmwareRevisionString, Mac,
    # HardwareVersion, Reachable) + Identify cluster, all handled by _fallback.
    "Root": _fallback.compose,
    "Speaker": speaker.compose,
    "Switch": switch.compose,
    # Multi-outlet plug strips (e.g. lumi.plug.aeu002 endpoints 2/3/4) declare
    # each socket as "Outlet". Trait surface per Outlet endpoint is identical
    # to a Switch endpoint -- just Output.OnOff -- so alias to the Switch
    # composer. Energy-metering traits sit on a separate Public endpoint.
    "Outlet": switch.compose,
    "TemperatureSensor": temperature_sensor.compose,
    "Thermostat": thermostat.compose,
    "VibrationSensor": vibration_sensor.compose,
    "WindowCovering": window_covering.compose,
    # AC endpoints (e.g. lumi.gateway.agl004 ep 3) carry HeaterCooler.* +
    # FanControl.* + Output.OnOff. HeaterCooler handling lives in the
    # thermostat composer (it now covers CoolingTemperature + CurrentHumidity
    # on top of the original Thermostat trait set); Output.OnOff -> Switch,
    # FanControl.* -> Select via _fallback.
    "AirConditioner": thermostat.compose,
    # IR-blaster endpoints (e.g. lumi.gateway.agl004 ep 4) carry only
    # IRControl.IRType + IRControl.IRKey enums. _fallback turns each into
    # a writable Select -- no structural fusion required, but registering
    # it explicitly suppresses the unknown-deviceType WARNING.
    "IRDevice": _fallback.compose,
    # Camera detector family. Each declares its own endpoint on multi-feature
    # cameras (G400, G350, G410, etc.); composers absorb the one matching
    # recognition/detection trait per endpoint into a binary sensor.
    "HumanDetector": _detectors.human_detector_compose,
    "FaceScanner": _detectors.face_scanner_compose,
    "PetMonitor": _detectors.pet_monitor_compose,
    "PackageDetector": _detectors.package_detector_compose,
    "GestureSensor": _detectors.gesture_sensor_compose,
    "SmileDetector": _detectors.smile_detector_compose,
    "VehicleDetector": _detectors.vehicle_detector_compose,
    "SoundSensor": _detectors.sound_sensor_compose,
    "FireAlarm": _detectors.fire_alarm_compose,
    # MotionSensor (cameras' motion endpoint, standalone PIR sensors) carries
    # OccupancySensing.Occupancy, but on this deviceType that trait IS motion
    # detection (the Aqara app labels it "Motion"; HumanRecognition is the
    # occupancy-like signal). Route to the motion variant -> device_class=motion.
    "MotionSensor": occupancy_sensor.motion_compose,
    # VideoDoorbell carries Camera.* traits identical to the Camera deviceType
    # (RTSP URL, P2P toggles, capture status), all delegated to _fallback like
    # Camera; the actual doorbell ring event lives on a separate Doorbell
    # endpoint.
    "VideoDoorbell": _fallback.compose,
}


def get_composer(device_type: str) -> Composer:
    """Return the composer for `device_type`, falling back to _fallback.compose
    for unknown deviceType strings."""
    return _COMPOSERS.get(device_type, _fallback.compose)


__all__ = ["ComposeContext", "Composer", "get_composer"]
