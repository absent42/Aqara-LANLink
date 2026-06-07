"""Regression guard: is_camera_model uses the V3 deviceTypes index, not Device subclasses."""
from __future__ import annotations

from custom_components.aqara_lanlink.device import catalog


def test_is_camera_model_true_for_real_camera_model():
    # lumi.camera.gwpgl1 is in the V3 catalogue with Camera deviceType.
    # If the data.json was regenerated this should resolve via device_types_for_model.
    assert catalog.is_camera_model("lumi.camera.gwpgl1") is True


def test_is_camera_model_false_for_non_camera():
    assert catalog.is_camera_model("lumi.sensor_occupy.agl8") is False


def test_is_camera_model_false_for_unknown_model():
    assert catalog.is_camera_model("lumi.nonexistent.never") is False
