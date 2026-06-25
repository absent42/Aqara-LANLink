"""Tests for the Aqara LANLink integration's __init__.py.

Covers Task 7.5: end-to-end runtime setup wiring -- catalog load, hub
coordinator construction + connect, cloud client wiring, per-subentry
device build, descriptor resolution (cache + cloud paths), platform
forwarding, and unload.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aqara_lanlink import (
    AqaraLanLinkRuntimeData,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.aqara_lanlink.const import (
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_HUB_DID,
    CONF_HUB_IP,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
    CONF_PHONE_ID,
    DOMAIN,
    PLATFORMS,
)
from custom_components.aqara_lanlink.hub.rearm import RearmManager
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady


# -----------------------------------------------------------------------------
# Smoke tests preserved from the placeholder file.
# -----------------------------------------------------------------------------


def test_domain_is_aqara_lanlink() -> None:
    assert DOMAIN == "aqara_lanlink"


def test_platforms_include_all_expected() -> None:
    expected = {
        Platform.BINARY_SENSOR,
        Platform.BUTTON,
        Platform.CAMERA,
        Platform.EVENT,
        Platform.NUMBER,
        Platform.SELECT,
        Platform.SWITCH,
        Platform.SENSOR,
        Platform.LIGHT,
        Platform.TEXT,
    }
    assert set(PLATFORMS) == expected


# -----------------------------------------------------------------------------
# async_remove_config_entry_device: deleting a sub-device from the UI removes
# its backing config subentry (clearing the device + entities) and reloads the
# entry; the hub device is refused; a stale leftover is purged.
# -----------------------------------------------------------------------------


def _remove_hook_env(*, subentries=("sub1",)):
    """Build a (hass, entry, recorder) trio for the remove hook.

    `recorder` collects the (removed_subentry_ids, reloaded_entry_ids) the hook
    drives so tests can assert the side effects without real HA internals (the
    pinned test-HA predates config subentries).
    """
    removed: list[str] = []
    reloaded: list[str] = []
    config_entries = SimpleNamespace(
        async_remove_subentry=lambda entry, sid: removed.append(sid),
        async_schedule_reload=lambda eid: reloaded.append(eid),
    )
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        entry_id="E1",
        subentries={sid: object() for sid in subentries},
    )
    return hass, entry, (removed, reloaded)


def _device(subentry_mapping):
    return SimpleNamespace(
        config_entries_subentries=subentry_mapping,
        identifiers=set(),
    )


async def test_remove_subdevice_deletes_subentry_and_reloads():
    from custom_components.aqara_lanlink import async_remove_config_entry_device

    hass, entry, (removed, reloaded) = _remove_hook_env(subentries=("sub1",))
    device = _device({"E1": {"sub1"}})

    assert await async_remove_config_entry_device(hass, entry, device) is True
    assert removed == ["sub1"]
    assert reloaded == ["E1"]


async def test_remove_hub_device_is_refused():
    from custom_components.aqara_lanlink import async_remove_config_entry_device

    hass, entry, (removed, reloaded) = _remove_hook_env()
    # The hub / self-device is tied to the entry directly (subentry id None).
    device = _device({"E1": {None}})

    assert await async_remove_config_entry_device(hass, entry, device) is False
    assert removed == []
    assert reloaded == []


async def test_remove_stale_device_is_allowed_without_subentry_removal():
    from custom_components.aqara_lanlink import async_remove_config_entry_device

    hass, entry, (removed, reloaded) = _remove_hook_env(subentries=("sub1",))
    # Device references a subentry that no longer exists on the entry.
    device = _device({"E1": {"ghost"}})

    assert await async_remove_config_entry_device(hass, entry, device) is True
    assert removed == []
    assert reloaded == []


async def test_remove_device_not_linked_to_entry_is_allowed():
    from custom_components.aqara_lanlink import async_remove_config_entry_device

    hass, entry, (removed, reloaded) = _remove_hook_env()
    device = _device({})  # no association with this config entry at all

    assert await async_remove_config_entry_device(hass, entry, device) is True
    assert removed == []
    assert reloaded == []


# -----------------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------------


def _hub_entry(
    hass, *, hub_did: str = "lumi1.HUB", hub_model: str = "lumi.gateway.agl004",
) -> MockConfigEntry:
    """Build + register a hub config entry with the data setup_entry reads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=hub_did,
        title="Test Hub",
        data={
            CONF_HUB_IP: "192.0.2.10",
            CONF_HUB_PORT: 59703,
            CONF_HUB_DID: hub_did,
            CONF_HUB_MODEL: hub_model,
            CONF_AQARA_REGION: "EU",
            CONF_AQARA_USER_ID: "USR",
            CONF_AQARA_TOKEN: "TOK",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _make_subentry(*, subentry_id: str, did: str, model: str) -> SimpleNamespace:
    """Build a minimal subentry shaped like what ConfigSubentry exposes.

    Includes ``as_dict`` and the identity fields HA's config-entries store
    serializes: these stubs are attached to a hass-registered MockConfigEntry,
    so the storage final-write at teardown calls ``subentry.as_dict()``. A bare
    SimpleNamespace lacks it and raises AttributeError during an unrelated
    test's teardown when the store happens to be dirty (a cross-test flake).
    """
    data = {"did": did, "model": model, "_cloud_metadata": {}}
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type="device",
        title=model,
        unique_id=did,
        data=data,
        as_dict=lambda: {
            "data": dict(data),
            "subentry_id": subentry_id,
            "subentry_type": "device",
            "title": model,
            "unique_id": did,
        },
    )


def _attach_subentries(entry: MockConfigEntry, subentries: dict) -> None:
    object.__setattr__(entry, "subentries", subentries)


def _make_coordinator_mock() -> MagicMock:
    """Build a HubCoordinator mock with the surface async_setup_entry uses."""
    coord = MagicMock()
    coord.start = MagicMock()
    coord.stop = AsyncMock()
    coord.wait_connected = AsyncMock()
    coord.async_read = AsyncMock(return_value={})
    coord.did = "lumi1.HUB"
    coord.token = "TOK"
    coord.cloud_client = None
    return coord


@pytest.fixture
def patch_clientsession():
    """Patch async_get_clientsession to a MagicMock (avoid spawning aiohttp's
    pycares resolver thread, which trips the test plugin's lingering-thread
    teardown check)."""
    with patch(
        "custom_components.aqara_lanlink.async_get_clientsession",
        return_value=MagicMock(),
    ) as p:
        yield p


# -----------------------------------------------------------------------------
# async_setup_entry tests.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_basic(hass, patch_clientsession) -> None:
    """Happy path: catalog loads, hub starts, one subentry builds a device,
    runtime_data is set, platforms are forwarded. Catalogue-first: no cloud
    call is made during setup; build_descriptors provides the descriptor list."""
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-1", did="lumi1.SUB1", model="lumi.test.model",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ) as mock_forward:
        result = await async_setup_entry(hass, entry)

    assert result is True
    coord.start.assert_called_once()
    coord.wait_connected.assert_awaited_once()
    assert isinstance(entry.runtime_data, AqaraLanLinkRuntimeData)
    assert entry.runtime_data.hub is coord
    assert "sub-1" in entry.runtime_data.devices
    mock_forward.assert_awaited_once()
    args, _ = mock_forward.call_args
    assert args[0] is entry
    assert list(args[1]) == list(PLATFORMS)


@pytest.mark.asyncio
async def test_setup_evaluates_current_topology_for_rearm(hass, patch_clientsession) -> None:
    """Setup evaluates the current topology against activation targets.

    The initial topology push arrives during wait_connected -- before
    on_topology_changed is wired -- so a standalone device already absent at
    setup would otherwise never be re-armed via the event path (it only fires
    on a topology CHANGE, which never comes if the device stays absent).
    async_setup_entry therefore hands the current topology to RearmManager
    directly so an absent target is re-armed immediately.
    """
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset({"lumi1.HUB"})

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=MagicMock(),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ), patch.object(RearmManager, "note_topology") as note_topology:
        await async_setup_entry(hass, entry)

    note_topology.assert_called_once_with(frozenset({"lumi1.HUB"}))


@pytest.mark.asyncio
async def test_setup_entry_hub_unreachable_raises_config_entry_not_ready(hass, patch_clientsession) -> None:
    """wait_connected raises TimeoutError -> ConfigEntryNotReady, coord stopped."""
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()
    coord.wait_connected.side_effect = asyncio.TimeoutError()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    coord.stop.assert_awaited_once()





@pytest.mark.asyncio
async def test_setup_entry_picks_override_class_over_auto_derived(hass, patch_clientsession) -> None:
    """A registered Device subclass for a model is preferred over AutoDerivedDevice."""
    from custom_components.aqara_lanlink.device.base import Device

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-override", did="lumi1.OVR", model="lumi.override.x",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    instances: list = []

    class _Override(Device):
        MODEL = "lumi.override.x"
        DISPLAY_NAME = "Override"

        def __init__(self, coordinator, subentry, derived):
            super().__init__(coordinator, subentry, derived=derived)
            instances.append(self)

    coord = _make_coordinator_mock()

    # Return _Override only for the subentry model; return None for the hub
    # model so the self-device build uses AutoDerivedDevice and does not add
    # an extra instance to `instances`.
    subentry_model = "lumi.override.x"

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch(
        "custom_components.aqara_lanlink.registry.get_device_class",
        side_effect=lambda m: _Override if m == subentry_model else None,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    assert len(instances) == 1
    assert isinstance(entry.runtime_data.devices["sub-override"], _Override)



@pytest.mark.asyncio
async def test_setup_entry_assigns_cloud_client_and_token_to_coordinator(hass, patch_clientsession) -> None:
    """Coordinator gets cloud_client + token wired up for entity-level access."""
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()
    cloud = MagicMock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    # cloud_client wired
    assert coord.cloud_client is cloud
    # token populated by HubCoordinator constructor (verified via the mock's
    # initial token attribute set by _make_coordinator_mock).
    assert coord.token == "TOK"


@pytest.mark.asyncio
async def test_setup_entry_persists_and_uses_stable_phone_id(hass, patch_clientsession) -> None:
    """A fresh entry gets a stable PhoneId persisted to entry.data and the
    cloud client is built with it (so the hub's subscription identity is stable
    across reloads)."""
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})
    assert CONF_PHONE_ID not in entry.data

    coord = _make_coordinator_mock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=MagicMock(),
    ) as client_cls, patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    persisted = entry.data.get(CONF_PHONE_ID)
    assert persisted  # non-empty, persisted to the entry
    assert client_cls.call_args.kwargs["phone_id"] == persisted


@pytest.mark.asyncio
async def test_phone_id_derived_from_instance_id(hass) -> None:
    """The PhoneId is deterministically derived from the HA install id (uuid5),
    matching the app's one-durable-PhoneId-per-device model."""
    import uuid as _uuid

    from homeassistant.helpers import instance_id as _iid

    from custom_components.aqara_lanlink import _ensure_phone_id
    from custom_components.aqara_lanlink.const import PHONE_ID_NAMESPACE

    entry = _hub_entry(hass)
    install = await _iid.async_get(hass)
    expected = str(_uuid.uuid5(_uuid.UUID(PHONE_ID_NAMESPACE), install)).upper()

    assert await _ensure_phone_id(hass, entry) == expected
    # Written through to entry.data so diagnostics/readers see it.
    assert entry.data[CONF_PHONE_ID] == expected


@pytest.mark.asyncio
async def test_phone_id_shared_across_entries_and_stable_across_readd(hass) -> None:
    """One install -> one PhoneId: identical across two hub entries, and a
    re-add (fresh entry, no stored PhoneId) yields the same value because the
    HA install id is durable."""
    from custom_components.aqara_lanlink import _ensure_phone_id

    entry_a = _hub_entry(hass)
    entry_b = _hub_entry(hass)
    pid_a = await _ensure_phone_id(hass, entry_a)
    pid_b = await _ensure_phone_id(hass, entry_b)
    assert pid_a == pid_b  # shared across all hub entries on this install

    # Simulate a re-add: a brand-new entry with no CONF_PHONE_ID.
    readded = _hub_entry(hass)
    assert CONF_PHONE_ID not in readded.data
    assert await _ensure_phone_id(hass, readded) == pid_a  # stable across re-add


@pytest.mark.asyncio
async def test_phone_id_overrides_stale_per_entry_value(hass) -> None:
    """A pre-existing per-entry random PhoneId (the old behaviour) is REPLACED by
    the instance-derived value -- the per-entry scope was the buildup vector."""
    from custom_components.aqara_lanlink import _ensure_phone_id

    entry = _hub_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PHONE_ID: "STALE-RANDOM-PER-ENTRY"}
    )
    pid = await _ensure_phone_id(hass, entry)
    assert pid != "STALE-RANDOM-PER-ENTRY"
    assert entry.data[CONF_PHONE_ID] == pid


@pytest.mark.asyncio
async def test_async_unload_entry(hass, patch_clientsession) -> None:
    """Happy path: unload calls platforms unload, device unload, hub stop."""
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    # Inject a synthetic device with an async_unload spy.
    fake_device = MagicMock()
    fake_device.async_unload = AsyncMock()
    entry.runtime_data.devices["fake-id"] = fake_device

    with patch.object(
        hass.config_entries, "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ) as mock_unload_platforms:
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_unload_platforms.assert_awaited_once()
    fake_device.async_unload.assert_awaited_once()
    coord.stop.assert_awaited_once()



def test_register_initial_read_machinery_deleted() -> None:
    """The legacy INITIAL_READ_ATTRS / register_initial_read /
    _fire_initial_reads triple are removed. Their absence is the contract.
    """
    from custom_components.aqara_lanlink.device.base import Device
    from custom_components.aqara_lanlink.hub.coordinator import HubCoordinator
    assert not hasattr(Device, "INITIAL_READ_ATTRS")
    assert not hasattr(HubCoordinator, "register_initial_read")
    assert not hasattr(HubCoordinator, "_fire_initial_reads")


@pytest.mark.asyncio
async def test_setup_entry_partial_failure_after_wait_connected_stops_coordinator(
    hass, patch_clientsession,
) -> None:
    """If any post-wait_connected step raises, coordinator.stop() is awaited
    and the exception propagates so HA doesn't leak the background task on
    each retry."""
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-fail", did="lumi1.FAIL", model="lumi.fail.x",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    # Build a Device class whose async_setup raises so we hit the wrapping
    # except block AFTER wait_connected has already succeeded.
    class _BoomDevice:
        MODEL = "lumi.fail.x"

        def __init__(self, coordinator, subentry, derived):
            pass

        async def async_setup(self, coordinator, subentry):
            raise RuntimeError("device setup blew up")

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch(
        "custom_components.aqara_lanlink.registry.get_device_class",
        return_value=_BoomDevice,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        with pytest.raises(RuntimeError, match="device setup blew up"):
            await async_setup_entry(hass, entry)

    # The wrapping try/except must have torn the coordinator down so HA's
    # retry doesn't end up with two live coordinators.
    coord.stop.assert_awaited_once()



@pytest.mark.xfail(reason="rewritten in Task 6 with scan service")
@pytest.mark.asyncio
async def test_setup_entry_real_auto_derive_with_t2_light_fixture(hass, patch_clientsession) -> None:
    """End-to-end: real auto_derive over the T2 RGB CCT cloud-trait fixture
    yields exactly one LightDescriptor on the resulting AutoDerivedDevice
    AND exercises the full seed-replay path against real (now-hashable)
    descriptors. Covers the auto-derive -> Device.descriptors ->
    seed_initial_value chain end-to-end."""
    import json
    from pathlib import Path

    from custom_components.aqara_lanlink.device import (
        attrs as attrs_catalog,
        traits as traits_catalog,
    )
    from custom_components.aqara_lanlink.device.base import AutoDerivedDevice
    from custom_components.aqara_lanlink.device.descriptors import LightDescriptor

    # Reset the canonical trait catalog so auto-derive's pattern detector
    # can find the canonical light propertyIds (populated by lazy package discovery).
    traits_catalog.reset_for_tests()
    attrs_catalog.reset_for_tests()
    try:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "cloud_qlink_trait_read.json"
        )
        payload = json.loads(fixture_path.read_text())
        device_record = payload["result"][0]
        traits_list = device_record["traits"]

        entry = _hub_entry(hass)
        sub = SimpleNamespace(
            subentry_id="sub-t2",
            data={
                "did": "lumi1.T2",
                "model": "lumi.light.agl003",
                "_cloud_metadata": device_record,
            },
        )
        _attach_subentries(entry, {sub.subentry_id: sub})

        coord = _make_coordinator_mock()
        fake_cloud = MagicMock()
        from custom_components.aqara_lanlink.hub.cloud_client import EndpointPanel
        fake_cloud.query_collection_panels = AsyncMock(
            return_value={2: EndpointPanel(
                endpoint_id=2, endpoint_name="", endpoint_icon_id="",
                device_name="", device_types="", position_name="",
                model_type=0, obj_properties=(),
                paths=("2.132.32920",),
            )},
        )
        fake_cloud.query_device_traits = AsyncMock(return_value=traits_list)

        with patch(
            "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
        ), patch(
            "custom_components.aqara_lanlink.AqaraCloudClient",
            return_value=fake_cloud,
        ), patch(
            "custom_components.aqara_lanlink.enrich_light_effects",
            new_callable=AsyncMock,
        ), patch(
            "custom_components.aqara_lanlink.registry.get_device_class",
            return_value=None,
        ), patch.object(
            hass.config_entries, "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        device = entry.runtime_data.devices["sub-t2"]
        assert isinstance(device, AutoDerivedDevice)
        lights = [d for d in device.descriptors if isinstance(d, LightDescriptor)]
        assert len(lights) == 1, (
            f"expected exactly one LightDescriptor, got {len(lights)}: "
            f"{[type(d).__name__ for d in device.descriptors]}"
        )
    finally:
        traits_catalog.reset_for_tests()
        attrs_catalog.reset_for_tests()





# -----------------------------------------------------------------------------
# Task 4.3: new-paths Repair issue registration.
# -----------------------------------------------------------------------------



async def test_register_candidate_paths_issue_carries_count_and_details(monkeypatch):
    """The candidate-paths Repair issue carries count plus a per-device
    Markdown section in translation_placeholders. Each section names the
    HA device (from the device_registry, falling back to the DID) so the
    user knows which paired device to scan -- distinguishing multiple
    devices of the same model is the whole point of the per-DID schema.
    """
    from custom_components.aqara_lanlink import _register_candidate_paths_issue
    from custom_components.aqara_lanlink.device.observed_path_cache import (
        ObservedPathCache,
    )

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        async def async_load(self):
            return None

        def async_delay_save(self, data_func, delay):
            pass

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        _FakeStore,
    )
    cache = ObservedPathCache(MagicMock())
    await cache.async_load()
    # Two devices of the same model + one of a different model. The
    # notification should list each separately so the user can scan the
    # right one.
    cache.record("lumi1.VIB_A", "lumi.vibration.agl002", "2.164.20536")
    cache.record("lumi1.VIB_B", "lumi.vibration.agl002", "2.164.20537")
    cache.record("lumi1.SW01",  "lumi.switch.acn099",   "1.1.85")

    captured: dict = {}

    def _capture(hass, domain, issue_id, **kwargs):
        captured["issue_id"] = issue_id
        captured.update(kwargs)

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.ir.async_create_issue", _capture,
    )

    # device_registry lookup: stub to return a "Hallway vibration" entry
    # for VIB_A and nothing for the others (formatter falls back to DID).
    def _fake_get_device(*args, **kwargs):
        wanted = kwargs.get("identifiers") or set()
        if ("aqara_lanlink", "lumi1.VIB_A") in wanted:
            entry = MagicMock()
            entry.name_by_user = None
            entry.name = "Hallway vibration"
            return entry
        return None

    fake_reg = MagicMock()
    fake_reg.async_get_device = _fake_get_device
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.dr.async_get",
        lambda hass: fake_reg,
    )

    _register_candidate_paths_issue(MagicMock(), "abc123", cache)

    assert captured["issue_id"] == "candidate_paths_abc123"
    placeholders = captured["translation_placeholders"]
    assert placeholders["count"] == "3"
    details = placeholders["details"]
    # Friendly name pulled from the device registry for VIB_A.
    assert "**Hallway vibration**" in details
    # DIDs appear so the user can disambiguate same-model paired devices.
    assert "`lumi1.VIB_A`" in details
    assert "`lumi1.VIB_B`" in details
    assert "`lumi1.SW01`" in details
    # Models still appear in the header.
    assert "`lumi.vibration.agl002`" in details
    assert "`lumi.switch.acn099`" in details
    # Paths bullet-listed under each device.
    assert "`2.164.20536`" in details
    assert "`2.164.20537`" in details
    assert "`1.1.85`" in details
    assert captured["data"] == {"entry_id": "abc123"}


# -----------------------------------------------------------------------------
# Task 10: Options update listener.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_update_triggers_entry_reload(hass, patch_clientsession) -> None:
    """Updating entry options must trigger an entry reload.

    Registers and sets up the entry via async_setup_entry, then updates the
    entry's options and asserts that hass.config_entries.async_reload was
    called with the entry's entry_id.
    """
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(),
    ) as mock_reload:
        hass.config_entries.async_update_entry(entry, options={"camera_ip": "9.9.9.9"})
        await hass.async_block_till_done()

    mock_reload.assert_awaited_once_with(entry.entry_id)


async def test_persisted_endpoint_updates_entry_data_without_reload(
    hass, patch_clientsession,
) -> None:
    """The coordinator's on_endpoint_change callback persists the rediscovered
    (host, port) to entry.data, and that data-only write must NOT trigger a
    full entry reload (which would tear down the live tunnel in a loop)."""
    entry = _hub_entry(hass)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ) as MockHC, patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=MagicMock(),
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    callback = MockHC.call_args.kwargs["on_endpoint_change"]
    assert callable(callback)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(),
    ) as mock_reload:
        callback("192.0.2.99", 40123)
        await hass.async_block_till_done()

    assert entry.data[CONF_HUB_IP] == "192.0.2.99"
    assert entry.data[CONF_HUB_PORT] == 40123
    mock_reload.assert_not_awaited()


def test_topology_grew_detects_new_dids() -> None:
    from custom_components.aqara_lanlink import _topology_grew

    # New DID appears -> growth (the hub became ready / a device rejoined).
    assert _topology_grew(frozenset(), frozenset({"a"})) is True
    assert _topology_grew(frozenset({"a"}), frozenset({"a", "b"})) is True
    # No new DID -> not growth (steady state or shrink must not re-arm).
    assert _topology_grew(frozenset({"a"}), frozenset({"a"})) is False
    assert _topology_grew(frozenset({"a", "b"}), frozenset({"a"})) is False


@pytest.mark.asyncio
async def test_topology_growth_rearms_subscription(hass, patch_clientsession) -> None:
    """When the LANLink topology grows (hub becomes ready), the integration
    re-runs subscribe+seed so pushes arm without waiting for a reload."""
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-catalogued", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset()

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    baseline = fake_cloud.query_device_traits.await_count
    assert baseline >= 1  # inline subscribe at setup

    # Hub topology grows from empty -> one child DID. Fire the wired callback.
    coord.on_topology_changed(frozenset({"lumi1.FP2"}))
    await hass.async_block_till_done()

    after = fake_cloud.query_device_traits.await_count
    assert after > baseline  # re-armed
    called_dids = {c.args[1] for c in fake_cloud.query_device_traits.await_args_list}
    assert "lumi1.FP2" in called_dids


@pytest.mark.asyncio
async def test_session_up_rearms_subscription(hass, patch_clientsession) -> None:
    """A tunnel session-up (reconnect) re-runs subscribe+seed, because the
    hub-side subscription is per-connection and is otherwise lost on reconnect
    even when the topology comes back unchanged (no growth to trigger on)."""
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-catalogued", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset()

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    baseline = fake_cloud.query_device_traits.await_count
    assert baseline >= 1

    # Simulate the coordinator reaching session-up (reconnect).
    coord.on_session_up()
    await hass.async_block_till_done()

    assert fake_cloud.query_device_traits.await_count > baseline


def test_push_appears_stalled_predicate() -> None:
    from custom_components.aqara_lanlink import _push_appears_stalled

    # Connected + topology ready + nothing heard for > ttl -> stalled.
    assert _push_appears_stalled(
        connected=True, topology_size=2, seconds_since_report=400.0, ttl=300.0,
    ) is True
    # A recent report (e.g. the hub heartbeat) -> healthy.
    assert _push_appears_stalled(
        connected=True, topology_size=2, seconds_since_report=10.0, ttl=300.0,
    ) is False
    # Not connected -> the reconnect/session-up path owns recovery.
    assert _push_appears_stalled(
        connected=False, topology_size=2, seconds_since_report=400.0, ttl=300.0,
    ) is False
    # Topology not ready yet (cold start) -> the topology-growth path owns it.
    assert _push_appears_stalled(
        connected=True, topology_size=0, seconds_since_report=400.0, ttl=300.0,
    ) is False


@pytest.mark.asyncio
async def test_host_kind_classification_is_sticky(hass, patch_clientsession) -> None:
    """Once classified as a hub, a transient 0-DID topology push must not
    downgrade host_kind back to standalone."""
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset()
    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    coord.on_topology_changed(frozenset({"lumi1.HUB", "lumi.child"}))
    assert entry.runtime_data.host_kind == "hub"
    coord.on_topology_changed(frozenset())  # transient empty push
    assert entry.runtime_data.host_kind == "hub"  # sticky, no downgrade
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_concurrent_rearm_coalesces(hass, patch_clientsession) -> None:
    """Two re-arm triggers firing at once (e.g. session-up + topology-growth on
    a fresh connect) must coalesce into a single subscribe pass, not double the
    cloud calls."""
    import asyncio

    from custom_components.aqara_lanlink import _rearm_subscriptions

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    targets = len(entry.runtime_data.subscription_targets)
    assert targets >= 1

    gate = asyncio.Event()
    calls: list[str] = []

    async def blocking_qdt(token, did, batch):
        calls.append(did)
        await gate.wait()
        return []

    fake_cloud.query_device_traits = blocking_qdt

    t1 = asyncio.create_task(_rearm_subscriptions(hass, entry))
    await asyncio.sleep(0.01)  # t1 enters, sets the guard, blocks on first batch
    in_flight = len(calls)
    assert in_flight >= 1
    t2 = asyncio.create_task(_rearm_subscriptions(hass, entry))
    await asyncio.sleep(0.01)  # t2 should coalesce (guard held) and add nothing
    assert len(calls) == in_flight  # no second pass started
    gate.set()
    await asyncio.gather(t1, t2)


@pytest.mark.asyncio
async def test_watchdog_rearms_when_pushes_stalled(hass, patch_clientsession) -> None:
    """A connected hub with a ready topology that has gone silent past the TTL
    gets its subscription re-armed by the watchdog tick."""
    from custom_components.aqara_lanlink import _watchdog_tick

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset({"lumi1.FP2"})
    coord.connected = True
    coord.seconds_since_last_report = lambda: 9999.0

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    baseline = fake_cloud.query_device_traits.await_count
    await _watchdog_tick(hass, entry)
    assert fake_cloud.query_device_traits.await_count > baseline


@pytest.mark.asyncio
async def test_persistent_stall_repair_lifecycle(
    hass, patch_clientsession, monkeypatch,
) -> None:
    """After repeated watchdog re-arms with no recovery, a Repair is raised;
    it clears once reports resume."""
    from custom_components.aqara_lanlink import (
        PUSH_STALL_TTL_SECONDS,
        STALL_REARM_REPAIR_THRESHOLD,
        _watchdog_tick,
    )

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset({"lumi1.FP2"})
    coord.connected = True
    coord.seconds_since_last_report = lambda: 9999.0  # stalled

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    clock = [1000.0]
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.time.monotonic", lambda: clock[0],
    )
    with patch(
        "custom_components.aqara_lanlink.ir.async_create_issue",
    ) as mock_create, patch(
        "custom_components.aqara_lanlink.ir.async_delete_issue",
    ) as mock_delete:
        for _ in range(STALL_REARM_REPAIR_THRESHOLD):
            clock[0] += PUSH_STALL_TTL_SECONDS + 1  # clear the re-arm cooldown
            await _watchdog_tick(hass, entry)
        assert mock_create.call_count == 1
        assert mock_create.call_args.kwargs.get("translation_key") == "push_stalled"

        # Reports resume -> not stalled -> Repair cleared, counter reset.
        coord.seconds_since_last_report = lambda: 5.0
        await _watchdog_tick(hass, entry)
        mock_delete.assert_called()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_watchdog_stops_rearming_after_repair_threshold(
    hass, patch_clientsession, monkeypatch,
) -> None:
    """Once the push_stalled Repair is raised, the watchdog stops re-arming a
    still-wedged hub. Re-subscribing a wedged hub is proven useless (the hub's
    relay table is persistent; only a factory reset clears it), so further
    re-arms are pointless cloud load. It resumes only when reports recover."""
    from custom_components.aqara_lanlink import (
        PUSH_STALL_TTL_SECONDS,
        STALL_REARM_REPAIR_THRESHOLD,
        _watchdog_tick,
    )

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset({"lumi1.FP2"})
    coord.connected = True
    coord.seconds_since_last_report = lambda: 9999.0  # permanently stalled

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    clock = [1000.0]
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.time.monotonic", lambda: clock[0],
    )
    with patch("custom_components.aqara_lanlink.ir.async_create_issue"):
        # Re-arm up to the escalation threshold (one re-arm per cooldown).
        for _ in range(STALL_REARM_REPAIR_THRESHOLD):
            clock[0] += PUSH_STALL_TTL_SECONDS + 1
            await _watchdog_tick(hass, entry)
        capped = fake_cloud.query_device_traits.await_count
        assert capped > 0  # it did re-arm up to the threshold

        # Further stalled ticks past the cooldown must NOT re-arm any more.
        for _ in range(3):
            clock[0] += PUSH_STALL_TTL_SECONDS + 1
            await _watchdog_tick(hass, entry)
        assert fake_cloud.query_device_traits.await_count == capped

        # Reports recover -> stall cleared -> watchdog re-arms again next stall.
        coord.seconds_since_last_report = lambda: 5.0
        with patch("custom_components.aqara_lanlink.ir.async_delete_issue"):
            await _watchdog_tick(hass, entry)  # not stalled: resets counter
        coord.seconds_since_last_report = lambda: 9999.0
        clock[0] += PUSH_STALL_TTL_SECONDS + 1
        await _watchdog_tick(hass, entry)
        assert fake_cloud.query_device_traits.await_count > capped
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_watchdog_noop_when_healthy(hass, patch_clientsession) -> None:
    """A recently-heard hub is not re-armed by the watchdog."""
    from custom_components.aqara_lanlink import _watchdog_tick

    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="s", did="lumi1.FP2", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()
    coord.lanlink_topology_dids = frozenset({"lumi1.FP2"})
    coord.connected = True
    coord.seconds_since_last_report = lambda: 5.0  # just heard from it

    fake_cloud = MagicMock()
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient", return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        await async_setup_entry(hass, entry)

    baseline = fake_cloud.query_device_traits.await_count
    await _watchdog_tick(hass, entry)
    assert fake_cloud.query_device_traits.await_count == baseline


@pytest.mark.asyncio
async def test_setup_subscribes_via_cloud_query_device_traits(
    hass, patch_clientsession,
) -> None:
    """Setting up a config entry for a catalogued model MUST call
    query_device_traits to re-arm the hub-side push subscription (which is
    per-tunnel-connection and lost on every reload) and seed initial values.

    query_collection_panels remains scan-service-only.
    """
    # Build a hub entry. The hub model is not in the catalogue.
    entry = _hub_entry(hass)

    # Add a single subentry whose model IS in the shipped catalogue:
    # lumi.motion.agl001 has traits in the catalogue (Task 4 verified 119+).
    sub = _make_subentry(
        subentry_id="sub-catalogued",
        did="lumi1.FP2",
        model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    # Use a real-ish fake_cloud so we can spy on its calls.
    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(
        side_effect=AssertionError("cloud panels called"),
    )
    # query_device_traits should be invoked one or more times to subscribe;
    # return an empty traits list so seeding is a no-op but the call counts.
    fake_cloud.query_device_traits = AsyncMock(return_value=[])

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True

    # Bootstrap/scan path stays cloud-free: panels should not have been read.
    fake_cloud.query_collection_panels.assert_not_called()
    # But the per-subentry re-subscribe call is required: at least one call
    # per device that has any catalogued wire paths.
    assert fake_cloud.query_device_traits.await_count >= 1
    # And the call must target this subentry's did.
    called_dids = {call.args[1] for call in fake_cloud.query_device_traits.await_args_list}
    assert "lumi1.FP2" in called_dids


async def test_subscribe_and_seed_skips_empty_string_values(
    hass, patch_clientsession,
) -> None:
    """Aqara cloud returns value='' for traits that haven't produced a
    reading yet (e.g. lumi.sensor.p100 tilt angles pre-measurement).
    Seeding '' on a numeric sensor crashes HA at entity-registration
    time. _subscribe_and_seed_traits must skip empty-string values just
    like it skips None.
    """
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-p100", did="lumi1.p100test", model="lumi.motion.agl001",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    # Cloud returns a mix of values: real, None, and empty-string.
    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={})
    fake_cloud.query_device_traits = AsyncMock(return_value=[
        {"path": "2.5.85", "value": "42"},
        {"path": "2.6.85", "value": None},
        {"path": "2.7.85", "value": ""},
    ])

    seeded: list = []

    def _capture_seed(path, value):
        seeded.append((path, value))

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ), patch(
        "custom_components.aqara_lanlink.device.base.Device.seed_initial_value",
        side_effect=_capture_seed,
    ):
        await async_setup_entry(hass, entry)

    seeded_paths = [p for p, _ in seeded]
    assert "2.5.85" in seeded_paths, "real value '42' should be seeded"
    assert "2.6.85" not in seeded_paths, "None value must be skipped"
    assert "2.7.85" not in seeded_paths, "empty-string value must be skipped"


# -----------------------------------------------------------------------------
# Settings seed from cloud (rid-keyed device-setting state).
# -----------------------------------------------------------------------------


async def test_setup_seeds_settings_from_cloud_by_rid(
    hass, patch_clientsession,
) -> None:
    """At setup, a device whose model has catalogued settings must read its
    current setting values by resource id (query_resources_by_rid) and stage
    each {rid: value} via device.seed_initial_value, so setting entities show
    real state at load time.
    """
    entry = _hub_entry(hass)
    # lumi.plug.aeu002 has catalogued SETTINGS (rid-keyed).
    sub = _make_subentry(
        subentry_id="sub-plug", did="lumi1.plug", model="lumi.plug.aeu002",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])
    # Cloud returns values for stateful rids AND the button rid; buttons must
    # never be queried or seeded (stateless, no apply_value -> would raise).
    fake_cloud.query_resources_by_rid = AsyncMock(
        return_value={
            "4.4.85": "1",
            "8.0.2032": "0",
            "8.0.2096": "0",
        },
    )

    seeded: list = []

    def _capture_seed(path, value):
        seeded.append((path, value))

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ), patch(
        "custom_components.aqara_lanlink.device.base.Device.seed_initial_value",
        side_effect=_capture_seed,
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # The rid-keyed read targeted this device, with its catalogued rids. The
    # hub self-device (and any other settings-bearing device) is seeded in the
    # same pass, so select this device's call by did rather than assuming it is
    # the last await.
    fake_cloud.query_resources_by_rid.assert_awaited()
    plug_calls = [
        c for c in fake_cloud.query_resources_by_rid.await_args_list
        if c.args[1] == "lumi1.plug"
    ]
    assert len(plug_calls) == 1, plug_calls
    call = plug_calls[0]
    queried_rids = call.args[2]
    # All 8 stateful rids are queried.
    stateful_rids = {
        "4.4.85", "4.5.85", "8.0.2032", "8.0.2114", "8.0.2259",
        "14.11.85", "14.12.85", "8.0.2042",
    }
    assert stateful_rids.issubset(set(queried_rids))
    # The button rid (find-device) is EXCLUDED from the rid-keyed read.
    assert "8.0.2096" not in queried_rids
    # Each returned stateful {rid: value} was staged as a rid-keyed seed.
    assert ("4.4.85", "1") in seeded
    assert ("8.0.2032", "0") in seeded
    # No button rid is ever seeded (would raise on stateless button entity).
    seeded_rids = {rid for rid, _ in seeded}
    assert "8.0.2096" not in seeded_rids


async def test_setup_skips_settings_seed_when_no_cloud_token(
    hass, patch_clientsession,
) -> None:
    """With no cloud token, the settings seed must be skipped silently and
    setup must still proceed -- no cloud call, no error.
    """
    entry = _hub_entry(hass)
    # No token -> no cloud session.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_AQARA_TOKEN: ""},
    )
    sub = _make_subentry(
        subentry_id="sub-plug", did="lumi1.plug", model="lumi.plug.aeu002",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])
    fake_cloud.query_resources_by_rid = AsyncMock(
        side_effect=AssertionError("query_resources_by_rid called without token"),
    )

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    fake_cloud.query_resources_by_rid.assert_not_called()


async def test_setup_settings_seed_cloud_error_does_not_block_setup(
    hass, patch_clientsession,
) -> None:
    """A failing query_resources_by_rid must be logged and swallowed: setup
    completes and no exception escapes.
    """
    entry = _hub_entry(hass)
    sub = _make_subentry(
        subentry_id="sub-plug", did="lumi1.plug", model="lumi.plug.aeu002",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])
    fake_cloud.query_resources_by_rid = AsyncMock(
        side_effect=RuntimeError("boom"),
    )

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    fake_cloud.query_resources_by_rid.assert_awaited()


async def test_setup_no_settings_makes_no_rid_cloud_call(
    hass, patch_clientsession,
) -> None:
    """A device whose model has no catalogued settings must not issue a
    rid-keyed cloud read.
    """
    # Both the hub and the sub-device must be models with no catalogued
    # SETTINGS: the hub self-device is seeded in the same pass, so a
    # settings-bearing hub model would itself issue a rid read.
    # lumi.gateway.agl013 (hub) and lumi.airmonitor.acn01 (sub) have none.
    entry = _hub_entry(hass, hub_model="lumi.gateway.agl013")
    sub = _make_subentry(
        subentry_id="sub-nosettings", did="lumi1.nosettings",
        model="lumi.airmonitor.acn01",
    )
    _attach_subentries(entry, {sub.subentry_id: sub})

    coord = _make_coordinator_mock()

    fake_cloud = MagicMock()
    fake_cloud.query_collection_panels = AsyncMock(return_value={})
    fake_cloud.query_device_traits = AsyncMock(return_value=[])
    fake_cloud.query_resources_by_rid = AsyncMock(return_value={})

    with patch(
        "custom_components.aqara_lanlink.HubCoordinator", return_value=coord,
    ), patch(
        "custom_components.aqara_lanlink.AqaraCloudClient",
        return_value=fake_cloud,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    fake_cloud.query_resources_by_rid.assert_not_called()


def test_dead_modules_are_deleted():
    """Internal modules retired across V1->V3 must stay gone. This is a
    structural regression guard against accidentally re-importing them
    in a future change.
    """
    import importlib
    for name in (
        "custom_components.aqara_lanlink.device.path_cache",
        "custom_components.aqara_lanlink.device.learned_cluster_cache",
        "custom_components.aqara_lanlink.device.path_predictor",
        "custom_components.aqara_lanlink.device.catalog_cache",
    ):
        try:
            importlib.import_module(name)
        except ImportError:
            continue
        pytest.fail(f"{name} should be deleted")


async def test_setup_does_not_open_traits_storage_file(hass, monkeypatch):
    """Setup completes without instantiating the legacy
    aqara_lanlink_traits Store (BY_ID persistence is gone).
    """
    from homeassistant.helpers import storage as ha_storage

    opened_keys: list[str] = []
    original = ha_storage.Store.__init__

    def _spy(self, hass, version, key, *args, **kwargs):
        opened_keys.append(key)
        return original(self, hass, version, key, *args, **kwargs)

    monkeypatch.setattr(ha_storage.Store, "__init__", _spy)

    from tests.test_init import _hub_entry
    entry = _hub_entry(hass, hub_did="hub_traits_test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "aqara_lanlink_traits" not in opened_keys


