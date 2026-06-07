"""Composer factory for the camera-detector deviceType family.

V3 catalogue declares a dedicated endpoint per detector kind on cameras
(e.g. HumanDetector ep 3, MotionSensor ep 5, VideoDoorbell ep 8 on the
G400). Each carries one detection-style trait plus assorted EndpointLabel
metadata.

Two trait shapes, two factories:

  - Recognition reports ("XxxRecognition.XxxRecognitionReport"): the
    firmware emits a payload code (person ID, classification index, etc.)
    each time the AI re-detects the subject. Modelled as a momentary
    BinarySensor with on_any_value=True and a default auto-clear of 60s
    so the sensor returns to 'off' after detections stop.

  - Detection booleans ("XxxDetection.XxxDetected"): the firmware
    toggles a 0/1 state. Modelled as a standard BinarySensor with
    on_values={"1"} and no auto-clear (the firmware drives the state).

Other traits on the endpoint (EndpointLabel.*, sensitivity knobs, etc.)
delegate to _fallback so config knobs surface as config entities.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, BinarySensorDescriptor, EventDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from . import _fallback
from ._base import DETECTOR_AUTO_CLEAR_SECONDS, ComposeContext, Composer, _ec


def make_recognition_composer(
    *,
    function_code: str,
    trait_code: str,
    device_class: BinarySensorDeviceClass | None = None,
    auto_clear_seconds: float = DETECTOR_AUTO_CLEAR_SECONDS,
) -> Composer:
    """Build a composer that absorbs one recognition-report trait into a
    momentary BinarySensor with auto-clear.

    The composer matches by (function_code, trait_code); any other trait
    on the endpoint delegates to _fallback.
    """
    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        out: list[AnyDescriptor] = []
        others: dict[str, TraitSpec] = {}
        for wp, spec in traits.items():
            if spec.function_code == function_code and spec.trait_code == trait_code:
                out.append(BinarySensorDescriptor(
                    key=f"auto_{spec.id.replace('.', '_')}",
                    name=spec.name,
                    trait=spec,
                    device_class=device_class,
                    on_any_value=True,
                    auto_clear_seconds=auto_clear_seconds,
                    entity_category=_ec(spec),
                    entity_registry_enabled_default=spec.default_enabled,
                ))
            else:
                others[wp] = spec
        out.extend(_fallback.compose(endpoint_id, others, context))
        return out
    return compose


def make_detection_composer(
    *,
    function_code: str,
    trait_code: str,
    device_class: BinarySensorDeviceClass | None = None,
) -> Composer:
    """Build a composer that absorbs one detection-boolean trait into a
    standard BinarySensor (no auto-clear; firmware drives state)."""
    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        out: list[AnyDescriptor] = []
        others: dict[str, TraitSpec] = {}
        for wp, spec in traits.items():
            if spec.function_code == function_code and spec.trait_code == trait_code:
                out.append(BinarySensorDescriptor(
                    key=f"auto_{spec.id.replace('.', '_')}",
                    name=spec.name,
                    trait=spec,
                    device_class=device_class,
                    on_values=frozenset({"1"}),
                    entity_category=_ec(spec),
                    entity_registry_enabled_default=spec.default_enabled,
                ))
            else:
                others[wp] = spec
        out.extend(_fallback.compose(endpoint_id, others, context))
        return out
    return compose


# Catch-all event_type appended to enum-recognition events. The cloud
# catalogue under-declares these enums (e.g. SoundDetection ships only
# CryDetected while the firmware emits several more), so an unmapped wire
# value fires this type -- carrying the raw code as payload -- instead of
# being dropped. See event.AqaraEvent.apply_value.
_UNKNOWN_EVENT_TYPE: str = "Unknown"


def make_enum_event_composer(
    *,
    function_code: str,
    trait_code: str,
) -> Composer:
    """Build a composer that turns one enum-valued recognition-report trait
    into an Event.

    The report is a momentary classification (which pet, which gesture, which
    sound), not a persistent on/off state, so an Event preserves *what* was
    recognised where a BinarySensor would collapse it to "something happened".
    event_types are the trait's enum labels plus a catch-all; the raw wire code
    rides along as payload under "code". Any other trait on the endpoint
    delegates to _fallback.

    The labels are already humanized at catalogue-generation time, so they are
    used verbatim as the event_types -- no runtime re-tokenization. event
    .apply_value maps a wire value to its label via the trait's enum_values.
    """
    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        out: list[AnyDescriptor] = []
        others: dict[str, TraitSpec] = {}
        for wp, spec in traits.items():
            if spec.function_code == function_code and spec.trait_code == trait_code:
                labels = tuple((spec.enum_values or {}).values())
                out.append(EventDescriptor(
                    key=f"auto_{spec.id.replace('.', '_')}",
                    name=spec.name,
                    trigger_trait=spec,
                    event_types=labels + (_UNKNOWN_EVENT_TYPE,),
                    unknown_event_type=_UNKNOWN_EVENT_TYPE,
                    value_payload_key="code",
                    entity_category=_ec(spec),
                    entity_registry_enabled_default=spec.default_enabled,
                ))
            else:
                others[wp] = spec
        out.extend(_fallback.compose(endpoint_id, others, context))
        return out
    return compose


def make_id_event_composer(
    *,
    function_code: str,
    trait_code: str,
    payload_key: str,
    event_type: str = "Detected",
) -> Composer:
    """Build a composer that turns one id-style recognition-report trait into
    a single-type Event carrying the raw id as a string payload.

    Used for reports whose value is an unbounded identifier rather than a
    small enum (canonical case: FaceRecognition.FaceIDReport, a registered-
    face index). The id can exceed 2**53, so it is forwarded as the raw
    string. The single event_type fires on every report -- including repeat
    detections of the same subject -- which a sticky sensor could not. Any
    other trait on the endpoint delegates to _fallback.
    """
    def compose(
        endpoint_id: int,
        traits: dict[str, TraitSpec],
        context: ComposeContext,
    ) -> list[AnyDescriptor]:
        out: list[AnyDescriptor] = []
        others: dict[str, TraitSpec] = {}
        for wp, spec in traits.items():
            if spec.function_code == function_code and spec.trait_code == trait_code:
                out.append(EventDescriptor(
                    key=f"auto_{spec.id.replace('.', '_')}",
                    name=spec.name,
                    trigger_trait=spec,
                    event_types=(event_type,),
                    value_payload_key=payload_key,
                    entity_category=_ec(spec),
                    entity_registry_enabled_default=spec.default_enabled,
                ))
            else:
                others[wp] = spec
        out.extend(_fallback.compose(endpoint_id, others, context))
        return out
    return compose


# =====================================================================
# Pre-built composers per camera detector deviceType.
# Add new entries here as deviceTypes are discovered (e.g. SmileDetector
# on the G350 is anticipated to follow SmileRecognition.SmileRecognitionReport).
# =====================================================================

human_detector_compose = make_recognition_composer(
    function_code="HumanRecognition",
    trait_code="HumanRecognitionReport",
    device_class=BinarySensorDeviceClass.OCCUPANCY,
)

# FaceIDReport carries a registered-face id (int, can exceed 2**53), not a
# detection flag -- model as a single-type Event forwarding the id as payload.
face_scanner_compose = make_id_event_composer(
    function_code="FaceRecognition",
    trait_code="FaceIDReport",
    payload_key="face_id",
)

# Pet / Gesture / Sound reports are momentary enum classifications -- model as
# Events whose event_types are the enum labels (+ an 'unknown' catch-all),
# preserving *what* was recognised rather than collapsing to on/off.
pet_monitor_compose = make_enum_event_composer(
    function_code="PetRecognition",
    trait_code="PetRecognitionReport",
)

# PackageRecognitionReport's enum encodes its own clear (1=PackageAppeared,
# 2=PackageDisappeared), so model it as a STATEFUL binary sensor: appeared
# (on_values={"1"}) turns it on, disappeared ("2", not in on_values) turns it
# off, no auto-clear (the firmware drives both transitions). The momentary
# recognition archetype was wrong here -- on_any_value made "disappeared" read
# as "on" and a 60s timer guessed the clear instead of honouring the signal.
package_detector_compose = make_detection_composer(
    function_code="PackageRecognition",
    trait_code="PackageRecognitionReport",
)

gesture_sensor_compose = make_enum_event_composer(
    function_code="GestureRecognition",
    trait_code="GestureRecognitionReport",
)

smile_detector_compose = make_recognition_composer(
    function_code="SmileRecognition",
    trait_code="SmileRecognitionReport",
)

# VehicleDetected is a momentary detection with no "cleared" value in its enum
# ({1: VehicleDetected}); a stateful binary sensor would latch on forever.
# Model as an Event so each detection -- including repeats -- fires cleanly.
vehicle_detector_compose = make_enum_event_composer(
    function_code="VehicleDetection",
    trait_code="VehicleDetected",
)

sound_sensor_compose = make_enum_event_composer(
    function_code="SoundDetection",
    trait_code="AnomalySoundDetected",
)

# FlameDetected has no "cleared" value in its enum ({1: FlameDetected}); a
# non-clearing binary sensor would latch on forever. Model as an Event so each
# detection fires cleanly.
fire_alarm_compose = make_enum_event_composer(
    function_code="FlameDetection",
    trait_code="FlameDetected",
)
