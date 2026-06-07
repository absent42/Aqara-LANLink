"""Tests for the local overlay store.

The overlay is a per-install file that extends the shipped catalogue;
the derive treats it identically. Under V3 it uses override-on-top
semantics - overlay entries replace shipped values at the same
wire_path - and is written only by the scan-acceptance flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.overlay import (
    Overlay,
    OverlayStore,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _make_hass():
    hass = MagicMock()
    hass.data = {}
    return hass


async def test_empty_overlay_loads_cleanly(monkeypatch):
    """A missing storage file yields an empty Overlay, no error."""
    store = OverlayStore(_make_hass())
    monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value=None))
    overlay = await store.async_load()
    assert overlay.traits_for_model("any") == {}


async def test_overlay_loads_and_exposes_traits(monkeypatch):
    """A populated storage file yields traits accessible by model + pid."""
    raw = {
        "version": 1,
        "traits": {
            "lumi.sensor.test": {
                "0.1.85": {
                    "name": "Temperature",
                    "wire_path": "4.143.32952",
                    "data_type": "number",
                    "platform": "sensor",
                    "device_class": "temperature",
                    "unit": "C",
                    "discovered_from": "cloud_scan",
                    "discovered_at": "2026-05-23T10:24:51Z",
                },
            },
        },
    }
    store = OverlayStore(_make_hass())
    monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value=raw))
    overlay = await store.async_load()

    traits = overlay.traits_for_model("lumi.sensor.test")
    assert "0.1.85" in traits
    assert isinstance(traits["0.1.85"], TraitSpec)
    assert traits["0.1.85"].name == "Temperature"
    assert traits["0.1.85"].wire_path == "4.143.32952"


async def test_overlay_drops_malformed_rows_with_warning(monkeypatch, caplog):
    """A row that fails TraitSpec validation is dropped with a warning;
    other rows in the same model load normally.

    The malformed row here triggers TraitSpec.__post_init__ which raises
    RuntimeError (auto_clear_seconds requires platform='binary_sensor').
    """
    raw = {
        "version": 1,
        "traits": {
            "lumi.test": {
                "0.1.85": {
                    "name": "Good",
                    "data_type": "number",
                    "platform": "sensor",
                },
                "0.2.85": {
                    "name": "Bad",
                    "data_type": "bool",
                    "platform": "sensor",
                    # auto_clear_seconds requires binary_sensor: invalid
                    "auto_clear_seconds": 30.0,
                },
            },
        },
    }
    import logging
    caplog.set_level(logging.WARNING)
    store = OverlayStore(_make_hass())
    monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value=raw))
    overlay = await store.async_load()

    traits = overlay.traits_for_model("lumi.test")
    assert "0.1.85" in traits  # good row survives
    assert "0.2.85" not in traits  # bad row dropped
    assert any(
        "overlay" in record.message.lower() and "0.2.85" in record.message
        for record in caplog.records
    )


async def test_overlay_preserves_enum_values_as_dict(monkeypatch):
    """TraitSpec.enum_values is dict[str, str]; the overlay serialises
    and deserialises that shape correctly.
    """
    raw = {
        "version": 1,
        "traits": {
            "lumi.test": {
                "0.1.85": {
                    "name": "Mode",
                    "data_type": "enum",
                    "enum_values": {"0": "Off", "1": "On", "2": "Auto"},
                },
            },
        },
    }
    store = OverlayStore(_make_hass())
    monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value=raw))
    overlay = await store.async_load()
    spec = overlay.traits_for_model("lumi.test")["0.1.85"]
    assert spec.enum_values == {"0": "Off", "1": "On", "2": "Auto"}


async def test_overlay_async_write_atomically_replaces():
    """async_write persists the full overlay; subsequent async_load returns
    the same shape.
    """
    hass = _make_hass()
    store = OverlayStore(hass)

    spec = TraitSpec(
        id="0.1.85",
        name="Temperature",
        wire_path="4.143.32952",
        data_type="number",
        platform="sensor",
    )
    overlay = Overlay()
    overlay.set_trait(
        model="lumi.test",
        pid="0.1.85",
        spec=spec,
        discovered_from="cloud_scan",
        discovered_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )
    store._store.async_save = AsyncMock()
    await store.async_write(overlay)
    store._store.async_save.assert_awaited_once()
    saved = store._store.async_save.await_args.args[0]
    assert saved["version"] == 1
    assert "lumi.test" in saved["traits"]
    assert saved["traits"]["lumi.test"]["0.1.85"]["name"] == "Temperature"


async def test_overlay_missing_keys_treated_as_empty(monkeypatch):
    """A file with missing or non-dict 'traits' is treated as empty, not
    an error.
    """
    store = OverlayStore(_make_hass())
    for raw in [{}, {"version": 1}, {"version": 1, "traits": None}]:
        monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value=raw))
        overlay = await store.async_load()
        assert overlay.traits_for_model("any") == {}
