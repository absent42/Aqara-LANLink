"""Tests for the trait specification catalog."""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device import traits as traits_catalog
from custom_components.aqara_lanlink.device.traits import (
    TraitSpec,
    get,
    register_trait,
    replace,
)


@pytest.fixture(autouse=True)
def _reset_traits():
    traits_catalog.reset_for_tests()
    yield
    traits_catalog.reset_for_tests()


def test_trait_spec_is_frozen_and_hashable():
    spec = TraitSpec(id="1.7.85", name="test_brightness")
    with pytest.raises(Exception):
        spec.id = "other"  # type: ignore[misc]
    # Hashable -- frozen dataclasses are hashable by default.
    assert hash(spec) == hash(TraitSpec(id="1.7.85", name="test_brightness"))


def test_register_and_get():
    spec = TraitSpec(id="1.7.85", name="test_brightness")
    returned = register_trait(spec)
    assert returned is spec
    assert get("1.7.85") is spec
    assert get("missing.id") is None


def test_register_idempotent_identical_fields():
    spec = TraitSpec(id="1.7.85", name="test_brightness", unit="%")
    register_trait(spec)
    again = TraitSpec(id="1.7.85", name="test_brightness", unit="%")
    returned = register_trait(again)
    # Identical fields -> existing entry is returned unchanged.
    assert returned is not again
    assert returned == spec


def test_register_overwrites_when_fields_differ():
    first = TraitSpec(id="1.7.85", name="auto_1_7_85", unit="%")
    register_trait(first)
    second = TraitSpec(id="1.7.85", name="auto_1_7_85", unit="%", min_value=0)
    returned = register_trait(second)
    assert returned is second
    assert get("1.7.85") is second


def test_change_listener_fires_on_mutation_only():
    fired: list[int] = []
    traits_catalog.set_change_listener(lambda: fired.append(1))
    spec = TraitSpec(id="1.7.85", name="test_brightness")
    register_trait(spec)
    assert len(fired) == 1
    # No-op identical re-registration -> no listener call.
    register_trait(TraitSpec(id="1.7.85", name="test_brightness"))
    assert len(fired) == 1
    # Mutation -> listener fires again.
    register_trait(TraitSpec(id="1.7.85", name="test_brightness", unit="%"))
    assert len(fired) == 2


def test_listener_failure_does_not_break_registration():
    def _boom() -> None:
        raise RuntimeError("listener failed")

    traits_catalog.set_change_listener(_boom)
    spec = TraitSpec(id="1.7.85", name="test_brightness")
    # Should not raise.
    returned = register_trait(spec)
    assert returned is spec
    assert get("1.7.85") is spec


def test_all_traits_returns_snapshot():
    register_trait(TraitSpec(id="1.7.85", name="test_brightness"))
    register_trait(TraitSpec(id="2.133.32923", name="test_motion"))
    snap = traits_catalog.all_traits()
    assert len(snap) == 2
    snap.append("not stored")  # type: ignore[arg-type]
    # Mutating the snapshot must not affect the catalog.
    assert len(traits_catalog.all_traits()) == 2


def test_traitspec_readable_defaults_true():
    spec = TraitSpec(id="1.1.1", name="x", data_type="number")
    assert spec.readable is True


def test_traitspec_writable_defaults_false():
    """V3 convention: traits are read-only by default. The V3 generator only
    writes writable=true to data.json when V3's spec says so; default must be
    False to avoid misclassifying read-only sensors as Number/Switch/Select."""
    spec = TraitSpec(id="1.1.1", name="x", data_type="number")
    assert spec.writable is False


def test_traitspec_existing_kwargs_default_readable_true_writable_false():
    """Construct without the new fields the way existing call sites do.
    Post-V3 cut-over: readable default stays True, writable flips to False."""
    spec = TraitSpec(id="1.7.85", name="brightness")
    assert spec.readable is True
    assert spec.writable is False


def test_traitspec_readable_writable_explicit():
    spec = TraitSpec(
        id="0.3.85", name="lux", data_type="uint",
        readable=True, writable=False,
    )
    assert spec.readable is True
    assert spec.writable is False


def test_traitspec_sources_defaults_to_observed_singleton():
    spec = TraitSpec(id="1.1.1", name="x", data_type="number")
    assert spec.sources == frozenset({"observed"})


def test_traitspec_default_enabled_defaults_true():
    spec = TraitSpec(id="1.1.1", name="x", data_type="number")
    assert spec.default_enabled is True


def test_traitspec_sources_explicit():
    spec = TraitSpec(
        id="0.3.85", name="lux", data_type="uint",
        sources=frozenset({"plugin", "api"}),
        default_enabled=True,
    )
    assert spec.sources == frozenset({"api", "plugin"})


def test_traitspec_default_enabled_false_for_ghost():
    spec = TraitSpec(
        id="0.7.85", name="termination_angle", data_type="number",
        sources=frozenset({"api"}),
        default_enabled=False,
    )
    assert spec.default_enabled is False
    assert spec.sources == frozenset({"api"})


def test_traitspec_existing_kwargs_still_work_with_safe_defaults():
    """Pre-existing call sites (no sources/default_enabled) keep their behaviour."""
    spec = TraitSpec(id="1.7.85", name="brightness")
    assert spec.sources == frozenset({"observed"})
    assert spec.default_enabled is True


def test_traitspec_trait_id_defaults_to_none():
    spec = TraitSpec(id="0.3.85", name="Lux", data_type="number")
    assert spec.trait_id is None


def test_traitspec_trait_id_explicit():
    spec = TraitSpec(
        id="0.3.85", name="Lux", data_type="number",
        trait_id=32989,
    )
    assert spec.trait_id == 32989


def test_traitspec_trait_id_preserves_hash_and_equality():
    """trait_id is part of the dataclass identity; specs with different
    trait_id are NOT equal and hash differently."""
    a = TraitSpec(id="0.3.85", name="Lux", data_type="number", trait_id=32989)
    b = TraitSpec(id="0.3.85", name="Lux", data_type="number", trait_id=32989)
    c = TraitSpec(id="0.3.85", name="Lux", data_type="number", trait_id=None)
    assert a == b
    assert hash(a) == hash(b)
    assert a != c


class TestSynthesisSource:
    def test_synthesis_source_defaults_to_none(self):
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        ts = TraitSpec(id="0.3.85", name="Lux")
        assert ts.synthesis_source is None

    def test_synthesis_source_accepts_cloud_trait(self):
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        ts = TraitSpec(id="0.3.85", name="Lux", synthesis_source="cloud_trait")
        assert ts.synthesis_source == "cloud_trait"

    def test_synthesis_source_accepts_panel_synth(self):
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        ts = TraitSpec(id="6.154.32989", name="Light sensor", synthesis_source="panel_synth")
        assert ts.synthesis_source == "panel_synth"

    def test_synthesis_source_survives_dataclass_replace(self):
        from custom_components.aqara_lanlink.device.traits import TraitSpec, replace
        ts = TraitSpec(id="6.154.32989", name="Light", synthesis_source="panel_synth")
        renamed = replace(ts, name="Light sensor")
        assert renamed.synthesis_source == "panel_synth"
        assert renamed.name == "Light sensor"

    def test_existing_callers_without_synthesis_source_still_work(self):
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        ts = TraitSpec(
            id="0.1.85",
            name="Environment temperature",
            description="Ambient temperature",
            data_type="number",
            readable=True,
            writable=False,
            sources=frozenset({"api", "plugin"}),
        )
        assert ts.synthesis_source is None


def test_enum_values_is_dict_str_str_or_none():
    """enum_values: None or dict[str, str], never a tuple."""
    spec_none = TraitSpec(id="0.0.0", name="x")
    assert spec_none.enum_values is None

    spec_dict = TraitSpec(
        id="0.0.1", name="y", enum_values={"0": "Off", "1": "On"}
    )
    assert spec_dict.enum_values == {"0": "Off", "1": "On"}
    assert isinstance(spec_dict.enum_values, dict)


def test_wire_path_defaults_to_none_and_accepts_string():
    """wire_path: str | None, default None."""
    assert TraitSpec(id="0.0.0", name="x").wire_path is None
    assert (
        TraitSpec(id="0.0.0", name="x", wire_path="2.160.33000").wire_path
        == "2.160.33000"
    )


def test_replace_works_with_wire_path():
    """dataclass.replace round-trips wire_path."""
    base = TraitSpec(id="0.0.0", name="x")
    updated = replace(base, wire_path="2.160.33000")
    assert updated.wire_path == "2.160.33000"
    assert base.wire_path is None  # immutable; base unchanged


def test_trait_spec_is_hashable_with_dict_enum_values():
    """Regression: dict enum_values must not break __hash__.

    A frozen dataclass with a dict field would normally raise
    TypeError on hash; TraitSpec's custom __hash__ handles this
    by hashing enum_values as sorted items.
    """
    spec = TraitSpec(
        id="0.0.0", name="x", enum_values={"0": "Off", "1": "On"}
    )
    # No exception:
    h = hash(spec)
    assert isinstance(h, int)


def test_trait_spec_hash_stable_across_enum_values_dict_iteration_order():
    """Hash is order-independent -- Python 3.7+ preserves insertion order, but
    semantically a dict with {0:Off, 1:On} == {1:On, 0:Off}.
    """
    a = TraitSpec(id="x", name="x", enum_values={"0": "Off", "1": "On"})
    b = TraitSpec(id="x", name="x", enum_values={"1": "On", "0": "Off"})
    assert a == b
    assert hash(a) == hash(b)


def test_trait_spec_hashable_in_set_membership():
    """A TraitSpec can be used as a set member or dict key."""
    spec = TraitSpec(id="0.0.0", name="x", enum_values={"0": "Off"})
    s = {spec}
    assert spec in s
