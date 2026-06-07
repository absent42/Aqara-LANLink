"""Tests for PTZ controller lifecycle + PTZ entity services (Chunk 6).

These tests follow the lightweight, direct-construction style of
test_camera.py (no HA harness): synthetic devices/subentries, mock
coordinators, and a recording fake controller injected into runtime_data.

The CRITICAL case the plan flags is the hub's OWN G350
(self_device, model lumi.camera.agl010) -- the spec's verified device --
not only a per-subentry camera with a static IP.
"""

from __future__ import annotations

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
    DOMAIN,
)
from custom_components.aqara_lanlink.device.base import SelfDeviceContext
from custom_components.aqara_lanlink.device.camera.base import CameraDevice

G350_MODEL = "lumi.camera.agl010"
NON_PTZ_CAMERA_MODEL = "lumi.camera.acn003"


# ---------------------------------------------------------------------------
# HA-harness helpers (mirror tests/test_init.py).
# ---------------------------------------------------------------------------


def _hub_entry(hass, *, hub_did="lumi1.HUB", hub_model=G350_MODEL) -> MockConfigEntry:
    """Hub config entry whose OWN model is the G350 (so self_device is PTZ)."""
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


def _make_subentry(*, subentry_id, did, model) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=subentry_id,
        data={"did": did, "model": model, "_cloud_metadata": {}},
    )


def _attach_subentries(entry, subentries) -> None:
    object.__setattr__(entry, "subentries", subentries)


def _make_coordinator_mock(hub_did="lumi1.HUB") -> MagicMock:
    coord = MagicMock()
    coord.start = MagicMock()
    coord.stop = AsyncMock()
    coord.wait_connected = AsyncMock()
    coord.async_read = AsyncMock(return_value={})
    coord.did = hub_did
    coord.token = "TOK"
    coord.cloud_client = None
    coord.lanlink_topology_dids = frozenset()
    return coord


def _setup_patches(hass, coord):
    """Common patch stack for driving async_setup_entry."""
    return (
        patch("custom_components.aqara_lanlink.HubCoordinator", return_value=coord),
        patch(
            "custom_components.aqara_lanlink.AqaraCloudClient",
            return_value=MagicMock(),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ),
    )


@pytest.fixture
def patch_clientsession():
    with patch(
        "custom_components.aqara_lanlink.async_get_clientsession",
        return_value=MagicMock(),
    ) as p:
        yield p


class _TestCamera(CameraDevice):
    MODEL = G350_MODEL
    RTSP_URL_TRAIT_PATH = "1.2.20368"


def _subentry_with_ip(**extra):
    return SimpleNamespace(
        subentry_id="sub_cam",
        data={
            "did": "lumi1.ptzcam",
            "model": G350_MODEL,
            "camera_ip": "10.1.20.150",
            "rtsp_username": "admin",
            "rtsp_password": "secret",
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# Task 6.0: async camera-IP resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_resolve_camera_ip_returns_resolved_ip():
    sub = _subentry_with_ip()
    device = _TestCamera(coordinator=MagicMock(), subentry=sub, derived=[])
    coordinator = SimpleNamespace(hass=None)

    assert await device.async_resolve_camera_ip(coordinator, sub) == "10.1.20.150"


@pytest.mark.asyncio
async def test_async_resolve_camera_ip_self_device_reads_trait():
    """For the hub's own camera (SelfDeviceContext, no static IP) the IP comes
    from an async RTSP-URL trait read via the coordinator."""
    self_ctx = SelfDeviceContext(did="lumi1.hub", model=G350_MODEL)
    device = _TestCamera(coordinator=MagicMock(), subentry=self_ctx, derived=[])

    coordinator = SimpleNamespace(
        hass=None,
        async_read=AsyncMock(
            return_value={
                "1.2.20368": "rtsp://admin:pw@192.168.1.77:554/stream",
            },
        ),
    )

    assert await device.async_resolve_camera_ip(coordinator, self_ctx) == "192.168.1.77"


# ---------------------------------------------------------------------------
# Task 6.1: build/teardown PtzController per PTZ camera
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_data_has_ptz_controllers_field():
    """The new field exists and defaults to an empty dict."""
    data = AqaraLanLinkRuntimeData(hub=MagicMock(), devices={})
    assert data.ptz_controllers == {}


@pytest.mark.asyncio
async def test_self_device_g350_builds_controller_with_full_features(
    hass, patch_clientsession,
):
    """CRITICAL: the hub's OWN G350 (self_device) gets a controller with the
    full feature set. A per-subentry non-PTZ camera gets none."""
    hub_did = "lumi1.HUB"
    entry = _hub_entry(hass, hub_did=hub_did, hub_model=G350_MODEL)
    non_ptz = _make_subentry(
        subentry_id="sub-nonptz", did="lumi1.NONPTZ", model=NON_PTZ_CAMERA_MODEL,
    )
    _attach_subentries(entry, {non_ptz.subentry_id: non_ptz})

    coord = _make_coordinator_mock(hub_did=hub_did)
    p1, p2, p3 = _setup_patches(hass, coord)
    with p1, p2, p3:
        await async_setup_entry(hass, entry)

    controllers = entry.runtime_data.ptz_controllers
    # self_device (the G350) is keyed by the hub DID with full PTZ features.
    assert hub_did in controllers
    assert controllers[hub_did].features == frozenset(
        {"pan_tilt", "zoom", "presets"},
    )
    # Non-PTZ per-subentry camera gets no controller.
    assert "lumi1.NONPTZ" not in controllers


@pytest.mark.asyncio
async def test_per_subentry_ptz_camera_builds_controller(hass, patch_clientsession):
    """A per-subentry PTZ camera gets a controller keyed by its DID."""
    entry = _hub_entry(hass, hub_model="lumi.gateway.agl004")  # non-camera hub
    ptz_sub = _make_subentry(
        subentry_id="sub-ptz", did="lumi1.PTZ", model="lumi.camera.acn007",
    )
    _attach_subentries(entry, {ptz_sub.subentry_id: ptz_sub})

    coord = _make_coordinator_mock()
    p1, p2, p3 = _setup_patches(hass, coord)
    with p1, p2, p3:
        await async_setup_entry(hass, entry)

    controllers = entry.runtime_data.ptz_controllers
    assert "lumi1.PTZ" in controllers
    assert controllers["lumi1.PTZ"].features == frozenset({"pan_tilt", "presets"})


@pytest.mark.asyncio
async def test_controller_built_lazily_not_connected_at_setup(
    hass, patch_clientsession,
):
    """The controller must NOT connect / resolve an IP at setup -- it connects
    lazily on first command."""
    entry = _hub_entry(hass, hub_model=G350_MODEL)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()
    p1, p2, p3 = _setup_patches(hass, coord)
    with p1, p2, p3:
        await async_setup_entry(hass, entry)

    controller = entry.runtime_data.ptz_controllers["lumi1.HUB"]
    # The PTZ controller establishes no P2P session and fetches no creds at
    # setup -- it connects lazily on first command. (The camera device's own
    # RTSP-endpoint resolution is independent and may run at setup.)
    assert controller._session is None
    assert controller._creds is None


@pytest.mark.asyncio
async def test_unload_shuts_down_controllers(hass, patch_clientsession):
    """async_unload_entry shuts every controller down and clears the dict."""
    entry = _hub_entry(hass, hub_model=G350_MODEL)
    _attach_subentries(entry, {})

    coord = _make_coordinator_mock()
    p1, p2, p3 = _setup_patches(hass, coord)
    with p1, p2, p3:
        await async_setup_entry(hass, entry)

    fake = MagicMock()
    fake.async_shutdown = AsyncMock()
    entry.runtime_data.ptz_controllers["fake"] = fake

    with patch.object(
        hass.config_entries, "async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        await async_unload_entry(hass, entry)

    fake.async_shutdown.assert_awaited_once()
    assert entry.runtime_data.ptz_controllers == {}


# ---------------------------------------------------------------------------
# Task 6.2: camera entity holds controller; register entity services
# ---------------------------------------------------------------------------


class _RecordingController:
    """Records the PTZ commands dispatched to it."""

    def __init__(self, features=frozenset({"pan_tilt", "zoom", "presets"})):
        self.features = features
        self.calls = []

    async def ptz_move(self, direction, *, continuous=False, duration=None):
        self.calls.append(("move", direction, continuous, duration))

    async def ptz_stop(self):
        self.calls.append(("stop",))

    async def ptz_zoom(self, scale):
        self.calls.append(("zoom", scale))

    async def zoom_step(self, delta):
        self.calls.append(("zoom_step", delta))

    async def goto_preset(self, position):
        self.calls.append(("preset", position))


def _camera_entity(controller=None):
    from custom_components.aqara_lanlink.camera import AqaraCameraEntity
    from .conftest import make_device, make_hub, make_subentry

    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(hub, device, sub, stream_source="rtsp://x/stream")
    cam._ptz_controller = controller
    return cam


@pytest.mark.asyncio
async def test_handler_dispatches_every_command_to_controller():
    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)

    await cam.async_ptz("left", continuous=True, duration=2.0)
    await cam.async_ptz("stop")
    await cam.async_ptz("zoom", value="3.0")
    await cam.async_ptz("zoom_in")
    await cam.async_ptz("zoom_out")

    assert ctrl.calls == [
        ("move", "left", True, 2.0),
        ("stop",),
        ("zoom", 3.0),
        ("zoom_step", 1.0),
        ("zoom_step", -1.0),
    ]


@pytest.mark.asyncio
async def test_zoom_in_out_step_defaults_and_honours_value():
    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)

    await cam.async_ptz("zoom_in")               # default step
    await cam.async_ptz("zoom_out")              # default step
    await cam.async_ptz("zoom_in", value="0.5")  # custom step
    await cam.async_ptz("zoom_out", value="0.5")

    assert ctrl.calls == [
        ("zoom_step", 1.0),
        ("zoom_step", -1.0),
        ("zoom_step", 0.5),
        ("zoom_step", -0.5),
    ]


@pytest.mark.asyncio
async def test_zoom_step_rejects_non_positive_or_non_numeric_value():
    from homeassistant.exceptions import HomeAssistantError

    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)

    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom_in", value="lots")  # not a number
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom_out", value="0")    # not positive
    assert ctrl.calls == []


@pytest.mark.asyncio
async def test_handler_raises_when_no_controller():
    from homeassistant.exceptions import HomeAssistantError

    cam = _camera_entity(controller=None)
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("left")
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("stop")
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom", value="2.0")


@pytest.mark.asyncio
async def test_zoom_command_requires_valid_numeric_value():
    from homeassistant.exceptions import HomeAssistantError

    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)

    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom")  # missing value
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom", value="wide")  # not a number
    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("zoom", value="12")  # out of 1.0-9.0 range
    assert ctrl.calls == []


@pytest.mark.asyncio
async def test_preset_command_requires_value():
    from homeassistant.exceptions import HomeAssistantError

    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)

    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("preset")  # missing value
    assert ctrl.calls == []


@pytest.mark.asyncio
async def test_setup_entry_assigns_controller_from_runtime_data():
    """camera.py:async_setup_entry looks up the device's controller in
    runtime_data.ptz_controllers and stashes it on the entity."""
    from custom_components.aqara_lanlink.camera import (
        AqaraCameraEntity,
        async_setup_entry as camera_setup,
    )
    from .conftest import make_device, make_hub, make_subentry

    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1", did="dev-1")
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(hub, device, sub, stream_source="rtsp://x")
    device.async_setup_camera = AsyncMock(return_value=[cam])  # type: ignore[method-assign]

    ctrl = _RecordingController()
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            hub=hub, devices={"sub-1": device}, self_device=None,
            ptz_controllers={"dev-1": ctrl},
            cloud_client=MagicMock(), cloud_token="TOK",
        ),
        subentries={"sub-1": sub},
    )
    # hass.data.get(sentinel) returns a truthy MagicMock here, so the service
    # registration short-circuits before touching the current platform.
    await camera_setup(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: None,
    )
    assert cam._ptz_controller is ctrl
    assert cam._cloud_token == "TOK"
    assert cam._did == "dev-1"


@pytest.mark.asyncio
async def test_ptz_service_dispatches_to_controller(hass):
    """A real `ptz` service call dispatches to the entity's controller."""
    from pytest_homeassistant_custom_component.common import MockEntityPlatform

    from custom_components.aqara_lanlink.camera import _register_ptz_services

    ctrl = _RecordingController()
    cam = _camera_entity(ctrl)
    cam.entity_id = "camera.g350_test"

    platform = MockEntityPlatform(
        hass, domain="camera", platform_name="aqara_lanlink",
    )
    await platform.async_add_entities([cam])
    _register_ptz_services(hass, platform)

    await hass.services.async_call(
        "aqara_lanlink", "ptz",
        {"entity_id": "camera.g350_test", "command": "left"},
        blocking=True,
    )
    assert ("move", "left", False, None) in ctrl.calls


@pytest.mark.asyncio
async def test_second_entry_setup_does_not_double_register(hass):
    """The hass.data sentinel guards against re-registering entity services
    when a second config entry sets the camera platform up."""
    from pytest_homeassistant_custom_component.common import MockEntityPlatform

    from custom_components.aqara_lanlink.camera import _register_ptz_services

    platform = MockEntityPlatform(hass, platform_name="aqara_lanlink")
    _register_ptz_services(hass, platform)
    # Second call (e.g. a second hub config entry) must be a no-op, not raise.
    platform2 = MockEntityPlatform(hass, platform_name="aqara_lanlink")
    _register_ptz_services(hass, platform2)


# ---------------------------------------------------------------------------
# Task 6.3: preset name/id -> position string resolution
# ---------------------------------------------------------------------------


def _ptz_camera_with_cloud(controller, positions):
    cam = _camera_entity(controller)
    cloud = MagicMock()
    cloud.query_ptz_positions = AsyncMock(return_value=positions)
    cam._cloud_client = cloud
    cam._cloud_token = "TOK"
    cam._did = "lumi1.PTZ"
    return cam, cloud


@pytest.mark.asyncio
async def test_goto_preset_resolves_name_to_position():
    ctrl = _RecordingController()
    cam, cloud = _ptz_camera_with_cloud(
        ctrl, [{"id": 1, "name": "Door", "position": "POS-A"}],
    )

    await cam.async_ptz("preset", value="Door")

    cloud.query_ptz_positions.assert_awaited_once_with("TOK", "lumi1.PTZ")
    assert ("preset", "POS-A") in ctrl.calls


@pytest.mark.asyncio
async def test_goto_preset_resolves_id_to_position():
    ctrl = _RecordingController()
    cam, _ = _ptz_camera_with_cloud(
        ctrl, [{"id": 1, "name": "Door", "position": "POS-A"}],
    )

    await cam.async_ptz("preset", value="1")

    assert ("preset", "POS-A") in ctrl.calls


@pytest.mark.asyncio
async def test_goto_preset_unknown_raises():
    from homeassistant.exceptions import HomeAssistantError

    ctrl = _RecordingController()
    cam, _ = _ptz_camera_with_cloud(
        ctrl, [{"id": 1, "name": "Door", "position": "POS-A"}],
    )

    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("preset", value="Garden")
    assert ctrl.calls == []


@pytest.mark.asyncio
async def test_goto_preset_cache_self_heals_for_app_added_preset():
    """A preset added in the app after the first fetch is recallable on the
    next recall: a cache miss triggers one refetch + retry."""
    ctrl = _RecordingController()
    cam, cloud = _ptz_camera_with_cloud(
        ctrl, [{"id": 1, "name": "Door", "position": "POS-A"}],
    )

    # First recall populates the cache (only Door known).
    await cam.async_ptz("preset", value="Door")
    assert ("preset", "POS-A") in ctrl.calls

    # The app adds a new preset; the cloud now returns both.
    cloud.query_ptz_positions = AsyncMock(
        return_value=[
            {"id": 1, "name": "Door", "position": "POS-A"},
            {"id": 2, "name": "Garden", "position": "POS-B"},
        ],
    )
    await cam.async_ptz("preset", value="Garden")
    assert ("preset", "POS-B") in ctrl.calls


@pytest.mark.asyncio
async def test_goto_preset_unknown_only_refetches_once():
    """A genuinely-unknown preset refetches once then raises (no infinite loop)."""
    from homeassistant.exceptions import HomeAssistantError

    ctrl = _RecordingController()
    cam, cloud = _ptz_camera_with_cloud(
        ctrl, [{"id": 1, "name": "Door", "position": "POS-A"}],
    )
    # Prime the cache.
    await cam.async_ptz("preset", value="Door")
    cloud.query_ptz_positions.reset_mock()

    with pytest.raises(HomeAssistantError):
        await cam.async_ptz("preset", value="Nope")
    # Exactly one refetch on the miss, no retry loop.
    cloud.query_ptz_positions.assert_awaited_once()
