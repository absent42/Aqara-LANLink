"""V3 trait classification policy.

Single source of truth for which (function_code, trait_code) tuples are
dropped at generator time, which are kept as diagnostic (hidden by
default), which are rendered as Button entities (press-to-trigger), and
which are visible. Used by both the offline generator
(tools/generate_catalogue.py) and the runtime classifier (classify_v3).

Pure data + one classification function. No callers beyond those two.
"""
from __future__ import annotations

from typing import Literal


# Functions whose every trait is administrative chatter -- dropped at
# generator time so they never reach data.json, the catalogue stays
# leaner, and the runtime never produces entities for them.
DROP_FUNCTIONS: frozenset[str] = frozenset({
    "Descriptor",
    "EndpointLabel",
    # ZCL/Matter network plumbing: PANID, CoordMac, NeighborTable, LQI,
    # retransmission counters, etc. Useful for protocol debugging but
    # never actionable inside HA.
    "ZigbeeNetworkDiagnostics",
    "ThreadNetworkDiagnostics",
})

DROP_BASIC_INFORMATION_TRAITS: frozenset[str] = frozenset({
    # Static identifiers (already exposed via HA's device registry).
    "SerialNumber", "VendorName", "VendorID", "ProductName",
    "Location", "DeviceID", "ChipTemperature", "FirmwareRevision",
    # Identity strings -- previously diagnostic, elevated to drop.
    "Reachable", "HardwareVersion", "HardwareVersionString",
    "Mac", "FirmwareRevisionString",
    "ProductID", "SupportedMatterRoles",
    # Internal model identifier -- redundant with HA's device registry model.
    "ModelValue",
})

DROP_GENERAL_DIAGNOSTICS_TRAITS: frozenset[str] = frozenset({
    # Reboot/debug noise: never user-facing, never an HA automation source.
    "RebootCount", "RebootReason", "DebugInfo", "NetworkInterfaces",
    # NOTE: AntiDeletionSetting / AntiDeletionLevelSetting /
    # AntiDeletionNotification are security-relevant toggles -- intentionally
    # NOT dropped; they remain visible.
})

DROP_IDENTIFY_TRAITS: frozenset[str] = frozenset({
    # Neither trait does anything useful when written. The actual
    # identify-device action is a cloud RPC with body
    # `{"data": {"identify": "1"}, "subjectId": "<did>"}` (captured from
    # the Aqara app -- works WAN-blocked, so cloud relays to hub locally).
    # Until that RPC is wired up as a service / synthetic button, the
    # trait surface is pure noise -- drop both.
    "IdentifyTime",
    "IdentifyType",
})

DROP_NETWORK_COMMISSIONING_TRAITS: frozenset[str] = frozenset({
    # Static / protocol-internal commissioning state.
    "SupportedNetwork",
    "ZigbeeAuthRandcode", "ZigbeeAuthRandcodeResponse", "MagicPairKeychain",
    # Opaque diagnostic blobs -- not useful in HA UX.
    "WiFiDiagnostics", "ThreadDiagnostics", "ZigbeeDiagnostics",
})

DROP_CAMERA_TRAITS: frozenset[str] = frozenset({
    # The RTSP stream URL is consumed by the integration's go2rtc wiring
    # internally; exposing it as a sensor entity adds clutter.
    "CameraRTSPURL",
})

# Generic (function_code, trait_code) drops that aren't scoped to a single
# function's dedicated set above. Checked before BUTTON/DIAGNOSTIC.
DROP_TRAITS: frozenset[tuple[str, str]] = frozenset({
    # OccupancySensorType (enum: PIR / Ultrasonic / fusion / PhysicalContact)
    # is the generic ZCL OccupancySensing descriptor attribute (0x0001),
    # inherited per-endpoint from the cluster template. Aqara's V3 spec
    # propagates it onto ~28 models -- including device classes with no
    # occupancy sensing at all (cameras, switches, a lock) -- so it's largely
    # template bleed. Over the LANLink transport the write is a confirmed
    # no-op: on agl8 (FP300, genuine PIR+mmWave fusion) writing '1' then '2'
    # both return result:None with no actuation and no readback, and the
    # device only ever reports Occupancy (33000), never the sensor type
    # (33001). Dropped catalogue-wide so it stops rendering a phantom
    # writable select. The real occupancy state (OccupancySensing/Occupancy)
    # is unaffected.
    ("OccupancySensing", "OccupancySensorType"),
})

# Diagnostic-category, default-disabled entities. The trait still ships
# in data.json and an entity is created, but it's hidden by default and
# carries entity_category="diagnostic".
DIAGNOSTIC_FUNCTIONS: frozenset[str] = frozenset()  # noqa: empty -- preserved for clarity

DIAGNOSTIC_TRAITS: frozenset[tuple[str, str]] = frozenset({
    # Hub connectivity status -- worth surfacing for debugging but not
    # interesting on a default dashboard.
    ("BasicInformation", "HubConnectionStatus"),
    # WiFi/Zigbee health metrics -- useful for signal-strength tuning.
    ("NetworkCommissioning", "WiFiRSSI"),
    ("NetworkCommissioning", "WiFiChannel"),
    ("NetworkCommissioning", "ChildDeviceProvisioningState"),
    # Battery diagnostics. BatPercentRemaining intentionally NOT diagnostic
    # -- it's the primary battery indicator users expect to see.
    ("PowerSource", "CurrentVoltage"),
    ("PowerSource", "StateOfLowBat"),
    # Power mode (DC source etc.) -- static, debug-only, not dashboard-worthy.
    ("PowerSource", "PowerMode"),
    # Camera plumbing toggles + readback flags. Buried under "diagnostic"
    # so they're available for debugging (toggle RTSP off for privacy,
    # check if streaming is currently active) without cluttering the
    # default device card.
    ("Camera", "CameraRTSPEnable"),
    ("Camera", "CameraActiveStatus"),
    ("Camera", "P2PCaptureEnabled"),
    ("Camera", "P2PCaptureStatus"),
})

# Traits the V3 spec claims as writable but which are actually state
# readbacks at the device level (naming convention: *Status suffix).
# Generator strips `writable` from data.json for these so the runtime
# fallback composer routes them to a (Binary)SensorDescriptor instead
# of a SwitchDescriptor.
FORCE_READONLY_TRAITS: frozenset[tuple[str, str]] = frozenset({
    ("Camera", "CameraActiveStatus"),
    ("Camera", "P2PCaptureStatus"),
})

# Traits Aqara's V3 spec marks as `writable=False` but the firmware
# actually accepts writes. Generator adds `writable=True` for these so
# the fallback composer routes them to a SelectDescriptor (for enums)
# or NumberDescriptor (for numerics) instead of a read-only Sensor.
# Truth-of-record for each entry is either a working Z2M converter or
# an empirical capture from the Aqara app -- not the cloud spec.
#
# OccupancySensorType was previously forced writable here on the strength
# of a Z2M converter + Aqara app picker, but live LANLink captures show the
# local res/write is a no-op (result:None) even on the fusion sensors, so
# it now lives in DROP_TRAITS instead. The Z2M path is direct Zigbee, a
# different transport; if cloud-rid writes ever prove out, re-introduce it
# routed through the cloud composer rather than as a local wire-path write.
FORCE_WRITABLE_TRAITS: frozenset[tuple[str, str]] = frozenset()

# Traits rendered as Button entities (press-to-trigger). Maps
# (function_code, trait_code) -> press_value to write on press.
#
# Currently empty. The Identify cluster looked like the natural fit but
# empirically neither IdentifyTime nor IdentifyType triggers anything
# when written; the actual identify action appears to live on a
# separate cloud RPC (likely the Aqara mobile app's "find device" path)
# that isn't a trait read/write at all. To be revisited once the wire
# traffic is captured.
BUTTON_TRAITS: dict[tuple[str, str], str] = {}


def classify_trait(
    function_code: str, trait_code: str,
) -> Literal["visible", "diagnostic", "button", "drop"]:
    """Return the classification for a V3 (function_code, trait_code) tuple.

    Decision order:
      1. function_code in DROP_FUNCTIONS -> "drop"
      2. (function_code, trait_code) special-cased drop -> "drop"
      3. (function_code, trait_code) in BUTTON_TRAITS -> "button"
      4. function_code in DIAGNOSTIC_FUNCTIONS -> "diagnostic"
      5. (function_code, trait_code) in DIAGNOSTIC_TRAITS -> "diagnostic"
      6. otherwise -> "visible"

    Unknown function codes default to "visible"; the generator's coverage
    report surfaces them for the maintainer to explicitly classify.
    """
    if function_code in DROP_FUNCTIONS:
        return "drop"
    if function_code == "BasicInformation" and trait_code in DROP_BASIC_INFORMATION_TRAITS:
        return "drop"
    if function_code == "GeneralDiagnostics" and trait_code in DROP_GENERAL_DIAGNOSTICS_TRAITS:
        return "drop"
    if function_code == "Identify" and trait_code in DROP_IDENTIFY_TRAITS:
        return "drop"
    if function_code == "NetworkCommissioning" and trait_code in DROP_NETWORK_COMMISSIONING_TRAITS:
        return "drop"
    if function_code == "Camera" and trait_code in DROP_CAMERA_TRAITS:
        return "drop"
    if (function_code, trait_code) in DROP_TRAITS:
        return "drop"
    if (function_code, trait_code) in BUTTON_TRAITS:
        return "button"
    if function_code in DIAGNOSTIC_FUNCTIONS:
        return "diagnostic"
    if (function_code, trait_code) in DIAGNOSTIC_TRAITS:
        return "diagnostic"
    return "visible"
