"""Tests for the camera platform.

Cameras are imperative: each device's `async_setup_camera` returns a list
of camera entities, or an empty list when the device exposes no camera.
The platform skips devices that return an empty list.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.camera import (
    AqaraCameraEntity,
    async_setup_entry,
)

from .conftest import make_device, make_hub, make_subentry


@pytest.mark.asyncio
async def test_camera_entity_stream_source_returns_url():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(
        hub, device, sub, stream_source="rtsp://test/stream",
    )
    assert await cam.stream_source() == "rtsp://test/stream"
    # Camera entities use the unique-id-suffix path: descriptor is None.
    assert cam.descriptor is None
    assert cam._attr_unique_id == f"{sub.subentry_id}_camera_ch1"


async def test_camera_overrides_ha_stream_source_method():
    """AqaraCameraEntity must override HA's Camera.stream_source -- that is
    the method HA's async_create_stream() calls. A wrongly-named override
    (e.g. async_stream_source) leaves the base no-op in place and breaks
    the stream."""
    from homeassistant.components.camera import Camera
    assert AqaraCameraEntity.stream_source is not Camera.stream_source


@pytest.mark.asyncio
async def test_async_setup_entry_skips_devices_returning_none():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([], subentry=sub)
    device.async_setup_camera = AsyncMock(return_value=[])  # type: ignore[method-assign]

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    assert added == []
    device.async_setup_camera.assert_awaited_once_with(hub, sub)


@pytest.mark.asyncio
async def test_async_setup_entry_adds_returned_entity():
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1")
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(
        hub, device, sub, stream_source="rtsp://test/stream",
    )
    device.async_setup_camera = AsyncMock(return_value=[cam])  # type: ignore[method-assign]

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(hub=hub, devices={"sub-1": device}, self_device=None),
        subentries={"sub-1": sub},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    assert added == [cam]


@pytest.mark.asyncio
async def test_async_setup_entry_handles_multiple_devices_mixed_results():
    hub = make_hub()
    sub_a = make_subentry(subentry_id="sub-a", did="dev-a")
    sub_b = make_subentry(subentry_id="sub-b", did="dev-b")
    device_a = make_device([], subentry=sub_a)
    device_b = make_device([], subentry=sub_b)
    cam_b = AqaraCameraEntity(hub, device_b, sub_b, stream_source="rtsp://b")
    device_a.async_setup_camera = AsyncMock(return_value=[])  # type: ignore[method-assign]
    device_b.async_setup_camera = AsyncMock(return_value=[cam_b])  # type: ignore[method-assign]

    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            hub=hub, devices={"sub-a": device_a, "sub-b": device_b}, self_device=None,
        ),
        subentries={"sub-a": sub_a, "sub-b": sub_b},
    )
    added: list = []
    await async_setup_entry(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    assert added == [cam_b]


@pytest.mark.asyncio
async def test_camera_advertises_stream_feature():
    from homeassistant.components.camera import CameraEntityFeature
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(hub, device, sub, stream_source="rtsp://test/stream")
    assert cam.supported_features & CameraEntityFeature.STREAM


@pytest.mark.asyncio
async def test_camera_image_uses_ffmpeg_helper(monkeypatch):
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(hub, device, sub, stream_source="rtsp://test/stream")
    cam.hass = MagicMock()

    async def _fake_get_image(hass, source, **kw):
        assert source == "rtsp://test/stream"
        return b"JPEGBYTES"

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.camera.async_get_image", _fake_get_image,
    )
    assert await cam.async_camera_image() == b"JPEGBYTES"


@pytest.mark.asyncio
async def test_camera_image_returns_none_when_no_hass():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(hub, device, sub, stream_source="rtsp://test/stream")
    cam.hass = None
    assert await cam.async_camera_image() is None


def test_camera_entity_channel_unique_id_and_translation_key():
    hub = make_hub()
    sub = make_subentry()
    device = make_device([], subentry=sub)
    cam = AqaraCameraEntity(
        hub, device, sub, stream_source="rtsp://x/ch2", channel=2,
    )
    assert cam._attr_unique_id == f"{sub.subentry_id}_camera_ch2"
    assert cam._attr_translation_key == "camera_ch2"
