"""Tests for Camera deviceType trait handling (routes to _fallback)."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.device_types import (
    _base, _fallback, get_composer,
)


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.camera.gwpgl1")


def test_camera_routes_to_fallback():
    assert get_composer("Camera") is _fallback.compose


def test_camera_empty_returns_empty():
    assert _fallback.compose(endpoint_id=2, traits={}, context=_ctx()) == []
