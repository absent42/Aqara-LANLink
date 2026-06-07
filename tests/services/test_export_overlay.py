"""Tests for aqara_lanlink.export_overlay."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest

from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.services.export_overlay import (
    _quote,
    async_handle_export,
    render_overlay,
)


def test_quote_escapes_newline_and_control_chars():
    # A string with a newline must render as a single-line literal with the
    # newline escaped, so untrusted content cannot break out of the literal
    # into the generated overrides.py.
    out = _quote('foo\nbar")\nimport os')
    assert "\n" not in out  # no raw newline survives
    assert eval(out) == 'foo\nbar")\nimport os'  # round-trips back to the value


def _populated_overlay() -> Overlay:
    o = Overlay()
    o.set_trait(
        model="lumi.test",
        pid="0.1.85",
        spec=TraitSpec(
            id="0.1.85",
            name="Temperature",
            wire_path="4.143.32952",
            data_type="number",
            unit="C",
            platform="sensor",
            device_class="temperature",
        ),
        discovered_from="cloud_scan",
        discovered_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )
    return o


def test_render_overlay_produces_traitspec_literal():
    """The renderer emits valid Python that constructs TraitSpec objects."""
    rendered = render_overlay(_populated_overlay(), model="lumi.test")
    assert "TraitSpec(" in rendered
    assert "id='0.1.85'" in rendered
    assert "name='Temperature'" in rendered
    assert "wire_path='4.143.32952'" in rendered
    assert "platform='sensor'" in rendered
    assert "OVERRIDES" in rendered


def test_render_overlay_returns_empty_message_for_unknown_model():
    """If the requested model has no overlay entries, the renderer
    emits an informational message (not an empty string)."""
    rendered = render_overlay(Overlay(), model="lumi.empty")
    assert "no overlay entries" in rendered.lower()


def test_render_overlay_renders_all_models_when_unspecified():
    """model=None emits a section per model in the overlay."""
    o = Overlay()
    for m in ("lumi.a", "lumi.b"):
        o.set_trait(
            model=m, pid="0.1.85",
            spec=TraitSpec(id="0.1.85", name=m, data_type="bool",
                           platform="binary_sensor"),
            discovered_from="cloud_scan",
            discovered_at=datetime.now(timezone.utc),
        )
    rendered = render_overlay(o, model=None)
    assert "lumi.a" in rendered and "lumi.b" in rendered


async def test_async_handle_export_creates_persistent_notification(hass):
    """The service handler resolves the entry, renders the overlay, and
    creates a Persistent Notification carrying the rendered text."""
    from homeassistant.components import persistent_notification as pn

    overlay = _populated_overlay()
    entry = MagicMock()
    entry.entry_id = "entry_test"
    entry.runtime_data = SimpleNamespace(overlay=overlay)
    hass.config_entries.async_entries = MagicMock(return_value=[entry])

    with patch.object(pn, "async_create") as pn_spy:
        call = SimpleNamespace(data={}, hass=hass)
        await async_handle_export(hass, call)
        pn_spy.assert_called_once()
        message = pn_spy.call_args.args[1]
        assert "TraitSpec(" in message
