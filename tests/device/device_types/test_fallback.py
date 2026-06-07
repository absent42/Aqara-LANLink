"""Tests for the _fallback composer (unknown deviceType handler)."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import _base, _fallback
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor, SensorDescriptor, SwitchDescriptor,
    SelectDescriptor, NumberDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.unknown.test")


def test_fallback_empty_traits_returns_empty_list():
    assert _fallback.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_fallback_bool_readable_becomes_binary_sensor():
    traits = {"2.99.32000": TraitSpec(
        id="2.99.32000", wire_path="2.99.32000",
        function_code="Unknown", trait_code="SomeBool",
        name="SomeBool", data_type="bool",
        readable=True, writable=False, subscribable=True, endpoint_id=2,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], BinarySensorDescriptor)
    assert descs[0].trait.id == "2.99.32000"


def test_fallback_bool_writable_becomes_switch():
    traits = {"2.99.32001": TraitSpec(
        id="2.99.32001", wire_path="2.99.32001",
        function_code="Unknown", trait_code="SomeWriteBool",
        name="SomeWriteBool", data_type="bool",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SwitchDescriptor)


def test_fallback_numeric_readable_becomes_sensor():
    for dtype in ("float", "int"):
        traits = {"2.99.32002": TraitSpec(
            id="2.99.32002", wire_path="2.99.32002",
            function_code="Unknown", trait_code="SomeNum",
            name="SomeNum", data_type=dtype,
            readable=True, writable=False, subscribable=True, endpoint_id=2,
        )}
        descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
        assert len(descs) == 1
        assert isinstance(descs[0], SensorDescriptor), f"data_type={dtype}"


def test_fallback_numeric_writable_becomes_number():
    traits = {"2.99.32003": TraitSpec(
        id="2.99.32003", wire_path="2.99.32003",
        function_code="Unknown", trait_code="SomeRange",
        name="SomeRange", data_type="int",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0, max_value=100,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], NumberDescriptor)


def test_fallback_enum_writable_becomes_select():
    traits = {"2.99.32004": TraitSpec(
        id="2.99.32004", wire_path="2.99.32004",
        function_code="Unknown", trait_code="SomeMode",
        name="SomeMode", data_type="enum",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        enum_values={"0": "off", "1": "on"},
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SelectDescriptor)


def test_fallback_enum_readonly_becomes_sensor():
    traits = {"2.99.32005": TraitSpec(
        id="2.99.32005", wire_path="2.99.32005",
        function_code="Unknown", trait_code="SomeState",
        name="SomeState", data_type="enum",
        readable=True, writable=False, subscribable=True, endpoint_id=2,
        enum_values={"0": "off", "1": "on"},
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_fallback_string_becomes_sensor():
    traits = {"2.99.32006": TraitSpec(
        id="2.99.32006", wire_path="2.99.32006",
        function_code="Unknown", trait_code="SomeString",
        name="SomeString", data_type="string",
        readable=True, writable=False, subscribable=False, endpoint_id=2,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)


def test_fallback_multiple_traits_produce_multiple_descriptors():
    traits = {
        "2.99.32000": TraitSpec(
            id="2.99.32000", wire_path="2.99.32000",
            function_code="X", trait_code="A", name="A", data_type="bool",
            readable=True, writable=False, subscribable=True, endpoint_id=2,
        ),
        "2.99.32001": TraitSpec(
            id="2.99.32001", wire_path="2.99.32001",
            function_code="X", trait_code="B", name="B", data_type="float",
            readable=True, writable=False, subscribable=True, endpoint_id=2,
        ),
    }
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 2


def test_fallback_propagates_diagnostic_entity_category():
    from homeassistant.helpers.entity import EntityCategory
    traits = {"1.205.33107": TraitSpec(
        id="1.205.33107", wire_path="1.205.33107",
        function_code="ZigbeeNetworkDiagnostics", trait_code="LQI",
        name="LQI", data_type="int",
        readable=True, writable=False, subscribable=True, endpoint_id=1,
        entity_category="diagnostic", default_enabled=False,
    )}
    descs = _fallback.compose(endpoint_id=1, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].entity_category == EntityCategory.DIAGNOSTIC


def test_fallback_propagates_default_enabled_to_entity_registry_flag():
    traits = {"1.205.33107": TraitSpec(
        id="1.205.33107", wire_path="1.205.33107",
        function_code="ZigbeeNetworkDiagnostics", trait_code="LQI",
        name="LQI", data_type="int",
        readable=True, writable=False, subscribable=True, endpoint_id=1,
        entity_category="diagnostic", default_enabled=False,
    )}
    descs = _fallback.compose(endpoint_id=1, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].entity_registry_enabled_default is False


def test_fallback_visible_trait_has_no_entity_category():
    traits = {"2.160.33000": TraitSpec(
        id="2.160.33000", wire_path="2.160.33000",
        function_code="OccupancySensing", trait_code="Occupancy",
        name="Occupancy", data_type="bool",
        readable=True, writable=False, subscribable=True, endpoint_id=2,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert descs[0].entity_category is None
    assert descs[0].entity_registry_enabled_default is True


# -----------------------------------------------------------------------------
# Button-trait recognition: traits in trait_policy.BUTTON_TRAITS render as
# stateless ButtonDescriptor (press to write press_value to the trait),
# overriding the data_type-dispatched default that would otherwise produce
# a Switch (for writable bools) or Number (for writable ints).
# -----------------------------------------------------------------------------


def test_fallback_emits_button_descriptor_when_trait_in_button_traits(monkeypatch):
    """When a (function, trait) pair is in trait_policy.BUTTON_TRAITS,
    _fallback.compose emits a ButtonDescriptor with the dict-configured
    press_value -- short-circuiting the writable-int -> Number / writable-
    bool -> Switch defaults. Uses a monkeypatched BUTTON_TRAITS so the
    test exercises the mechanism even though no production traits
    currently use it (the Identify experiment was rolled back).
    """
    from custom_components.aqara_lanlink.device import trait_policy
    from custom_components.aqara_lanlink.device.descriptors import ButtonDescriptor
    monkeypatch.setitem(
        trait_policy.BUTTON_TRAITS, ("TestFunction", "TestTrigger"), "42",
    )
    traits = {"1.99.999": TraitSpec(
        id="1.99.999", wire_path="1.99.999",
        function_code="TestFunction", trait_code="TestTrigger",
        name="TestTrigger", data_type="int",
        readable=True, writable=True, subscribable=True, endpoint_id=1,
        min_value=0.0, max_value=255.0,
    )}
    descs = _fallback.compose(endpoint_id=1, traits=traits, context=_ctx())
    assert len(descs) == 1
    desc = descs[0]
    assert isinstance(desc, ButtonDescriptor)
    assert desc.attr.name == "1.99.999"
    assert desc.press_value == "42"


def test_non_button_writable_int_still_renders_as_number():
    """Regression guard: only traits in BUTTON_TRAITS get the button
    short-circuit -- any other writable int with min/max still becomes a
    NumberDescriptor as before.
    """
    from custom_components.aqara_lanlink.device.descriptors import NumberDescriptor
    traits = {"2.133.33125": TraitSpec(
        id="2.133.33125", wire_path="2.133.33125",
        function_code="LevelControl", trait_code="OnTransitionTime",
        name="OnTransitionTime", data_type="int", unit="ms",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=10000.0,
    )}
    descs = _fallback.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], NumberDescriptor)


def test_fallback_carries_device_class_without_explicit_platform():
    """A read-only int trait with device_class set (but no platform) must
    surface that device_class on the inferred SensorDescriptor."""
    spec = TraitSpec(
        id="1.10.100", wire_path="1.10.100", name="Pressure",
        data_type="int", device_class="atmospheric_pressure",
        unit="hPa", endpoint_id=1,
    )
    ctx = _base.ComposeContext(model="m")
    descs = _fallback.compose(endpoint_id=1, traits={spec.id: spec}, context=ctx)
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].device_class == "atmospheric_pressure"

