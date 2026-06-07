"""Self-contained test for the V3-shaped TraitSpec catalogue contract.

We added four new optional fields to TraitSpec (`function_code`, `trait_code`,
`subscribable`, `endpoint_id`) to consume the catalogue generated from V3
Open Cloud responses. The actual generated per-model files live under
`docs/dev/generated/catalogue/` (gitignored), so this test inlines a small
sample of the new shape rather than depending on those files being present.

What we assert:
  - TraitSpec accepts the four new fields and they round-trip through
    construction.
  - The new fields hash and compare correctly.
  - A `build_descriptors` call on a V3-shaped TRAITS dict produces
    descriptors keyed by wire path (no propertyId fallback needed).
  - Mixing V3-shaped traits with pre-V3 traits in the same catalogue
    is safe (the new fields are optional with safe defaults).
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _v3_trait(wire_path: str, **overrides) -> TraitSpec:
    """Build a TraitSpec the way the V3 catalogue generator emits them."""
    ep, fn, tr = wire_path.split(".")
    defaults = dict(
        id=wire_path,
        name=overrides.get("trait_code", "Unknown"),
        wire_path=wire_path,
        data_type="bool",
        readable=True,
        writable=False,
        function_code="BasicInformation",
        trait_code="Reachable",
        subscribable=True,
        endpoint_id=int(ep),
    )
    defaults.update(overrides)
    return TraitSpec(**defaults)


def test_traitspec_accepts_v3_fields() -> None:
    """The four new fields round-trip through construction."""
    spec = _v3_trait(
        "4.143.32952",
        trait_code="CurrentTemperature",
        function_code="Temperature",
        data_type="float",
        unit="℃",
    )
    assert spec.function_code == "Temperature"
    assert spec.trait_code == "CurrentTemperature"
    assert spec.subscribable is True
    assert spec.endpoint_id == 4


def test_traitspec_v3_fields_default_to_none_or_false() -> None:
    """Pre-V3 catalogue entries (no V3 fields) still construct cleanly."""
    spec = TraitSpec(id="0.1.85", name="Legacy")
    assert spec.function_code is None
    assert spec.trait_code is None
    assert spec.subscribable is False
    assert spec.endpoint_id is None


def test_traitspec_v3_fields_participate_in_hash() -> None:
    """Two specs differing only in V3 fields hash differently.

    Regression guard: TraitSpec is frozen + hashable; if __hash__ omits the
    new fields, dict/set membership becomes incorrect for V3-shaped specs.
    """
    a = _v3_trait("4.143.32952", trait_code="CurrentTemperature")
    b = _v3_trait("4.143.32952", trait_code="OtherTrait")
    assert a != b
    assert hash(a) != hash(b)

    c = _v3_trait("4.143.32952", function_code="Temperature")
    d = _v3_trait("4.143.32952", function_code="OtherFunction")
    assert c != d
    assert hash(c) != hash(d)


def test_build_descriptors_consumes_v3_shaped_traits(monkeypatch) -> None:
    """A V3-shaped catalogue entry produces descriptors keyed by wire path.

    Stubs `catalog.all_traits_for_model` to return a V3-shaped TRAITS dict
    and verifies build_descriptors completes without error and the resulting
    descriptors' wire-path keys match the catalogue keys.
    """
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod

    model = "test.v3.shape"
    # Minimal V3-shaped catalogue: one occupancy boolean and one temperature
    # number. Both keyed by wire path (endpoint.function.trait).
    v3_catalogue = {
        "2.160.33000": _v3_trait(
            "2.160.33000",
            trait_code="Occupancy",
            function_code="OccupancySensing",
            data_type="bool",
        ),
        "4.143.32952": _v3_trait(
            "4.143.32952",
            trait_code="CurrentTemperature",
            function_code="Temperature",
            data_type="float",
            unit="℃",
        ),
    }
    monkeypatch.setattr(
        bd_mod.catalog,
        "all_traits_for_model",
        lambda m: v3_catalogue if m == model else {},
    )
    descriptors = bd_mod.build_descriptors(model, Overlay())
    # build_descriptors returns AnyDescriptor instances; their trait.id is
    # set to the wire path by auto_derive._classify (single-pass construction).
    wire_paths_built = {
        getattr(getattr(d, "trait", None), "id", None)
        or getattr(getattr(d, "trigger_trait", None), "id", None)
        or getattr(getattr(d, "attr", None), "name", None)
        for d in descriptors
    }
    wire_paths_built.discard(None)
    assert wire_paths_built <= set(v3_catalogue.keys()), (
        f"build_descriptors emitted wire paths not in the V3 catalogue: "
        f"{wire_paths_built - set(v3_catalogue.keys())}"
    )
    # And at minimum one descriptor came out (proves the classifier didn't
    # silently drop the V3-shaped entries).
    assert descriptors, "build_descriptors returned no descriptors for V3-shaped catalogue"


def test_v3_and_pre_v3_traits_coexist_in_one_catalogue(monkeypatch) -> None:
    """A catalogue mixing V3-shaped entries (with new fields) and legacy
    entries (without) is consumed cleanly by build_descriptors. This proves
    the new fields' defaults don't break the existing derive pipeline.
    """
    from custom_components.aqara_lanlink.device import build_descriptors as bd_mod

    model = "test.mixed.shape"
    catalogue = {
        # V3-shaped
        "2.160.33000": _v3_trait(
            "2.160.33000", trait_code="Occupancy",
            function_code="OccupancySensing", data_type="bool",
        ),
        # Legacy (no V3 fields)
        "0.1.85": TraitSpec(
            id="0.1.85",
            name="Legacy temp",
            data_type="float",
            readable=True,
            writable=False,
        ),
    }
    monkeypatch.setattr(
        bd_mod.catalog,
        "all_traits_for_model",
        lambda m: catalogue if m == model else {},
    )
    # Just confirm no exception. The legacy entry has no wire_path so its
    # descriptor wire path will fall back to the spec.id (the propertyId
    # form). That's fine for this test - we only care that mixed shapes
    # don't crash the pipeline.
    descriptors = bd_mod.build_descriptors(model, Overlay())
    assert isinstance(descriptors, list)
