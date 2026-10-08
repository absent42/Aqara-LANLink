"""Tests for the Doorbell deviceType composer."""
from __future__ import annotations

import dataclasses

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


def test_multi_press_doorbell_keeps_ring_and_preserves_extra_presses():
    """A multi-press doorbell (e.g. camera_agl013: Single/Double press) must
    still SUPPORT the 'ring' event_type -- HA deprecates DOORBELL event
    entities without it (removal in 2027.4) -- while preserving the extra
    presses. The primary press IS the ring; additional presses keep their
    humanized labels."""
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
    assert "ring" in d.event_types          # HA DOORBELL contract
    assert d.event_types == ("ring", "Double press")
    # Single press -> ring (canonical); double press keeps its label.
    assert d.trigger_trait.enum_values == {"0": "ring", "1": "Double press"}


def test_event_types_default_to_ring_when_no_enum_values():
    spec = _button_event()
    # Spec without enum_values (firmware that reports a bare event signal).
    import dataclasses
    spec = dataclasses.replace(spec, enum_values=None)
    descs = doorbell.compose(
        endpoint_id=9, traits={"9.170.32928": spec}, context=_ctx(),
    )
    assert descs[0].event_types == ("ring",)


def test_camera_endpoint_doorbell_button_event_is_event_with_zero_code():
    """Trait semantics win over Camera endpoint enum-sensor fallback."""
    from custom_components.aqara_lanlink.device.device_types import _fallback

    spec = dataclasses.replace(
        _button_event(), endpoint_id=2, id="2.170.32928",
        wire_path="2.170.32928", enum_values={"0": "Single press"},
    )
    descs = _fallback.compose(
        endpoint_id=2, traits={spec.wire_path: spec}, context=_ctx(),
    )
    assert len(descs) == 1
    desc = descs[0]
    assert isinstance(desc, EventDescriptor)
    assert desc.device_class == EventDeviceClass.DOORBELL
    assert desc.trigger_trait.enum_values == {"0": "ring"}
    assert desc.event_types == ("ring",)


def test_camera_endpoint_button_event_and_volume_keep_distinct_descriptors():
    """The cross-type event override must not absorb ordinary traits."""
    from custom_components.aqara_lanlink.device.device_types import _fallback

    event = dataclasses.replace(
        _button_event(), endpoint_id=2, id="2.170.32928",
        wire_path="2.170.32928", enum_values={"0": "Single press"},
    )
    volume = dataclasses.replace(
        _volume(), endpoint_id=2, id="2.170.32960", wire_path="2.170.32960",
    )
    descs = _fallback.compose(
        endpoint_id=2,
        traits={event.wire_path: event, volume.wire_path: volume},
        context=_ctx(),
    )
    assert len(descs) == 2
    event_desc = next(d for d in descs if d.key.endswith("2_170_32928"))
    volume_desc = next(d for d in descs if d.key.endswith("2_170_32960"))
    assert isinstance(event_desc, EventDescriptor)
    assert isinstance(volume_desc, NumberDescriptor)


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


def test_every_doorbell_event_in_catalogue_supports_ring():
    """Invariant: every DOORBELL-class event entity across all shipped models
    must include 'ring' in its event_types. HA deprecates doorbell event
    entities that don't (removal in 2027.4)."""
    from pathlib import Path

    from custom_components.aqara_lanlink.device.classify_v3 import classify_v3
    from custom_components.aqara_lanlink.device.models._loader import load_model_data
    from custom_components.aqara_lanlink.device.descriptors import EventDescriptor

    models_root = (
        Path(__file__).resolve().parents[3]
        / "custom_components" / "aqara_lanlink" / "device" / "models"
    )
    offenders = []
    for pkg in sorted(p for p in models_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        data = load_model_data(pkg)
        descs = classify_v3(
            model=(data["MODELS"] or [pkg.name])[0],
            endpoints=data["ENDPOINTS"], traits=data["TRAITS"],
        )
        for d in descs:
            if isinstance(d, EventDescriptor) and d.device_class == EventDeviceClass.DOORBELL:
                if "ring" not in d.event_types:
                    offenders.append((pkg.name, d.key, d.event_types))
    assert not offenders, (
        "DOORBELL event entities missing the 'ring' event_type:\n"
        + "\n".join(f"  {m} {k}: {et}" for m, k, et in offenders)
    )


def test_every_catalogue_button_event_is_an_event_descriptor():
    """Every shipped ButtonEvent trait must retain event semantics, regardless
    of whether its endpoint is declared Button, Doorbell, or Camera."""
    from pathlib import Path

    from custom_components.aqara_lanlink.device.classify_v3 import classify_v3
    from custom_components.aqara_lanlink.device.models._loader import load_model_data

    models_root = (
        Path(__file__).resolve().parents[3]
        / "custom_components" / "aqara_lanlink" / "device" / "models"
    )
    offenders = []
    for pkg in sorted(p for p in models_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        data = load_model_data(pkg)
        descs = classify_v3(
            model=(data["MODELS"] or [pkg.name])[0],
            endpoints=data["ENDPOINTS"], traits=data["TRAITS"],
        )
        button_event_paths = {
            trait.wire_path or trait.id
            for trait in data["TRAITS"].values()
            if trait.trait_code == "ButtonEvent"
        }
        event_paths = {
            d.trigger_trait.wire_path or d.trigger_trait.id
            for d in descs if isinstance(d, EventDescriptor)
        }
        offenders.extend(
            (pkg.name, path) for path in sorted(button_event_paths - event_paths)
        )
    assert not offenders, (
        "ButtonEvent traits missing EventDescriptor classification:\n"
        + "\n".join(f"  {model} {path}" for model, path in offenders)
    )
