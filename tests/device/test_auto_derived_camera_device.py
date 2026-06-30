"""Regression guard for V3 camera-model routing.

When the V3 catalogue marks a model with `deviceType="Camera"` and the model
has NO hand-authored `@register_device` subclass (the common case after the
V3 cut-over), `__init__.py` must route to `AutoDerivedCameraDevice` rather
than `AutoDerivedDevice`. Otherwise camera entities, talk-client setup, and
the RTSP URL trait read all silently no-op.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.device.base import (
    AutoDerivedDevice,
    resolve_subentry_metadata,
)
from custom_components.aqara_lanlink.device.camera.base import (
    AutoDerivedCameraDevice,
    CameraDevice,
)
from custom_components.aqara_lanlink.device import catalog


def _fake_subentry(model: str, display_name: str = "G400 Test") -> MagicMock:
    sub = MagicMock()
    sub.data = {
        "did": "lumi3.testdid",
        "model": model,
        "_cloud_metadata": {
            "model": model,
            "deviceName": display_name,
            "parentDeviceId": "",
        },
    }
    sub.subentry_id = "test-subentry"
    return sub


def test_auto_derived_camera_device_inherits_camera_setup():
    """AutoDerivedCameraDevice must be a CameraDevice so async_setup_camera is available."""
    assert issubclass(AutoDerivedCameraDevice, CameraDevice)
    # async_setup_camera lives on CameraDevice; subclass inherits it.
    assert hasattr(AutoDerivedCameraDevice, "async_setup_camera")
    assert hasattr(AutoDerivedCameraDevice, "async_setup_extra_numbers")


def test_auto_derived_camera_device_carries_rtsp_url_trait_path_default():
    """The default RTSP_URL_TRAIT_PATH must propagate so async_prefill_extras
    and _resolve_camera_endpoint work without a hand-authored subclass."""
    assert AutoDerivedCameraDevice.RTSP_URL_TRAIT_PATH == "7.136.20368"


def test_auto_derived_camera_device_is_not_auto_derived_device():
    """The two stock dispatch targets are siblings; mixing them would defeat the
    routing fix's purpose."""
    assert not issubclass(AutoDerivedCameraDevice, AutoDerivedDevice)
    assert not issubclass(AutoDerivedDevice, AutoDerivedCameraDevice)


def test_resolve_subentry_metadata_picks_cloud_device_name():
    """The helper extracted from AutoDerivedDevice gives the same answer
    AutoDerivedDevice's __init__ used to compute inline."""
    sub = _fake_subentry("lumi.camera.agl013", display_name="G400 Video Doorbell")
    model, parent_did, manufacturer, display_name = resolve_subentry_metadata(sub)
    assert model == "lumi.camera.agl013"
    assert parent_did == ""
    assert manufacturer == "Aqara"
    assert display_name == "G400 Video Doorbell"


def test_resolve_subentry_metadata_falls_back_to_model_when_cloud_silent():
    sub = MagicMock()
    sub.data = {"model": "lumi.camera.unknown"}
    model, parent_did, manufacturer, display_name = resolve_subentry_metadata(sub)
    assert model == "lumi.camera.unknown"
    assert parent_did == ""
    assert manufacturer == "Aqara"
    assert display_name == "lumi.camera.unknown"


@pytest.mark.parametrize("devicetype", [1, 8, "1", "8"])
def test_resolve_subentry_metadata_forces_empty_parent_for_gateway_class(devicetype):
    """Gateway-class devices (devicetype 1=hub, 8=camera/standalone) are their own
    parent on the LAN -> PARENT_DID forced empty so coordinator framing is
    did==sdid==device, even if the cloud record carries a parentDeviceId."""
    sub = MagicMock()
    sub.data = {"model": "lumi.camera.agl010", "_cloud_metadata": {
        "model": "lumi.camera.agl010", "devicetype": devicetype,
        "parentDeviceId": "lumi1.somehub",  # relayed-by-hub: must be ignored
    }}
    _, parent_did, _, _ = resolve_subentry_metadata(sub)
    assert parent_did == ""


def test_resolve_subentry_metadata_keeps_parent_for_subdevice():
    """A Zigbee sub-device (devicetype 2) keeps its parent hub -> framing-via-parent."""
    sub = MagicMock()
    sub.data = {"model": "lumi.plug.aeu002", "_cloud_metadata": {
        "model": "lumi.plug.aeu002", "devicetype": 2,
        "parentDeviceId": "lumi1.54ef447b3c21",
    }}
    _, parent_did, _, _ = resolve_subentry_metadata(sub)
    assert parent_did == "lumi1.54ef447b3c21"


def test_v3_camera_models_route_to_camera_device_via_is_camera_model():
    """Models marked Camera in the V3 catalogue must be detectable by the
    dispatch logic. is_camera_model() is the gate __init__.py's dispatch
    consults after registry.get_device_class returns None.
    """
    # Both recovered camera models are in the V3 catalogue:
    assert catalog.is_camera_model("lumi.camera.agl005") is True
    assert catalog.is_camera_model("lumi.camera.agl013") is True
    # Sanity: a non-camera model is not.
    assert catalog.is_camera_model("lumi.sensor_occupy.agl8") is False
    assert catalog.is_camera_model("lumi.does.not.exist") is False
