"""Unit tests for the V3 trait_policy module."""
from __future__ import annotations

from custom_components.aqara_lanlink.device import trait_policy


def test_descriptor_function_drops():
    """Descriptor cluster (endpoint topology plumbing) is always dropped."""
    assert trait_policy.classify_trait("Descriptor", "EndpointCount") == "drop"
    assert trait_policy.classify_trait("Descriptor", "EndpointArray") == "drop"
    assert trait_policy.classify_trait("Descriptor", "SupportedEndpointDynamic") == "drop"


def test_endpoint_label_function_drops():
    """EndpointLabel cluster is HA-managed; drop entirely."""
    assert trait_policy.classify_trait("EndpointLabel", "EndpointName") == "drop"
    assert trait_policy.classify_trait("EndpointLabel", "EndpointIcon") == "drop"
    assert trait_policy.classify_trait("EndpointLabel", "EndpointVisibility") == "drop"


def test_basic_information_drops_static_identifiers():
    """Static identity strings drop: HA's device registry already exposes
    them, and they're never actionable inside an entity state."""
    for tc in (
        "SerialNumber", "VendorName", "Location", "FirmwareRevisionString",
        "Mac", "HardwareVersion", "HardwareVersionString", "Reachable",
        "ProductID", "SupportedMatterRoles",
    ):
        assert trait_policy.classify_trait("BasicInformation", tc) == "drop", tc


def test_basic_information_hub_connection_is_diagnostic():
    """HubConnectionStatus is useful for debugging hub uplink but not for
    a default dashboard -- diagnostic, default-disabled."""
    assert trait_policy.classify_trait(
        "BasicInformation", "HubConnectionStatus",
    ) == "diagnostic"


def test_general_diagnostics_traits_drop():
    """Reboot/debug noise is never user-actionable -- drop entirely.
    AntiDeletion* traits remain visible (security-relevant toggles)."""
    for tc in ("RebootCount", "RebootReason", "DebugInfo", "NetworkInterfaces"):
        assert trait_policy.classify_trait("GeneralDiagnostics", tc) == "drop", tc
    # Security-relevant traits stay visible.
    for tc in ("AntiDeletionSetting", "AntiDeletionLevelSetting", "AntiDeletionNotification"):
        assert trait_policy.classify_trait("GeneralDiagnostics", tc) == "visible", tc


def test_identify_traits_are_both_dropped():
    """Empirically, neither Identify.IdentifyTime nor Identify.IdentifyType
    triggers the device's identify pattern when written -- both writes
    are silently ignored on every Aqara device tested. The actual
    identify-device action is a separate cloud RPC (request body
    `{"data": {"identify": "1"}, "subjectId": did}` captured from the
    Aqara app; works WAN-blocked, so the cloud relays to the hub locally).
    Until that RPC is wired up as a service / synthetic button, the
    trait surface is pure noise -- both drop.
    """
    assert trait_policy.classify_trait("Identify", "IdentifyTime") == "drop"
    assert trait_policy.classify_trait("Identify", "IdentifyType") == "drop"
    # And neither is registered as a button.
    assert ("Identify", "IdentifyTime") not in trait_policy.BUTTON_TRAITS
    assert ("Identify", "IdentifyType") not in trait_policy.BUTTON_TRAITS


def test_network_commissioning_classification():
    """Drop static or protocol-internal commissioning state; keep
    signal-strength / hub-pairing diagnostics as default-disabled."""
    for tc in (
        "SupportedNetwork",
        "ZigbeeAuthRandcode", "ZigbeeAuthRandcodeResponse", "MagicPairKeychain",
        "WiFiDiagnostics", "ThreadDiagnostics", "ZigbeeDiagnostics",
    ):
        assert trait_policy.classify_trait("NetworkCommissioning", tc) == "drop", tc
    for tc in ("WiFiRSSI", "WiFiChannel", "ChildDeviceProvisioningState"):
        assert trait_policy.classify_trait("NetworkCommissioning", tc) == "diagnostic", tc


def test_zigbee_network_diagnostics_drops_entirely():
    """The whole ZigbeeNetworkDiagnostics cluster is protocol plumbing --
    never useful in HA. PANID, LQI, neighbor table, retransmission
    counters, coord keys etc. all drop."""
    for tc in (
        "LQI", "PANID", "ZigbeeExtendedPANID", "ZigbeeCoordMac",
        "ZigbeeCoordNetworkKey", "ZigbeeNeighborTableInfo",
        "ZigbeeGeneralDiagnostics", "Channel",
        "APSRetransmissionCount", "NWKRetransmissionCount",
        "RoutingAgingCountThreshold", "ZigbeeFrameCounter",
        "ZigbeeKeySeqNumber",
        # Pre-existing per-trait drops (per-hour counters, hop info).
        "ZigbeeFailedMessageInOneHour", "ZigbeeNextHopMac",
    ):
        assert trait_policy.classify_trait("ZigbeeNetworkDiagnostics", tc) == "drop", tc


def test_thread_network_diagnostics_drops_entirely():
    """ThreadNetworkDiagnostics is the Thread protocol equivalent of
    ZigbeeNetworkDiagnostics; same wholesale drop policy."""
    assert trait_policy.classify_trait(
        "ThreadNetworkDiagnostics", "ThreadTopologyData",
    ) == "drop"
    assert trait_policy.classify_trait(
        "ThreadNetworkDiagnostics", "ThreadPingResult",
    ) == "drop"


def test_power_source_battery_traits_classification():
    """CurrentVoltage and StateOfLowBat stay diagnostic (default-disabled).
    BatPercentRemaining is the primary battery indicator users expect on
    the main view -- it stays visible."""
    assert trait_policy.classify_trait("PowerSource", "CurrentVoltage") == "diagnostic"
    assert trait_policy.classify_trait("PowerSource", "StateOfLowBat") == "diagnostic"
    assert trait_policy.classify_trait("PowerSource", "BatPercentRemaining") == "visible"
    # PowerMode is diagnostic (see test_power_mode_is_diagnostic); the other
    # power-source disclosure traits stay visible.
    for tc in ("Rechargeable", "WiredPresent"):
        assert trait_policy.classify_trait("PowerSource", tc) == "visible", tc


def test_sensor_clusters_stay_visible():
    """Sensor/control traits default to visible."""
    assert trait_policy.classify_trait("OccupancySensing", "Occupancy") == "visible"
    assert trait_policy.classify_trait("Temperature", "CurrentTemperature") == "visible"
    assert trait_policy.classify_trait("RelativeHumidity", "CurrentHumidity") == "visible"
    assert trait_policy.classify_trait("Illuminance", "CurrentIlluminance") == "visible"
    assert trait_policy.classify_trait("Button", "ButtonEvent") == "visible"
    assert trait_policy.classify_trait("Output", "OnOff") == "visible"


def test_unknown_function_defaults_visible():
    """Trait codes we haven't catalogued yet default to visible; the generator
    coverage report surfaces them for explicit handling."""
    assert trait_policy.classify_trait("BrandNewCluster", "SomeNewTrait") == "visible"


def test_camera_rtsp_url_drops():
    """Camera.CameraRTSPURL is consumed internally by the go2rtc bridge --
    exposing it as a string sensor is clutter, never user-facing data."""
    assert trait_policy.classify_trait("Camera", "CameraRTSPURL") == "drop"


def test_camera_internal_toggles_are_diagnostic():
    """The four Camera.* toggles (RTSP/P2P enable + active/status flags)
    are streaming-internal plumbing. Hidden under the diagnostic category
    so they're available for debugging (toggle RTSP off for privacy,
    inspect streaming-active state) but absent from the main device card."""
    for tc in (
        "CameraRTSPEnable", "CameraActiveStatus",
        "P2PCaptureEnabled", "P2PCaptureStatus",
    ):
        assert trait_policy.classify_trait("Camera", tc) == "diagnostic", tc


def test_status_traits_are_force_readonly():
    """*Status traits read back device state -- the V3 spec marks them
    writable=True but the generator strips that so the runtime fallback
    composer renders them as (Binary)SensorDescriptors instead of
    SwitchDescriptors."""
    assert ("Camera", "CameraActiveStatus") in trait_policy.FORCE_READONLY_TRAITS
    assert ("Camera", "P2PCaptureStatus") in trait_policy.FORCE_READONLY_TRAITS


def test_occupancy_sensor_type_is_dropped():
    """OccupancySensorType is the generic ZCL OccupancySensing descriptor
    attribute (0x0001), inherited per-endpoint from the cluster template and
    bled onto ~28 models including cameras/switches/a lock. Over LANLink the
    local write is a confirmed no-op (result:None even on the FP300 fusion
    sensor) and the attribute is never reported -- only Occupancy (33000) is.
    Dropped catalogue-wide so it stops rendering a phantom writable select."""
    assert ("OccupancySensing", "OccupancySensorType") in trait_policy.DROP_TRAITS
    assert trait_policy.classify_trait("OccupancySensing", "OccupancySensorType") == "drop"
    # The real occupancy state is unaffected.
    assert trait_policy.classify_trait("OccupancySensing", "Occupancy") == "visible"
    # No longer forced writable -- the local res/write doesn't actuate it.
    assert ("OccupancySensing", "OccupancySensorType") not in trait_policy.FORCE_WRITABLE_TRAITS


def test_drop_functions_set_is_frozenset():
    """All exported sets are immutable to prevent accidental mutation."""
    assert isinstance(trait_policy.DROP_FUNCTIONS, frozenset)
    assert isinstance(trait_policy.DIAGNOSTIC_FUNCTIONS, frozenset)
    assert isinstance(trait_policy.DROP_BASIC_INFORMATION_TRAITS, frozenset)
    assert isinstance(trait_policy.DROP_GENERAL_DIAGNOSTICS_TRAITS, frozenset)
    assert isinstance(trait_policy.DROP_IDENTIFY_TRAITS, frozenset)
    assert isinstance(trait_policy.DROP_NETWORK_COMMISSIONING_TRAITS, frozenset)
    assert isinstance(trait_policy.DIAGNOSTIC_TRAITS, frozenset)
    # BUTTON_TRAITS is a dict (mapping (fn, tc) -> press_value), not a frozenset.
    assert isinstance(trait_policy.BUTTON_TRAITS, dict)
    assert isinstance(trait_policy.DROP_CAMERA_TRAITS, frozenset)
    assert isinstance(trait_policy.DROP_TRAITS, frozenset)
    assert isinstance(trait_policy.FORCE_READONLY_TRAITS, frozenset)
    assert isinstance(trait_policy.FORCE_WRITABLE_TRAITS, frozenset)


def test_model_value_is_dropped():
    """BasicInformation.ModelValue is a static identifier of no user value
    (the model is already in HA's device registry) -- drop it."""
    assert trait_policy.classify_trait("BasicInformation", "ModelValue") == "drop"


def test_power_mode_is_diagnostic():
    """PowerSource.PowerMode (DC source etc.) is diagnostic-only -- shipped as a
    diagnostic, disabled-by-default entity, not a default-dashboard sensor."""
    assert trait_policy.classify_trait("PowerSource", "PowerMode") == "diagnostic"
