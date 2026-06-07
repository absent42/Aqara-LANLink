"""Tests for v3d-7 TraitSpec.__post_init__ validation."""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ts(**kwargs):
    """Build a minimal valid TraitSpec, allowing kwargs to override."""
    defaults = dict(
        id="9.9.85", name="Test", data_type="uint",
        readable=True, writable=False,
    )
    defaults.update(kwargs)
    return TraitSpec(**defaults)


def test_valid_basic_traitspec_constructs():
    """The minimal field set (existing fields only) constructs without error."""
    spec = _ts()
    assert spec.platform is None
    assert spec.entity_category is None


@pytest.mark.parametrize("field", ["id", "name", "unit", "wire_path", "device_class"])
def test_control_char_in_string_field_rejected(field):
    # Control characters (newline etc.) in free-form fields would corrupt the
    # generated overrides.py snippet (second-order injection) and have no
    # legitimate place in trait metadata. Reject at construction.
    with pytest.raises(RuntimeError):
        _ts(**{field: "bad\nvalue"})


def test_printable_unicode_unit_allowed():
    # Regression: legitimate units like degrees-C / micrograms must NOT be
    # rejected -- only control characters are.
    spec = _ts(unit="°C", platform="sensor")  # degree sign
    assert spec.unit == "°C"
    spec2 = _ts(unit="µg/m³", platform="sensor")  # micro, superscript 3
    assert spec2.unit == "µg/m³"


def test_control_char_in_enum_values_rejected():
    with pytest.raises(RuntimeError):
        _ts(enum_values={"0": "of\nf"})


def test_platform_known_value_accepted():
    for p in ("sensor", "binary_sensor", "switch", "number", "select", "event"):
        spec = _ts(platform=p)
        assert spec.platform == p


def test_platform_unknown_value_raises():
    with pytest.raises(RuntimeError, match="platform"):
        _ts(platform="bogus")


def test_entity_category_known_values_accepted():
    for c in (None, "config", "diagnostic"):
        _ts(entity_category=c)


def test_entity_category_unknown_value_raises():
    with pytest.raises(RuntimeError, match="entity_category"):
        _ts(entity_category="bogus")


def test_state_class_requires_explicit_sensor_platform():
    """state_class with no platform set -> error (no inference at construction time)."""
    with pytest.raises(RuntimeError, match="state_class"):
        _ts(state_class="measurement")  # platform=None
    with pytest.raises(RuntimeError, match="state_class"):
        _ts(platform="binary_sensor", state_class="measurement")


def test_state_class_with_sensor_platform_accepted():
    _ts(platform="sensor", state_class="measurement")
    _ts(platform="sensor", state_class="total_increasing")


def test_scale_requires_explicit_sensor_or_number_platform():
    with pytest.raises(RuntimeError, match="scale"):
        _ts(scale=0.001)
    with pytest.raises(RuntimeError, match="scale"):
        _ts(platform="binary_sensor", scale=0.001)
    _ts(platform="sensor", scale=0.001)
    _ts(platform="number", scale=0.001)


def test_unit_of_measurement_requires_explicit_sensor_or_number_platform():
    with pytest.raises(RuntimeError, match="unit_of_measurement"):
        _ts(unit_of_measurement="V")
    _ts(platform="sensor", unit_of_measurement="V")


def test_suggested_display_precision_requires_explicit_sensor_or_number_platform():
    with pytest.raises(RuntimeError, match="suggested_display_precision"):
        _ts(suggested_display_precision=2)
    _ts(platform="sensor", suggested_display_precision=2)


def test_device_class_accepts_any_string():
    """device_class is platform-specific; we don't validate the value here."""
    _ts(device_class="voltage")
    _ts(device_class="battery")
    _ts(device_class="some_future_class")


def test_icon_accepts_any_string():
    _ts(icon="mdi:test")


def test_existing_validation_still_works():
    """The new __post_init__ must not break existing TraitSpec construction
    pathways (e.g. with all existing fields populated)."""
    spec = TraitSpec(
        id="0.4.85", name="Lux", description="Light value",
        data_type="number", readable=True, writable=False,
        sources=frozenset({"api"}), default_enabled=False,
    )
    assert spec.id == "0.4.85"
    assert spec.platform is None


def test_scale_must_be_finite():
    """scale=nan or scale=inf raises at construction."""
    with pytest.raises(RuntimeError, match="scale.*finite"):
        _ts(platform="sensor", scale=float('nan'))
    with pytest.raises(RuntimeError, match="scale.*finite"):
        _ts(platform="sensor", scale=float('inf'))
    with pytest.raises(RuntimeError, match="scale.*finite"):
        _ts(platform="sensor", scale=float('-inf'))


def test_scale_zero_rejected():
    """scale=0.0 raises at construction.

    scale=0 would silently produce always-zero reads (apply_value
    multiplies by 0) AND skip the number-write divide-by-scale path
    entirely, leaving wire writes unscaled. Asymmetric, silent, bad.
    """
    with pytest.raises(RuntimeError, match="scale.*non-zero"):
        _ts(platform="sensor", scale=0.0)
    with pytest.raises(RuntimeError, match="scale.*non-zero"):
        _ts(platform="number", scale=0.0)


def test_v3d7_fields_participate_in_hash_and_equality():
    """Two TraitSpecs differing in a v3d-7 field must be unequal AND have different hashes."""
    a = _ts(platform="sensor")
    b = _ts(platform="binary_sensor")
    assert a != b
    assert hash(a) != hash(b)

    # Same applied to entity_category:
    c = _ts(entity_category="config")
    d = _ts(entity_category="diagnostic")
    assert c != d
    assert hash(c) != hash(d)

    # Sanity: identical specs are equal AND share a hash.
    e1 = _ts(platform="sensor", scale=0.001)
    e2 = _ts(platform="sensor", scale=0.001)
    assert e1 == e2
    assert hash(e1) == hash(e2)


def test_trait_spec_auto_clear_seconds_requires_binary_sensor():
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    with pytest.raises(RuntimeError, match="auto_clear_seconds"):
        TraitSpec(id="1.1.1", name="x", platform="sensor", auto_clear_seconds=30.0)
    TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=30.0)


def test_trait_spec_auto_clear_seconds_must_be_positive_finite():
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    with pytest.raises(RuntimeError, match="auto_clear_seconds"):
        TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=0.0)
    with pytest.raises(RuntimeError, match="auto_clear_seconds"):
        TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=float("nan"))
    with pytest.raises(RuntimeError, match="auto_clear_seconds"):
        TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=float("-inf"))


def test_button_is_a_valid_platform():
    # Must not raise; button is an overridable platform.
    TraitSpec(id="1.1.1", wire_path="1.1.1", name="Identify",
              data_type="enum", platform="button")


def test_trait_spec_auto_clear_seconds_hashable():
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    a = TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=30.0)
    b = TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=30.0)
    assert hash(a) == hash(b)
    c = TraitSpec(id="1.1.1", name="x", platform="binary_sensor", auto_clear_seconds=60.0)
    assert a != c
    assert hash(a) != hash(c)
