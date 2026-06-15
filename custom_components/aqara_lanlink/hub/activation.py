"""Relay activation for standalone Wi-Fi devices (proven on the FP2, device-agnostic).

A standalone Wi-Fi device is not relayed by the hub over LANLink until a
controller opens TLS to its local :443 service and sends a LANLink handshake
frame; the hub then adopts it into its relay cluster (topology push + reports).
The handshake ALWAYS fails (reset/timeout) - the act of sending it is the
trigger, the response is irrelevant. So activation is best-effort: connect,
send the handshake, ignore the outcome.
"""
from __future__ import annotations

import asyncio
import logging
import ssl

from .tunnel import EncryptedTunnel

_LOGGER = logging.getLogger(__name__)

ACTIVATION_PORT = 443
# Match the proven tool's budget. NB: the activation is not reliable on a single
# poke -- the hub adopts the device only after several attempts / once the device
# has settled after a power-cycle. So callers must RETRY until the device appears
# in topology rather than rely on one call here. The exact timeout value is not
# the deciding factor (8/12 failed, 10 worked, on the same device -- noise).
_ACTIVATION_TIMEOUT = 10.0


def activation_tls_context() -> ssl.SSLContext:
    """A context that accepts the device's self-signed *.aqaralife.kr cert."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def activate_relay(host: str, did: str, port: int = ACTIVATION_PORT) -> None:
    """Trigger the hub to relay `did` by sending a LANLink handshake to host:port over TLS.

    Best-effort and never raises: the handshake is expected to fail; the frame
    has already been sent by the time it does. Idempotent - safe to call when
    already relayed.
    """
    tunnel = EncryptedTunnel(device_id=did, keepalive_interval=0)
    try:
        await asyncio.wait_for(
            tunnel.connect(host, port, ssl=activation_tls_context()),
            _ACTIVATION_TIMEOUT,
        )
        _LOGGER.debug("activate_relay: handshake to %s:%d (did=%s) completed", host, port, did)
    except Exception as exc:  # noqa: BLE001 - failure is the normal path; the frame was sent
        _LOGGER.debug("activate_relay: handshake to %s:%d (did=%s) ended as expected: %r",
                      host, port, did, exc)
    finally:
        try:
            await tunnel.close()
        except Exception:  # noqa: BLE001
            pass


# NB: there is deliberately no ":443 reachability/cert probe" helper here. The
# firmware ignores an activation poke that arrives shortly after another :443
# connection, so probing the endpoint before poking it breaks activation (the
# hub never adopts the device). The poke is the only :443 connection the
# activation path makes, and it doubles as the reachability check -- a
# still-booting device simply fails the connect and re-arm retries. See
# docs/dev/FP2_ACTIVATION_FINDINGS.md.
