"""Tests for the subentry-less Device construction path (self-device).

The self-device represents a tunnel host's own traits (e.g. the M3 hub's
built-in sensors / IR / AC control) without a HA config subentry. A
`SelfDeviceContext` carries just enough information -- `did` and `model` --
to construct a `Device` (and create `AqaraEntity` instances) without any
subentry object.

Unique-id stability contract:
- Self-device entity unique_ids derive from the tunnel-host DID, which is
  globally unique per device. The namespace is ``self_<did>``, so a
  descriptor with key ``temperature`` on host did ``lumi.abcdef012345``
  gets unique_id ``self_lumi.abcdef012345_temperature``.
- Subentry devices use ``<subentry_id>_<key>`` (e.g. ``sub_abc_temperature``).
  Because subentry_id is a UUID4 string, these can never collide with
  ``self_<did>_<key>``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.base import (
    Device,
    SelfDeviceContext,
)
from custom_components.aqara_lanlink.device.descriptors import (
    BinarySensorDescriptor,
    SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.entity import AqaraEntity
from custom_components.aqara_lanlink.hub.topology import classify_tunnel_host


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

TEST_TRAIT_TEMP = TraitSpec(id="3.1.85", name="temperature")
TEST_ATTR_POWER = AttrSpec(name="power", id="4.1.85")


def _sensor_descriptor() -> BinarySensorDescriptor:
    return BinarySensorDescriptor(key="temperature", trait=TEST_TRAIT_TEMP)


def _switch_descriptor() -> SwitchDescriptor:
    return SwitchDescriptor(key="power", attr=TEST_ATTR_POWER)


def _make_hub(did: str = "test_hub_did") -> MagicMock:
    hub = MagicMock()
    hub.did = did
    hub.connected = True
    hub.is_lan_direct_offline = lambda _did: False
    return hub


def _make_subentry_device(
    descriptors: list,
    subentry_id: str = "sub_abc",
    did: str = "sub-dev-did-001",
    model: str = "test.model.sub",
) -> tuple[Device, SimpleNamespace]:
    """Return a (Device, subentry) pair built the traditional subentry path."""
    subentry = SimpleNamespace(
        subentry_id=subentry_id,
        data={"did": did, "model": model},
    )

    class _Sub(Device):
        MODEL = model
        DISPLAY_NAME = "Sub Device"
        MANUFACTURER = "Aqara"

    device = _Sub(coordinator=MagicMock(), subentry=subentry, derived=descriptors)
    return device, subentry


# ---------------------------------------------------------------------------
# Step 6.1: SelfDeviceContext construction.
# ---------------------------------------------------------------------------


def test_self_device_context_exposes_did_and_model():
    ctx = SelfDeviceContext(did="lumi.abcdef012345", model="lumi.gateway.m3")
    assert ctx.did == "lumi.abcdef012345"
    assert ctx.model == "lumi.gateway.m3"


def test_self_device_context_subentry_id_is_stable_and_did_scoped():
    """subentry_id is deterministic and encodes the did."""
    ctx = SelfDeviceContext(did="lumi.abcdef012345", model="lumi.gateway.m3")
    assert ctx.subentry_id == "self_lumi.abcdef012345"
    # Re-constructing with the same did gives the same subentry_id.
    ctx2 = SelfDeviceContext(did="lumi.abcdef012345", model="lumi.gateway.m3")
    assert ctx.subentry_id == ctx2.subentry_id


def test_self_device_context_data_exposes_did_and_model():
    """ctx.data behaves like subentry.data: contains 'did' and 'model' keys."""
    ctx = SelfDeviceContext(did="lumi.abcdef012345", model="lumi.gateway.m3")
    assert ctx.data["did"] == "lumi.abcdef012345"
    assert ctx.data["model"] == "lumi.gateway.m3"


# ---------------------------------------------------------------------------
# Device construction without a subentry.
# ---------------------------------------------------------------------------


def test_device_constructs_with_self_device_context():
    """A Device can be constructed using SelfDeviceContext in place of subentry."""
    ctx = SelfDeviceContext(did="lumi.host.001", model="lumi.gateway.m3")
    desc = _sensor_descriptor()
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[desc])
    assert device is not None


def test_device_exposes_descriptors_when_built_with_context():
    """Descriptors are accessible after construction via SelfDeviceContext."""
    ctx = SelfDeviceContext(did="lumi.host.001", model="lumi.gateway.m3")
    desc = _sensor_descriptor()
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[desc])
    keys = [d.key for d in device.descriptors]
    assert keys == ["temperature"]


def test_device_did_property_returns_context_did():
    """device.did reads from the context just as it reads from subentry.data."""
    ctx = SelfDeviceContext(did="lumi.host.uniqueid", model="lumi.gateway.m3")
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[])
    assert device.did == "lumi.host.uniqueid"


# ---------------------------------------------------------------------------
# AqaraEntity unique-id stability and collision-freedom.
# ---------------------------------------------------------------------------


def test_self_device_entity_unique_id_is_stable():
    """Same did + same descriptor key always produces the same unique_id."""
    ctx = SelfDeviceContext(did="lumi.host.001", model="lumi.gateway.m3")
    desc = _sensor_descriptor()
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[desc])
    hub = _make_hub()

    entity_a = AqaraEntity(hub, device, ctx, desc)
    entity_b = AqaraEntity(hub, device, ctx, desc)
    assert entity_a._attr_unique_id == entity_b._attr_unique_id


def test_self_device_entity_unique_id_encodes_did_and_descriptor_key():
    """Self-device unique_id has the form 'self_<did>_<key>'."""
    ctx = SelfDeviceContext(did="lumi.host.001", model="lumi.gateway.m3")
    desc = _sensor_descriptor()
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[desc])
    hub = _make_hub()

    entity = AqaraEntity(hub, device, ctx, desc)
    assert entity._attr_unique_id == "self_lumi.host.001_temperature"


def test_self_device_entity_unique_id_does_not_collide_with_subentry_entity():
    """Self-device and subentry entity unique_ids are distinct for the same trait key."""
    host_did = "lumi.host.001"
    ctx = SelfDeviceContext(did=host_did, model="lumi.gateway.m3")
    self_desc = _sensor_descriptor()
    self_device = Device(coordinator=MagicMock(), subentry=ctx, derived=[self_desc])

    sub_desc = _sensor_descriptor()
    sub_device, subentry = _make_subentry_device(
        [sub_desc], subentry_id="sub_abc", did="sub-dev-did-001",
    )

    hub = _make_hub(did=host_did)
    self_entity = AqaraEntity(hub, self_device, ctx, self_desc)
    sub_entity = AqaraEntity(hub, sub_device, subentry, sub_desc)

    assert self_entity._attr_unique_id != sub_entity._attr_unique_id


def test_self_device_entity_unique_ids_differ_across_descriptors():
    """Different descriptors on the same self-device get different unique_ids."""
    ctx = SelfDeviceContext(did="lumi.host.001", model="lumi.gateway.m3")
    desc1 = _sensor_descriptor()
    desc2 = _switch_descriptor()
    device = Device(coordinator=MagicMock(), subentry=ctx, derived=[desc1, desc2])
    hub = _make_hub()

    entity1 = AqaraEntity(hub, device, ctx, desc1)
    entity2 = AqaraEntity(hub, device, ctx, desc2)
    assert entity1._attr_unique_id != entity2._attr_unique_id


def test_self_device_entity_unique_id_differs_for_different_dids():
    """Two different tunnel hosts produce non-colliding unique_ids for the same key."""
    ctx_a = SelfDeviceContext(did="lumi.host.aaa", model="lumi.gateway.m3")
    ctx_b = SelfDeviceContext(did="lumi.host.bbb", model="lumi.gateway.m3")
    desc_a = _sensor_descriptor()
    desc_b = _sensor_descriptor()
    dev_a = Device(coordinator=MagicMock(), subentry=ctx_a, derived=[desc_a])
    dev_b = Device(coordinator=MagicMock(), subentry=ctx_b, derived=[desc_b])
    hub = _make_hub()

    ent_a = AqaraEntity(hub, dev_a, ctx_a, desc_a)
    ent_b = AqaraEntity(hub, dev_b, ctx_b, desc_b)
    assert ent_a._attr_unique_id != ent_b._attr_unique_id


# ---------------------------------------------------------------------------
# Existing subentry path is unchanged.
# ---------------------------------------------------------------------------


def test_subentry_path_unique_id_unchanged():
    """Existing subentry-based construction still produces subentry_id-prefixed unique_ids."""
    desc = _sensor_descriptor()
    device, subentry = _make_subentry_device([desc], subentry_id="sub_xyz")
    hub = _make_hub()
    entity = AqaraEntity(hub, device, subentry, desc)
    assert entity._attr_unique_id == "sub_xyz_temperature"


def test_subentry_path_did_property_unchanged():
    """device.did still returns the subentry's did for subentry-based devices."""
    desc = _sensor_descriptor()
    device, subentry = _make_subentry_device([desc], did="sub-dev-999")
    assert device.did == "sub-dev-999"


# ---------------------------------------------------------------------------
# Task 7: async_setup_entry builds a self-device for the tunnel host.
# ---------------------------------------------------------------------------

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.aqara_lanlink import (  # noqa: E402
    AqaraLanLinkRuntimeData,
    async_setup_entry,
)
from custom_components.aqara_lanlink.const import (  # noqa: E402
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_HUB_DID,
    CONF_HUB_IP,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
    DOMAIN,
)


def _make_hub_entry(hass, *, hub_did: str = "lumi1.HUB_SELF") -> MockConfigEntry:
    """Build and register a hub config entry for self-device tests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=hub_did,
        title="Self Device Test Hub",
        data={
            CONF_HUB_IP: "192.0.2.20",
            CONF_HUB_PORT: 59703,
            CONF_HUB_DID: hub_did,
            CONF_HUB_MODEL: "lumi.gateway.m3",
            CONF_AQARA_REGION: "EU",
            CONF_AQARA_USER_ID: "USR",
            CONF_AQARA_TOKEN: "TOK",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _make_coordinator_mock_self() -> MagicMock:
    """Build a HubCoordinator mock for self-device integration tests."""
    coord = MagicMock()
    coord.start = MagicMock()
    coord.stop = AsyncMock()
    coord.wait_connected = AsyncMock()
    coord.async_read = AsyncMock(return_value={})
    coord.register_report_handler = MagicMock(return_value=MagicMock())
    coord.did = "lumi1.HUB_SELF"
    coord.token = "TOK"
    coord.cloud_client = None
    return coord


@pytest.fixture
def patch_clientsession_self():
    """Avoid spawning aiohttp resolver threads in self-device tests."""
    with patch(
        "custom_components.aqara_lanlink.async_get_clientsession",
        return_value=MagicMock(),
    ) as p:
        yield p


@pytest.mark.asyncio
async def test_async_setup_entry_populates_self_device(
    hass, patch_clientsession_self,
) -> None:
    """async_setup_entry builds runtime_data.self_device from the hub's own DID/model.

    The self-device should be a Device instance whose DID matches the hub DID
    stored in entry.data[CONF_HUB_DID].
    """
    hub_did = "lumi1.HUB_SELF"
    entry = _make_hub_entry(hass, hub_did=hub_did)
    # No subentries -- the self-device exists independently of subentries.
    object.__setattr__(entry, "subentries", {})

    coord = _make_coordinator_mock_self()
    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={2: ["2.132.32920"]})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch(
        "custom_components.aqara_lanlink.OverlayStore",
        return_value=MagicMock(async_load=AsyncMock(return_value=MagicMock(
            traits_for_model=lambda m: {},
        ))),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert isinstance(entry.runtime_data, AqaraLanLinkRuntimeData)
    # self_device must be present
    assert entry.runtime_data.self_device is not None
    # It must be a Device (or subclass) with the hub's DID
    from custom_components.aqara_lanlink.device.base import Device
    assert isinstance(entry.runtime_data.self_device, Device)
    assert entry.runtime_data.self_device.did == hub_did


@pytest.mark.asyncio
async def test_async_setup_entry_self_device_report_handler_registered(
    hass, patch_clientsession_self,
) -> None:
    """The self-device's handle_report is registered with the coordinator."""
    hub_did = "lumi1.HUB_SELF"
    entry = _make_hub_entry(hass, hub_did=hub_did)
    object.__setattr__(entry, "subentries", {})

    coord = _make_coordinator_mock_self()
    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={2: ["2.132.32920"]})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch(
        "custom_components.aqara_lanlink.OverlayStore",
        return_value=MagicMock(async_load=AsyncMock(return_value=MagicMock(
            traits_for_model=lambda m: {},
        ))),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    # register_report_handler must have been called with the hub DID
    calls = coord.register_report_handler.call_args_list
    dids_registered = [c.args[0] for c in calls if c.args]
    assert hub_did in dids_registered, (
        f"Expected hub DID {hub_did!r} in register_report_handler calls, "
        f"got: {dids_registered}"
    )


@pytest.mark.asyncio
async def test_async_setup_entry_self_device_graceful_on_empty_traits(
    hass, patch_clientsession_self,
) -> None:
    """When the hub's own trait resolution returns no descriptors, setup still
    succeeds. self_device is set (to an empty-descriptor device) or None, and
    the subentry devices dict and platform forwarding are unaffected.

    This is the 'self-traits unreachable' path: a hub whose qlink/trait/read
    returns nothing is a normal case, not an error.
    """
    hub_did = "lumi1.HUB_SELF"
    entry = _make_hub_entry(hass, hub_did=hub_did)

    # One ordinary subentry to verify it is NOT disturbed by the self-device path.
    from types import SimpleNamespace
    sub = SimpleNamespace(
        subentry_id="sub-ok",
        data={"did": "lumi1.SUB", "model": "lumi.test.sub", "_cloud_metadata": {}},
    )
    object.__setattr__(entry, "subentries", {sub.subentry_id: sub})

    coord = _make_coordinator_mock_self()
    coord.did = hub_did

    fake_cloud = MagicMock()

    # build_descriptors returns [] for lumi.gateway.m3 (not in catalogue),
    # which is the "empty traits" scenario this test exercises.
    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch(
        "custom_components.aqara_lanlink.OverlayStore",
        return_value=MagicMock(async_load=AsyncMock(return_value=MagicMock(
            traits_for_model=lambda m: {},
        ))),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True, "async_setup_entry must not raise on empty self-traits"
    # Subentry device must still be present.
    assert "sub-ok" in entry.runtime_data.devices
    # self_device is either None or a Device with empty descriptors -- either
    # satisfies the graceful-degradation contract.
    self_device = entry.runtime_data.self_device
    if self_device is not None:
        from custom_components.aqara_lanlink.device.base import Device
        assert isinstance(self_device, Device)
        assert self_device.descriptors == []


@pytest.mark.asyncio
async def test_async_setup_entry_self_device_graceful_on_exception(
    hass, patch_clientsession_self,
) -> None:
    """When the self-device build raises an unexpected exception, setup still
    succeeds. self_device is None (or not populated) and subentry devices +
    platform forwarding are unaffected.
    """
    hub_did = "lumi1.HUB_SELF"
    entry = _make_hub_entry(hass, hub_did=hub_did)

    from types import SimpleNamespace
    sub = SimpleNamespace(
        subentry_id="sub-ok2",
        data={"did": "lumi1.SUB2", "model": "lumi.test.sub2", "_cloud_metadata": {}},
    )
    object.__setattr__(entry, "subentries", {sub.subentry_id: sub})

    coord = _make_coordinator_mock_self()
    coord.did = hub_did

    fake_cloud = MagicMock()

    hub_model = "lumi.gateway.m3"

    def _boom_on_hub_model(model, overlay):
        if model == hub_model:
            raise RuntimeError("simulated self-device build failure")
        return []

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch(
        "custom_components.aqara_lanlink.OverlayStore",
        return_value=MagicMock(async_load=AsyncMock(return_value=MagicMock(
            traits_for_model=lambda m: {},
        ))),
    ), patch(
        "custom_components.aqara_lanlink.build_descriptors",
        side_effect=_boom_on_hub_model,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True, "async_setup_entry must not raise when self-device build fails"
    assert "sub-ok2" in entry.runtime_data.devices
    # self_device must be None when build failed
    assert entry.runtime_data.self_device is None


# ---------------------------------------------------------------------------
# Task 8: Platform setup files expose self-device entities.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_setup_creates_self_device_number_entity() -> None:
    """number.async_setup_entry adds a NumberEntity for the self-device.

    When runtime_data.self_device has a NumberDescriptor, the platform must
    build the corresponding AqaraNumber and add it WITHOUT config_subentry_id
    (so HA attaches it to the main config entry / hub HA device, not a subentry).

    Unique-id must be 'self_<did>_<key>'.
    """
    from types import SimpleNamespace

    from custom_components.aqara_lanlink.device.attrs import AttrSpec
    from custom_components.aqara_lanlink.device.descriptors import NumberDescriptor
    from custom_components.aqara_lanlink.number import async_setup_entry

    hub_did = "lumi.self.test001"
    ctx = SelfDeviceContext(did=hub_did, model="lumi.gateway.m3")
    number_desc = NumberDescriptor(
        key="hub_volume",
        attr=AttrSpec(name="hub_volume_attr"),
        min_value=0,
        max_value=100,
        step=5,
    )
    self_device = Device(coordinator=MagicMock(), subentry=ctx, derived=[number_desc])
    hub = _make_hub(did=hub_did)

    # Track calls to async_add_entities, capturing the config_subentry_id kwarg.
    add_calls: list[tuple[list, dict]] = []

    def _add_entities(entities, **kwargs):
        add_calls.append((list(entities), kwargs))

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            hub=hub,
            devices={},
            self_device=self_device,
        ),
        subentries={},
    )
    await async_setup_entry(
        hass=MagicMock(), entry=entry, async_add_entities=_add_entities,
    )

    # Exactly one call must have been made -- for the self-device's entity.
    assert len(add_calls) == 1, (
        f"Expected 1 async_add_entities call, got {len(add_calls)}"
    )
    entities, kwargs = add_calls[0]

    # The entity must have been added WITHOUT config_subentry_id.
    assert "config_subentry_id" not in kwargs, (
        "Self-device entity must NOT be added with config_subentry_id; "
        f"got kwargs={kwargs!r}"
    )

    # One entity for the one NumberDescriptor.
    assert len(entities) == 1, f"Expected 1 entity, got {len(entities)}"
    entity = entities[0]

    # Unique-id must follow the self_<did>_<key> form.
    expected_uid = f"self_{hub_did}_hub_volume"
    assert entity._attr_unique_id == expected_uid, (
        f"Expected unique_id={expected_uid!r}, got {entity._attr_unique_id!r}"
    )

    # Descriptor back-reference must be the number descriptor.
    assert entity.descriptor is number_desc


# ---------------------------------------------------------------------------
# Task 9: classify_tunnel_host -- pure helper in hub/topology.py.
# ---------------------------------------------------------------------------


def test_classify_tunnel_host_returns_hub_when_other_dids_present():
    """A topology containing DIDs beyond the host's own DID classifies as 'hub'."""
    host_did = "lumi.gateway.m3.001"
    topology_dids = frozenset({host_did, "lumi.sub.001", "lumi.sub.002"})
    assert classify_tunnel_host(topology_dids, host_did) == "hub"


def test_classify_tunnel_host_returns_standalone_when_only_host_did():
    """A topology containing only the host's own DID classifies as 'standalone'."""
    host_did = "lumi.gateway.m3.001"
    topology_dids = frozenset({host_did})
    assert classify_tunnel_host(topology_dids, host_did) == "standalone"


def test_classify_tunnel_host_returns_standalone_when_empty():
    """An empty topology (push not yet received) classifies as 'standalone'."""
    host_did = "lumi.gateway.m3.001"
    assert classify_tunnel_host(frozenset(), host_did) == "standalone"


def test_classify_tunnel_host_returns_standalone_when_none():
    """A None topology (push not yet received) classifies as 'standalone'."""
    host_did = "lumi.gateway.m3.001"
    assert classify_tunnel_host(None, host_did) == "standalone"


# ---------------------------------------------------------------------------
# Task 9 (gap fix): on_topology_changed callback wiring.
#
# The LANLink topology push arrives asynchronously after async_setup_entry
# completes. host_kind starts as 'standalone' (the conservative default) and
# must be updated to 'hub' once a topology push with child DIDs is processed.
# These tests exercise the actual callback wiring set up in async_setup_entry.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_topology_changed_updates_host_kind_to_hub(
    hass, patch_clientsession_self,
) -> None:
    """When a topology push with child DIDs arrives after setup, host_kind
    is updated from 'standalone' to 'hub' via the on_topology_changed callback.

    The test drives the actual callback wiring assigned in async_setup_entry
    (coordinator.on_topology_changed) rather than a hand-rolled stand-in.
    """
    hub_did = "lumi1.HUB_TOPO_A"
    entry = _make_hub_entry(hass, hub_did=hub_did)
    object.__setattr__(entry, "subentries", {})

    coord = _make_coordinator_mock_self()
    coord.did = hub_did
    # Topology empty at setup time (the typical async-arrival case).
    coord.lanlink_topology_dids = frozenset()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=MagicMock(),
    ), patch(
        "custom_components.aqara_lanlink.registry.get_device_class",
        return_value=None,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # Initial classification with empty topology: standalone.
    assert entry.runtime_data.host_kind == "standalone"

    # The callback must have been wired onto the coordinator.
    assert coord.on_topology_changed is not None

    # Simulate a topology push arriving with child DIDs.
    child_did = "lumi3.CHILD001"
    coord.on_topology_changed(frozenset({hub_did, child_did}))

    # host_kind must now reflect 'hub'.
    assert entry.runtime_data.host_kind == "hub"


@pytest.mark.asyncio
async def test_on_topology_changed_keeps_standalone_when_only_host_did(
    hass, patch_clientsession_self,
) -> None:
    """When a topology push arrives containing only the host's own DID,
    host_kind stays 'standalone'.
    """
    hub_did = "lumi1.HUB_TOPO_B"
    entry = _make_hub_entry(hass, hub_did=hub_did)
    object.__setattr__(entry, "subentries", {})

    coord = _make_coordinator_mock_self()
    coord.did = hub_did
    coord.lanlink_topology_dids = frozenset()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=MagicMock(),
    ), patch(
        "custom_components.aqara_lanlink.registry.get_device_class",
        return_value=None,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert entry.runtime_data.host_kind == "standalone"
    assert coord.on_topology_changed is not None

    # Topology push with only the host DID: still standalone.
    coord.on_topology_changed(frozenset({hub_did}))
    assert entry.runtime_data.host_kind == "standalone"


@pytest.mark.asyncio
async def test_on_topology_changed_does_not_log_when_value_unchanged(
    hass, patch_clientsession_self, caplog,
) -> None:
    """Repeated topology pushes that do not change the classification must not
    produce INFO log spam. Only actual changes log at INFO.
    """
    import logging

    hub_did = "lumi1.HUB_TOPO_C"
    entry = _make_hub_entry(hass, hub_did=hub_did)
    object.__setattr__(entry, "subentries", {})

    coord = _make_coordinator_mock_self()
    coord.did = hub_did
    coord.lanlink_topology_dids = frozenset()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=MagicMock(),
    ), patch(
        "custom_components.aqara_lanlink.registry.get_device_class",
        return_value=None,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    # Confirm initial state.
    assert entry.runtime_data.host_kind == "standalone"

    # First push: changes value to 'hub'; should log.
    child_did = "lumi3.CHILD002"
    with caplog.at_level(logging.INFO, logger="custom_components.aqara_lanlink"):
        coord.on_topology_changed(frozenset({hub_did, child_did}))
    assert entry.runtime_data.host_kind == "hub"
    reclassify_logs = [
        r for r in caplog.records if "reclassified" in r.message
    ]
    assert len(reclassify_logs) == 1

    # Second push: same classification ('hub'); must NOT add another log.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="custom_components.aqara_lanlink"):
        coord.on_topology_changed(frozenset({hub_did, child_did, "lumi3.CHILD003"}))
    assert entry.runtime_data.host_kind == "hub"
    reclassify_logs_2 = [
        r for r in caplog.records if "reclassified" in r.message
    ]
    assert len(reclassify_logs_2) == 0


# ---------------------------------------------------------------------------
# Task 4: SelfDeviceContext camera-field passthrough.
# ---------------------------------------------------------------------------


def test_self_device_context_data_omits_camera_fields_when_unset():
    ctx = SelfDeviceContext(did="d1", model="lumi.camera.agl005")
    assert ctx.data == {"did": "d1", "model": "lumi.camera.agl005"}


def test_self_device_context_data_surfaces_camera_fields():
    ctx = SelfDeviceContext(
        did="d1", model="lumi.camera.agl005",
        camera_ip="192.168.1.9", rtsp_username="admin",
        rtsp_password="pw", backchannel_channel=2,
    )
    assert ctx.data["camera_ip"] == "192.168.1.9"
    assert ctx.data["rtsp_username"] == "admin"
    assert ctx.data["rtsp_password"] == "pw"
    assert ctx.data["backchannel_channel"] == 2
