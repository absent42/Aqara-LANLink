"""Tests for the catalogue-first deterministic derive."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from custom_components.aqara_lanlink.device.build_descriptors import (
    build_descriptors,
)
from custom_components.aqara_lanlink.device.descriptors import BinarySensorDescriptor
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.device import catalog


# Parametrize across a representative slice of camera + non-camera models.
_MODELS = [
    "lumi.camera.agl005",
    "lumi.camera.agl013",
    "lumi.camera.acn003",
]


def _descriptor_key(desc) -> tuple:
    """Stable comparator for a descriptor: type + wire path + name + key."""
    trait = getattr(desc, "trait", None) or getattr(desc, "trigger_trait", None)
    return (
        type(desc).__name__,
        getattr(trait, "id", None) if trait else None,
        getattr(desc, "name", None),
        getattr(desc, "key", None),
    )


@pytest.mark.parametrize("model", _MODELS)
def test_build_descriptors_is_deterministic_with_empty_overlay(model):
    """Two calls with identical inputs return identical descriptor sets.
    This is the headline regression test for the determinism property.
    """
    overlay = Overlay()
    a = build_descriptors(model, overlay)
    b = build_descriptors(model, overlay)
    assert sorted(map(_descriptor_key, a)) == sorted(map(_descriptor_key, b))


@pytest.mark.parametrize("model", _MODELS)
def test_build_descriptors_is_deterministic_with_overlay(model):
    """Determinism holds when an overlay adds traits too."""
    overlay = Overlay()
    overlay.set_trait(
        model=model,
        pid="99.99.85",
        spec=TraitSpec(
            id="99.99.85",
            name="Synthetic",
            wire_path="9.9.99999",
            data_type="bool",
            platform="binary_sensor",
        ),
        discovered_from="cloud_scan",
        discovered_at=datetime.now(timezone.utc),
    )
    a = build_descriptors(model, overlay)
    b = build_descriptors(model, overlay)
    assert sorted(map(_descriptor_key, a)) == sorted(map(_descriptor_key, b))


def test_build_descriptors_overlay_overrides_catalogue(monkeypatch):
    """Overlay entries replace catalogue entries at the same wire path.

    REGRESSION GUARD: the pre-V3 contract was additive-only (catalogue won
    on conflict). V3 inverts this - overlay wins. See the design doc:
    `docs/dev/superpowers/specs/2026-05-24-v3-classifier-design.md`,
    "device/overlay.py (modified)".
    """
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod
    from custom_components.aqara_lanlink.device.overlay import Overlay
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    model = "lumi.test.override"
    catalogue_spec = TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )
    overlay_spec = TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="OccupancyOverridden",  # the override marker
        data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )
    monkeypatch.setattr(
        bd_mod.catalog, "all_traits_for_model",
        lambda m: {"2.160.33000": catalogue_spec} if m == model else {},
    )
    overlay = Overlay()
    overlay.set_trait(
        model=model,
        pid="2.160.33000",
        spec=overlay_spec,
        discovered_from="cloud_scan",
        discovered_at=datetime.now(timezone.utc),
    )

    # We are not yet at the classify_v3 cut-over, so build_descriptors is
    # still calling auto_derive._classify. But the merge step is what we
    # care about here. Test the merge helper directly when possible:
    merged = bd_mod._merge_overlay_into_catalogue(
        {"2.160.33000": catalogue_spec}, {"2.160.33000": overlay_spec},
    )
    assert merged["2.160.33000"].name == "OccupancyOverridden"


def test_build_descriptors_overlay_none_drops_catalogue_entry():
    """An overlay entry with spec=None removes the catalogue entry at that
    wire path."""
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    catalogue_spec = TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        name="Occupancy", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
    )
    merged = bd_mod._merge_overlay_into_catalogue(
        {"2.160.33000": catalogue_spec}, {"2.160.33000": None},
    )
    assert "2.160.33000" not in merged


def test_build_descriptors_does_not_call_cloud():
    """The function is pure - no cloud client access. Any such side
    effect would break determinism.
    """
    overlay = Overlay()
    with patch(
        "custom_components.aqara_lanlink.hub.cloud_client.AqaraCloudClient",
        side_effect=AssertionError("build_descriptors must not call the cloud"),
    ):
        build_descriptors("lumi.camera.agl005", overlay)


def test_build_descriptors_unknown_model_returns_empty_list():
    """A model the catalogue does not know AND that has no overlay
    entries yields an empty list - never an error.
    """
    overlay = Overlay()
    assert build_descriptors("lumi.unknown.model", overlay) == []


def test_build_descriptors_uses_classify_v3(monkeypatch):
    """build_descriptors routes through classify_v3, NOT auto_derive._classify.

    Regression guard: a code reviewer in 2026-07 noticed the cut-over had
    been silently reverted via a merge conflict resolution. This test pins
    the contract.
    """
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod
    from custom_components.aqara_lanlink.device.overlay import Overlay
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    seen = {"classify_v3_called": False}

    def fake_classify_v3(model, endpoints, traits, active_endpoints=None):
        seen["classify_v3_called"] = True
        return []

    monkeypatch.setattr(bd_mod, "classify_v3", fake_classify_v3)
    bd_mod.build_descriptors("lumi.test.x", Overlay())
    assert seen["classify_v3_called"]


def test_trait_dict_from_spec_is_deleted():
    """The synthetic V1-trait-dict helper has no callers post-cut-over and
    must not exist anymore."""
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod
    assert not hasattr(bd_mod, "_trait_dict_from_spec")


def test_descriptor_names_are_humanized_for_display():
    """Composers set `descriptor.name = spec.name` (V3-spec PascalCase
    identifier like `OnOff`). The boundary transform in
    build_descriptors rewrites those to human-readable form
    (`On off`) so HA renders sensible entity sub-names.

    TraitSpec.name itself stays PascalCase -- the transform only
    applies to the descriptor.name field (the display sink).
    """
    overlay = Overlay()
    descs = build_descriptors("lumi.light.agl003", overlay)
    assert descs, "expected at least one descriptor for agl003"
    # Every descriptor.name must be either humanized (no internal CamelCase
    # boundaries) or None (some descriptors rely on translation_key).
    for d in descs:
        if d.name is None:
            continue
        # A humanized name never has a lowercase-then-uppercase transition
        # (e.g. `OnOff`); the transform splits those into separate words.
        import re
        assert not re.search(r"[a-z][A-Z]", d.name), (
            f"descriptor name not humanized: {d.name!r}"
        )
    # Spot-check a known-shipped trait on agl003 -- OnOff -> "On off".
    onoff = next(
        (d for d in descs if getattr(d, "key", "") == "auto_2_132_32920"), None,
    )
    assert onoff is not None, "expected the OnOff descriptor on agl003"
    assert onoff.name == "On off", f"unexpected humanized name: {onoff.name!r}"


def test_overlay_platform_override_reverts_recognition_event_to_binary():
    """A user override setting platform='binary_sensor' on the Pet report turns
    the Unit-A enum Event back into a BinarySensor end-to-end via build_descriptors."""
    model = "lumi.camera.agl010"
    wp = "6.218.20216"  # PetRecognition.PetRecognitionReport
    base = catalog.all_traits_for_model(model)[wp]
    forced = replace(base, platform="binary_sensor")

    overlay = Overlay()
    overlay.set_trait(
        model=model, pid=wp, spec=forced,
        discovered_from="test", discovered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    descs = build_descriptors(model, overlay)
    pet = [
        d for d in descs
        if getattr(getattr(d, "trait", None), "trait_code", None) == "PetRecognitionReport"
    ]
    assert len(pet) == 1
    assert isinstance(pet[0], BinarySensorDescriptor)
