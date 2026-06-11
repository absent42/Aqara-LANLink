"""Tests for the classify_v3 orchestrator."""
from __future__ import annotations

import logging

from custom_components.aqara_lanlink.device.classify_v3 import classify_v3
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor, EventDescriptor, SensorDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _occupancy_trait() -> TraitSpec:
    return TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )


def _temp_trait() -> TraitSpec:
    return TraitSpec(
        id="4.143.32952", wire_path="4.143.32952",
        function_code="Temperature", trait_code="CurrentTemperature",
        name="CurrentTemperature", data_type="float", unit="°C",
        readable=True, subscribable=True, endpoint_id=4,
    )


def test_dispatches_one_trait_per_endpoint():
    """Two endpoints with different deviceTypes -> two descriptors via the right composers."""
    endpoints = {
        2: {"deviceType": "OccupancySensor"},
        4: {"deviceType": "TemperatureSensor"},
    }
    traits = {t.id: t for t in (_occupancy_trait(), _temp_trait())}
    descs = classify_v3("lumi.test.fp1", endpoints, traits)
    assert len(descs) == 2
    types = sorted(type(d).__name__ for d in descs)
    assert types == ["BinarySensorDescriptor", "SensorDescriptor"]


def test_traits_filtered_by_endpoint_id_before_dispatch():
    """A composer sees only the traits whose endpoint_id matches its endpoint."""
    endpoints = {
        2: {"deviceType": "OccupancySensor"},
        4: {"deviceType": "TemperatureSensor"},
    }
    traits = {t.id: t for t in (_occupancy_trait(), _temp_trait())}
    descs = classify_v3("lumi.test.fp1", endpoints, traits)
    # The Sensor descriptor's trait must be the temperature one, and the
    # BinarySensor's must be the occupancy one -- otherwise traits leaked
    # across endpoint boundaries.
    sensor = next(d for d in descs if isinstance(d, SensorDescriptor))
    bsens = next(d for d in descs if isinstance(d, BinarySensorDescriptor))
    assert sensor.trait.trait_code == "CurrentTemperature"
    assert bsens.trait.trait_code == "Occupancy"


def test_unknown_device_type_logs_and_falls_back(caplog):
    """An endpoint with an unknown deviceType falls through to _fallback, which
    emits per-trait primitive descriptors based on data_type."""
    endpoints = {2: {"deviceType": "ExoticUnseenDevice"}}
    traits = {"2.160.33000": _occupancy_trait()}
    with caplog.at_level("WARNING", "custom_components.aqara_lanlink.device.classify_v3"):
        descs = classify_v3("lumi.test.x", endpoints, traits)
    assert "ExoticUnseenDevice" in caplog.text
    # _fallback emits a BinarySensor for a bool trait.
    assert len(descs) >= 1


def test_empty_traits_returns_empty():
    assert classify_v3("lumi.test.empty", {2: {"deviceType": "OccupancySensor"}}, {}) == []


def test_no_endpoint_metadata_routes_to_fallback():
    """A model with NO endpoints dict (uncatalogued / legacy) still classifies via
    _fallback per-trait, grouped by trait.endpoint_id."""
    traits = {t.id: t for t in (_occupancy_trait(), _temp_trait())}
    descs = classify_v3("lumi.test.uncatalogued", {}, traits)
    assert len(descs) >= 2


def test_active_endpoints_filters_dynamic_endpoints():
    """When active_endpoints is provided, endpoints not in the active set are
    skipped (used for models with SupportedEndpointDynamic)."""
    endpoints = {
        2: {"deviceType": "OccupancySensor"},
        4: {"deviceType": "TemperatureSensor"},
    }
    traits = {t.id: t for t in (_occupancy_trait(), _temp_trait())}
    descs = classify_v3(
        "lumi.test.fp1", endpoints, traits,
        active_endpoints=frozenset({2}),
    )
    # Only endpoint 2 (Occupancy) was active.
    assert len(descs) == 1
    assert isinstance(descs[0], BinarySensorDescriptor)


def test_endpoint_with_no_traits_emits_nothing():
    """An endpoint in the endpoints map with no traits in the trait dict
    contributes no descriptors."""
    endpoints = {
        2: {"deviceType": "OccupancySensor"},
        4: {"deviceType": "TemperatureSensor"},
    }
    traits = {"2.160.33000": _occupancy_trait()}  # only endpoint 2
    descs = classify_v3("lumi.test.fp1", endpoints, traits)
    assert len(descs) == 1


def test_explicit_platform_overrides_devicetype_composer():
    """An OccupancySensing trait that the occupancy composer would build as a
    BinarySensor is forced to a Sensor when the spec sets platform='sensor'."""
    spec = TraitSpec(
        id="5.10.100", wire_path="5.10.100", name="Occ raw",
        function_code="OccupancySensing", trait_code="Occupancy",
        data_type="int", platform="sensor", endpoint_id=5,
    )
    descs = classify_v3(
        "m", {5: {"deviceType": "OccupancySensor"}}, {spec.id: spec},
    )
    forced = [d for d in descs if getattr(d, "trait", None) is spec]
    assert len(forced) == 1
    assert isinstance(forced[0], SensorDescriptor)


def test_explicit_platform_event_on_detector_endpoint():
    """platform override works regardless of deviceType, including detectors."""
    spec = TraitSpec(
        id="3.216.20215", wire_path="3.216.20215", name="Human raw",
        function_code="HumanRecognition", trait_code="HumanRecognitionReport",
        data_type="enum", enum_values={"1": "HumanDetected"},
        platform="event", endpoint_id=3,
    )
    descs = classify_v3("m", {3: {"deviceType": "HumanDetector"}}, {spec.id: spec})
    assert any(isinstance(d, EventDescriptor) for d in descs)


def test_fused_light_endpoint_ignores_platform_override(caplog):
    """Platform override on a Light-absorbed trait is ignored with a warning;
    the light is still built."""
    onoff = TraitSpec(
        id="1.10.10", wire_path="1.10.10", name="On off",
        function_code="Output", trait_code="OnOff", data_type="bool",
        writable=True, platform="sensor", endpoint_id=1,
    )
    with caplog.at_level(logging.WARNING):
        descs = classify_v3("m", {1: {"deviceType": "Light"}}, {onoff.id: onoff})
    # No standalone Sensor forced out of the light.
    assert not any(isinstance(d, SensorDescriptor) for d in descs)
    assert "ignored" in caplog.text.lower()


def test_videodoorbell_endpoint_honors_platform_override(caplog):
    """VideoDoorbell -> camera composer fuses nothing (pure passthrough), so a
    per-trait platform override on it IS honored, not ignored. Guards against
    over-including non-fusing deviceTypes in _FUSED_DEVICE_TYPES."""
    spec = TraitSpec(
        id="8.10.100", wire_path="8.10.100", name="Some field",
        function_code="Camera", trait_code="SomeField",
        data_type="int", platform="sensor", endpoint_id=8,
    )
    with caplog.at_level(logging.WARNING):
        descs = classify_v3("m", {8: {"deviceType": "VideoDoorbell"}}, {spec.id: spec})
    forced = [d for d in descs if getattr(d, "trait", None) is spec]
    assert len(forced) == 1
    assert isinstance(forced[0], SensorDescriptor)
    assert "ignored" not in caplog.text.lower()


def _occ(ep: int) -> TraitSpec:
    wp = f"{ep}.160.33000"
    return TraitSpec(
        id=wp, wire_path=wp, function_code="OccupancySensing",
        trait_code="Occupancy", name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=ep,
    )


def test_disambiguates_occupancy_zones_and_disables_them():
    """Multi-zone occupancy: the low-endpoint catchall keeps its bare name and
    stays enabled; the >=100 zone endpoints get a '(zone N)' suffix and are
    disabled. A unique high-endpoint sensor (heart rate) is left untouched."""
    heart = TraitSpec(
        id="131.225.20232", wire_path="131.225.20232",
        function_code="HeartMonitoring", trait_code="HeartRate",
        name="Heart rate", data_type="int", subscribable=True, endpoint_id=131,
    )
    endpoints = {
        2: {"deviceType": "OccupancySensor"},
        101: {"deviceType": "OccupancySensor"},
        102: {"deviceType": "OccupancySensor"},
        131: {},  # no deviceType -> fallback sensor, no warning
    }
    traits = {t.id: t for t in (_occ(2), _occ(101), _occ(102), heart)}
    descs = classify_v3("lumi.motion.agl001", endpoints, traits)
    by_name = {d.name: d for d in descs}

    assert by_name["Occupancy"].entity_registry_enabled_default is True
    assert by_name["Occupancy (zone 1)"].entity_registry_enabled_default is False
    assert by_name["Occupancy (zone 2)"].entity_registry_enabled_default is False
    # Unique high-endpoint sensor is not a zone -> name + default untouched.
    assert by_name["Heart rate"].entity_registry_enabled_default is True
