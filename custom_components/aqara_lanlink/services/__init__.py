"""Service registration for the Aqara LANLink integration."""
from __future__ import annotations

from collections.abc import Callable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from ..const import DOMAIN
from .scan_device import async_handle_scan
from .export_overlay import async_handle_export

_SCAN_SCHEMA = vol.Schema({vol.Required("device_id"): str})
_EXPORT_SCHEMA = vol.Schema({vol.Optional("model"): str})


def register_services(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[], None]:
    """Register the scan_device and export_overlay services.

    Called once per setup; the service handlers look up the entry's
    runtime_data on each invocation via the call's device_id.

    Returns a callable that removes both services, suitable for passing
    directly to entry.async_on_unload.
    """
    if (hass.services.has_service(DOMAIN, "scan_device") or
            hass.services.has_service(DOMAIN, "export_overlay")):
        # Already registered for another entry; return a no-op so the
        # async_on_unload contract is always satisfied.
        return lambda: None

    async def _scan(call: ServiceCall) -> None:
        await async_handle_scan(hass, call)

    async def _export(call: ServiceCall) -> None:
        await async_handle_export(hass, call)

    hass.services.async_register(
        DOMAIN, "scan_device", _scan, schema=_SCAN_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, "export_overlay", _export, schema=_EXPORT_SCHEMA,
    )

    def _remove() -> None:
        hass.services.async_remove(DOMAIN, "scan_device")
        hass.services.async_remove(DOMAIN, "export_overlay")

    return _remove
