"""Tests for the Button deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, button, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import EventDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.remote.b1acn01")


def _button_event_trait() -> TraitSpec:
    """Realistic V3 button-event trait: Enum with click types."""
    return TraitSpec(
        id="2.135.32928", wire_path="2.135.32928",
        function_code="Button", trait_code="ButtonEvent",
        name="ButtonEvent", data_type="enum",
        readable=False, writable=False, subscribable=True, endpoint_id=2,
        enum_values={
            "1": "single_press", "2": "double_press", "16": "long_press",
        },
    )


def test_button_event_classifies_as_event_descriptor():
    """The critical contract: Button.ButtonEvent MUST be EventDescriptor."""
    traits = {"2.135.32928": _button_event_trait()}
    descs = button.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], EventDescriptor)


def test_button_event_wire_path_preserved():
    """EventDescriptor.trigger_trait.id is the wire path so push routing works."""
    traits = {"2.135.32928": _button_event_trait()}
    descs = button.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert descs[0].trigger_trait is not None
    assert descs[0].trigger_trait.id == "2.135.32928"


def test_button_event_types_derived_from_enum_values():
    """event_types is populated from the trait's enum_values labels."""
    traits = {"2.135.32928": _button_event_trait()}
    descs = button.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert set(descs[0].event_types) == {"single_press", "double_press", "long_press"}


def test_button_composer_registered_in_dispatcher():
    """get_composer('Button') returns button.compose, not _fallback."""
    assert get_composer("Button") is button.compose


def test_button_emits_no_event_when_buttonevent_missing_but_falls_back_for_others():
    """A Button endpoint with no ButtonEvent trait emits no Event entity, but
    any other traits still flow through _fallback so config/diagnostic knobs
    surface as Number/Switch/etc. -- they are not dropped silently."""
    from custom_components.aqara_lanlink.device.descriptors import BinarySensorDescriptor
    traits = {"2.135.99999": TraitSpec(
        id="2.135.99999", wire_path="2.135.99999",
        function_code="Button", trait_code="OtherTrait",
        name="OtherTrait", data_type="bool",
        readable=True, subscribable=True, endpoint_id=2,
        # explicit writable=False -> _fallback emits a BinarySensor, not a Switch
    )}
    descs = button.compose(endpoint_id=2, traits=traits, context=_ctx())
    # No EventDescriptor (no ButtonEvent), but the unknown bool trait surfaces
    # via _fallback as a BinarySensor rather than being dropped.
    from custom_components.aqara_lanlink.device.descriptors import EventDescriptor
    assert not any(isinstance(d, EventDescriptor) for d in descs)
    assert any(isinstance(d, BinarySensorDescriptor) for d in descs)
