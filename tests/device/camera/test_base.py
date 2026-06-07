"""Tests for the CameraDevice base class.

Exercises the shared imperative infrastructure that every camera model
inherits: EXTRA_CONFIG_SCHEMA, async_prefill_extras, async_setup_camera,
async_setup, async_unload, and the play_audio_file service wiring.

Tests that are specific to the G400 model (EVENTS, AUGMENT_AUTO_DERIVE,
trait-driven doorbell event, etc.) remain in
tests/device/models/camera_agl013/test_camera_agl013.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.aqara_lanlink.camera import AqaraCameraEntity
from custom_components.aqara_lanlink.device.base import SelfDeviceContext
from custom_components.aqara_lanlink.device.camera.base import CameraDevice
from custom_components.aqara_lanlink.device.camera.number_entities import (
    DEFAULT_DETECTION_CLEAR_DELAY_S,
    DetectionClearDelayNumber,
)


# ---------------------------------------------------------------------------
# Minimal concrete subclass — CameraDevice has no MODEL of its own.
# ---------------------------------------------------------------------------

class _TestCamera(CameraDevice):
    MODEL = "lumi.camera.test"
    RTSP_URL_TRAIT_PATH = "1.2.20368"


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _camera_subentry(**extra):
    return SimpleNamespace(
        subentry_id="sub_camera",
        data={
            "did": "lumi1.abc",
            "model": "lumi.camera.test",
            "camera_ip": "10.1.20.150",
            "rtsp_username": "admin",
            "rtsp_password": "p@ss#word",
            **extra,
        },
    )


def _hub(hass=None):
    """Minimal hub stub with the public surface async_setup_camera consumes."""
    return SimpleNamespace(
        connected=True,
        did="lumi.gateway.123",
        hass=hass,
    )


def _hass_with_services():
    """Build a fake hass with services + data dict suitable for register/remove."""
    services_registry: dict[tuple[str, str], object] = {}

    def has_service(domain: str, name: str) -> bool:
        return (domain, name) in services_registry

    def async_register(domain, name, handler, schema=None, **kwargs):
        services_registry[(domain, name)] = handler

    def async_remove(domain, name):
        services_registry.pop((domain, name), None)

    hass = MagicMock()
    hass.config = SimpleNamespace(config_dir="/fake/config", is_allowed_path=lambda p: True)
    hass.services = SimpleNamespace(
        has_service=has_service,
        async_register=async_register,
        async_remove=async_remove,
        _registry=services_registry,
    )
    hass.async_add_executor_job = AsyncMock(return_value=True)
    hass.data = {}
    return hass


def _patch_camera_runtime():
    """Patch AqaraLanTalkClient in CameraDevice's module with an async-friendly mock.

    Returns a context manager whose __enter__ yields talk_inst.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        talk_inst = MagicMock()
        talk_inst.is_connected = False
        talk_inst.connect = AsyncMock(return_value=True)
        talk_inst.disconnect = AsyncMock()
        with patch(
            "custom_components.aqara_lanlink.device.camera.base."
            "AqaraLanTalkClient",
            return_value=talk_inst,
        ):
            yield talk_inst

    return _ctx()


# ---------------------------------------------------------------------------
# TestCameraDeviceExtraConfigSchema
# ---------------------------------------------------------------------------


class TestCameraDeviceExtraConfigSchema:
    def test_schema_accepts_valid_rtsp_data(self):
        valid = {
            "camera_ip": "10.1.20.150",
            "rtsp_username": "admin",
            "rtsp_password": "secret",
        }
        validated = CameraDevice.EXTRA_CONFIG_SCHEMA(valid)
        assert validated["camera_ip"] == "10.1.20.150"
        assert validated["rtsp_username"] == "admin"
        assert validated["rtsp_password"] == "secret"
        assert validated["backchannel_channel"] == 1

    def test_extra_config_schema_fields_are_optional(self):
        assert CameraDevice.EXTRA_CONFIG_SCHEMA({}) == {"backchannel_channel": 1}

    def test_extra_config_schema_accepts_backchannel_channel(self):
        out = CameraDevice.EXTRA_CONFIG_SCHEMA(
            {"camera_ip": "1.2.3.4", "backchannel_channel": 2},
        )
        assert out["backchannel_channel"] == 2

    def test_extra_config_schema_backchannel_defaults_to_1(self):
        out = CameraDevice.EXTRA_CONFIG_SCHEMA({"camera_ip": "1.2.3.4"})
        assert out["backchannel_channel"] == 1

    def test_extra_config_schema_rejects_out_of_range_channel(self):
        with pytest.raises(vol.Invalid):
            CameraDevice.EXTRA_CONFIG_SCHEMA(
                {"camera_ip": "1.2.3.4", "backchannel_channel": 9},
            )


# ---------------------------------------------------------------------------
# Prefill extras
# ---------------------------------------------------------------------------


def _coord_with_cloud(traits_response=None, raise_on_query=None):
    """Build a mock hub coordinator with a cloud_client whose
    query_device_traits returns `traits_response` (or raises)."""
    coord = MagicMock()
    cloud = MagicMock()
    if raise_on_query is not None:
        cloud.query_device_traits = AsyncMock(side_effect=raise_on_query)
    else:
        cloud.query_device_traits = AsyncMock(return_value=traits_response or [])
    coord.cloud_client = cloud
    coord.token = "test-token"
    return coord


def _patch_catalog_known_paths(monkeypatch, paths: set[str]) -> None:
    """Stub out catalog.all_traits_for_model / dropped_paths_for_model so
    that prefill's "is this candidate a known catalogue path" filter
    accepts the synthetic wire paths used in these unit tests.
    """
    from custom_components.aqara_lanlink.device import catalog
    monkeypatch.setattr(
        catalog, "all_traits_for_model", lambda model: {p: None for p in paths},
    )
    monkeypatch.setattr(
        catalog, "dropped_paths_for_model", lambda model: frozenset(),
    )


async def test_prefill_extras_returns_parsed_details(monkeypatch):
    coordinator = _coord_with_cloud(traits_response=[
        {"path": "camera_rtsp_path",
         "value": "rtsp://admin:secret@10.1.20.150:8554/ch1"},
    ])
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "camera_rtsp_path")
    _patch_catalog_known_paths(monkeypatch, {"camera_rtsp_path"})
    result = await _TestCamera.async_prefill_extras(
        MagicMock(), {"did": "cam-did", "model": "lumi.camera.test"}, coordinator,
    )
    assert result == {
        "camera_ip": "10.1.20.150",
        "rtsp_username": "admin",
        "rtsp_password": "secret",
    }


async def test_prefill_extras_returns_none_on_cloud_error(monkeypatch):
    """Cloud read failure (timeout, network drop, auth) -> None, blank form.
    Replaces the LANLink read-error case: prefill switched to cloud after
    LANLink relay reads for CameraRTSPURL were observed to time out."""
    coordinator = _coord_with_cloud(raise_on_query=RuntimeError("cloud unreachable"))
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "camera_rtsp_path")
    _patch_catalog_known_paths(monkeypatch, {"camera_rtsp_path"})
    result = await _TestCamera.async_prefill_extras(
        MagicMock(), {"did": "cam-did", "model": "lumi.camera.test"}, coordinator,
    )
    assert result is None


async def test_prefill_extras_returns_none_without_coordinator():
    result = await _TestCamera.async_prefill_extras(
        MagicMock(), {"did": "cam-did"}, None,
    )
    assert result is None


async def test_prefill_extras_returns_none_when_cloud_client_missing(monkeypatch):
    """A coordinator with no cloud_client (e.g. cloud-disabled install) cannot
    do the trait read -- prefill bails out and the form renders blank."""
    coordinator = MagicMock()
    coordinator.cloud_client = None
    coordinator.token = "test-token"
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "camera_rtsp_path")
    _patch_catalog_known_paths(monkeypatch, {"camera_rtsp_path"})
    result = await _TestCamera.async_prefill_extras(
        MagicMock(), {"did": "cam-did", "model": "lumi.camera.test"}, coordinator,
    )
    assert result is None


async def test_prefill_extras_discovers_camera_endpoint_from_catalogue(monkeypatch):
    """The RTSP wire path varies per camera model: G400 (agl013) is ep 7,
    every other Aqara camera is ep 2. async_prefill_extras must discover
    the actual Camera endpoint(s) from catalog.endpoints_for_model and
    try those wire paths instead of relying on the (G400-only) hardcoded
    RTSP_URL_TRAIT_PATH default.
    """
    # Use a real catalogued non-G400 camera: lumi.camera.agl005 (G100) has
    # its Camera endpoint at ep 2. The discovered candidate must be
    # "2.136.20368"; if only the hardcoded "7.136.20368" were tried, the
    # cloud would return nothing and the form would render blank.
    rtsp_url = "rtsp://admin:secret@10.1.20.151:8554/ch1"
    coordinator = _coord_with_cloud(traits_response=[
        {"path": "2.136.20368", "value": rtsp_url},
    ])
    # The hardcoded class-level path is wrong for this model; the test
    # asserts discovery fills the gap. Patch the catalogue to advertise
    # 2.136.20368 as a known path so the discovery filter accepts it.
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "7.136.20368")
    _patch_catalog_known_paths(monkeypatch, {"2.136.20368", "7.136.20368"})
    from custom_components.aqara_lanlink.device import catalog
    monkeypatch.setattr(
        catalog, "endpoints_for_model",
        lambda model: {2: {"deviceType": "Camera"}},
    )
    result = await _TestCamera.async_prefill_extras(
        MagicMock(),
        {"did": "g100-did", "model": "lumi.camera.agl005"},
        coordinator,
    )
    assert result == {
        "camera_ip": "10.1.20.151",
        "rtsp_username": "admin",
        "rtsp_password": "secret",
    }
    # And the cloud call must have included the discovered path, not just
    # the legacy hardcoded one.
    call_args = coordinator.cloud_client.query_device_traits.call_args
    assert call_args is not None
    paths_arg = call_args[0][2]  # third positional: list of trait paths
    assert "2.136.20368" in paths_arg, (
        f"discovery should have added 2.136.20368 (G100's Camera endpoint); "
        f"got {paths_arg}"
    )


async def test_prefill_extras_returns_none_when_path_not_in_response(monkeypatch):
    """Cloud returned traits but the requested trait_path isn't among them
    (the wire path is wrong for this model) -> None, blank form."""
    coordinator = _coord_with_cloud(traits_response=[
        {"path": "some.other.path", "value": "rtsp://admin:secret@1.2.3.4/"},
    ])
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "camera_rtsp_path")
    _patch_catalog_known_paths(monkeypatch, {"camera_rtsp_path"})
    result = await _TestCamera.async_prefill_extras(
        MagicMock(), {"did": "cam-did", "model": "lumi.camera.test"}, coordinator,
    )
    assert result is None


async def test_prefill_extras_returns_none_when_model_has_no_rtsp_trait(monkeypatch):
    """The G100 (lumi.camera.agl005) has a Camera endpoint but its V3 spec
    doesn't include CameraRTSPURL at all. Prefill must detect this from the
    catalogue (no candidate wire path is a known path) and skip the cloud
    query entirely -- form renders blank with an INFO-level log, no warning,
    no failed cloud round-trip.
    """
    coordinator = _coord_with_cloud(traits_response=[])
    monkeypatch.setattr(_TestCamera, "RTSP_URL_TRAIT_PATH", "")
    # Catalogue exposes a Camera endpoint at ep 2 but no CameraRTSPURL path.
    _patch_catalog_known_paths(monkeypatch, {"2.136.32930", "2.136.20030"})
    from custom_components.aqara_lanlink.device import catalog
    monkeypatch.setattr(
        catalog, "endpoints_for_model",
        lambda model: {2: {"deviceType": "Camera"}},
    )
    result = await _TestCamera.async_prefill_extras(
        MagicMock(),
        {"did": "g100-did", "model": "lumi.camera.agl005"},
        coordinator,
    )
    assert result is None
    # Cloud must NOT have been queried: there was nothing valid to ask for.
    coordinator.cloud_client.query_device_traits.assert_not_called()


# ---------------------------------------------------------------------------
# TestCameraDeviceSetupCamera
# ---------------------------------------------------------------------------


class TestCameraDeviceSetupCamera:
    async def test_returns_camera_entity_with_rtsp_url(self):
        """async_setup_camera builds three AqaraCameraEntity instances; the
        channel-1 entity's stream URL is composed from the subentry's camera
        fields."""
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        hub = _hub(hass=None)
        cameras = await device.async_setup_camera(hub, _camera_subentry())
        assert len(cameras) == 3
        camera = next(c for c in cameras if c._channel == 1)
        assert isinstance(camera, AqaraCameraEntity)
        # RTSP URL: scheme + url-encoded creds + host:port + path.
        # admin:p@ss#word -> admin:p%40ss%23word
        assert camera._stream_source == (
            "rtsp://admin:p%40ss%23word@10.1.20.150:8554/ch1"
        )

    async def test_kicks_off_go2rtc_registration_via_executor(self):
        """async_setup_camera calls hass.async_add_executor_job with
        go2rtc.register_stream and the three sources."""
        captured_args: list = []

        async def fake_executor_job(func, *args):
            captured_args.append((func, args))
            return True

        hass = MagicMock()
        hass.config = SimpleNamespace(config_dir="/fake/config")
        hass.async_add_executor_job = AsyncMock(side_effect=fake_executor_job)

        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        hub = _hub(hass=hass)
        cameras = await device.async_setup_camera(hub, _camera_subentry())
        assert cameras

        from custom_components.aqara_lanlink.device.camera import go2rtc
        assert len(captured_args) == 1
        func, args = captured_args[0]
        assert func is go2rtc.register_stream

        config_dir, stream_name, sources = args
        assert config_dir == "/fake/config"
        assert stream_name == "aqara_lanlink_10_1_20_150"
        # Three sources: RTSP camera input, ffmpeg transcode/repack, exec backchannel.
        assert len(sources) == 3
        assert any(s.startswith("rtsp://") for s in sources)
        assert any(s.startswith("ffmpeg:") for s in sources)
        assert any(s.startswith("exec:") for s in sources)

    async def test_no_hass_skips_go2rtc_registration_but_returns_camera(self):
        """If the coordinator has no hass (standalone tooling), the camera
        is still constructed but go2rtc registration is skipped without
        raising."""
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        hub = _hub(hass=None)
        cameras = await device.async_setup_camera(hub, _camera_subentry())
        assert cameras

    async def test_go2rtc_stream_name_recorded_for_unload(self):
        """The stream name we registered is stashed on the device so
        async_unload can call go2rtc.remove_stream with it."""
        hass = MagicMock()
        hass.config = SimpleNamespace(config_dir="/fake/config")
        hass.async_add_executor_job = AsyncMock(return_value=True)

        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        hub = _hub(hass=hass)
        await device.async_setup_camera(hub, _camera_subentry())
        assert device.go2rtc_stream_name == "aqara_lanlink_10_1_20_150"

    async def test_ffmpeg_source_includes_video_copy(self):
        """The ffmpeg producer source must include #video=copy so go2rtc
        passes the H.264 stream through without re-encoding."""
        captured_args: list = []

        async def fake_executor_job(func, *args):
            captured_args.append((func, args))
            return True

        hass = MagicMock()
        hass.config = SimpleNamespace(config_dir="/fake/config")
        hass.async_add_executor_job = AsyncMock(side_effect=fake_executor_job)

        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        hub = _hub(hass=hass)
        await device.async_setup_camera(hub, _camera_subentry())

        assert len(captured_args) == 1
        _func, args = captured_args[0]
        _config_dir, stream_name, sources = args
        ffmpeg_sources = [s for s in sources if s.startswith("ffmpeg:")]
        assert len(ffmpeg_sources) == 1
        expected = f"ffmpeg:{stream_name}#video=copy#audio=opus#audio=copy"
        assert ffmpeg_sources[0] == expected, (
            f"Expected ffmpeg source {expected!r}, got {ffmpeg_sources[0]!r}"
        )


# ---------------------------------------------------------------------------
# TestCameraDeviceSetup
# ---------------------------------------------------------------------------


class TestCameraDeviceSetup:
    async def test_setup_registers_play_audio_file_service(self):
        hass = _hass_with_services()
        hub = _hub(hass=hass)
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        with _patch_camera_runtime():
            await device.async_setup(hub, _camera_subentry())
        from custom_components.aqara_lanlink.const import DOMAIN
        assert (DOMAIN, "play_audio_file") in hass.services._registry

    async def test_setup_does_not_double_register_services(self):
        hass = _hass_with_services()
        from custom_components.aqara_lanlink.const import DOMAIN
        sentinel = object()
        hass.services._registry[(DOMAIN, "play_audio_file")] = sentinel

        hub = _hub(hass=hass)
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        with _patch_camera_runtime():
            await device.async_setup(hub, _camera_subentry())

        assert hass.services._registry[(DOMAIN, "play_audio_file")] is sentinel


# ---------------------------------------------------------------------------
# TestCameraDeviceUnload
# ---------------------------------------------------------------------------


class TestCameraDeviceUnload:
    async def test_unload_disconnects_client_removes_stream(self):
        hass = _hass_with_services()
        hub = _hub(hass=hass)
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        with patch(
            "custom_components.aqara_lanlink.device.camera.base."
            "AqaraLanTalkClient"
        ) as talk_cls:
            talk_inst = MagicMock()
            talk_inst.is_connected = True
            talk_inst.connect = AsyncMock()
            talk_inst.disconnect = AsyncMock()
            talk_cls.return_value = talk_inst

            await device.async_setup(hub, _camera_subentry())
            device.go2rtc_stream_name = "aqara_lanlink_10_1_20_150"

            await device.async_unload()

            talk_inst.disconnect.assert_awaited_once()
            from custom_components.aqara_lanlink.device.camera import go2rtc
            executor_calls = [
                c for c in hass.async_add_executor_job.call_args_list
                if c.args and c.args[0] is go2rtc.remove_stream
            ]
            assert len(executor_calls) == 1

    async def test_unload_deregisters_services_when_last_camera(self):
        hass = _hass_with_services()
        hub = _hub(hass=hass)
        device = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(),
            derived=[],
        )
        from custom_components.aqara_lanlink.const import DOMAIN

        with _patch_camera_runtime():
            await device.async_setup(hub, _camera_subentry())
            assert (DOMAIN, "play_audio_file") in hass.services._registry
            await device.async_unload()
            assert (DOMAIN, "play_audio_file") not in hass.services._registry

    async def test_unload_keeps_services_when_other_camera_still_active(self):
        """When more than one camera is registered, unloading one preserves
        services for the remaining devices."""
        hass = _hass_with_services()
        hub = _hub(hass=hass)
        from custom_components.aqara_lanlink.const import DOMAIN

        device1 = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(did="lumi1.first"),
            derived=[],
        )
        device2 = _TestCamera(
            coordinator=SimpleNamespace(),
            subentry=_camera_subentry(did="lumi1.second"),
            derived=[],
        )
        sub2 = _camera_subentry(did="lumi1.second")

        with _patch_camera_runtime():
            await device1.async_setup(hub, _camera_subentry(did="lumi1.first"))
            await device2.async_setup(hub, sub2)
            await device1.async_unload()

            assert (DOMAIN, "play_audio_file") in hass.services._registry
            await device2.async_unload()
            assert (DOMAIN, "play_audio_file") not in hass.services._registry


# ---------------------------------------------------------------------------
# Module-level generic tests (shared CameraDevice behaviour)
# ---------------------------------------------------------------------------


def test_register_services_registers_only_play_audio_file():
    hass = MagicMock()
    registered: dict = {}
    hass.services.has_service = lambda dom, svc: svc in registered
    hass.services.async_register = (
        lambda dom, svc, handler, schema=None: registered.__setitem__(svc, handler)
    )
    device = _TestCamera(
        coordinator=MagicMock(),
        subentry=_camera_subentry(),
        derived=[],
    )
    device._register_services_if_first(hass)
    assert set(registered) == {"play_audio_file"}


def test_services_yaml_declares_play_audio_file():
    import pathlib
    import yaml
    # test file is tests/device/camera/test_base.py
    # parents[0]=camera, parents[1]=device, parents[2]=tests, parents[3]=repo root.
    path = (
        pathlib.Path(__file__).parents[3]
        / "custom_components" / "aqara_lanlink" / "services.yaml"
    )
    doc = yaml.safe_load(path.read_text())
    assert "play_audio_file" in doc
    assert "talk_start" not in doc
    assert "talk_stop" not in doc
    assert set(doc["play_audio_file"]["fields"]) == {"device_id", "file_path"}


def _auto_clear_descriptor():
    from custom_components.aqara_lanlink.device.descriptors import (
        BinarySensorDescriptor,
    )
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    return BinarySensorDescriptor(
        key="motion", name="Motion",
        trait=TraitSpec(id="5.160.33000", name="Motion"),
        auto_clear_seconds=30.0,
    )


async def test_extra_numbers_skipped_without_auto_clear_descriptor():
    device = _TestCamera(
        coordinator=MagicMock(), subentry=_camera_subentry(), derived=[],
    )
    assert await device.async_setup_extra_numbers(
        MagicMock(), device.subentry,
    ) == []


async def test_extra_numbers_created_with_auto_clear_descriptor():
    device = _TestCamera(
        coordinator=MagicMock(), subentry=_camera_subentry(),
        derived=[_auto_clear_descriptor()],
    )
    nums = await device.async_setup_extra_numbers(MagicMock(), device.subentry)
    assert len(nums) == 1
    assert isinstance(nums[0], DetectionClearDelayNumber)


async def test_async_setup_extra_numbers_returns_delay_number():
    device = _TestCamera(
        coordinator=MagicMock(),
        subentry=_camera_subentry(),
        derived=[_auto_clear_descriptor()],
    )
    hub = MagicMock()
    nums = await device.async_setup_extra_numbers(hub, device.subentry)
    assert len(nums) == 1
    assert isinstance(nums[0], DetectionClearDelayNumber)


async def test_async_setup_extra_numbers_is_idempotent():
    device = _TestCamera(
        coordinator=MagicMock(),
        subentry=_camera_subentry(),
        derived=[_auto_clear_descriptor()],
    )
    first = await device.async_setup_extra_numbers(MagicMock(), device.subentry)
    second = await device.async_setup_extra_numbers(MagicMock(), device.subentry)
    assert len(first) == 1 and len(second) == 1
    assert first[0] is second[0]


async def test_resolve_auto_clear_seconds_uses_live_number():
    from custom_components.aqara_lanlink.device.descriptors import (
        BinarySensorDescriptor,
    )
    from custom_components.aqara_lanlink.device.traits import TraitSpec
    desc = BinarySensorDescriptor(
        key="k", name="n", trait=TraitSpec(id="1.1.1", name="n"),
        auto_clear_seconds=30.0,
    )
    device = _TestCamera(
        coordinator=MagicMock(),
        subentry=_camera_subentry(),
        derived=[desc],
    )
    # Before the number is set up, falls back to the descriptor value.
    assert device.resolve_auto_clear_seconds(desc) == 30.0
    # After setup, the live number value wins.
    nums = await device.async_setup_extra_numbers(MagicMock(), device.subentry)
    nums[0].async_write_ha_state = MagicMock()
    await nums[0].async_set_native_value(200.0)
    assert device.resolve_auto_clear_seconds(desc) == 200.0


def test_camera_device_rtsp_trait_path_default():
    from custom_components.aqara_lanlink.device.camera.base import CameraDevice
    assert CameraDevice.RTSP_URL_TRAIT_PATH == "7.136.20368"


async def test_async_setup_has_no_multicast_listener():
    device = _TestCamera(
        coordinator=MagicMock(),
        subentry=_camera_subentry(),
        derived=[],
    )
    coordinator = MagicMock()
    coordinator.hass = None
    with _patch_camera_runtime():
        await device.async_setup(coordinator, device.subentry)
    assert not hasattr(device, "_multicast_listener") or device._multicast_listener is None
    await device.async_unload()


# ---------------------------------------------------------------------------
# Cross-model service-resolution test (Step 4)
# ---------------------------------------------------------------------------


async def test_play_audio_file_resolves_across_camera_models():
    """The shared _camera_devices registry resolves a service call to the
    right device when two different camera models are both registered.

    This test would fail if the registry were still per-model (_g400_devices),
    because a second model's devices would never appear in the first model's
    private registry.
    """
    from custom_components.aqara_lanlink.const import DOMAIN
    from custom_components.aqara_lanlink.device.camera.base import (
        _CAMERA_REGISTRY_KEY,
        _resolve_camera_for_call,
    )

    # Two distinct camera model subclasses.
    class _ModelA(CameraDevice):
        MODEL = "lumi.camera.modela"
        RTSP_URL_TRAIT_PATH = "7.136.20368"

    class _ModelB(CameraDevice):
        MODEL = "lumi.camera.modelb"
        RTSP_URL_TRAIT_PATH = "7.136.20368"

    hass = _hass_with_services()
    hub = _hub(hass=hass)

    did_a = "lumi1.cam_a"
    did_b = "lumi1.cam_b"

    subentry_a = _camera_subentry(did=did_a, model="lumi.camera.modela")
    subentry_b = _camera_subentry(did=did_b, model="lumi.camera.modelb")

    device_a = _ModelA(
        coordinator=SimpleNamespace(),
        subentry=subentry_a,
        derived=[],
    )
    device_b = _ModelB(
        coordinator=SimpleNamespace(),
        subentry=subentry_b,
        derived=[],
    )

    with _patch_camera_runtime():
        await device_a.async_setup(hub, subentry_a)
        await device_b.async_setup(hub, subentry_b)

    # Both devices should be in the shared registry, keyed by DID.
    cam_registry = hass.data[DOMAIN][_CAMERA_REGISTRY_KEY]
    assert cam_registry[did_a] is device_a
    assert cam_registry[did_b] is device_b

    # Build fake HA device-registry entries for each camera.
    fake_ha_device_a = SimpleNamespace(identifiers={(DOMAIN, did_a)})
    fake_ha_device_b = SimpleNamespace(identifiers={(DOMAIN, did_b)})

    # Build fake service calls referencing each HA device id.
    call_a = SimpleNamespace(data={"device_id": "ha_dev_id_a", "file_path": "/tmp/a.aac"})
    call_b = SimpleNamespace(data={"device_id": "ha_dev_id_b", "file_path": "/tmp/b.aac"})

    # _resolve_camera_for_call does `from homeassistant.helpers import device_registry as dr`
    # inside the function, so we patch at the HA module level.
    with patch(
        "homeassistant.helpers.device_registry.async_get"
    ) as mock_async_get:
        dev_reg_mock = MagicMock()
        dev_reg_mock.async_get.side_effect = lambda device_id: (
            fake_ha_device_a if device_id == "ha_dev_id_a" else fake_ha_device_b
        )
        mock_async_get.return_value = dev_reg_mock

        resolved_a = _resolve_camera_for_call(hass, call_a)
        resolved_b = _resolve_camera_for_call(hass, call_b)

    # Each call resolves to the correct model's device instance.
    assert resolved_a is device_a, (
        f"Expected device_a (_ModelA/{did_a}), got {resolved_a!r}"
    )
    assert resolved_b is device_b, (
        f"Expected device_b (_ModelB/{did_b}), got {resolved_b!r}"
    )
    # And they are distinct model classes.
    assert type(resolved_a) is _ModelA
    assert type(resolved_b) is _ModelB


# ---------------------------------------------------------------------------
# Endpoint resolution (Task 5)
# ---------------------------------------------------------------------------


async def test_resolve_endpoint_from_subentry_data():
    device = _TestCamera(
        coordinator=SimpleNamespace(), subentry=_camera_subentry(), derived=[],
    )
    ip, user, pw = await device._resolve_camera_endpoint(
        SimpleNamespace(), _camera_subentry(),
    )
    assert (ip, user, pw) == ("10.1.20.150", "admin", "p@ss#word")


async def test_resolve_endpoint_self_device_trait_read():
    ctx = SelfDeviceContext(did="d1", model="lumi.camera.test")
    coord = SimpleNamespace(
        async_read=AsyncMock(
            return_value={"1.2.20368": "rtsp://admin:pw@192.168.1.20:8554/ch1"},
        ),
    )
    device = _TestCamera(coordinator=coord, subentry=ctx, derived=[])
    ip, user, pw = await device._resolve_camera_endpoint(coord, ctx)
    assert (ip, user, pw) == ("192.168.1.20", "admin", "pw")


async def test_resolve_endpoint_self_device_read_failure_is_safe():
    ctx = SelfDeviceContext(did="d1", model="lumi.camera.test")
    coord = SimpleNamespace(async_read=AsyncMock(side_effect=RuntimeError("boom")))
    device = _TestCamera(coordinator=coord, subentry=ctx, derived=[])
    assert await device._resolve_camera_endpoint(coord, ctx) == (None, None, None)


async def test_resolve_endpoint_options_win_over_trait_read():
    ctx = SelfDeviceContext(
        did="d1", model="lumi.camera.test", camera_ip="10.0.0.1",
        rtsp_username="u", rtsp_password="p",
    )
    coord = SimpleNamespace(
        async_read=AsyncMock(
            return_value={"1.2.20368": "rtsp://x:y@9.9.9.9:8554/ch1"},
        ),
    )
    device = _TestCamera(coordinator=coord, subentry=ctx, derived=[])
    assert await device._resolve_camera_endpoint(coord, ctx) == ("10.0.0.1", "u", "p")


async def test_async_setup_caches_endpoint():
    device = _TestCamera(
        coordinator=SimpleNamespace(), subentry=_camera_subentry(), derived=[],
    )
    with _patch_camera_runtime():
        await device.async_setup(_hub(hass=None), _camera_subentry())
    assert device._camera_ip == "10.1.20.150"
    assert device._endpoint_resolved is True


async def test_ensure_camera_endpoint_resolves_only_once():
    ctx = SelfDeviceContext(did="d1", model="lumi.camera.test")
    read = AsyncMock(
        return_value={"1.2.20368": "rtsp://admin:pw@192.168.1.20:8554/ch1"},
    )
    coord = SimpleNamespace(async_read=read)
    device = _TestCamera(coordinator=coord, subentry=ctx, derived=[])
    await device._ensure_camera_endpoint(coord, ctx)
    await device._ensure_camera_endpoint(coord, ctx)
    assert read.await_count == 1
    assert device._camera_ip == "192.168.1.20"


async def test_async_setup_camera_returns_three_channelled_entities():
    device = _TestCamera(
        coordinator=SimpleNamespace(), subentry=_camera_subentry(), derived=[],
    )
    cameras = await device.async_setup_camera(_hub(hass=None), _camera_subentry())
    assert len(cameras) == 3
    sources = sorted(c._stream_source for c in cameras)
    assert sources[0].endswith("/ch1")
    assert sources[1].endswith("/ch2")
    assert sources[2].endswith("/ch3")
    assert {c._channel for c in cameras} == {1, 2, 3}
