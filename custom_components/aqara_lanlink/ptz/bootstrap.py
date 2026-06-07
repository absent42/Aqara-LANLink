"""Async cloud-assisted auth bootstrap for the CS2/PPPP camera P2P plane.

Asyncio port of tools/ptz_poc/auth.py. Bridges the integration's async cloud
client to the on-the-wire P2P auth handshake:

  1. ``query_camera_p2p_info`` -> cipher key (initStringApp suffix) + p2pId.
  2. Local Curve25519 keypair (PyNaCl).
  3. ``request_camera_p2p_sign`` -> per-session sign + time (+ dev pubkey).
  4. AUTH IOCTL 4096 body built from the app pubkey + time + sign.

The captured ``time``/``sign`` appear reusable across sessions, so :class:`Creds`
keeps them as plain fields a caller may supply from a cache; the default
:func:`bootstrap` coroutine fetches them live.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ioctl


def encode_did(p2p_id: str) -> bytes:
    """Encode a PPPP p2pId string into the 20-byte wire DID blob.

    Verified byte-exact against captures. Split on ``-`` into
    (prefix, serial, suffix); blob is::

        prefix.encode().ljust(8, b"\\0")
        + int(serial).to_bytes(4, "big")
        + suffix.encode().ljust(8, b"\\0")

    Example: ``"AQARADE-359455-ZUXDL"`` ->
    ``415141524144450000057c1f5a5558444c000000`` (20 bytes;
    ``00057c1f`` == 359455).
    """
    prefix, serial, suffix = p2p_id.split("-")
    return (
        prefix.encode().ljust(8, b"\0")
        + int(serial).to_bytes(4, "big")
        + suffix.encode().ljust(8, b"\0")
    )


@dataclass
class Creds:
    """Everything the PPPP session needs once the cloud handshake is done."""

    cipher_key: str
    p2p_id: str
    did_blob: bytes
    app_priv: Any
    app_pub_hex: str
    sign: str
    time: str
    dev_pub_hex: str


async def bootstrap(client, token: str, user_id: str, did: str) -> Creds:
    """Run the cloud-assisted auth handshake and return :class:`Creds`.

    Awaits the integration's async cloud client (``query_camera_p2p_info`` /
    ``request_camera_p2p_sign``). Tests pass a mock client whose methods are
    coroutines returning canned dicts.
    """
    info = await client.query_camera_p2p_info(token, did)
    cipher_key = info["initStringApp"].split(":")[-1]
    p2p_id = info["p2pId"]

    # Curve25519 keypair (PyNaCl).
    from nacl.public import PrivateKey

    priv = PrivateKey.generate()
    app_pub_hex = bytes(priv.public_key).hex()

    sgn = await client.request_camera_p2p_sign(token, did, app_pub_hex)

    return Creds(
        cipher_key=cipher_key,
        p2p_id=p2p_id,
        did_blob=encode_did(p2p_id),
        app_priv=priv,
        app_pub_hex=app_pub_hex,
        sign=sgn["sign"],
        time=sgn["time"],
        # The device pubkey is unused on the LAN AUTH path, so tolerate its
        # absence (intentional divergence from the PoC's verbatim sgn[...]).
        dev_pub_hex=sgn.get("p2pDevPublicKey", ""),
    )


def build_auth_ioctl(creds: Creds, did: str, id: int) -> bytes:
    """Build the AUTH IOCTL 4096 request body.

    Captured working body (AUTH succeeds on LAN with no session login) was
    ``{"app_public_key":"<hex>","app_sign":"<hex>","device_id":"<lumi did>","timestamp":"<ts>"}``;
    the camera replies IOCTL 4097 ``{"code":0,"message":"success"}``.
    """
    return ioctl.build(
        4096,
        {
            "app_public_key": creds.app_pub_hex,
            "app_sign": creds.sign,
            "device_id": did,
            "timestamp": creds.time,
        },
        id,
    )
