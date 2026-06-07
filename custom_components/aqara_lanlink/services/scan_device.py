"""The `aqara_lanlink.scan_device` service.

User-triggered cloud scan for one device. Performs one cloud
round-trip, builds the gap report, and surfaces the result as a Repair
issue. Does not modify entities or the overlay; the user accepts via the
Repair flow.

Post-V3 role: edge-case tool. The shipped V3 catalogue covers ~90% of
known models; this service exists for the remaining ~37 uncatalogued
models and for firmware-revision drift on catalogued models. Not for
routine use. See docs/dev/audits/2026-05-24-cloud-surface.md.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from ..const import DOMAIN
from ..device import catalog
from ..gap_report import build_gap_report_from_cloud
from ..hub.cloud_client import AqaraCloudAuthError

_LOGGER = logging.getLogger(__name__)


async def _cloud_call(hass, entry, did, coro, *, op: str):
    """Await a scan cloud call, handling the two failure modes uniformly.

    Returns ``(result, ok)``. On an auth failure it starts HA's re-auth flow
    (the Repair card is the actionable signal); on any other failure it creates
    a scan-failure Repair issue. ``ok=False`` means the caller should return
    without proceeding.
    """
    try:
        result = await coro
    except AqaraCloudAuthError as exc:
        _LOGGER.warning(
            "scan_device: cloud auth failed for did=%s (%s); triggering re-auth",
            did, exc,
        )
        entry.async_start_reauth(hass)
        return None, False
    except Exception as exc:  # noqa: BLE001 - cloud is external
        _create_failure_issue(hass, entry.entry_id, did, f"{op} failed: {exc}")
        return None, False
    return result, True


async def async_handle_scan(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle one invocation of aqara_lanlink.scan_device."""
    device_id = call.data["device_id"]
    resolution = _resolve_device(hass, device_id)
    if resolution is None:
        _LOGGER.warning(
            "scan_device: device_id=%s does not belong to %s",
            device_id, DOMAIN,
        )
        return
    entry, did, model = resolution
    runtime = entry.runtime_data
    cloud = runtime.cloud_client
    if cloud is None:
        _create_failure_issue(
            hass, entry.entry_id, did, "Cloud client not available",
        )
        return

    panels, ok = await _cloud_call(
        hass, entry, did,
        cloud.query_collection_panels(runtime.cloud_token, did),
        op="query_collection_panels",
    )
    if not ok:
        return

    paths = tuple({p for panel in panels.values() for p in panel.paths})
    if not paths:
        # Panels returned no probe paths - nothing to scan. Clear any
        # prior issue and exit cleanly rather than letting
        # query_device_traits raise ValueError on empty paths.
        ir.async_delete_issue(
            hass, DOMAIN, f"scan_review_{entry.entry_id}_{did}",
        )
        _LOGGER.info(
            "scan_device: %s (model=%s) has no probe paths from panels",
            did, model,
        )
        return
    traits_response, ok = await _cloud_call(
        hass, entry, did,
        cloud.query_device_traits(runtime.cloud_token, did, paths=list(paths)),
        op="query_device_traits",
    )
    if not ok:
        return

    catalogue = catalog.all_traits_for_model(model)
    report = build_gap_report_from_cloud(
        panels=panels,
        traits_response=traits_response,
        catalogue=catalogue,
        overlay=runtime.overlay,
        model=model,
    )

    issue_id = f"scan_review_{entry.entry_id}_{did}"
    if not report.entries:
        # Delete any prior scan-review issue for this device - the
        # catalogue/overlay already cover the cloud's view.
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        _LOGGER.info(
            "scan_device: %s (model=%s) has no gaps", did, model,
        )
        return

    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="scan_review",
        translation_placeholders={
            "model": model,
            "did": did,
            "count": str(len(report.entries)),
        },
        data={
            "entry_id": entry.entry_id,
            "did": did,
            "model": model,
            "report": _serialise_report(report),
        },
    )
    _LOGGER.info(
        "scan_device: %s (model=%s) has %d gaps; review-and-select issue created",
        did, model, len(report.entries),
    )


def _resolve_device(hass, device_id: str):
    """Resolve HA device_id -> (entry, did, model). Returns None on miss."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return None
    for domain, ident in device.identifiers:
        if domain != DOMAIN:
            continue
        did = ident
        # First-wins: in practice each device has a unique did so only one
        # entry will match; returning early here is intentional.
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                continue
            model = device.model or ""
            return (entry, did, model)
    return None


def _serialise_report(report) -> list[dict]:
    """Encode the gap report as a JSON-friendly list for the issue data dict."""
    return [
        {
            "pid": e.pid,
            "wire_path": e.wire_path,
            "cloud_value": e.cloud_value,
            "cloud_unit": e.cloud_unit,
            "proposed_spec": _spec_to_dict(e.proposed_spec),
        }
        for e in report.entries
    ]


def _spec_to_dict(spec) -> dict:
    return {
        "id": spec.id,
        "name": spec.name,
        "wire_path": spec.wire_path,
        "data_type": spec.data_type,
        "unit": spec.unit,
        "platform": spec.platform,
        "device_class": spec.device_class,
        "enum_values": dict(spec.enum_values) if spec.enum_values else None,
        "readable": spec.readable,
        "writable": spec.writable,
        "trait_id": spec.trait_id,
    }


def _create_failure_issue(hass, entry_id: str, did: str, message: str) -> None:
    ir.async_create_issue(
        hass, DOMAIN, f"scan_failure_{entry_id}_{did}",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="scan_failure",
        translation_placeholders={"did": did, "message": message},
    )
