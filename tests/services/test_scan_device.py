"""Tests for aqara_lanlink.scan_device."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.helpers import device_registry as dr

from custom_components.aqara_lanlink.const import DOMAIN
from custom_components.aqara_lanlink.device.overlay import Overlay


def _build_test_entry(hass, *, model: str, did: str = "lumi1.test"):
    """Build a hub entry + one device with runtime_data populated for
    the scan service. The device is registered in HA's device registry
    so async_handle_scan's _resolve_device finds it.
    """
    from tests.test_init import _hub_entry, _make_subentry
    entry = _hub_entry(hass, hub_did="hub_test")
    sub = _make_subentry(subentry_id="sub_test", did=did, model=model)
    entry.subentries = {"sub_test": sub}
    entry.runtime_data = SimpleNamespace(
        hub=MagicMock(),
        devices={},
        self_device=None,
        host_kind="standalone",
        overlay=Overlay(),
        overlay_store=MagicMock(async_write=AsyncMock()),
        cloud_client=AsyncMock(),
        cloud_token="t",
        cloud_region="r",
    )
    entry.add_to_hass(hass)
    # Register the device so dr.async_get(hass).async_get(device_id)
    # returns it from the service handler.
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, did)},
        manufacturer="Aqara",
        model=model,
    )
    entry._test_device_id = dev.id  # stash for _device_id_of below
    return entry


def _build_panel(*, paths):
    from custom_components.aqara_lanlink.hub.cloud_client import EndpointPanel
    return EndpointPanel(
        endpoint_id=4, endpoint_name="Test", endpoint_icon_id="",
        device_name="Test", device_types="", position_name="", model_type=2,
        obj_properties=("0.1.85",), paths=paths,
    )


def _make_service_call(hass, data: dict):
    """Wrap a dict into the shape async_handle_scan expects."""
    return SimpleNamespace(data=data, hass=hass)


def _device_id_of(entry):
    """Return the HA device_id `_build_test_entry` registered above."""
    return entry._test_device_id


async def test_scan_device_creates_repair_issue_when_gaps_exist(hass):
    """A device with cloud-visible traits not in catalogue/overlay
    produces a Repair issue carrying the gap report.
    """
    from custom_components.aqara_lanlink.services.scan_device import async_handle_scan

    entry = _build_test_entry(hass, model="lumi.test")
    entry.runtime_data.cloud_client.query_collection_panels = AsyncMock(
        return_value={4: _build_panel(paths=("4.143.32952",))},
    )
    entry.runtime_data.cloud_client.query_device_traits = AsyncMock(
        return_value=[
            {"path": "4.143.32952", "propertyId": ["0.1.85"], "value": "23.5", "unit": "C"},
        ],
    )

    from homeassistant.helpers import issue_registry as ir

    call = _make_service_call(hass, {"device_id": _device_id_of(entry)})
    await async_handle_scan(hass, call)

    issues = ir.async_get(hass).issues
    assert any(
        i.issue_id.startswith("scan_review_") for i in issues.values()
    )


async def test_scan_device_creates_no_issue_when_catalogue_already_covers_all(
    hass, monkeypatch,
):
    """If the cloud returns only traits already in catalogue/overlay,
    no Repair issue is created.

    V3 catalogue match is by `wire_path`, so we inject a synthetic
    catalogue TraitSpec carrying a wire_path and feed the cloud a probe
    that pushes the same wire_path. The shipped V1 catalogue has no
    wire_paths set; full V3 regen lands in Chunk 6.
    """
    from custom_components.aqara_lanlink.services.scan_device import async_handle_scan
    from homeassistant.helpers import issue_registry as ir
    from custom_components.aqara_lanlink.device import registry
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    model = "lumi.vibration.agl002"
    wp = "2.160.33000"
    # Inject a synthetic catalogued trait for this model so the V3
    # wire_path filter has something to match. _build_test_entry below
    # also triggers discovery; seeding after `_build_test_entry`
    # guarantees the index already contains the real packages.
    entry = _build_test_entry(hass, model=model)
    monkeypatch.setattr(registry, "_discovered", True)
    registry._put_trait(
        model,
        wp,
        TraitSpec(
            id=wp, name="Seeded occupancy", wire_path=wp,
            data_type="bool", endpoint_id=2,
        ),
    )

    entry.runtime_data.cloud_client.query_collection_panels = AsyncMock(
        return_value={4: _build_panel(paths=(wp,))},
    )
    entry.runtime_data.cloud_client.query_device_traits = AsyncMock(
        return_value=[
            {"path": wp, "propertyId": [wp], "value": "1"},
        ],
    )

    call = _make_service_call(hass, {"device_id": _device_id_of(entry)})
    await async_handle_scan(hass, call)

    issues = ir.async_get(hass).issues
    assert not any(
        i.issue_id.startswith("scan_review_") for i in issues.values()
    )


async def test_scan_device_handles_cloud_failure_with_repair_issue(hass):
    """When query_collection_panels raises, a Repair issue describing
    the failure is created (rather than a silent error).
    """
    from custom_components.aqara_lanlink.services.scan_device import async_handle_scan
    from homeassistant.helpers import issue_registry as ir

    entry = _build_test_entry(hass, model="lumi.test")
    entry.runtime_data.cloud_client.query_collection_panels = AsyncMock(
        side_effect=RuntimeError("cloud down"),
    )

    call = _make_service_call(hass, {"device_id": _device_id_of(entry)})
    await async_handle_scan(hass, call)

    issues = ir.async_get(hass).issues
    assert any(
        i.issue_id.startswith("scan_failure_") for i in issues.values()
    )
