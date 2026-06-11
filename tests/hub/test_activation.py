import asyncio

import pytest

from custom_components.aqara_lanlink.hub import activation


@pytest.mark.asyncio
async def test_activate_relay_swallows_handshake_failure(monkeypatch):
    calls = {}

    class FakeTunnel:
        def __init__(self, device_id, keepalive_interval=0):
            calls["did"] = device_id

        async def connect(self, host, port, *, ssl=None):
            calls["host"], calls["port"], calls["ssl_set"] = host, port, ssl is not None
            raise asyncio.TimeoutError()  # handshake always fails - that's fine

        async def close(self):
            calls["closed"] = True

    monkeypatch.setattr(activation, "EncryptedTunnel", FakeTunnel)
    await activation.activate_relay("10.0.0.9", "lumi1.test")  # must NOT raise
    assert calls["host"] == "10.0.0.9" and calls["port"] == 443
    assert calls["ssl_set"] is True and calls["closed"] is True


def _make_der(cn: str) -> bytes:
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.mark.asyncio
async def test_validate_aqara_endpoint_returns_false_on_connect_failure(monkeypatch):
    async def boom(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr("asyncio.open_connection", boom)
    assert await activation.validate_aqara_endpoint("10.0.0.9") is False


def test_cert_is_aqara_accepts_aqaralife_and_rejects_others():
    assert activation._cert_is_aqara(_make_der("*.aqaralife.kr")) is True
    assert activation._cert_is_aqara(_make_der("example.com")) is False
    assert activation._cert_is_aqara(b"not-a-cert") is False
