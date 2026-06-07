"""Tests for the camera-detector composer family."""
from __future__ import annotations

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.aqara_lanlink.device.device_types import (
    _base, _detectors, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor, EventDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx(device_type: str) -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.camera.agl013")


def _trait(wp: str, fn: str, tc: str) -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp, function_code=fn, trait_code=tc,
        name=tc, data_type="int",
        readable=True, subscribable=True, endpoint_id=3,
    )


def _enum_trait(wp: str, fn: str, tc: str, enum: dict[str, str]) -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp, function_code=fn, trait_code=tc,
        name=tc, data_type="enum", enum_values=enum,
        readable=False, subscribable=True, endpoint_id=3,
    )


# ---------------------------------------------------------------------------
# Recognition-report family: BinarySensor with on_any_value + auto-clear.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "device_type,fn,tc,composer_name,expected_device_class",
    [
        ("HumanDetector", "HumanRecognition", "HumanRecognitionReport",
         "human_detector_compose", BinarySensorDeviceClass.OCCUPANCY),
        ("SmileDetector", "SmileRecognition", "SmileRecognitionReport",
         "smile_detector_compose", None),
    ],
)
def test_recognition_composer_emits_binary_sensor_with_auto_clear(
    device_type, fn, tc, composer_name, expected_device_class,
):
    """Recognition-report endpoints fold their *RecognitionReport trait
    into a momentary BinarySensor: on_any_value so any payload counts as
    a detection, plus a default auto-clear so the sensor returns to off
    when the firmware stops reporting.
    """
    composer = getattr(_detectors, composer_name)
    spec = _trait("3.216.20215", fn, tc)
    descs = composer(endpoint_id=3, traits={spec.id: spec}, context=_ctx(device_type))
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, BinarySensorDescriptor)
    assert d.trait.id == "3.216.20215"
    assert d.on_any_value is True, "recognition payloads vary; must accept any non-empty value"
    assert d.auto_clear_seconds == 60.0, "default 60s auto-clear so stale 'on' state self-recovers"
    assert d.device_class == expected_device_class


# ---------------------------------------------------------------------------
# Detection-boolean family: BinarySensor with explicit on_values, no auto-clear.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "device_type,fn,tc,composer_name,expected_device_class",
    [
        ("PackageDetector", "PackageRecognition", "PackageRecognitionReport",
         "package_detector_compose", None),
    ],
)
def test_detection_composer_emits_binary_sensor_without_auto_clear(
    device_type, fn, tc, composer_name, expected_device_class,
):
    """Detection-boolean endpoints fold their *Detected trait into a
    standard BinarySensor: on_values={"1"} matching firmware-driven
    state, no auto-clear (firmware emits explicit 0 when condition ends).
    """
    composer = getattr(_detectors, composer_name)
    spec = _trait("3.180.20100", fn, tc)
    descs = composer(endpoint_id=3, traits={spec.id: spec}, context=_ctx(device_type))
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, BinarySensorDescriptor)
    assert d.on_any_value is False
    assert d.on_values == frozenset({"1"})
    assert d.auto_clear_seconds is None
    assert d.device_class == expected_device_class


# ---------------------------------------------------------------------------
# Enum-recognition family: Event with one event_type per enum label plus a
# catch-all fallback type, carrying the raw code as payload.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "device_type,fn,tc,composer_name,enum,expected_types",
    [
        ("PetMonitor", "PetRecognition", "PetRecognitionReport",
         "pet_monitor_compose",
         {"1": "Cat detected", "2": "Dog detected", "3": "Cat or dog detected"},
         ("Cat detected", "Dog detected", "Cat or dog detected", "Unknown")),
        ("GestureSensor", "GestureRecognition", "GestureRecognitionReport",
         "gesture_sensor_compose", {"2": "Two", "5": "Five", "104": "Eight both hands"},
         ("Two", "Five", "Eight both hands", "Unknown")),
        ("SoundSensor", "SoundDetection", "AnomalySoundDetected",
         "sound_sensor_compose", {"1": "Cry detected"},
         ("Cry detected", "Unknown")),
        ("VehicleDetector", "VehicleDetection", "VehicleDetected",
         "vehicle_detector_compose", {"1": "Vehicle detected"},
         ("Vehicle detected", "Unknown")),
        ("FireAlarm", "FlameDetection", "FlameDetected",
         "fire_alarm_compose", {"1": "Flame detected"},
         ("Flame detected", "Unknown")),
    ],
)
def test_enum_recognition_composer_emits_event(
    device_type, fn, tc, composer_name, enum, expected_types,
):
    """Enum-valued recognition reports are momentary classifications, not
    persistent on/off state: model them as an Event whose event_types are
    the (already-humanized) enum labels from the catalogue plus an
    'Unknown' catch-all, with the raw wire code forwarded as payload.

    Humanization happens at catalogue-generation time, so the composer
    passes the labels through verbatim -- no runtime re-tokenization."""
    composer = getattr(_detectors, composer_name)
    spec = _enum_trait("3.218.20216", fn, tc, enum)
    descs = composer(endpoint_id=3, traits={spec.id: spec}, context=_ctx(device_type))
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, EventDescriptor)
    assert d.trigger_trait.id == "3.218.20216"
    assert d.event_types == expected_types
    assert d.unknown_event_type == "Unknown"
    assert d.value_payload_key == "code"
    # The trigger trait's enum labels reach the descriptor unchanged, so
    # apply_value maps a wire value straight to the event_type it fires.
    assert d.trigger_trait.enum_values == enum


def test_face_scanner_emits_id_event_with_payload():
    """FaceRecognition.FaceIDReport carries a registered-face id (an int that
    can exceed 2**53), emitted on every match. Model it as a single-type Event
    that forwards the id as a string payload so per-face automations work and
    repeat detections of the same face each fire."""
    composer = _detectors.face_scanner_compose
    spec = _trait("4.219.20217", "FaceRecognition", "FaceIDReport")
    descs = composer(endpoint_id=3, traits={spec.id: spec}, context=_ctx("FaceScanner"))
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, EventDescriptor)
    assert d.trigger_trait.id == "4.219.20217"
    assert d.event_types == ("Detected",)  # humanized, consistent with enum events
    assert d.value_payload_key == "face_id"
    assert d.unknown_event_type is None


# ---------------------------------------------------------------------------
# Dispatch registration -- every detector deviceType must be registered.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "device_type",
    [
        "HumanDetector", "FaceScanner", "PetMonitor", "PackageDetector",
        "GestureSensor", "SmileDetector", "VehicleDetector", "SoundSensor",
        "FireAlarm", "MotionSensor",
    ],
)
def test_detector_devicetype_registered(device_type):
    """The classify_v3 dispatch table MUST have an entry for every detector
    deviceType in the family. Missing entries surface as runtime WARNINGs
    from classify_v3 and devices route through per-trait _fallback,
    losing the binary-sensor consolidation.
    """
    from custom_components.aqara_lanlink.device.device_types import _fallback
    composer = get_composer(device_type)
    assert composer is not _fallback.compose, (
        f"{device_type} falls back to per-trait classification; "
        f"register a composer in _COMPOSERS"
    )


def test_motion_sensor_uses_motion_variant_of_occupancy_composer():
    """MotionSensor endpoints (camera motion ep5, standalone PIR sensors) carry
    OccupancySensing.Occupancy, but on this deviceType that trait IS motion
    detection -- so it routes to the motion variant (device_class=motion,
    name 'Motion'), NOT the plain occupancy composer."""
    from custom_components.aqara_lanlink.device.device_types import occupancy_sensor
    assert get_composer("MotionSensor") is occupancy_sensor.motion_compose


def test_video_doorbell_routes_to_fallback():
    """VideoDoorbell endpoints (e.g. agl013 ep 8) carry only Camera.* traits
    (RTSP URL, P2P enables, capture status) -- identical shape to a plain
    Camera endpoint. Both route straight to _fallback."""
    from custom_components.aqara_lanlink.device.device_types import _fallback
    assert get_composer("VideoDoorbell") is _fallback.compose


# ---------------------------------------------------------------------------
# Other traits on a detector endpoint should fall through to _fallback.
# ---------------------------------------------------------------------------

def test_extra_traits_on_endpoint_delegate_to_fallback():
    """Captured V3 specs show some detector endpoints also carry
    EndpointLabel.* traits (EndpointName/Icon/etc.). Those should fall
    through to per-trait classification, not be silently dropped."""
    report = _trait("3.216.20215", "HumanRecognition", "HumanRecognitionReport")
    config = TraitSpec(
        id="3.130.32913", wire_path="3.130.32913",
        function_code="EndpointLabel", trait_code="EndpointName",
        name="EndpointName", data_type="string",
        readable=True, subscribable=True, endpoint_id=3,
    )
    descs = _detectors.human_detector_compose(
        endpoint_id=3,
        traits={report.id: report, config.id: config},
        context=_ctx("HumanDetector"),
    )
    # 1 BinarySensor for the report + at least one descriptor for the config
    # trait (fallback produces a SensorDescriptor for read-only strings).
    binary = [d for d in descs if isinstance(d, BinarySensorDescriptor)]
    assert len(binary) == 1
    assert len(descs) >= 2, (
        "EndpointLabel.EndpointName must survive as a fallback descriptor"
    )
