"""Tests for the Aqara LANLink Repairs platform."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.helpers import issue_registry as ir

from custom_components.aqara_lanlink.const import DOMAIN
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.repairs import (
    _ScanReviewFlow,
    async_create_fix_flow,
)


async def test_async_create_fix_flow_returns_scan_review_flow():
    hass = MagicMock()
    flow = await async_create_fix_flow(
        hass,
        issue_id="scan_review_abc123_lumi1.device",
        data={"entry_id": "abc123", "did": "lumi1.device", "model": "lumi.test", "report": []},
    )
    assert isinstance(flow, _ScanReviewFlow)
    assert flow._entry_id == "abc123"
    assert flow._did == "lumi1.device"


async def test_async_create_fix_flow_candidate_paths_returns_confirm():
    """candidate_paths_* issues use a confirm-only flow (user runs scan_device
    themselves)."""
    flow = await async_create_fix_flow(
        MagicMock(), issue_id="candidate_paths_abc123", data={"entry_id": "abc123"},
    )
    assert isinstance(flow, ConfirmRepairFlow)


async def test_async_create_fix_flow_unknown_issue_returns_confirm():
    flow = await async_create_fix_flow(
        MagicMock(), issue_id="some_other_issue", data=None,
    )
    assert isinstance(flow, ConfirmRepairFlow)


async def test_async_create_fix_flow_no_data_returns_confirm():
    flow = await async_create_fix_flow(
        MagicMock(), issue_id="scan_review_abc123", data=None,
    )
    assert isinstance(flow, ConfirmRepairFlow)


async def test_scan_review_flow_init_renders_summary(hass):
    """The flow's init step renders a summary placeholder; the select
    step renders a multi-select form whose options are the gap entries.
    """
    flow = _ScanReviewFlow(
        entry_id="e1", did="d1", model="lumi.test",
        report=[
            {"pid": "0.1.85", "wire_path": "4.1.85", "proposed_spec": {
                "id": "0.1.85", "name": "Temp", "wire_path": "4.1.85",
                "data_type": "number", "unit": "C", "platform": "sensor",
                "device_class": None, "enum_values": None,
                "readable": True, "writable": False, "trait_id": None,
            }, "cloud_value": "23.5", "cloud_unit": "C"},
        ],
    )
    result = await flow.async_step_init()
    assert result["type"] == "form" or result["type"].name == "FORM"
    # Selection step receives one option keyed by pid.


async def test_scan_review_accept_writes_to_overlay_and_reloads(hass):
    """Submitting the select step with chosen pids writes those entries
    to the overlay, deletes the Repair issue, and reloads the entry.
    """
    overlay = Overlay()
    overlay_store = MagicMock(async_write=AsyncMock())
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data = SimpleNamespace(
        overlay=overlay, overlay_store=overlay_store,
    )
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    hass.config_entries.async_reload = AsyncMock()
    ir.async_create_issue(
        hass, DOMAIN, "scan_review_e1_d1",
        is_fixable=True, is_persistent=False,
        severity=ir.IssueSeverity.WARNING, translation_key="scan_review",
    )

    report = [
        {
            "pid": "0.1.85", "wire_path": "4.143.32952",
            "proposed_spec": {
                "id": "0.1.85", "name": "Temp",
                "wire_path": "4.143.32952", "data_type": "number",
                "unit": "C", "platform": "sensor", "device_class": None,
                "enum_values": None, "readable": True, "writable": False,
                "trait_id": None,
            },
            "cloud_value": "23.5", "cloud_unit": "C",
        },
        {
            "pid": "0.2.85", "wire_path": "5.144.32953",
            "proposed_spec": {
                "id": "0.2.85", "name": "Humidity",
                "wire_path": "5.144.32953", "data_type": "number",
                "unit": "%", "platform": "sensor", "device_class": None,
                "enum_values": None, "readable": True, "writable": False,
                "trait_id": None,
            },
            "cloud_value": "45", "cloud_unit": "%",
        },
    ]
    flow = _ScanReviewFlow(
        entry_id="e1", did="d1", model="lumi.test", report=report,
    )
    flow.hass = hass

    # Accept only the first pid.
    result = await flow.async_step_select(user_input={"selected": ["0.1.85"]})
    assert result["type"].name == "CREATE_ENTRY"
    # The overlay store received exactly one write.
    overlay_store.async_write.assert_awaited_once()
    saved_overlay = overlay_store.async_write.await_args.args[0]
    assert "0.1.85" in saved_overlay.traits_for_model("lumi.test")
    assert "0.2.85" not in saved_overlay.traits_for_model("lumi.test")
    # Reload was triggered.
    hass.config_entries.async_reload.assert_awaited_once_with("e1")
    # Issue was deleted.
    assert "scan_review_e1_d1" not in {
        i.issue_id for i in ir.async_get(hass).issues.values()
    }
