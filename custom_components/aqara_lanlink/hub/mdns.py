"""mDNS-based discovery of Aqara LANLink services.

Aqara hubs advertise `_aqara-setup._tcp.local.` with TXT records that
include `id=<did>`. The advertised port rotates on hub reboot, so we
look up the current port on demand instead of trusting cached values.

Two entry points:

    await discover_hubs(hass, timeout=3.0) -> list[AqaraServiceRecord]
        Broad browse for anything advertising the service type.

    await discover_hub_by_did(hass, did, timeout=3.0) -> AqaraServiceRecord | None
        Shortcut when we already know the DID and just need the current port.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from typing import Any

try:
    from homeassistant.components import zeroconf as ha_zeroconf
except Exception:  # pragma: no cover -- standalone tooling doesn't have HA
    ha_zeroconf = None  # type: ignore[assignment]

from zeroconf import ServiceStateChange
from zeroconf.asyncio import (
    AsyncServiceBrowser,
    AsyncServiceInfo,
    AsyncZeroconf,
)

_LOGGER = logging.getLogger(__name__)

AQARA_SERVICE_TYPE = "_aqara-setup._tcp.local."
_RESOLVE_TIMEOUT_MS = 2000


@dataclass(frozen=True)
class AqaraServiceRecord:
    """One advertised Aqara LANLink service, post-resolution."""

    host: str
    port: int
    did: str
    name: str   # e.g. "Aqara-Hub-M3-3C21" (instance name, sans service type)


async def discover_hubs(
    hass: Any,
    *,
    timeout: float = 3.0,
) -> list[AqaraServiceRecord]:
    """Browse the network for Aqara LANLink services.

    Uses HA's shared zeroconf instance when available; otherwise creates
    and closes its own (the standalone-tool path). Browsers get `timeout`
    seconds before being cancelled -- hubs usually respond in well under
    one second but some networks are slow.
    """
    aiozc, owns_aiozc = await _get_zeroconf(hass)

    results: dict[str, AqaraServiceRecord] = {}
    tasks: list[asyncio.Task[None]] = []

    # The zeroconf ServiceStateChange signal fires handlers with keyword
    # arguments named `zeroconf`, `service_type`, `name`, `state_change`.
    # Using any other parameter name here raises TypeError on every
    # received multicast packet.
    def _on_change(zeroconf, service_type, name, state_change):
        if state_change != ServiceStateChange.Added:
            return
        tasks.append(asyncio.create_task(
            _resolve(zeroconf, service_type, name, results)
        ))

    browser = AsyncServiceBrowser(
        aiozc.zeroconf,
        AQARA_SERVICE_TYPE,
        handlers=[_on_change],
    )
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        if tasks:
            # Let any in-flight resolves finish (they have their own timeout).
            await asyncio.gather(*tasks, return_exceptions=True)
        if owns_aiozc:
            await aiozc.async_close()

    return list(results.values())


# The canonical `_aqara-setup._tcp` browse returns EVERY advertising Aqara
# device on the LAN -- not only hubs, but cameras and standalone sensors like
# the FP2. Call sites that look for activatable standalone devices use this
# honest alias; `discover_hubs` is kept for the hub-discovery call sites.
discover_lan_devices = discover_hubs


async def discover_hub_by_did(
    hass: Any,
    did: str,
    *,
    timeout: float = 3.0,
) -> AqaraServiceRecord | None:
    """Find the record for a specific DID, or None if not found."""
    for record in await discover_hubs(hass, timeout=timeout):
        if record.did == did:
            return record
    return None


# =============================================================================
# Internals
# =============================================================================


async def _get_zeroconf(hass: Any) -> tuple[AsyncZeroconf, bool]:
    """Return (AsyncZeroconf, owns_it). True `owns_it` means caller must close."""
    if hass is not None and ha_zeroconf is not None:
        try:
            # HA 2024+ uses async_get_async_instance
            return await ha_zeroconf.async_get_async_instance(hass), False
        except AttributeError:
            pass
    return AsyncZeroconf(), True


def _is_lan_address(host: str) -> bool:
    """True if `host` is a private / link-local / loopback / ULA IP address.

    LANLink is a LAN protocol, so a hub should only ever advertise a local
    address. Rejecting global/public addresses (and unparseable values) stops
    an mDNS responder from redirecting the integration's authenticated session
    to an attacker-chosen endpoint. The IPv6 scope id (``%eth0``) is stripped
    before parsing.
    """
    candidate = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate).is_private
    except ValueError:
        return False


async def _resolve(
    zc,
    service_type: str,
    name: str,
    results: dict[str, AqaraServiceRecord],
) -> None:
    """Resolve a discovered service to (host, port, did) and store in results."""
    info = AsyncServiceInfo(service_type, name)
    if not await info.async_request(zc, _RESOLVE_TIMEOUT_MS):
        return

    props = _decode_properties(info)
    did = props.get("id")
    if not did:
        return

    addresses = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
    if not addresses:
        return
    host = addresses[0]
    if not _is_lan_address(host):
        # LANLink hubs are always on the local network. A responder
        # advertising a global/public address for a hub DID is treated as a
        # spoofed redirect (it would point the authenticated session at an
        # attacker-chosen endpoint) and ignored.
        _LOGGER.debug(
            "Ignoring mDNS record for did=%s: non-LAN address %s", did, host,
        )
        return
    port = info.port or 0
    if not port:
        return

    instance = name.split(".", 1)[0] if "." in name else name
    # If this DID shows up on v4 and v6, keep whichever came first -- the
    # coordinator uses asyncio.open_connection which handles both.
    results.setdefault(did, AqaraServiceRecord(
        host=host, port=port, did=did, name=instance,
    ))


def _decode_properties(info: AsyncServiceInfo) -> dict[str, str]:
    """Return TXT record properties as a str->str dict."""
    # AsyncServiceInfo.properties is {bytes: bytes|None}
    raw = getattr(info, "properties", None) or {}
    decoded: dict[str, str] = {}
    for key, value in raw.items():
        k = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
        if value is None:
            decoded[k] = ""
        elif isinstance(value, bytes):
            decoded[k] = value.decode("utf-8", errors="replace")
        else:
            decoded[k] = str(value)
    return decoded
