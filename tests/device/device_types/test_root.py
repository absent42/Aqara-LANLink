"""Tests for the Root deviceType composer (endpoint 0)."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, _fallback, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    SensorDescriptor, SelectDescriptor, NumberDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from homeassistant.helpers.entity import EntityCategory


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.test")


def test_root_firmware_revision_string_becomes_diagnostic_sensor():
    traits = {"0.128.33047": TraitSpec(
        id="0.128.33047", wire_path="0.128.33047",
        function_code="BasicInformation", trait_code="FirmwareRevisionString",
        name="FirmwareRevisionString", data_type="string",
        readable=True, subscribable=True, endpoint_id=0,
        entity_category="diagnostic", default_enabled=False,
    )}
    descs = _fallback.compose(endpoint_id=0, traits=traits, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].entity_category == EntityCategory.DIAGNOSTIC
    assert descs[0].entity_registry_enabled_default is False


def test_root_identify_traits_are_diagnostic_fallback_entities():
    """Identify.IdentifyTime and Identify.IdentifyType are both config /
    state attributes that don't trigger anything when written (empirically
    confirmed on every Aqara device). Surfaced via _fallback's data_type
    dispatch under the diagnostic category set by the generator; no
    BUTTON_TRAITS short-circuit applies."""
    from custom_components.aqara_lanlink.device.descriptors import ButtonDescriptor
    traits = {
        "0.131.32916": TraitSpec(
            id="0.131.32916", wire_path="0.131.32916",
            function_code="Identify", trait_code="IdentifyTime",
            name="IdentifyTime", data_type="float",
            readable=True, writable=False, subscribable=True, endpoint_id=0,
            min_value=0.0, max_value=255.0, unit="s",
        ),
        "0.131.32917": TraitSpec(
            id="0.131.32917", wire_path="0.131.32917",
            function_code="Identify", trait_code="IdentifyType",
            name="IdentifyType", data_type="enum",
            readable=True, writable=True, subscribable=True, endpoint_id=0,
            enum_values={"0": "blink", "1": "breathe"},
        ),
    }
    descs = _fallback.compose(endpoint_id=0, traits=traits, context=_ctx())
    # Neither becomes a ButtonDescriptor.
    assert not any(isinstance(d, ButtonDescriptor) for d in descs)
    # IdentifyType (writable enum) is rendered as a Select via the
    # _fallback enum branch.
    selects = [d for d in descs if isinstance(d, SelectDescriptor)]
    assert any(s.attr.name == "0.131.32917" for s in selects)


def test_root_composer_registered():
    assert get_composer("Root") is _fallback.compose


def test_root_empty_traits_returns_empty():
    assert _fallback.compose(endpoint_id=0, traits={}, context=_ctx()) == []
