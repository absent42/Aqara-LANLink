"""Tests for the generic event platform."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.device.descriptors import EventDescriptor
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.event import AqaraEvent, async_setup_entry

from .conftest import make_device, make_hub, make_subentry


def _trait_event_descriptor() -> EventDescriptor:
    return EventDescriptor(
        key="test_ring",
        trigger_trait=TraitSpec(id="13.1.85", name="test_ring"),
        event_types=("ring",),
    )


def _source_event_descriptor() -> EventDescriptor:
    return EventDescriptor(
        key="multicast_ring",
        trigger_source="multicast_ring",
        event_types=("ring",),
    )


def test_trait_descriptor_with_multiple_event_types_without_enum_raises():
    """Multi-event-type trait-triggered events still raise WITHOUT an enum_values
    mapping -- there's no way to decide which event_type to fire."""
    desc = EventDescriptor(
        key="test_multi",
        trigger_trait=TraitSpec(id="13.1.85", name="test_multi"),
        event_types=("ring", "double_ring"),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    with pytest.raises(ValueError, match="no enum_values"):
        AqaraEvent(hub, device, sub, desc)


def test_trait_descriptor_with_multiple_event_types_and_enum_is_allowed():
    """V3 button traits have enum_values mapping wire codes to press labels;
    these MUST be allowed as multi-event-type trait-triggered events."""
    desc = EventDescriptor(
        key="auto_2_135_32928",
        trigger_trait=TraitSpec(
            id="2.135.32928", wire_path="2.135.32928",
            function_code="Button", trait_code="ButtonEvent",
            name="ButtonEvent", data_type="enum",
            readable=False, writable=False, subscribable=True, endpoint_id=2,
            enum_values={"1": "single_press", "2": "double_press", "16": "long_press"},
        ),
        event_types=("single_press", "double_press", "long_press"),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    assert entity._attr_event_types == ["single_press", "double_press", "long_press"]


def test_apply_value_with_enum_routes_wire_value_to_correct_event_type():
    """V3 button press: wire value '2' must fire 'double_press', not 'single_press'."""
    desc = EventDescriptor(
        key="auto_2_135_32928",
        trigger_trait=TraitSpec(
            id="2.135.32928", wire_path="2.135.32928",
            function_code="Button", trait_code="ButtonEvent",
            name="ButtonEvent", data_type="enum",
            readable=False, writable=False, subscribable=True, endpoint_id=2,
            enum_values={"1": "single_press", "2": "double_press", "16": "long_press"},
        ),
        event_types=("single_press", "double_press", "long_press"),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]

    entity.apply_value("1")
    entity._trigger_event.assert_called_with("single_press")
    entity.apply_value("2")
    entity._trigger_event.assert_called_with("double_press")
    entity.apply_value("16")
    entity._trigger_event.assert_called_with("long_press")
    assert entity._trigger_event.call_count == 3


def test_apply_value_with_enum_ignores_unknown_wire_value():
    """Wire codes not in enum_values are silently ignored (don't fire wrong event)."""
    desc = EventDescriptor(
        key="auto_2_135_32928",
        trigger_trait=TraitSpec(
            id="2.135.32928", wire_path="2.135.32928",
            function_code="Button", trait_code="ButtonEvent",
            name="ButtonEvent", data_type="enum",
            readable=False, writable=False, subscribable=True, endpoint_id=2,
            enum_values={"1": "single_press"},
        ),
        event_types=("single_press",),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]

    entity.apply_value("99")  # unknown wire code
    entity._trigger_event.assert_not_called()


def test_apply_value_attaches_raw_value_as_payload_when_key_set():
    """Face recognition: a single-type event whose payload carries the raw
    wire value (a registered-face id). The id can exceed 2^53, so it MUST be
    forwarded as the raw string, never coerced to int."""
    desc = EventDescriptor(
        key="auto_4_219_20217",
        trigger_trait=TraitSpec(
            id="4.219.20217", wire_path="4.219.20217",
            function_code="FaceRecognition", trait_code="FaceIDReport",
            name="FaceIDReport", data_type="int",
            readable=False, writable=False, subscribable=True, endpoint_id=4,
        ),
        event_types=("detected",),
        value_payload_key="face_id",
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]

    entity.apply_value("1509604449461735425")
    entity._trigger_event.assert_called_once_with(
        "detected", {"face_id": "1509604449461735425"}
    )


def test_apply_value_fires_unknown_event_type_for_unmapped_wire_value_when_set():
    """Recognition/detection events under-declare their enum (the cloud spec
    is incomplete). When unknown_event_type is set, an unmapped wire value
    fires that fallback type carrying the raw code as payload -- a discovery
    aid -- instead of being silently dropped like a strict button event."""
    desc = EventDescriptor(
        key="auto_7_217_20214",
        trigger_trait=TraitSpec(
            id="7.217.20214", wire_path="7.217.20214",
            function_code="SoundDetection", trait_code="AnomalySoundDetected",
            name="AnomalySoundDetected", data_type="enum",
            readable=False, writable=False, subscribable=True, endpoint_id=7,
            enum_values={"1": "CryDetected"},
        ),
        event_types=("CryDetected", "unknown"),
        unknown_event_type="unknown",
        value_payload_key="code",
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]

    # Mapped value still routes to its label (with the raw code as payload).
    entity.apply_value("1")
    entity._trigger_event.assert_called_with("CryDetected", {"code": "1"})
    # Unmapped value fires the fallback type instead of being dropped.
    entity.apply_value("5")
    entity._trigger_event.assert_called_with("unknown", {"code": "5"})
    assert entity._trigger_event.call_count == 2


def test_apply_value_without_unknown_event_type_still_drops_unmapped():
    """Strict events (buttons/doorbells: no unknown_event_type) keep dropping
    unmapped wire values -- the recognition fallback must not regress them."""
    desc = EventDescriptor(
        key="auto_2_135_32928",
        trigger_trait=TraitSpec(
            id="2.135.32928", wire_path="2.135.32928",
            function_code="Button", trait_code="ButtonEvent",
            name="ButtonEvent", data_type="enum",
            readable=False, writable=False, subscribable=True, endpoint_id=2,
            enum_values={"1": "single_press"},
        ),
        event_types=("single_press",),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]

    entity.apply_value("99")
    entity._trigger_event.assert_not_called()


def test_source_descriptor_with_multiple_event_types_is_allowed():
    """Source-triggered events choose event_type imperatively."""
    desc = EventDescriptor(
        key="multicast_multi",
        trigger_source="multicast_multi",
        event_types=("ring", "double_ring"),
    )
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    assert entity._attr_event_types == ["ring", "double_ring"]


def test_event_types_exposed_to_ha():
    desc = _trait_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    assert entity._attr_event_types == ["ring"]


def test_apply_value_trait_mode_fires_first_event_type():
    desc = _trait_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]
    entity.apply_value("1")
    entity._trigger_event.assert_called_once_with("ring")


def test_trigger_event_source_mode_passes_payload_dict():
    desc = _source_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]
    entity.trigger_event("ring", source_ip="192.0.2.1")
    entity._trigger_event.assert_called_once_with("ring", {"source_ip": "192.0.2.1"})


def test_trigger_event_source_mode_no_payload_passes_none():
    desc = _source_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]
    entity.trigger_event("ring")
    entity._trigger_event.assert_called_once_with("ring", None)


def test_device_handle_report_routes_to_event_apply_value():
    """Trait-mode end-to-end: device dispatch reaches the entity."""
    desc = _trait_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]
    device.register_entity(desc, entity)

    device.handle_report(SimpleNamespace(values={"13.1.85": "1"}))
    entity._trigger_event.assert_called_once_with("ring")


def test_device_fire_event_routes_to_entity_trigger_event():
    """Source-mode end-to-end: Device.fire_event calls entity.trigger_event."""
    desc = _source_event_descriptor()
    hub = make_hub()
    sub = make_subentry()
    device = make_device([desc], subentry=sub)
    entity = AqaraEvent(hub, device, sub, desc)
    entity._trigger_event = MagicMock()  # type: ignore[method-assign]
    device.register_entity(desc, entity)

    device.fire_event("multicast_ring", "ring", source_ip="192.0.2.1")
    entity._trigger_event.assert_called_once_with("ring", {"source_ip": "192.0.2.1"})


@pytest.mark.asyncio
async def test_async_setup_entry_creates_entity_per_descriptor():
    trait_desc = _trait_event_descriptor()
    source_desc = _source_event_descriptor()
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([trait_desc, source_desc], subentry=sub)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(hass=MagicMock(), entry=entry, async_add_entities=lambda es, **_: added.extend(es))
    keys = sorted(e.descriptor.key for e in added)
    assert keys == ["multicast_ring", "test_ring"]
