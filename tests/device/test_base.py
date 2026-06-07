"""Tests for the Device base class and AutoDerivedDevice stock subclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.base import (
    AutoDerivedDevice,
    Device,
)
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor,
    EventDescriptor,
    LightDescriptor,
    NumberDescriptor,
    SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


# ---------------------------------------------------------------------------
# Test fixtures: synthetic descriptors + a fake report shape that matches
# `LANReport` enough for the dispatch path.
# ---------------------------------------------------------------------------

TEST_TRAIT_MOTION = TraitSpec(id="3.1.85", name="test_motion")
TEST_TRAIT_RING = TraitSpec(id="13.1.85", name="test_ring")
TEST_ATTR_POWER = AttrSpec(name="test_power", id="4.1.85")
TEST_ATTR_BRIGHTNESS = AttrSpec(name="test_brightness", id="1.7.85")
TEST_ATTR_BRIGHTNESS_WRITE = AttrSpec(name="test_brightness_write", id="1.7.85")


def _binary_sensor() -> BinarySensorDescriptor:
    return BinarySensorDescriptor(key="motion", trait=TEST_TRAIT_MOTION)


def _switch() -> SwitchDescriptor:
    return SwitchDescriptor(key="power", attr=TEST_ATTR_POWER)


def _switch_with_write_alias() -> SwitchDescriptor:
    return SwitchDescriptor(
        key="brightness_switch",
        attr=TEST_ATTR_BRIGHTNESS,
        attr_write=TEST_ATTR_BRIGHTNESS_WRITE,
    )


def _event_with_trait() -> EventDescriptor:
    return EventDescriptor(
        key="ring",
        trigger_trait=TEST_TRAIT_RING,
        event_types=("ring",),
    )


def _event_with_source() -> EventDescriptor:
    return EventDescriptor(
        key="multicast_ring",
        trigger_source="multicast_ring",
        event_types=("ring",),
    )


@dataclass
class FakeReport:
    """Stand-in for hub.protocol.LANReport, just enough for dispatch."""

    values: dict[str, Any] = field(default_factory=dict)


class FakeEntity:
    """Records every apply_value / trigger_event call."""

    def __init__(self, raise_on_apply: bool = False) -> None:
        self.applied: list[Any] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._raise_on_apply = raise_on_apply

    def apply_value(self, value: Any) -> None:
        self.applied.append(value)
        if self._raise_on_apply:
            raise RuntimeError("synthetic apply_value failure")

    def trigger_event(self, event_type: str, **payload: Any) -> None:
        self.events.append((event_type, dict(payload)))


def _subentry(did: str = "test_did_001", model: str = "test.model.x", **extras) -> Any:
    data = {"did": did, "model": model, **extras}
    return SimpleNamespace(data=data)


# ---------------------------------------------------------------------------
# Effective descriptor list assembly.
# ---------------------------------------------------------------------------


def test_default_replace_uses_class_lists_when_present():
    class _Sub(Device):
        BINARY_SENSORS = [_binary_sensor()]

    derived = [_switch()]
    sub = _Sub(coordinator=MagicMock(), subentry=_subentry(), derived=derived)
    keys = {d.key for d in sub.descriptors}
    assert keys == {"motion"}  # auto-derive ignored when class has lists.


def test_augment_mode_includes_both():
    class _Sub(Device):
        AUGMENT_AUTO_DERIVE = True
        BINARY_SENSORS = [_binary_sensor()]

    derived = [_switch()]
    sub = _Sub(coordinator=MagicMock(), subentry=_subentry(), derived=derived)
    keys = {d.key for d in sub.descriptors}
    assert keys == {"motion", "power"}


def test_no_class_lists_falls_back_to_derived_only():
    class _Sub(Device):
        pass

    derived = [_binary_sensor(), _switch()]
    sub = _Sub(coordinator=MagicMock(), subentry=_subentry(), derived=derived)
    keys = {d.key for d in sub.descriptors}
    assert keys == {"motion", "power"}


def test_class_lights_slot_replaces_auto_derive_when_augment_false():
    """Subclass with non-empty LIGHTS and AUGMENT_AUTO_DERIVE=False replaces auto-derived."""
    authored = LightDescriptor(
        key="authored_light", translation_key="light",
        power_trait=TraitSpec(id="auth_path", name="auth", data_type="bool"),
        power_attr=AttrSpec(name="auth_path", id="4.1.85", data_type="bool"),
    )
    auto = LightDescriptor(
        key="auto_light", translation_key="light",
        power_trait=TraitSpec(id="auto_path", name="auto", data_type="bool"),
        power_attr=AttrSpec(name="auto_path", id="4.1.85", data_type="bool"),
    )

    class _OverrideLight(Device):
        LIGHTS = [authored]
        AUGMENT_AUTO_DERIVE = False

    device = _OverrideLight(MagicMock(), MagicMock(), derived=[auto])
    keys = [d.key for d in device.descriptors]
    # Auto-derived dropped; only authored survives.
    assert keys == ["authored_light"]


def test_class_lights_slot_augments_auto_derive_when_augment_true():
    """AUGMENT_AUTO_DERIVE=True keeps both."""
    authored = LightDescriptor(
        key="authored_light", translation_key="light",
        power_trait=TraitSpec(id="auth_path", name="auth", data_type="bool"),
        power_attr=AttrSpec(name="auth_path", id="4.1.85", data_type="bool"),
    )
    auto = LightDescriptor(
        key="auto_light", translation_key="light",
        power_trait=TraitSpec(id="auto_path", name="auto", data_type="bool"),
        power_attr=AttrSpec(name="auto_path", id="4.1.85", data_type="bool"),
    )

    class _AugmentedLight(Device):
        LIGHTS = [authored]
        AUGMENT_AUTO_DERIVE = True

    device = _AugmentedLight(MagicMock(), MagicMock(), derived=[auto])
    keys = sorted(d.key for d in device.descriptors)
    assert keys == ["authored_light", "auto_light"]


def test_no_class_lights_keeps_auto_derived():
    """Empty LIGHTS slot with default AUGMENT_AUTO_DERIVE=False keeps auto-derived."""
    auto = LightDescriptor(
        key="auto_light", translation_key="light",
        power_trait=TraitSpec(id="auto_path", name="auto", data_type="bool"),
        power_attr=AttrSpec(name="auto_path", id="4.1.85", data_type="bool"),
    )
    device = Device(MagicMock(), MagicMock(), derived=[auto])
    assert [d.key for d in device.descriptors] == ["auto_light"]


# ---------------------------------------------------------------------------
# AutoDerivedDevice metadata sourcing.
# ---------------------------------------------------------------------------


def test_auto_derived_device_pulls_from_cloud_metadata():
    derived = [_binary_sensor(), _switch()]
    cloud = {"model": "lumi.cloud.model", "deviceName": "Test Device"}
    subentry = _subentry(_cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.cloud.model"
    assert auto.DISPLAY_NAME == "Test Device"
    assert auto.MANUFACTURER == "Aqara"
    keys = {d.key for d in auto.descriptors}
    assert keys == {"motion", "power"}


def test_auto_derived_device_falls_back_to_subentry_model():
    # No _cloud_metadata; the constructor should fall back to subentry.data["model"].
    subentry = _subentry(model="test.model.fallback")
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=[],
    )
    assert auto.MODEL == "test.model.fallback"
    # DISPLAY_NAME defaults to MODEL when no deviceName is present.
    assert auto.DISPLAY_NAME == "test.model.fallback"


def test_auto_derived_device_cloud_name_wins_over_catalog_display_name():
    """Cloud deviceName wins over catalog display_name (e.g. FP3: English vs Chinese).

    Regression test for the v3a naming gap: the FP300 cloud returns English
    "Presence Multi-Sensor FP300" but the catalogue has Chinese display_name.
    MANUFACTURER still comes from the catalog, not the cloud.
    """
    derived = []
    # lumi.light.agl003 is in catalog (T2 RGB LED Bulb E27), but cloud provides
    # a more specific English name — cloud should win on display_name.
    cloud = {"deviceName": "Presence Multi-Sensor FP300"}
    subentry = _subentry(model="lumi.light.agl003", _cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.agl003"
    # Cloud name wins.
    assert auto.DISPLAY_NAME == "Presence Multi-Sensor FP300"
    # Manufacturer still comes from catalog.
    assert auto.MANUFACTURER == "Aqara"


def test_auto_derived_device_falls_back_to_catalog_when_no_cloud_name():
    """When cloud has no deviceName, fall back to catalog's display_name."""
    derived = []
    # Cloud record with no deviceName, model known to catalog. The original
    # test used lumi.light.agl003 (T2 RGB LED Bulb E27) but that model was
    # dropped from the V3 catalogue regen (c2d133f); use lumi.light.agl004
    # (LED Bulb T2 E27 CCT), which IS in the V3 catalogue.
    cloud = {}
    subentry = _subentry(model="lumi.light.agl004", _cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.agl004"
    # Catalog display_name is the fallback.
    assert auto.DISPLAY_NAME == "LED Bulb T2 (E27, CCT)"
    assert auto.MANUFACTURER == "Aqara"


def test_auto_derived_device_consults_catalog_for_known_model():
    """When model is in catalog and cloud provides a name, cloud name is used.

    Also verifies that MANUFACTURER still comes from catalog.
    """
    derived = []
    # Cloud metadata has no model field, so subentry.data["model"] is used.
    cloud = {"deviceName": "Some Random Cloud Name"}
    # lumi.light.agl003 is in light_agl003's MODELS, so catalog should know it.
    subentry = _subentry(model="lumi.light.agl003", _cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.agl003"
    # Cloud name wins over catalog display_name.
    assert auto.DISPLAY_NAME == "Some Random Cloud Name"
    assert auto.MANUFACTURER == "Aqara"


def test_auto_derived_device_consults_catalog_whitelabel():
    """When model is in WHITELABELS, cloud name is still used for display_name."""
    derived = []
    cloud = {"deviceName": "Cloud Name"}
    # lumi.light.agl001 is in light_agl003's WHITELABELS.
    subentry = _subentry(model="lumi.light.agl001", _cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.agl001"
    # Cloud name wins; manufacturer still from catalog (whitelabel).
    assert auto.DISPLAY_NAME == "Cloud Name"
    assert auto.MANUFACTURER == "Aqara"


def test_auto_derived_device_falls_back_to_cloud_for_unknown_model():
    """For unknown models not in catalog, fall back to cloud metadata."""
    derived = []
    cloud = {"deviceName": "Cloud Fallback Name"}
    subentry = _subentry(model="lumi.light.unknown", _cloud_metadata=cloud)
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.unknown"
    # Catalog doesn't know this model, so cloud metadata takes over.
    assert auto.DISPLAY_NAME == "Cloud Fallback Name"
    assert auto.MANUFACTURER == "Aqara"


def test_auto_derived_device_unknown_model_no_cloud_fallback():
    """For unknown models with no cloud metadata, fall back to MODEL string."""
    derived = []
    subentry = _subentry(model="lumi.light.unknown")
    auto = AutoDerivedDevice(
        coordinator=MagicMock(), subentry=subentry, derived=derived,
    )
    assert auto.MODEL == "lumi.light.unknown"
    # No catalog entry and no cloud metadata -> DISPLAY_NAME is the MODEL itself.
    assert auto.DISPLAY_NAME == "lumi.light.unknown"
    assert auto.MANUFACTURER == "Aqara"


# ---------------------------------------------------------------------------
# Index construction and report dispatch.
# ---------------------------------------------------------------------------


def test_handle_report_routes_trait_id_keyed_values_to_correct_entity():
    bs = _binary_sensor()
    sw = _switch()
    ev = _event_with_trait()
    sub = Device(
        coordinator=MagicMock(),
        subentry=_subentry(),
        derived=[bs, sw, ev],
    )
    bs_entity = FakeEntity()
    sw_entity = FakeEntity()
    ev_entity = FakeEntity()
    sub.register_entity(bs, bs_entity)
    sub.register_entity(sw, sw_entity)
    sub.register_entity(ev, ev_entity)

    # Trait-id-keyed: motion fires only the binary sensor.
    sub.handle_report(FakeReport(values={TEST_TRAIT_MOTION.id: "1"}))
    assert bs_entity.applied == ["1"]
    assert sw_entity.applied == []
    assert ev_entity.applied == []

    # Trait-id-keyed: ring fires only the event entity (apply_value path).
    sub.handle_report(FakeReport(values={TEST_TRAIT_RING.id: "1"}))
    assert ev_entity.applied == ["1"]


def test_register_entity_accepts_enum_sensor_descriptor():
    """An auto-derived ENUM sensor descriptor must register without crashing.

    Regression: build_descriptor stored `options` as a list, making the
    frozen SensorDescriptor unhashable, so register_entity raised
    TypeError ('unhashable type: list') when using it as a dict key.
    """
    from custom_components.aqara_lanlink.device.device_types._build import (
        build_descriptor,
    )

    spec = TraitSpec(
        id="2.163.20165", wire_path="2.163.20165", name="Top face",
        data_type="enum", function_code="Cube", trait_code="TopFace",
        enum_values={"0": "Face 1 Up", "1": "Face 2 Up", "2": "Face 3 Up"},
        subscribable=True, endpoint_id=2,
    )
    desc = build_descriptor(spec, "sensor")
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[desc])
    entity = FakeEntity()
    sub.register_entity(desc, entity)  # must not raise
    sub.handle_report(FakeReport(values={spec.id: "2"}))
    assert entity.applied == ["2"]


def test_handle_report_routes_attr_name_keyed_values_to_switch():
    bs = _binary_sensor()
    sw = _switch()
    sub = Device(
        coordinator=MagicMock(), subentry=_subentry(), derived=[bs, sw],
    )
    bs_entity = FakeEntity()
    sw_entity = FakeEntity()
    sub.register_entity(bs, bs_entity)
    sub.register_entity(sw, sw_entity)

    sub.handle_report(FakeReport(values={TEST_ATTR_POWER.name: "0"}))
    assert sw_entity.applied == ["0"]
    assert bs_entity.applied == []


def test_handle_report_aliases_attr_write_when_distinct():
    sw = _switch_with_write_alias()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[sw])
    entity = FakeEntity()
    sub.register_entity(sw, entity)
    # Either name should land on the same entity.
    sub.handle_report(FakeReport(values={TEST_ATTR_BRIGHTNESS.name: "50"}))
    sub.handle_report(FakeReport(values={TEST_ATTR_BRIGHTNESS_WRITE.name: "60"}))
    assert entity.applied == ["50", "60"]


def test_event_with_trigger_source_only_is_not_indexed_for_dispatch():
    ev = _event_with_source()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[ev])
    # No trait id and no attr name -> not in either index.
    assert sub._by_trait_id == {}  # type: ignore[attr-defined]
    assert sub._by_attr_name == {}  # type: ignore[attr-defined]


def test_apply_value_exception_does_not_break_other_entities():
    # Two descriptors share neither trait nor attr; one entity raises,
    # the other should still receive its value.
    bs = _binary_sensor()
    sw = _switch()
    sub = Device(
        coordinator=MagicMock(), subentry=_subentry(), derived=[bs, sw],
    )
    bs_entity = FakeEntity(raise_on_apply=True)
    sw_entity = FakeEntity()
    sub.register_entity(bs, bs_entity)
    sub.register_entity(sw, sw_entity)
    # Same report carries both keys -- the bs_entity raises but sw_entity
    # must still see its value.
    sub.handle_report(FakeReport(values={
        TEST_TRAIT_MOTION.id: "1",
        TEST_ATTR_POWER.name: "1",
    }))
    assert bs_entity.applied == ["1"]
    assert sw_entity.applied == ["1"]


# ---------------------------------------------------------------------------
# register_entity / unregister + seed_initial_value.
# ---------------------------------------------------------------------------


def test_register_entity_returns_unregister_callable():
    bs = _binary_sensor()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[bs])
    entity = FakeEntity()
    unreg = sub.register_entity(bs, entity)
    sub.handle_report(FakeReport(values={TEST_TRAIT_MOTION.id: "1"}))
    assert entity.applied == ["1"]
    unreg()
    sub.handle_report(FakeReport(values={TEST_TRAIT_MOTION.id: "0"}))
    assert entity.applied == ["1"]  # nothing new received after unregister.


def test_seed_initial_values_replay_in_insertion_order():
    """All seeded values for a descriptor replay in order on first register."""
    bs = _binary_sensor()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[bs])
    sub.seed_initial_value(TEST_TRAIT_MOTION.id, "1")
    sub.seed_initial_value(TEST_TRAIT_MOTION.id, "0")

    entity = FakeEntity()
    sub.register_entity(bs, entity)
    assert entity.applied == ["1", "0"]

    # Re-registering a new entity for the same descriptor doesn't replay
    # the seeds (they've been delivered exactly once).
    second = FakeEntity()
    sub.register_entity(bs, second)
    assert second.applied == []


def test_seed_initial_value_supports_multiple_seeds_per_descriptor():
    """A LightDescriptor receives one seed per absorbed component wire path."""
    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="path1", name="power_status", data_type="bool"),
        power_attr=AttrSpec(name="path1", id="4.1.85", data_type="bool"),
        brightness_trait=TraitSpec(id="path2", name="light_level", data_type="number"),
        brightness_attr=AttrSpec(name="path2", id="1.7.85", data_type="number"),
    )
    device = Device(coordinator=MagicMock(), subentry=MagicMock(),
                    derived=[desc])
    device.seed_initial_value("path1", "1")
    device.seed_initial_value("path2", "65")

    # Pending storage holds each seed under its wire path.
    assert device._pending_initial_values["path1"] == ["1"]
    assert device._pending_initial_values["path2"] == ["65"]


def test_seed_initial_value_atomic_keyed_by_wire_path():
    """Atomic descriptors are seeded by their wire path string."""
    desc = SwitchDescriptor(key="sw",
                            attr=AttrSpec(name="x", id="4.1.85", data_type="bool"))
    device = Device(coordinator=MagicMock(), subentry=MagicMock(),
                    derived=[desc])
    device.seed_initial_value("4.1.85", "1")
    assert device._pending_initial_values["4.1.85"] == ["1"]


def test_register_entity_replays_all_seeds_in_order():
    """LightDescriptor entity gets apply_value called once per seed, in order."""
    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="path1", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path1", id="4.1.85", data_type="bool"),
        brightness_trait=TraitSpec(id="path2", name="brightness", data_type="number"),
        brightness_attr=AttrSpec(name="path2", id="1.7.85", data_type="number"),
    )
    device = Device(coordinator=MagicMock(), subentry=MagicMock(), derived=[desc])
    # Seed by wire path, one per absorbed component.
    device.seed_initial_value("path1", "1")
    device.seed_initial_value("path2", "65")

    entity = MagicMock()
    device.register_entity(desc, entity)

    # apply_value called twice; component kwarg derived from absorbed trait.
    calls = entity.apply_value.call_args_list
    assert len(calls) == 2
    assert calls[0][0][0] == "1"
    assert calls[0][1] == {"component": "power"}
    assert calls[1][0][0] == "65"
    assert calls[1][1] == {"component": "brightness"}


def test_seed_after_delivery_is_no_op():
    bs = _binary_sensor()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[bs])
    sub.seed_initial_value(TEST_TRAIT_MOTION.id, "1")
    entity = FakeEntity()
    sub.register_entity(bs, entity)
    assert entity.applied == ["1"]
    # After delivery, subsequent seed calls don't queue new values.
    sub.seed_initial_value(TEST_TRAIT_MOTION.id, "2")
    second = FakeEntity()
    sub.register_entity(bs, second)
    assert second.applied == []


# ---------------------------------------------------------------------------
# Seed-replay gate. Reload of the integration causes _subscribe_and_seed_traits
# to re-read every trait's last cloud-stored value and seed it into the device.
# For stateful traits that's correct (battery=85, switch=on). For event-shaped
# or momentary traits the cloud's last value is whatever the last activation
# emitted -- replaying it via apply_value treats it as "happening now" and
# refires automations. The gate skips the apply step for those descriptor
# shapes while still consuming the seed (so re-registers behave the same).
# ---------------------------------------------------------------------------


def test_seed_replay_skipped_for_event_descriptor():
    """An EventDescriptor never replays a seeded value: events are by
    construction notifications of an occurrence, and re-firing on reload
    would retrigger every 'when button pressed' automation."""
    ev = _event_with_trait()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[ev])
    sub.seed_initial_value(TEST_TRAIT_RING.id, "1")
    entity = FakeEntity()
    sub.register_entity(ev, entity)
    assert entity.events == []
    assert entity.applied == []


def test_seed_replay_skipped_for_binary_sensor_with_auto_clear():
    """A binary sensor declaring auto_clear_seconds is a momentary trigger
    (motion pulse, doorbell ring, recognition). The cloud's last value
    is by definition stale -- skip the replay so the entity starts off."""
    motion_trait = TraitSpec(id="3.1.99", name="motion")
    momentary = BinarySensorDescriptor(
        key="motion_momentary", trait=motion_trait, auto_clear_seconds=30.0,
    )
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[momentary])
    sub.seed_initial_value(motion_trait.id, "1")
    entity = FakeEntity()
    sub.register_entity(momentary, entity)
    assert entity.applied == []


def test_seed_replay_skipped_for_binary_sensor_with_on_any_value():
    """on_any_value descriptors (recognition reports) treat any payload as
    'detected just now'. The cloud's sticky last-payload would refire the
    detection on every reload -- skip the replay."""
    recog_trait = TraitSpec(id="2.155.32990", name="recognition")
    recog = BinarySensorDescriptor(
        key="recognition", trait=recog_trait, on_any_value=True,
    )
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[recog])
    sub.seed_initial_value(recog_trait.id, "some_payload_id")
    entity = FakeEntity()
    sub.register_entity(recog, entity)
    assert entity.applied == []


def test_seed_replay_applied_for_stateful_binary_sensor():
    """Baseline: a contact / leak / presence sensor without auto_clear or
    on_any_value IS stateful -- the cloud's last value IS the current
    state. Seed replay must still fire so the entity boots with the
    correct is_on."""
    bs = _binary_sensor()  # plain BinarySensorDescriptor, no momentary markers
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[bs])
    sub.seed_initial_value(TEST_TRAIT_MOTION.id, "1")
    entity = FakeEntity()
    sub.register_entity(bs, entity)
    assert entity.applied == ["1"]


def test_seed_replay_applied_for_switch():
    """Baseline: switches are inherently stateful. Their last cloud value
    is the canonical current state -- replay must fire."""
    sw = _switch()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[sw])
    sub.seed_initial_value(TEST_ATTR_POWER.name, "1")
    entity = FakeEntity()
    sub.register_entity(sw, entity)
    assert entity.applied == ["1"]


def test_seed_marked_delivered_when_gated():
    """When the gate skips a seed, the seed is still consumed -- a second
    register call for the same descriptor must not replay anything (the
    `_delivered_initial_values` bookkeeping is unchanged by the gate)."""
    ev = _event_with_trait()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[ev])
    sub.seed_initial_value(TEST_TRAIT_RING.id, "1")
    sub.register_entity(ev, FakeEntity())
    # Subsequent seed call after delivery is a no-op (verified elsewhere);
    # also verify the pending bucket is empty so memory doesn't leak.
    assert TEST_TRAIT_RING.id not in sub._pending_initial_values


# ---------------------------------------------------------------------------
# async_write delegation.
# ---------------------------------------------------------------------------


async def test_async_write_delegates_to_coordinator():
    sw = _switch()
    coordinator = MagicMock()
    coordinator.async_write = AsyncMock()
    sub = Device(
        coordinator=coordinator,
        subentry=_subentry(did="test_did_007"),
        derived=[sw],
    )
    sub.MODEL = "test.model.x"
    await sub.async_write({TEST_ATTR_POWER: "1"})
    # Device.async_write also forwards self.PARENT_DID so the coordinator
    # can route via the cluster mesh when the parent is a peer hub. For
    # the base Device class default PARENT_DID is "" (no override), which
    # the coordinator interprets as the legacy fallback.
    coordinator.async_write.assert_awaited_once_with(
        "test_did_007",
        "test.model.x",
        {TEST_ATTR_POWER: "1"},
        parent_did="",
    )


# ---------------------------------------------------------------------------
# fire_event lookup.
# ---------------------------------------------------------------------------


def test_fire_event_dispatches_to_event_entity_by_key():
    ev = _event_with_source()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[ev])
    entity = FakeEntity()
    sub.register_entity(ev, entity)
    sub.fire_event("multicast_ring", "ring", source="udp")
    assert entity.events == [("ring", {"source": "udp"})]


def test_fire_event_unknown_key_is_silent():
    ev = _event_with_source()
    sub = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[ev])
    entity = FakeEntity()
    sub.register_entity(ev, entity)
    # No matching key -> no entity invoked, no exception raised.
    sub.fire_event("nonexistent", "ring")
    assert entity.events == []


# ---------------------------------------------------------------------------
# Default lifecycle hooks are awaitable; async_setup_camera returns an empty
# list, async_setup/async_unload return None.
# ---------------------------------------------------------------------------


async def test_default_async_setup_returns_none():
    dev = Device(coordinator=MagicMock(), subentry=_subentry(), derived=[])
    assert await dev.async_setup(MagicMock(), MagicMock()) is None
    assert await dev.async_setup_camera(MagicMock(), MagicMock()) == []
    assert await dev.async_unload() is None


# ---------------------------------------------------------------------------
# Task 4.3: LightDescriptor indexing + component-kwarg dispatch + handle_report dedup.
# ---------------------------------------------------------------------------


def test_apply_dispatches_component_kwarg_for_light_descriptor():
    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="path_power", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path_power", id="4.1.85", data_type="bool"),
        color_xy_trait=TraitSpec(id="path_xy", name="xy", data_type="uint"),
        color_xy_attr=AttrSpec(name="path_xy", id="14.8.85", data_type="uint"),
    )
    device = Device(MagicMock(), MagicMock(), derived=[desc])
    entity = MagicMock()
    device.register_entity(desc, entity)

    report = SimpleNamespace(values={"path_power": "1"})
    device.handle_report(report)
    # apply_value invoked with component="power"
    entity.apply_value.assert_called_once_with("1", component="power")
    entity.apply_value.reset_mock()

    report = SimpleNamespace(values={"path_xy": "1234567890"})
    device.handle_report(report)
    entity.apply_value.assert_called_once_with("1234567890", component="color_xy")


def test_apply_does_not_pass_component_for_atomic_descriptor():
    """Backward compat: SwitchDescriptor entities receive apply_value(value) only."""
    desc = SwitchDescriptor(key="sw",
                            attr=AttrSpec(name="path_switch", id="4.1.85", data_type="bool"))
    device = Device(MagicMock(), MagicMock(), derived=[desc])
    entity = MagicMock()
    device.register_entity(desc, entity)

    report = SimpleNamespace(values={"path_switch": "1"})
    device.handle_report(report)
    entity.apply_value.assert_called_once_with("1")
    # No 'component' kwarg in the call.
    assert "component" not in entity.apply_value.call_args.kwargs


def test_handle_report_does_not_double_apply_light_descriptor():
    """A LightDescriptor's absorbed path lives in both indices; apply must fire once."""
    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="path_power", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path_power", id="4.1.85", data_type="bool"),
    )
    device = Device(MagicMock(), MagicMock(), derived=[desc])
    entity = MagicMock()
    device.register_entity(desc, entity)

    report = SimpleNamespace(values={"path_power": "1"})
    device.handle_report(report)
    # Even though path_power is in both _by_trait_id and _by_attr_name,
    # apply_value must fire exactly once.
    entity.apply_value.assert_called_once_with("1", component="power")


def test_light_descriptor_indexed_by_all_absorbed_trait_paths():
    """A LightDescriptor with 4 absorbed traits is reachable via all 4 trait paths."""
    desc = LightDescriptor(
        key="light", translation_key="light",
        power_trait=TraitSpec(id="path_power", name="power", data_type="bool"),
        power_attr=AttrSpec(name="path_power", id="4.1.85", data_type="bool"),
        brightness_trait=TraitSpec(id="path_bright", name="bright", data_type="number"),
        brightness_attr=AttrSpec(name="path_bright", id="1.7.85", data_type="number"),
        color_temp_trait=TraitSpec(id="path_ct", name="ct", data_type="number"),
        color_temp_attr=AttrSpec(name="path_ct", id="1.9.85", data_type="number"),
        color_xy_trait=TraitSpec(id="path_xy", name="xy", data_type="uint"),
        color_xy_attr=AttrSpec(name="path_xy", id="14.8.85", data_type="uint"),
    )
    device = Device(MagicMock(), MagicMock(), derived=[desc])
    for path in ("path_power", "path_bright", "path_ct", "path_xy"):
        assert desc in device._by_trait_id.get(path, []), f"{path} not indexed"


# ---------------------------------------------------------------------------
# Task 6: resolve_auto_clear_seconds hook.
# ---------------------------------------------------------------------------


def test_resolve_auto_clear_seconds_returns_descriptor_value():
    from custom_components.aqara_lanlink.device.base import Device
    from custom_components.aqara_lanlink.device.descriptors import (
        BinarySensorDescriptor,
    )
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    from types import SimpleNamespace

    desc = BinarySensorDescriptor(
        key="k", name="n", trait=TraitSpec(id="1.1.1", name="n"),
        auto_clear_seconds=42.0,
    )
    dev = Device(coordinator=None, subentry=SimpleNamespace(data={}), derived=[desc])
    assert dev.resolve_auto_clear_seconds(desc) == 42.0


# ---------------------------------------------------------------------------
# Task 7: async_setup_extra_numbers hook.
# ---------------------------------------------------------------------------


async def test_async_setup_extra_numbers_default_empty():
    from custom_components.aqara_lanlink.device.base import Device
    from types import SimpleNamespace
    dev = Device(coordinator=None, subentry=SimpleNamespace(data={}), derived=[])
    assert await dev.async_setup_extra_numbers(None, None) == []


# ---------------------------------------------------------------------------
# Task 1: stable-key initial-value delivery (wire-path keying).
# ---------------------------------------------------------------------------


async def test_seed_initial_value_keyed_by_wire_path_survives_descriptor_replacement():
    """A descriptor that gets replaced (e.g. by dataclasses.replace) does
    not orphan its seeded initial value: lookup uses the wire path, not
    object identity. This is the regression test for the rename-orphaning
    bug.
    """
    from dataclasses import replace
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from custom_components.aqara_lanlink.device.base import Device
    from custom_components.aqara_lanlink.device.descriptors import (
        BinarySensorDescriptor,
    )
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    spec = TraitSpec(id="5.160.33000", name="Original", data_type="bool")
    desc_old = BinarySensorDescriptor(
        key="motion", name="Original", trait=spec,
    )
    desc_new = replace(desc_old, name="Motion")  # simulates a rename pass

    device = Device(
        coordinator=MagicMock(),
        subentry=SimpleNamespace(
            subentry_id="s1", data={"did": "d1", "model": "lumi.test"},
        ),
        derived=[desc_new],  # device knows the NEW object
    )
    # Seed BEFORE the entity registers - keyed only by wire path.
    device.seed_initial_value("5.160.33000", "1")
    # An entity built from the NEW object registers; should still receive "1".
    entity = MagicMock()
    device.register_entity(desc_new, entity)
    entity.apply_value.assert_called_once_with("1")


async def test_seed_replay_for_light_descriptor_components_routes_to_component():
    """LightDescriptor seeds (keyed by each absorbed component's wire
    path) replay with the correct `component=` argument so the light
    entity's apply_value updates the right field.
    """
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from custom_components.aqara_lanlink.device.base import Device
    from custom_components.aqara_lanlink.device.descriptors import (
        LightDescriptor,
    )
    from custom_components.aqara_lanlink.device.attrs import AttrSpec
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    power = TraitSpec(id="2.1.85", name="Power", data_type="bool")
    brightness = TraitSpec(id="2.2.85", name="Brightness", data_type="number")
    light_desc = LightDescriptor(
        key="light",
        translation_key="light",
        power_trait=power,
        power_attr=AttrSpec(name="2.1.85", id="2.1.85", data_type="bool"),
        brightness_trait=brightness,
        brightness_attr=AttrSpec(name="2.2.85", id="2.2.85", data_type="number"),
    )
    device = Device(
        coordinator=MagicMock(),
        subentry=SimpleNamespace(
            subentry_id="s1", data={"did": "d1", "model": "lumi.test"},
        ),
        derived=[light_desc],
    )
    device.seed_initial_value("2.1.85", "1")
    device.seed_initial_value("2.2.85", "75")

    entity = MagicMock()
    device.register_entity(light_desc, entity)

    # Both seeds delivered; each routed with the correct component.
    calls = entity.apply_value.call_args_list
    assert len(calls) == 2
    components_seen = {c.kwargs.get("component") for c in calls}
    assert components_seen == {"power", "brightness"}


# ---------------------------------------------------------------------------
# Task 7 regression: attr-backed descriptors in _descriptor_wire_paths and
# seed_initial_value delivery.
# ---------------------------------------------------------------------------


def test_descriptor_wire_paths_yields_attr_backed_paths():
    """Switch/Select/Number descriptors expose their wire path on
    desc.attr.name, NOT desc.trait.id. _descriptor_wire_paths must yield
    those too, otherwise values seeded via seed_initial_value(attr.name, ...)
    cannot be delivered to the entity at register_entity time.
    """
    from custom_components.aqara_lanlink.device.base import _descriptor_wire_paths
    from custom_components.aqara_lanlink.device.descriptors import (
        NumberDescriptor,
        SelectDescriptor,
        SwitchDescriptor,
    )

    sw = SwitchDescriptor(
        name="Test switch", key="auto_4_85_1234",
        attr=AttrSpec(name="4.85.1234"),
    )
    se = SelectDescriptor(
        name="Test select", key="auto_5_86_1234",
        attr=AttrSpec(name="5.86.1234"),
        options_map=(("a", "a"), ("b", "b")),
    )
    nu = NumberDescriptor(
        name="Test number", key="auto_6_87_1234",
        attr=AttrSpec(name="6.87.1234"),
        min_value=0, max_value=100,
    )
    paths_sw = list(_descriptor_wire_paths(sw))
    paths_se = list(_descriptor_wire_paths(se))
    paths_nu = list(_descriptor_wire_paths(nu))

    # Each should yield exactly one (wire_path, None) tuple.
    assert paths_sw == [("4.85.1234", None)]
    assert paths_se == [("5.86.1234", None)]
    assert paths_nu == [("6.87.1234", None)]


def test_seed_initial_value_delivers_to_attr_backed_entity_on_register():
    """A seed inserted via seed_initial_value(attr.name, value) must be
    delivered to a Switch entity at register_entity time. Regression for
    the gap that stranded initial values for writable controls when
    LANLink read-on-connect was added in Task 7.
    """
    sw = SwitchDescriptor(
        key="power", attr=AttrSpec(name="4.85.1234"),
    )
    device = Device(
        coordinator=MagicMock(),
        subentry=_subentry(),
        derived=[sw],
    )
    device.seed_initial_value("4.85.1234", "1")
    entity = FakeEntity()
    device.register_entity(sw, entity)
    # The seeded value must be delivered to the entity.
    assert entity.applied == ["1"]
