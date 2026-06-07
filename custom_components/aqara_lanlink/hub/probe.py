"""Credential-free LANLink tunnel-host capability probe.

`probe_tunnel_host` performs a TCP connect + ECDH handshake against a
candidate (host, port, did) and immediately closes. The ECDH handshake
needs no cloud credentials, so this runs at discovery time -- before the
user supplies anything -- to tell a real LANLink tunnel host (M3,
standalone camera) apart from a non-tunnel hub (M100) or a device that
does not speak LANLink at all.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum

from .tunnel import EncryptedTunnel, TunnelError

_LOGGER = logging.getLogger(__name__)


class ProbeResult(Enum):
    """Outcome of a tunnel-host probe."""

    OK = "ok"                    # connect + handshake succeeded -> tunnel host
    REFUSED = "refused"          # TCP refused -> reachable, not a tunnel host
    TIMEOUT = "timeout"          # no answer -> offline / unreachable
    NOT_LANLINK = "not_lanlink"  # connected but handshake failed


async def probe_tunnel_host(
    host: str, port: int, did: str, *, timeout: float = 3.0,
) -> ProbeResult:
    """Probe one candidate. Never raises; always returns a ProbeResult."""
    tunnel = EncryptedTunnel(did, keepalive_interval=0)
    try:
        await asyncio.wait_for(tunnel.connect(host, port), timeout)
        return ProbeResult.OK
    except ConnectionRefusedError:
        return ProbeResult.REFUSED
    except (asyncio.TimeoutError, OSError):
        return ProbeResult.TIMEOUT
    except TunnelError:
        return ProbeResult.NOT_LANLINK
    except Exception:  # noqa: BLE001 -- a probe must never crash discovery
        _LOGGER.debug("probe of %s:%d raised unexpectedly", host, port,
                      exc_info=True)
        return ProbeResult.NOT_LANLINK
    finally:
        await tunnel.close()
