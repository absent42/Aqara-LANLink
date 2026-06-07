"""Tests for the Doorbell deviceType composer."""
from __future__ import annotations

from homeassistant.components.event import EventDeviceClass

from custom_components.aqara_lanlink.device.device_types import (
    _base, doorbell, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    EventDescriptor, NumberDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.camera.agl013")


def _button_event() -> TraitSpec:
    """The G400's doorbell button: enum trait at wire path 9.170.32928."""
    return TraitSpec(
        id="9.170.32928", wire_path="9.170.32928",
        function_code="Doorbell", trait_code="ButtonEvent",
        name="ButtonEvent", data_type="enum",
        readable=True, subscribable=True, endpoint_id=9,
        enum_values={"1": "press"},
    )


def _volume() -> TraitSpec:
    """G400 doorbell volume (writable number)."""
    return TraitSpec(
        id="9.170.32960", wire_path="9.170.32960",
        function_code="Doorbell", trait_code="Volume",
        name="Volume", data_type="float",
        readable=True, writable=True, subscribable=True, endpoint_id=9,
        min_value=0.0, max_value=100.0, step=1.0,
    )


def test_button_event_becomes_doorbell_event_descriptor():
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32928": _button_event()}, context=_ctx(),
    )
    assert len(descs) == 1
    assert isinstance(descs[0], EventDescriptor)
    assert descs[0].device_class == EventDeviceClass.DOORBELL
    assert descs[0].trigger_trait.id == "9.170.32928"


def test_event_types_normalised_to_ring_regardless_of_v3_enum_label():
    """HA's EventDeviceClass.DOORBELL canonical event_type is "ring". V3's
    enum may label the wire code as "press" or "ring" or anything else, but
    the doorbell composer normalises every wire code to "ring" so user
    automations subscribe to a stable event_type."""
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32928": _button_event()}, context=_ctx(),
    )
    assert descs[0].event_types == ("ring",)
    # The trigger_trait carries a normalised enum_values where every wire
    # code maps to "ring". The original V3 enum_values had {"1": "press"},
    # but the composer rewrote it.
    assert descs[0].trigger_trait.enum_values == {"1": "ring"}


def test_multi_press_doorbell_preserves_each_press_type():
    """A multi-press doorbell (e.g. camera_agl013: Single/Double press) must
    expose each press as a distinct event_type, not collapse both to "ring"
    -- otherwise double-press is unusable. Labels are humanized in the
    catalogue, so they're used verbatim and device_class stays DOORBELL."""
    import dataclasses
    spec = dataclasses.replace(
        _button_event(),
        enum_values={"0": "Single press", "1": "Double press"},
    )
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32928": spec}, context=_ctx(),
    )
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, EventDescriptor)
    assert d.device_class == EventDeviceClass.DOORBELL
    assert d.event_types == ("Single press", "Double press")
    # labels pass through unchanged so apply_value maps wire -> press type
    assert d.trigger_trait.enum_values == {"0": "Single press", "1": "Double press"}


def test_event_types_default_to_ring_when_no_enum_values():
    spec = _button_event()
    # Spec without enum_values (firmware that reports a bare event signal).
    import dataclasses
    spec = dataclasses.replace(spec, enum_values=None)
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32928": spec}, context=_ctx(),
    )
    assert descs[0].event_types == ("ring",)


def test_volume_delegates_to_fallback_number():
    """Volume is a writable numeric range — _fallback emits a NumberDescriptor."""
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32960": _volume()}, context=_ctx(),
    )
    assert len(descs) == 1
    assert isinstance(descs[0], NumberDescriptor)


def test_full_doorbell_set():
    """ButtonEvent + Volume together produce one Event + one Number."""
    traits = {t.id: t for t in (_button_event(), _volume())}
    descs = doorbell.compose(endpoint_id=9, traits=traits, context=_ctx())
    assert len(descs) == 2
    kinds = sorted(type(d).__name__ for d in descs)
    assert kinds == ["EventDescriptor", "NumberDescriptor"]


def test_doorbell_composer_registered():
    assert get_composer("Doorbell") is doorbell.compose
