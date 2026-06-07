"""Tests for the Switch deviceType composer."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, switch, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.switch.l3acn1")


def _onoff_trait(wp: str = "2.130.32945") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="Output", trait_code="OnOff",
        name="OnOff", data_type="bool",
        readable=True, writable=True, subscribable=True,
        endpoint_id=int(wp.split(".")[0]),
    )


def test_onoff_becomes_switch_descriptor():
    descs = switch.compose(endpoint_id=2, traits={"2.130.32945": _onoff_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SwitchDescriptor)


def test_switch_attr_name_is_wire_path():
    """SwitchDescriptor's attr.name must be the wire path for routing."""
    descs = switch.compose(endpoint_id=2, traits={"2.130.32945": _onoff_trait()}, context=_ctx())
    assert descs[0].attr.name == "2.130.32945"


def test_switch_composer_registered():
    assert get_composer("Switch") is switch.compose


def test_outlet_alias_routes_to_switch_composer():
    """Multi-outlet plug strips (e.g. lumi.plug.aeu002) declare each socket
    as deviceType "Outlet". Trait surface per Outlet endpoint is just
    Output.OnOff -- identical to a Switch endpoint -- so the alias must
    route to the same composer rather than falling through to per-trait
    classification (which logs a WARNING per endpoint per setup)."""
    assert get_composer("Outlet") is switch.compose


def test_switch_without_onoff_emits_nothing():
    assert switch.compose(endpoint_id=2, traits={}, context=_ctx()) == []
