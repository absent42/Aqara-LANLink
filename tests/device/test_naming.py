"""Tests for disambiguate_names: differentiates entities that would otherwise
share an identical friendly name across a device's endpoints.

Rules under test:
  - Zone endpoints (>= 100) in a name-collision group get a "(zone N)" suffix
    (N = endpoint - 100) and are disabled by default.
  - Low-endpoint collisions (switch gangs, light channels, buttons) get a
    1-based index suffix and stay enabled.
  - A lone low-endpoint member sharing a group with zones (the catchall, e.g.
    FP2 endpoint 2) keeps its bare name and stays enabled.
  - Unique names are never touched -- even on a high endpoint (FP2 HeartRate
    on endpoint 131 is a single sensor, not a zone).
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types._build import build_descriptor
from custom_components.aqara_lanlink.device.naming import disambiguate_names
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _spec(ep: int, attr: int, *, name: str, data_type: str, fn: str, tc: str,
          writable: bool = False) -> TraitSpec:
    wp = f"{ep}.160.{attr}"
    return TraitSpec(
        id=wp, wire_path=wp, name=name, data_type=data_type,
        function_code=fn, trait_code=tc, endpoint_id=ep, writable=writable,
    )


def _occupancy(ep: int):
    return build_descriptor(
        _spec(ep, 33000, name="Occupancy", data_type="bool",
              fn="OccupancySensing", tc="Occupancy"),
        "binary_sensor",
    )


def _by_name(descs):
    return {d.name: d for d in descs}


def test_zone_endpoints_get_zone_suffix_and_disabled():
    descs = [_occupancy(2), _occupancy(101), _occupancy(102)]
    out = _by_name(disambiguate_names(descs))

    assert "Occupancy" in out  # ep2 catchall keeps bare name
    assert out["Occupancy"].entity_registry_enabled_default is True

    assert out["Occupancy (zone 1)"].entity_registry_enabled_default is False
    assert out["Occupancy (zone 2)"].entity_registry_enabled_default is False


def test_low_endpoint_collisions_get_index_and_stay_enabled():
    descs = [
        build_descriptor(
            _spec(ep, 32913, name="On off", data_type="bool",
                  fn="Output", tc="OnOff", writable=True),
            "switch",
        )
        for ep in (2, 3, 4)
    ]
    out = _by_name(disambiguate_names(descs))

    assert set(out) == {"On off 1", "On off 2", "On off 3"}
    assert all(d.entity_registry_enabled_default is True for d in out.values())


def test_unique_name_on_high_endpoint_is_untouched():
    heart = build_descriptor(
        _spec(131, 20232, name="Heart rate", data_type="int",
              fn="HeartMonitoring", tc="HeartRate"),
        "sensor",
    )
    out = disambiguate_names([heart])
    assert len(out) == 1
    assert out[0].name == "Heart rate"
    assert out[0].entity_registry_enabled_default is True


def test_catchall_duration_enabled_zone_duration_disabled():
    descs = [
        build_descriptor(
            _spec(ep, 33044, name="Duration occupied within one day",
                  data_type="float", fn="OccupancySensing",
                  tc="DurationOccupiedWithinOneDay"),
            "sensor",
        )
        for ep in (2, 101)
    ]
    out = _by_name(disambiguate_names(descs))

    assert out["Duration occupied within one day"].entity_registry_enabled_default is True
    zone = out["Duration occupied within one day (zone 1)"]
    assert zone.entity_registry_enabled_default is False
