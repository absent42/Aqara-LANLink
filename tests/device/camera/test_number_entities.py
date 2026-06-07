"""Tests for the G400 detection-clear-delay number entity."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aqara_lanlink.device.camera.number_entities import (
    DEFAULT_DETECTION_CLEAR_DELAY_S,
    DetectionClearDelayNumber,
)


def _hub():
    hub = MagicMock()
    hub.did = "hub-did"
    hub.connected = True
    return hub


def _subentry():
    return SimpleNamespace(
        subentry_id="sub-1", data={"did": "cam-did", "model": "lumi.camera.agl013"},
    )


def test_defaults():
    n = DetectionClearDelayNumber(_hub(), MagicMock(), _subentry())
    assert n.native_value == DEFAULT_DETECTION_CLEAR_DELAY_S
    assert n.native_min_value == 15.0
    assert n.native_max_value == 600.0
    assert n._attr_unique_id == "sub-1_detection_clear_delay"


async def test_set_native_value_stores_locally():
    n = DetectionClearDelayNumber(_hub(), MagicMock(), _subentry())
    n.hass = MagicMock()
    n.async_write_ha_state = MagicMock()
    await n.async_set_native_value(120.0)
    assert n.native_value == 120.0
    n.async_write_ha_state.assert_called_once()


async def test_async_added_to_hass_restores_previous_value():
    n = DetectionClearDelayNumber(_hub(), MagicMock(), _subentry())
    n.async_get_last_number_data = AsyncMock(
        return_value=SimpleNamespace(native_value=210.0),
    )
    with patch(
        "custom_components.aqara_lanlink.device.camera.number_entities"
        ".AqaraEntity.async_added_to_hass", new=AsyncMock(),
    ), patch(
        "custom_components.aqara_lanlink.device.camera.number_entities"
        ".RestoreNumber.async_added_to_hass", new=AsyncMock(),
    ):
        await n.async_added_to_hass()
    assert n.native_value == 210.0


async def test_async_added_to_hass_keeps_default_when_no_restore():
    n = DetectionClearDelayNumber(_hub(), MagicMock(), _subentry())
    n.async_get_last_number_data = AsyncMock(return_value=None)
    with patch(
        "custom_components.aqara_lanlink.device.camera.number_entities"
        ".AqaraEntity.async_added_to_hass", new=AsyncMock(),
    ), patch(
        "custom_components.aqara_lanlink.device.camera.number_entities"
        ".RestoreNumber.async_added_to_hass", new=AsyncMock(),
    ):
        await n.async_added_to_hass()
    assert n.native_value == DEFAULT_DETECTION_CLEAR_DELAY_S
