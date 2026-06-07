"""HA diagnostics export for the Aqara LANLink integration.

Each dump contains three sections:

- `overlay`: the per-model overlay contents (the user's accepted
  discoveries), as a JSON-friendly nested dict. Safe to attach to
  public issues - the overlay contains only trait metadata (path,
  pid, type, name) and provenance timestamps, no PII.
- `model_summaries`: per-model trait counts (catalogue + overlay +
  total descriptor count from the deterministic derive).
- `candidate_paths`: per-model wire paths the integration has
  observed via LANLink push that are not yet in catalogue or
  overlay; these are the discoveries waiting to be scanned.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    CONF_AQARA_ACCOUNT,
    CONF_AQARA_PASSWORD,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_PHONE_ID,
    DOMAIN,
)
from .device import catalog
from .device.build_descriptors import build_descriptors
from .device.overlay import Overlay

# Keys whose values must never appear in a downloadable diagnostics file
# (users routinely attach these to public issues). Today's output does not
# embed entry.data, but this scaffold guards against any future field that
# does, and redacts the device-identifier list and cloud account fields.
TO_REDACT = frozenset(
    {
        CONF_AQARA_TOKEN,
        CONF_AQARA_USER_ID,
        CONF_AQARA_ACCOUNT,
        CONF_AQARA_PASSWORD,
        CONF_PHONE_ID,
        "rtsp_username",
        "rtsp_password",
        "token",
        "userId",
        "topology_dids",
    }
)


def _model_from_device_entry(device_entry: DeviceEntry) -> str | None:
    for ident in device_entry.identifiers:
        if ident[0] == DOMAIN:
            return ident[1]
    return None


def _serialise_overlay(overlay: Overlay) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    provenance = overlay.all_provenance()
    for model in sorted(overlay._traits):
        out[model] = {}
        for pid, spec in sorted(overlay._traits[model].items()):
            row: dict[str, Any] = {
                "name": spec.name,
                "wire_path": spec.wire_path,
                "data_type": spec.data_type,
                "unit": spec.unit,
                "platform": spec.platform,
                "device_class": spec.device_class,
                "enum_values": dict(spec.enum_values) if spec.enum_values else None,
                "readable": spec.readable,
                "writable": spec.writable,
            }
            prov = provenance.get(model, {}).get(pid)
            if prov is not None:
                row["discovered_from"] = prov.discovered_from
                row["discovered_at"] = prov.discovered_at.isoformat()
            out[model][pid] = row
    return out


def _model_summaries(
    overlay: Overlay, models: list[str],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for model in sorted(set(models)):
        catalogue_count = len(catalog.all_traits_for_model(model))
        overlay_count = len(overlay.traits_for_model(model))
        derived = build_descriptors(model, overlay)
        out[model] = {
            "catalogue_traits": catalogue_count,
            "overlay_traits": overlay_count,
            "derived_descriptors": len(derived),
        }
    return out


def _candidate_paths(coordinator: Any) -> dict[str, list[str]]:
    cache = getattr(coordinator, "observed_path_cache", None)
    if cache is None:
        return {}
    out: dict[str, list[str]] = {}
    for model in sorted(cache.models()):
        paths = sorted(cache.get_paths(model))
        if paths:
            out[model] = paths
    return out


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> dict[str, Any]:
    """Dump scoped to one device.

    Includes the overlay slice for the device's model, the per-model
    summary for that model, and any candidate paths recorded against
    that model.
    """
    runtime = entry.runtime_data
    overlay: Overlay = runtime.overlay
    if _model_from_device_entry(device_entry) is None:
        return {"overlay": {}, "model_summaries": {}, "candidate_paths": {}}
    model = device_entry.model or ""
    overlay_slice = (
        {model: _serialise_overlay(overlay).get(model, {})}
        if model else {}
    )
    summaries = _model_summaries(overlay, [model]) if model else {}
    coord = runtime.hub if runtime is not None else None
    candidates = _candidate_paths(coord)
    candidates_for_model = (
        {model: candidates[model]} if model in candidates else {}
    )
    return async_redact_data(
        {
            "overlay": overlay_slice,
            "model_summaries": summaries,
            "candidate_paths": candidates_for_model,
        },
        TO_REDACT,
    )


def _tunnel_health(runtime: Any) -> dict[str, Any]:
    """Runtime tunnel/push-liveness health. Defensive (getattr) so partial or
    mocked runtime objects never break the diagnostics download."""
    coord = getattr(runtime, "hub", None)
    seconds_since = None
    ssr = getattr(coord, "seconds_since_last_report", None)
    if callable(ssr):
        try:
            seconds_since = round(ssr(), 1)
        except Exception:  # noqa: BLE001
            seconds_since = None
    topo = getattr(coord, "lanlink_topology_dids", None) or frozenset()
    return {
        "connected": getattr(coord, "connected", None),
        "host_kind": getattr(runtime, "host_kind", None),
        "topology_did_count": len(topo),
        "topology_dids": sorted(topo),
        "seconds_since_last_report": seconds_since,
        "rearm_in_flight": getattr(runtime, "rearm_in_flight", None),
        "watchdog_last_rearm": getattr(runtime, "watchdog_last_rearm", None),
        "subscription_target_count": len(
            getattr(runtime, "subscription_targets", None) or [],
        ),
        "region": getattr(runtime, "cloud_region", None),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Whole-entry diagnostics: tunnel health, overlay, summaries, candidates."""
    runtime = entry.runtime_data
    overlay: Overlay = runtime.overlay
    coord = runtime.hub if runtime is not None else None
    models = sorted({
        *overlay._traits.keys(),
        *_candidate_paths(coord).keys(),
    })
    return async_redact_data(
        {
            "tunnel": _tunnel_health(runtime),
            "overlay": _serialise_overlay(overlay),
            "model_summaries": _model_summaries(overlay, models),
            "candidate_paths": _candidate_paths(coord),
        },
        TO_REDACT,
    )
