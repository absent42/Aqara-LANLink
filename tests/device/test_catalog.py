"""Tests for the model-scoped lookup API in device/catalog.py."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from custom_components.aqara_lanlink.device import catalog
from custom_components.aqara_lanlink.device.catalog import (
    DisplayMetadata,
    get_display_metadata,
    get_enum_labels,
    get_trait,
)


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    yield
    catalog.reset_for_tests()


def test_get_trait_returns_none_for_unknown_model():
    """get_trait returns None for a model that is not in any package."""
    # Use a model string that is definitely not implemented.
    result = get_trait("lumi.unknown.model", "1.7.85")
    assert result is None


def test_get_enum_labels_returns_none_for_unknown_model():
    """get_enum_labels returns None for a model that is not in any package."""
    # Use a model string that is definitely not implemented.
    result = get_enum_labels("lumi.unknown.model", "8.0.2259")
    assert result is None


def test_get_display_metadata_returns_none_for_unknown_model():
    """get_display_metadata returns None for a model that is not in any package."""
    # Use a model string that is definitely not implemented.
    result = get_display_metadata("lumi.unknown.model")
    assert result is None


def test_display_metadata_is_named_tuple():
    """DisplayMetadata is a NamedTuple with named fields and positional construction."""
    # Positional construction
    meta = DisplayMetadata("Aqara", "T2 RGB LED Bulb")
    assert meta.manufacturer == "Aqara"
    assert meta.display_name == "T2 RGB LED Bulb"

    # Keyword construction
    meta2 = DisplayMetadata(manufacturer="Aqara", display_name="T2 RGB LED Bulb")
    assert meta2.manufacturer == "Aqara"
    assert meta2.display_name == "T2 RGB LED Bulb"

    # Equality
    assert meta == meta2
    assert meta == DisplayMetadata("Aqara", "T2 RGB LED Bulb")
    assert meta != DisplayMetadata("Other", "T2 RGB LED Bulb")


def test_display_metadata_is_named_tuple_subclass():
    """DisplayMetadata is an instance of NamedTuple."""
    meta = DisplayMetadata("Aqara", "T2 RGB LED Bulb")
    assert isinstance(meta, tuple)
    # Access by index
    assert meta[0] == "Aqara"
    assert meta[1] == "T2 RGB LED Bulb"


class TestAllowUnauthored:
    def test_returns_empty_tuple_when_model_lacks_field(self):
        from custom_components.aqara_lanlink.device import catalog
        result = catalog.allow_unauthored("lumi.sensor.motion.aq2")
        assert result == ()

    def test_returns_tuple_when_model_declares_field(self, monkeypatch):
        from custom_components.aqara_lanlink.device import catalog, registry
        from types import ModuleType
        monkeypatch.setattr(registry, "_discovered", True)
        fake = ModuleType("fake_pkg")
        fake.MODELS = ("lumi.fake.allow_unauth",)
        fake.MANUFACTURER = "Aqara"
        fake.DISPLAY_NAME = "fake"
        fake.REGIONS = ()
        fake.BUNDLE_IDS = ()
        fake.TRAITS = {}
        fake.ALLOW_UNAUTHORED_TRAITS = (33001,)
        registry._ALLOW_UNAUTHORED_INDEX["lumi.fake.allow_unauth"] = (33001,)
        try:
            assert catalog.allow_unauthored("lumi.fake.allow_unauth") == (33001,)
        finally:
            registry._ALLOW_UNAUTHORED_INDEX.pop("lumi.fake.allow_unauth", None)

    def test_returns_empty_tuple_for_unknown_model(self):
        from custom_components.aqara_lanlink.device import catalog
        catalog.reset_for_tests()
        assert catalog.allow_unauthored("lumi.unknown.model.zzz") == ()


def test_get_enum_labels_reads_traitspec_enum_values(monkeypatch):
    """get_enum_labels returns the TraitSpec.enum_values dict, or None."""
    import types
    from custom_components.aqara_lanlink.device import catalog, registry, traits

    registry.reset_for_tests()
    fake = types.ModuleType("fake_pkg_c")
    fake.MODELS = ("lumi.fake.c",)
    fake.MANUFACTURER = "X"
    fake.DISPLAY_NAME = "Fake C"
    fake.TRAITS = {
        "0.0.0": traits.TraitSpec(
            id="0.0.0", name="x", enum_values={"0": "Off", "1": "On"}
        ),
        "0.0.1": traits.TraitSpec(id="0.0.1", name="y"),  # no enum_values
    }
    registry._index_package(fake, "fake_pkg_c")

    assert catalog.get_enum_labels("lumi.fake.c", "0.0.0") == {"0": "Off", "1": "On"}
    assert catalog.get_enum_labels("lumi.fake.c", "0.0.1") is None
    assert catalog.get_enum_labels("lumi.fake.c", "missing") is None
