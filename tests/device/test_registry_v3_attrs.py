"""Tests for registry indexing of ENDPOINTS and DEVICE_TYPES."""
from __future__ import annotations

import types

from custom_components.aqara_lanlink.device import catalog, registry


def _fake_pkg(
    models, endpoints=None, device_types=None, traits=None,
):
    mod = types.SimpleNamespace()
    mod.MODELS = tuple(models)
    mod.TRAITS = traits or {}
    mod.MANUFACTURER = "Aqara"
    mod.DISPLAY_NAME = "Test"
    mod.REGIONS = ()
    mod.BUNDLE_IDS = ()
    if endpoints is not None:
        mod.ENDPOINTS = endpoints
    if device_types is not None:
        mod.DEVICE_TYPES = device_types
    return mod


def test_endpoints_indexed_when_present(monkeypatch):
    registry.reset_for_tests()
    pkg = _fake_pkg(
        ["lumi.test.a"],
        endpoints={1: {"deviceType": "Hub"}, 2: {"deviceType": "OccupancySensor"}},
    )
    registry._index_package(pkg, "test.pkg")
    assert catalog.endpoints_for_model("lumi.test.a") == {
        1: {"deviceType": "Hub"}, 2: {"deviceType": "OccupancySensor"},
    }


def test_device_types_indexed_when_present(monkeypatch):
    registry.reset_for_tests()
    pkg = _fake_pkg(
        ["lumi.test.b"], device_types=("OccupancySensor", "TemperatureSensor"),
    )
    registry._index_package(pkg, "test.pkg")
    assert catalog.device_types_for_model("lumi.test.b") == (
        "OccupancySensor", "TemperatureSensor",
    )


def test_missing_attrs_yield_empty(monkeypatch):
    """Legacy package with no ENDPOINTS/DEVICE_TYPES -> catalog returns empty
    dict/tuple rather than raising KeyError."""
    registry.reset_for_tests()
    pkg = _fake_pkg(["lumi.test.legacy"])
    registry._index_package(pkg, "test.pkg")
    assert catalog.endpoints_for_model("lumi.test.legacy") == {}
    assert catalog.device_types_for_model("lumi.test.legacy") == ()


def test_unknown_model_returns_empty(monkeypatch):
    registry.reset_for_tests()
    assert catalog.endpoints_for_model("lumi.never.indexed") == {}
    assert catalog.device_types_for_model("lumi.never.indexed") == ()


def test_endpoints_returned_dict_is_a_copy(monkeypatch):
    """catalog.endpoints_for_model must return a defensive copy -- callers
    must not be able to mutate the registry's internal state."""
    registry.reset_for_tests()
    pkg = _fake_pkg(["lumi.test.c"], endpoints={1: {"deviceType": "Hub"}})
    registry._index_package(pkg, "test.pkg")
    snapshot = catalog.endpoints_for_model("lumi.test.c")
    snapshot[99] = {"deviceType": "Injected"}
    assert 99 not in catalog.endpoints_for_model("lumi.test.c")
