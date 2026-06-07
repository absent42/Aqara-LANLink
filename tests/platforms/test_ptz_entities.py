"""Tests for the PTZ wrapper entities (Chunk 7).

Thin entities over the warm-session PtzController, gated per sub-capability
and built imperatively in each platform's async_setup_entry. These tests drive
the real button/number/select async_setup_entry with a SimpleNamespace
runtime_data (the lightweight, no-HA-harness style of test_camera.py) and
assert entity presence/gating plus controller delegation.

Feature matrix exercised:
  - G350      : pan_tilt + zoom + presets
  - G3/E1     : pan_tilt + presets (no zoom)
  - non-PTZ   : no controller -> no PTZ entities
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.button import (
    AqaraPtzDirectionButton,
    AqaraPtzStopButton,
    AqaraPtzZoomButton,
    async_setup_entry as button_setup,
)
from custom_components.aqara_lanlink.number import (
    AqaraPtzZoomNumber,
    async_setup_entry as number_setup,
)
from custom_components.aqara_lanlink.select import (
    AqaraPtzPresetSelect,
    async_setup_entry as select_setup,
)

from .conftest import make_device, make_hub, make_subentry

FULL = frozenset({"pan_tilt", "zoom", "presets"})
NO_ZOOM = frozenset({"pan_tilt", "presets"})


class _RecordingController:
    """Records the PTZ commands dispatched to it."""

    def __init__(self, features=FULL, current_zoom=1.0):
        self.features = features
        self.calls: list = []
        self._current_zoom = current_zoom

    @property
    def current_zoom(self):
        return self._current_zoom

    async def ptz_move(self, direction, *, continuous=False, duration=None):
        self.calls.append(("move", direction))

    async def ptz_stop(self):
        self.calls.append(("stop",))

    async def ptz_zoom(self, scale):
        self.calls.append(("zoom", scale))
        self._current_zoom = scale

    async def zoom_step(self, delta):
        self.calls.append(("zoom_step", delta))

    async def goto_preset(self, position):
        self.calls.append(("preset", position))


def _make_entry(*, controller, did="dev-ptz", positions=None, self_device=False):
    """Build a SimpleNamespace entry + runtime_data wiring one PTZ device.

    When `self_device` is True the device is wired as the hub's own camera
    (the self_device branch) rather than a per-subentry device.
    """
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-1", did=did)
    device = make_device([], subentry=sub)

    cloud = MagicMock()
    cloud.query_ptz_positions = AsyncMock(return_value=positions or [])

    runtime = SimpleNamespace(
        hub=hub,
        devices={},
        self_device=None,
        ptz_controllers={did: controller} if controller else {},
        cloud_client=cloud,
        cloud_token="TOK",
    )
    if self_device:
        runtime.self_device = device
    else:
        runtime.devices = {"sub-1": device}

    entry = SimpleNamespace(runtime_data=runtime, subentries={"sub-1": sub})
    return entry, device, cloud


async def _collect(setup, entry):
    added: list = []
    await setup(
        hass=MagicMock(), entry=entry,
        async_add_entities=lambda es, **_: added.extend(es),
    )
    return added


# ---------------------------------------------------------------------------
# Buttons (Task 7.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_button_g350_has_pan_tilt_stop_and_zoom():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(button_setup, entry)

    suffixes = {e._attr_translation_key for e in added}
    assert {"ptz_up", "ptz_down", "ptz_left", "ptz_right", "ptz_stop",
            "ptz_zoom_in", "ptz_zoom_out"} <= suffixes


@pytest.mark.asyncio
async def test_button_g350_self_device_branch():
    """The verified G350 is the hub's own camera (self_device branch)."""
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl, self_device=True)
    added = await _collect(button_setup, entry)

    suffixes = {e._attr_translation_key for e in added}
    assert {"ptz_left", "ptz_stop", "ptz_zoom_in"} <= suffixes


@pytest.mark.asyncio
async def test_button_g3_has_no_zoom_buttons():
    ctrl = _RecordingController(NO_ZOOM)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(button_setup, entry)

    suffixes = {e._attr_translation_key for e in added}
    assert {"ptz_up", "ptz_down", "ptz_left", "ptz_right", "ptz_stop"} <= suffixes
    assert "ptz_zoom_in" not in suffixes
    assert "ptz_zoom_out" not in suffixes


@pytest.mark.asyncio
async def test_button_non_ptz_camera_has_no_ptz_buttons():
    entry, _, _ = _make_entry(controller=None)
    added = await _collect(button_setup, entry)
    assert added == []


@pytest.mark.asyncio
async def test_button_press_delegates_to_controller():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(button_setup, entry)

    left = next(e for e in added if e._attr_translation_key == "ptz_left")
    assert isinstance(left, AqaraPtzDirectionButton)
    await left.async_press()
    assert ("move", "left") in ctrl.calls

    zoom_in = next(e for e in added if e._attr_translation_key == "ptz_zoom_in")
    assert isinstance(zoom_in, AqaraPtzZoomButton)
    await zoom_in.async_press()
    assert ("zoom_step", 1.0) in ctrl.calls

    zoom_out = next(e for e in added if e._attr_translation_key == "ptz_zoom_out")
    await zoom_out.async_press()
    assert ("zoom_step", -1.0) in ctrl.calls

    stop = next(e for e in added if e._attr_translation_key == "ptz_stop")
    assert isinstance(stop, AqaraPtzStopButton)
    await stop.async_press()
    assert ("stop",) in ctrl.calls


# ---------------------------------------------------------------------------
# Zoom number (Task 7.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_number_g350_has_zoom_number_with_range():
    ctrl = _RecordingController(FULL, current_zoom=2.0)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(number_setup, entry)

    zoom = next(e for e in added if isinstance(e, AqaraPtzZoomNumber))
    assert zoom._attr_native_min_value == 1.0
    assert zoom._attr_native_max_value == 9.0
    assert zoom._attr_native_step == 0.1
    assert zoom.native_value == 2.0


@pytest.mark.asyncio
async def test_number_set_value_calls_controller():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(number_setup, entry)

    zoom = next(e for e in added if isinstance(e, AqaraPtzZoomNumber))
    await zoom.async_set_native_value(3.0)
    assert ("zoom", 3.0) in ctrl.calls
    assert zoom.native_value == 3.0


@pytest.mark.asyncio
async def test_number_g3_has_no_zoom_number():
    ctrl = _RecordingController(NO_ZOOM)
    entry, _, _ = _make_entry(controller=ctrl)
    added = await _collect(number_setup, entry)
    assert not any(isinstance(e, AqaraPtzZoomNumber) for e in added)


@pytest.mark.asyncio
async def test_number_non_ptz_has_no_zoom_number():
    entry, _, _ = _make_entry(controller=None)
    added = await _collect(number_setup, entry)
    assert added == []


@pytest.mark.asyncio
async def test_number_self_device_branch_has_zoom():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl, self_device=True)
    added = await _collect(number_setup, entry)
    assert any(isinstance(e, AqaraPtzZoomNumber) for e in added)


# ---------------------------------------------------------------------------
# Preset select (Task 7.3)
# ---------------------------------------------------------------------------


_POSITIONS = [
    {"id": 1, "name": "Door", "position": "POS-A"},
    {"id": 2, "name": "Garden", "position": "POS-B"},
]


@pytest.mark.asyncio
async def test_select_g350_lists_preset_names():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl, positions=_POSITIONS)
    added = await _collect(select_setup, entry)

    sel = next(e for e in added if isinstance(e, AqaraPtzPresetSelect))
    await sel.async_added_to_hass()
    assert sel.options == ["Door", "Garden"]


@pytest.mark.asyncio
async def test_select_option_recalls_position():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl, positions=_POSITIONS)
    added = await _collect(select_setup, entry)

    sel = next(e for e in added if isinstance(e, AqaraPtzPresetSelect))
    await sel.async_added_to_hass()
    await sel.async_select_option("Garden")
    assert ("preset", "POS-B") in ctrl.calls
    assert sel.current_option == "Garden"


@pytest.mark.asyncio
async def test_select_self_heals_when_preset_added_later():
    """A preset added in the app after the first fetch becomes recallable."""
    ctrl = _RecordingController(FULL)
    entry, _, cloud = _make_entry(controller=ctrl, positions=[_POSITIONS[0]])
    added = await _collect(select_setup, entry)

    sel = next(e for e in added if isinstance(e, AqaraPtzPresetSelect))
    await sel.async_added_to_hass()
    assert sel.options == ["Door"]

    # The app adds a preset; the cloud list now returns both.
    cloud.query_ptz_positions = AsyncMock(return_value=_POSITIONS)
    await sel.async_select_option("Garden")  # not in the cached map -> refetch
    assert ("preset", "POS-B") in ctrl.calls


@pytest.mark.asyncio
async def test_select_g3_has_preset_no_zoom():
    ctrl = _RecordingController(NO_ZOOM)
    entry, _, _ = _make_entry(controller=ctrl, positions=_POSITIONS)
    added = await _collect(select_setup, entry)
    assert any(isinstance(e, AqaraPtzPresetSelect) for e in added)


@pytest.mark.asyncio
async def test_select_non_ptz_has_no_preset_select():
    entry, _, _ = _make_entry(controller=None)
    added = await _collect(select_setup, entry)
    assert added == []


@pytest.mark.asyncio
async def test_select_self_device_branch_has_preset():
    ctrl = _RecordingController(FULL)
    entry, _, _ = _make_entry(controller=ctrl, positions=_POSITIONS, self_device=True)
    added = await _collect(select_setup, entry)
    assert any(isinstance(e, AqaraPtzPresetSelect) for e in added)
