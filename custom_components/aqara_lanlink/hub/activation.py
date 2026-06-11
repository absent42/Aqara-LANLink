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


async def validate_aqara_endpoint(host: str, port: int = ACTIVATION_PORT, timeout: float = 5.0) -> bool:
    """True iff host:port presents an Aqara TLS service (cert names contain aqaralife.kr)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=activation_tls_context(), server_hostname=host),
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("validate_aqara_endpoint: %s:%d connect failed: %r", host, port, exc)
        return False
    try:
        sslobj = writer.get_extra_info("ssl_object")
        der = sslobj.getpeercert(binary_form=True) if sslobj else None
        return bool(der) and _cert_is_aqara(der)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


def _cert_is_aqara(der: bytes) -> bool:
    from cryptography import x509
    try:
        cert = x509.load_der_x509_certificate(der)
    except Exception:  # noqa: BLE001
        return False
    text = cert.subject.rfc4514_string()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        text += " " + " ".join(san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    return "aqaralife.kr" in text
