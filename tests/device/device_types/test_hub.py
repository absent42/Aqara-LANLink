"""Tests for the Hub deviceType composer (endpoint 1; delegates to _fallback)."""
from __future__ import annotations

from homeassistant.helpers.entity import EntityCategory

from custom_components.aqara_lanlink.device.device_types import (
    _base, _fallback, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SensorDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.gateway.aeu01")


def _lqi() -> TraitSpec:
    return TraitSpec(
        id="1.137.32944", wire_path="1.137.32944",
        function_code="EndpointDescriptor", trait_code="Lqi",
        name="Lqi", data_type="uint8",
        readable=True, subscribable=True, endpoint_id=1,
        entity_category="diagnostic",
    )


def _reboot_count() -> TraitSpec:
    return TraitSpec(
        id="1.150.32988", wire_path="1.150.32988",
        function_code="GeneralDiagnostics", trait_code="RebootCount",
        name="RebootCount", data_type="uint32",
        readable=True, subscribable=True, endpoint_id=1,
        entity_category="diagnostic",
    )


def test_hub_lqi_becomes_diagnostic_sensor():
    descs = _fallback.compose(endpoint_id=1, traits={"1.137.32944": _lqi()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].entity_category == EntityCategory.DIAGNOSTIC


def test_hub_reboot_count_becomes_diagnostic_sensor():
    descs = _fallback.compose(endpoint_id=1, traits={"1.150.32988": _reboot_count()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SensorDescriptor)
    assert descs[0].entity_category == EntityCategory.DIAGNOSTIC


def test_hub_empty_traits_returns_empty():
    assert _fallback.compose(endpoint_id=1, traits={}, context=_ctx()) == []


def test_hub_composer_registered():
    assert get_composer("Hub") is _fallback.compose
