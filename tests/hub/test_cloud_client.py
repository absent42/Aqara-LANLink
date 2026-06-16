"""Tests for the Aqara Legacy RPC cloud client."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from custom_components.aqara_lanlink.hub.cloud_client import (
    AREAS,
    AqaraAuthError,
    AqaraCloudAuth,
    AqaraCloudAuthError,
    AqaraCloudClient,
    AqaraTokens,
    _AUTH_FAILURE_CODES,
    _parse_effects,
    _UNIVERSAL_PATHS,
)


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture from tests/fixtures/."""
    return json.loads((_FIXTURES / name).read_text())


# =============================================================================
# Regions
# =============================================================================

class TestRegions:
    def test_eu_area_config(self):
        eu = AREAS["EU"]
        assert eu.server == "https://rpc-ger.aqara.com"
        assert eu.appid == "7be1984f0556276133336839"
        assert eu.appkey == "Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3"

    def test_unknown_region_rejected(self):
        with pytest.raises(ValueError, match="unknown Aqara region"):
            AqaraCloudAuth(region="ZZ")

    def test_all_advertised_regions_resolve(self):
        for region in ("CN", "EU", "US", "HMT", "OTHER", "AF", "RU", "AU", "ME", "KR", "JP"):
            AqaraCloudAuth(region=region)  # must not raise


# =============================================================================
# Password encryption: base64(RSA-PKCS1v15(md5(pw).hexdigest()))
# =============================================================================

class TestPasswordEncryption:
    def test_encrypts_md5_hex_of_password(self):
        """Decrypting the ciphertext must yield the MD5 hex of the original."""
        # Load the embedded public key and use it to RE-ENCRYPT md5(pw).hex();
        # different ciphertexts per call due to PKCS1v15 random padding, so we
        # can only verify the ciphertext DECRYPTS to the expected plaintext
        # using a controlled test key.
        from cryptography.hazmat.primitives.asymmetric import rsa
        priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        import custom_components.aqara_lanlink.hub.cloud_client as ca
        orig_pem = ca._AQARA_PUBLIC_KEY_PEM
        ca._AQARA_PUBLIC_KEY_PEM = pub_pem
        try:
            password = "correcthorsebatterystaple"
            encrypted_b64 = AqaraCloudAuth._encrypt_password(password)
            raw = base64.b64decode(encrypted_b64)
            plaintext = priv.decrypt(raw, padding.PKCS1v15())
            expected = hashlib.md5(password.encode()).hexdigest().encode()
            assert plaintext == expected
        finally:
            ca._AQARA_PUBLIC_KEY_PEM = orig_pem

    def test_output_is_base64(self):
        out = AqaraCloudAuth._encrypt_password("x")
        # Must round-trip through base64 decode without error.
        base64.b64decode(out)


# =============================================================================
# Signing
# =============================================================================

class TestSign:
    def test_sign_without_token(self):
        auth = AqaraCloudAuth(region="EU")
        nonce, ts, body = "NONCE", "1700000000000", '{"account":"x"}'
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={nonce}&Time={ts}"
            f"&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert auth._sign(nonce, ts, body) == expected

    def test_sign_with_token_inserts_token_field(self):
        auth = AqaraCloudAuth(region="EU")
        nonce, ts, body = "NONCE", "1700000000000", '{}'
        token = "TOK-123"
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={nonce}&Time={ts}"
            f"&Token={token}&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert auth._sign(nonce, ts, body, token=token) == expected

    def test_sign_not_lowercased(self):
        """Unlike the v3 Open API, legacy RPC preserves header case in sign."""
        auth = AqaraCloudAuth(region="EU")
        # If we lowercased the source, md5 of the upper and lower forms would
        # match; they should NOT match here.
        upper = auth._sign("ABC", "1", "X")
        lower = auth._sign("abc", "1", "x")
        assert upper != lower


# =============================================================================
# Headers
# =============================================================================

class TestHeaders:
    def test_headers_present(self):
        auth = AqaraCloudAuth(region="EU")
        h = auth._build_headers('{"a":1}')
        for k in ("Area", "Appid", "Nonce", "Time", "Sign", "Content-Type",
                  "User-Agent", "Lang", "Sys-Type"):
            assert k in h
        assert h["Area"] == "EU"
        assert h["Appid"] == AREAS["EU"].appid
        # Appkey and RequestBody must NEVER appear on the wire.
        assert "Appkey" not in h
        assert "RequestBody" not in h

    def test_token_included_when_present(self):
        auth = AqaraCloudAuth(region="EU")
        h = auth._build_headers("{}", token="t123")
        assert h["Token"] == "t123"

    def test_nonce_looks_like_md5_hex(self):
        auth = AqaraCloudAuth(region="EU")
        h = auth._build_headers("{}")
        assert len(h["Nonce"]) == 32
        int(h["Nonce"], 16)  # pure hex

    def test_phone_id_stable_across_requests(self):
        # Aqara namespaces subscription state by (user, PhoneId); a PhoneId
        # that changes per request orphans every subscription on the hub.
        auth = AqaraCloudAuth(region="EU")
        first = auth._build_headers("{}")["PhoneId"]
        second = auth._build_headers('{"a":1}', token="t")["PhoneId"]
        assert first == second

    def test_phone_id_uses_provided_value(self):
        auth = AqaraCloudAuth(region="EU", phone_id="STABLE-PHONE-ID")
        assert auth._build_headers("{}")["PhoneId"] == "STABLE-PHONE-ID"


# =============================================================================
# Login
# =============================================================================

def _fake_response(text):
    resp = MagicMock()
    resp.text = AsyncMock(return_value=text)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestLogin:
    async def test_login_success_returns_tokens(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0,
            "result": {"userId": "u-1", "token": "t-1", "refreshToken": "r-1"},
        })))
        auth = AqaraCloudAuth(region="EU", session=session)
        tokens = await auth.login("user@example.com", "pw")
        assert isinstance(tokens, AqaraTokens)
        assert tokens.user_id == "u-1"
        assert tokens.token == "t-1"
        assert tokens.raw_result["refreshToken"] == "r-1"

    async def test_login_posts_to_legacy_rpc_endpoint(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": {"userId": "u", "token": "t"}})))
        auth = AqaraCloudAuth(region="EU", session=session)
        await auth.login("user@example.com", "pw")

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "POST"
        assert url == "https://rpc-ger.aqara.com/app/v1.0/lumi/user/login"

        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict["account"] == "user@example.com"
        assert body_dict["encryptType"] == 2
        # Password is an RSA-encrypted base64 blob; can't decrypt, but shape:
        base64.b64decode(body_dict["password"])

    async def test_login_signs_exact_body_string(self):
        """Server validates sign against the body bytes sent on the wire."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": {"userId": "u", "token": "t"}})))
        auth = AqaraCloudAuth(region="EU", session=session)
        await auth.login("user@example.com", "pw")

        body = session.request.call_args.kwargs["data"]
        headers = session.request.call_args.kwargs["headers"]
        # Recompute the sign against the body and compare.
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected

    async def test_login_body_uses_default_json_spacing(self):
        """Body is NOT compact -- default json.dumps with spaces."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": {"userId": "u", "token": "t"}})))
        auth = AqaraCloudAuth(region="EU", session=session)
        await auth.login("user@example.com", "pw")

        body = session.request.call_args.kwargs["data"]
        # default json.dumps uses ', ' and ': ' separators -- verify the wire
        # bytes carry those spaces.
        assert ", " in body or len(json.loads(body)) <= 1
        assert '": ' in body

    async def test_login_rejected_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 108, "message": "account or password error",
        })))
        auth = AqaraCloudAuth(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="108"):
            await auth.login("u", "bad")

    async def test_login_missing_fields_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": {"userId": "u"},  # no token
        })))
        auth = AqaraCloudAuth(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="no userId/token"):
            await auth.login("u", "pw")

    async def test_non_json_response_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response("<html>500 error</html>"))
        auth = AqaraCloudAuth(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="non-JSON"):
            await auth.login("u", "pw")


# =============================================================================
# query_device_list - GET /app/v1.0/lumi/app/position/device/query
# =============================================================================


class TestQueryDeviceList:
    async def test_returns_devices_list(self):
        fixture = _load_fixture("cloud_device_list.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        devices = await client.query_device_list(token="tok-1")
        assert devices == fixture["result"]["devices"]
        # spot-check that expected sample DIDs are present
        dids = {d["did"] for d in devices}
        assert "lumi1.TESTHUB00001" in dids
        assert "lumi.TESTZIG0000000C" in dids

    async def test_sends_get_to_correct_url(self):
        fixture = _load_fixture("cloud_device_list.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_list(token="tok-1")

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "GET"
        assert url == (
            "https://rpc-ger.aqara.com/app/v1.0/lumi/app/position/device/query"
            "?size=300&startIndex=0"
        )

    async def test_sign_source_is_alpha_sorted_query_string(self):
        """Sign source must be the alpha-sorted query string (size first, then startIndex)."""
        fixture = _load_fixture("cloud_device_list.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_list(token="tok-1")

        headers = session.request.call_args.kwargs["headers"]
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            f"&size=300&startIndex=0&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        assert headers["Token"] == "tok-1"

    async def test_server_error_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 106, "message": "Invalid sign",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="Invalid sign"):
            await client.query_device_list(token="tok-1")

    async def test_http_error_raises(self):
        session = MagicMock()

        def _raise(*_a, **_kw):
            raise aiohttp.ClientError("connection refused")

        session.request = MagicMock(side_effect=_raise)
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="HTTP error"):
            await client.query_device_list(token="tok-1")

    async def test_missing_devices_key_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": {"positionIds": []},  # no 'devices'
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="missing 'devices'"):
            await client.query_device_list(token="tok-1")


# =============================================================================
# query_device_traits - POST /app/v1.0/lumi/app/qlink/trait/read
# =============================================================================


class TestQueryDeviceTraits:
    async def test_returns_traits_list(self):
        fixture = _load_fixture("cloud_qlink_trait_read.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        traits = await client.query_device_traits(
            token="tok-1", did="lumi.TESTZIG0000000C",
            paths=["2.132.32920", "2.133.32923"],
        )
        assert traits == fixture["result"][0]["traits"]
        # representative shape: every trait has a 'path'
        assert all("path" in t for t in traits)

    async def test_does_not_log_request_body_at_info(self, caplog):
        """The request body (device ids, paths) must not be emitted at INFO;
        verbose body logging belongs at DEBUG so it stays out of shared logs."""
        fixture = _load_fixture("cloud_qlink_trait_read.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.aqara_lanlink.hub.cloud_client",
        ):
            await client.query_device_traits(
                token="tok-1", did="lumi.TESTZIG0000000C",
                paths=["2.132.32920"],
            )

        info_msgs = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
        ]
        assert not any(
            "body=" in m or "deviceId" in m or "lumi.TESTZIG0000000C" in m
            for m in info_msgs
        )

    async def test_sends_post_to_correct_url_with_paths_in_body(self):
        fixture = _load_fixture("cloud_qlink_trait_read.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_traits(
            token="tok-1", did="lumi.TESTZIG0000000C",
            paths=["2.132.32920", "2.133.32923"],
        )

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "POST"
        assert url == "https://rpc-ger.aqara.com/app/v1.0/lumi/app/qlink/trait/read"

        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict == {
            "devices": [{
                "deviceId": "lumi.TESTZIG0000000C",
                "traits": [
                    {"path": "2.132.32920", "needSubscribe": True},
                    {"path": "2.133.32923", "needSubscribe": True},
                ],
            }],
            "needParam": True,
        }

    async def test_query_device_traits_with_paths_builds_correct_body(self):
        """Test that paths are correctly transformed to trait objects."""
        fixture = _load_fixture("cloud_qlink_trait_read.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        await client.query_device_traits(
            token="tok-1", did="lumi.TESTZIG0000000C",
            paths=["2.132.32920", "2.133.32923"],
        )

        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict == {
            "devices": [{
                "deviceId": "lumi.TESTZIG0000000C",
                "traits": [
                    {"path": "2.132.32920", "needSubscribe": True},
                    {"path": "2.133.32923", "needSubscribe": True},
                ],
            }],
            "needParam": True,
        }

    async def test_query_device_traits_empty_paths_raises(self):
        """Passing paths=[] raises ValueError before any HTTP call."""
        session = MagicMock()
        client = AqaraCloudClient(region="EU", session=session)

        with pytest.raises(ValueError):
            await client.query_device_traits(
                token="tok-1", did="lumi.TESTZIG0000000C",
                paths=[],
            )

        # Verify no HTTP call was made
        session.request.assert_not_called()

    async def test_sign_source_is_json_body_verbatim(self):
        """Sign source for POST is the JSON body bytes sent on the wire."""
        fixture = _load_fixture("cloud_qlink_trait_read.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_traits(
            token="tok-1", did="lumi.TESTZIG0000000C",
            paths=["2.132.32920", "2.133.32923"],
        )

        body = session.request.call_args.kwargs["data"]
        headers = session.request.call_args.kwargs["headers"]
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            f"&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        # default json.dumps spacing - server requires those bytes
        assert ", " in body
        assert ": " in body

    async def test_server_error_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 106, "message": "Invalid sign",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="Invalid sign"):
            await client.query_device_traits(
                token="tok-1", did="lumi.TESTZIG0000000C",
                paths=["2.132.32920"],
            )

    async def test_http_error_raises(self):
        session = MagicMock()

        def _raise(*_a, **_kw):
            raise aiohttp.ClientError("connection refused")

        session.request = MagicMock(side_effect=_raise)
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="HTTP error"):
            await client.query_device_traits(
                token="tok-1", did="lumi.TESTZIG0000000C",
                paths=["2.132.32920"],
            )

    async def test_empty_result_list_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": [],
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="missing 'result'"):
            await client.query_device_traits(
                token="tok-1", did="lumi.TESTZIG0000000C",
                paths=["2.132.32920"],
            )


# =============================================================================
# query_device_detail - GET /app/v1.0/lumi/app/dev/query/detail
# =============================================================================


class TestQueryDeviceDetail:
    async def test_returns_first_result_dict(self):
        fixture = _load_fixture("cloud_dev_query_detail.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        detail = await client.query_device_detail(
            token="tok-1", did="lumi.TESTZIG0000000C",
        )
        assert detail == fixture["result"][0]
        assert detail["mac"] == "00:11:22:33:44:55"
        assert detail["parentDeviceId"] == "lumi1.TESTHUB00001"
        assert detail["homeId"] == "real1.TESTPOSITION0001"

    async def test_sends_get_with_dids_in_url(self):
        fixture = _load_fixture("cloud_dev_query_detail.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_detail(
            token="tok-1", did="lumi.TESTZIG0000000C",
        )

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "GET"
        # Sign convention requires the JSON brackets to appear literally
        # in the URL (no percent-encoding) so they match the sign source.
        assert url == (
            "https://rpc-ger.aqara.com/app/v1.0/lumi/app/dev/query/detail"
            '?area=EU&dids=["lumi.TESTZIG0000000C"]'
        )

    async def test_sign_source_alpha_sorted_with_json_dids(self):
        """Sign source: alpha-sorted query string with JSON-encoded dids list."""
        fixture = _load_fixture("cloud_dev_query_detail.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_device_detail(
            token="tok-1", did="lumi.TESTZIG0000000C",
        )

        headers = session.request.call_args.kwargs["headers"]
        sign_source = (
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            '&area=EU&dids=["lumi.TESTZIG0000000C"]'
            f"&{AREAS['EU'].appkey}"
        )
        expected = hashlib.md5(sign_source.encode()).hexdigest()
        assert headers["Sign"] == expected

    async def test_server_error_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 106, "message": "Invalid sign",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="Invalid sign"):
            await client.query_device_detail(
                token="tok-1", did="lumi.TESTZIG0000000C",
            )

    async def test_http_error_raises(self):
        session = MagicMock()

        def _raise(*_a, **_kw):
            raise aiohttp.ClientError("connection refused")

        session.request = MagicMock(side_effect=_raise)
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="HTTP error"):
            await client.query_device_detail(
                token="tok-1", did="lumi.TESTZIG0000000C",
            )

    async def test_empty_result_list_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": [],
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="missing 'result'"):
            await client.query_device_detail(
                token="tok-1", did="lumi.TESTZIG0000000C",
            )


# =============================================================================
# Backward-compat alias: AqaraCloudAuth keeps working as a name for the new class
# =============================================================================


class TestBackwardCompatAlias:
    def test_alias_is_same_class(self):
        assert AqaraCloudAuth is AqaraCloudClient

    async def test_login_via_alias_returns_tokens(self):
        """Existing code that imports AqaraCloudAuth keeps working unchanged."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0,
            "result": {"userId": "u-1", "token": "t-1"},
        })))
        # Exercise the aliased name explicitly.
        client = AqaraCloudAuth(region="EU", session=session)
        tokens = await client.login("user@example.com", "pw")
        assert isinstance(tokens, AqaraTokens)
        assert tokens.user_id == "u-1"
        assert tokens.token == "t-1"


# =============================================================================
# query_custom_actions - POST /app/v1.0/lumi/app/customaction/query
# =============================================================================


class TestQueryCustomActions:
    async def test_returns_result_dict(self):
        """Happy path: POST /customaction/query returns the cloud's result dict verbatim.

        Verified empirically (real T2 RGB CCT capture, 2026-05-05): the result
        is a dict with UserCustomActions + DefaultCustomActions lists, NOT a
        flat list. Earlier spec drafts had this wrong.
        """
        dyn_payload = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(dyn_payload)))
        client = AqaraCloudClient(region="EU", session=session)
        result = await client.query_custom_actions(
            "test_token", "scene_mode",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
        )
        assert isinstance(result, dict)
        assert "UserCustomActions" in result
        assert "DefaultCustomActions" in result
        assert result == dyn_payload["result"]

    async def test_sends_post_to_correct_url_and_body(self):
        dyn_payload = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(dyn_payload)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_custom_actions(
            "test_token", "scene_mode",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
        )

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "POST"
        assert url.endswith("/app/v1.0/lumi/app/customaction/query")
        assert url == "https://rpc-ger.aqara.com/app/v1.0/lumi/app/customaction/query"

        # Body uses deviceModel + userDeviceId (NOT model + deviceId).
        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict == {
            "actionId": "scene_mode",
            "deviceModel": "lumi.light.agl003",
            "userDeviceId": "lumi.TESTZIG0000000C",
        }

    async def test_sign_source_is_json_body_verbatim(self):
        """Sign source for POST is the JSON body bytes sent on the wire."""
        dyn_payload = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(dyn_payload)))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_custom_actions(
            "test_token", "scene_mode",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
        )

        body = session.request.call_args.kwargs["data"]
        headers = session.request.call_args.kwargs["headers"]
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=test_token"
            f"&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        # default json.dumps spacing - server requires those bytes
        assert ", " in body
        assert ": " in body

    async def test_raises_on_non_zero_code(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 108, "message": "auth failure",
            "result": {"UserCustomActions": [], "DefaultCustomActions": []},
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError):
            await client.query_custom_actions(
                "test_token", "scene_mode",
                device_model="lumi.light.agl003",
                user_device_id="lumi.TESTZIG0000000C",
            )

    async def test_raises_when_result_is_not_a_dict(self):
        """A list (or any non-dict) in `result` violates the contract."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": [],
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="is not a dict"):
            await client.query_custom_actions(
                "test_token", "scene_mode",
                device_model="lumi.light.agl003",
                user_device_id="lumi.TESTZIG0000000C",
            )


# =============================================================================
# run_sequence - POST /app/v1.0/lumi/app/sequence/run
# =============================================================================


class TestRunSequence:
    async def test_happy_path(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": "", "message": "Success",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        # No return value; success is "did not raise".
        await client.run_sequence("test_token", did="lumi.TESTZIG0000000C", seq_id="100001")

    async def test_sends_post_to_correct_url_and_body(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 0, "result": "", "message": "Success",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        await client.run_sequence("test_token", did="lumi.TESTZIG0000000C", seq_id="100001")

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "POST"
        assert url.endswith("/app/v1.0/lumi/app/sequence/run")
        assert url == "https://rpc-ger.aqara.com/app/v1.0/lumi/app/sequence/run"

        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict == {
            "actionId": "",
            "deviceId": "lumi.TESTZIG0000000C",
            "seqId": "100001",
        }

    async def test_raises_on_non_zero_code(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 200, "message": "device offline", "result": "",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError):
            await client.run_sequence("test_token", did="lumi.TESTZIG0000000C", seq_id="100001")


# =============================================================================
# _parse_effects - module-level helper
# =============================================================================


class TestParseEffects:
    def test_parse_effects_static_from_real_fixture(self):
        """Real T2 RGB CCT capture: 4 default static scenes, mired -> Kelvin."""
        static_payload = json.loads((_FIXTURES / "cloud_customaction_static.json").read_text())
        dynamic_payload = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        effects = _parse_effects(static_payload["result"], dynamic_payload["result"])

        # 4 static (Good night, Work, Reading, Chill Out) + 6 dynamic
        # (Security, Wake, Sleep, Colorful, Breath, Candlelight) = 10.
        assert len(effects) == 10
        static_effects = [e for e in effects if e.seq_id is None]
        assert {e.name for e in static_effects} == {"Good night", "Work", "Reading", "Chill Out"}
        by_name = {e.name: e for e in static_effects}
        # Real captured wire values: Good night -> 500 mired = 2000K, brightness 1
        assert by_name["Good night"].color_temp_kelvin == 2000
        assert by_name["Good night"].brightness_pct == 1
        # Reading -> 250 mired = 4000K, brightness 100
        assert by_name["Reading"].color_temp_kelvin == 4000
        assert by_name["Reading"].brightness_pct == 100

    def test_parse_effects_dynamic_from_real_fixture(self):
        """Real T2 RGB CCT capture: 6 dynamic scenes with seqIds 64-69."""
        static_payload = json.loads((_FIXTURES / "cloud_customaction_static.json").read_text())
        dynamic_payload = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        effects = _parse_effects(static_payload["result"], dynamic_payload["result"])

        dynamic_effects = [e for e in effects if e.seq_id is not None]
        assert {e.name for e in dynamic_effects} == {
            "Security", "Wake", "Sleep", "Colorful", "Breath", "Candlelight",
        }
        by_name = {e.name: e for e in dynamic_effects}
        assert by_name["Wake"].seq_id == "65"
        assert by_name["Candlelight"].seq_id == "69"
        for eff in dynamic_effects:
            assert eff.color_temp_kelvin is None
            assert eff.brightness_pct is None

    def test_parse_effects_handles_empty_results(self):
        """No scenes returned -> empty tuple, no errors."""
        empty = {"UserCustomActions": [], "DefaultCustomActions": []}
        effects = _parse_effects(empty, empty)
        assert effects == ()

    def test_parse_effects_walks_user_then_default_lists(self):
        """Synthetic: user-defined scenes appear before default scenes in iteration order."""
        static = {
            "UserCustomActions": [
                {"customName": "MyScene", "value": json.dumps({"colour_temperature": "250", "light_level": "80"})},
            ],
            "DefaultCustomActions": [
                {"customName": "Reading", "value": json.dumps({"colour_temperature": "250", "light_level": "100"})},
            ],
        }
        dynamic = {
            "UserCustomActions": [],
            "DefaultCustomActions": [{"customName": "Aurora", "seqId": 100001}],
        }
        effects = _parse_effects(static, dynamic)
        assert [e.name for e in effects] == ["MyScene", "Reading", "Aurora"]
        assert effects[0].seq_id is None
        assert effects[2].seq_id == "100001"  # int seqId stringified

    def test_parse_effects_skips_dynamic_without_seq_id(self):
        """A malformed entry (missing seqId AND missing color_temp/brightness) is skipped from dynamic."""
        # In the dynamic-result branch, only entries with seq_id != None are dynamic; entries
        # without seq_id ARE classified as static (and parse with color_temp=None, brightness=None).
        dyn = {
            "UserCustomActions": [],
            "DefaultCustomActions": [{"customName": "broken", "value": "{}"}],  # no seqId
        }
        effects = _parse_effects({"UserCustomActions": [], "DefaultCustomActions": []}, dyn)
        # The "broken" entry is treated as static with both color_temp and brightness = None.
        assert len(effects) == 1
        assert effects[0].name == "broken"
        assert effects[0].seq_id is None
        assert effects[0].color_temp_kelvin is None
        assert effects[0].brightness_pct is None

    def test_parse_effects_zero_or_invalid_color_temp_safe(self):
        """Defensive: a 0 or non-numeric colour_temperature does not divide-by-zero."""
        static = {
            "UserCustomActions": [],
            "DefaultCustomActions": [
                {"customName": "BadCT", "value": json.dumps({"colour_temperature": "0", "light_level": "50"})},
                {"customName": "NonNumeric", "value": json.dumps({"colour_temperature": "abc", "light_level": "50"})},
            ],
        }
        empty = {"UserCustomActions": [], "DefaultCustomActions": []}
        effects = _parse_effects(static, empty)
        for eff in effects:
            assert eff.color_temp_kelvin is None
            assert eff.brightness_pct == 50


# =============================================================================
# enrich_light_effects - module-level helper
# =============================================================================


class TestEnrichLightEffects:

    @pytest.mark.asyncio
    async def test_replaces_descriptor_with_effects(self):
        """The helper fetches both action_ids and produces an enriched LightDescriptor."""
        from custom_components.aqara_lanlink.device.attrs import AttrSpec
        from custom_components.aqara_lanlink.device.descriptors import LightDescriptor
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.hub.cloud_client import (
            AqaraCloudClient,
            enrich_light_effects,
        )

        static = json.loads((_FIXTURES / "cloud_customaction_static.json").read_text())
        dynamic = json.loads((_FIXTURES / "cloud_customaction_dynamic.json").read_text())
        # session.request is called twice (once per action_id); side_effect feeds
        # responses in order.
        session = MagicMock()
        session.request = MagicMock(side_effect=[
            _fake_response(json.dumps(static)),
            _fake_response(json.dumps(dynamic)),
        ])
        client = AqaraCloudClient(region="EU", session=session)

        desc = LightDescriptor(
            key="light",
            translation_key="light",
            power_trait=TraitSpec(id="4.1.85", name="power_status", data_type="bool"),
            power_attr=AttrSpec(name="2.130.32913", id="4.1.85", data_type="bool"),
        )
        descriptors = [desc]
        await enrich_light_effects(
            client, "test_token",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
            descriptors=descriptors,
        )
        enriched = descriptors[0]
        assert isinstance(enriched, LightDescriptor)
        # 4 static + 6 dynamic from the real captures.
        assert len(enriched.effects) == 10
        # Original descriptor unchanged (frozen dataclass; replace produced a new instance).
        assert desc.effects == ()


    @pytest.mark.asyncio
    async def test_swallows_server_errors(self, caplog):
        """Cloud returns server error (code != 0): log warning, leave effects=()."""
        caplog.set_level(
            logging.WARNING,
            logger="custom_components.aqara_lanlink.hub.cloud_client",
        )
        from custom_components.aqara_lanlink.device.attrs import AttrSpec
        from custom_components.aqara_lanlink.device.descriptors import LightDescriptor
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.hub.cloud_client import (
            AqaraCloudClient,
            enrich_light_effects,
        )

        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(
            json.dumps({"code": 500, "message": "internal",
                        "result": {"UserCustomActions": [], "DefaultCustomActions": []}})
        ))
        client = AqaraCloudClient(region="EU", session=session)

        desc = LightDescriptor(
            key="light",
            translation_key="light",
            power_trait=TraitSpec(id="4.1.85", name="power_status", data_type="bool"),
            power_attr=AttrSpec(name="2.130.32913", id="4.1.85", data_type="bool"),
        )
        descriptors = [desc]
        await enrich_light_effects(
            client, "test_token",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
            descriptors=descriptors,
        )
        assert descriptors[0].effects == ()
        assert any("Effect fetch failed" in rec.message for rec in caplog.records)


    @pytest.mark.asyncio
    async def test_swallows_network_errors(self, caplog):
        """Cloud unreachable (aiohttp.ClientError): log warning, leave effects=()."""
        caplog.set_level(
            logging.WARNING,
            logger="custom_components.aqara_lanlink.hub.cloud_client",
        )
        from custom_components.aqara_lanlink.device.attrs import AttrSpec
        from custom_components.aqara_lanlink.device.descriptors import LightDescriptor
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.hub.cloud_client import (
            AqaraCloudClient,
            enrich_light_effects,
        )

        session = MagicMock()
        session.request = MagicMock(
            side_effect=aiohttp.ClientConnectionError("connection refused")
        )
        client = AqaraCloudClient(region="EU", session=session)

        desc = LightDescriptor(
            key="light",
            translation_key="light",
            power_trait=TraitSpec(id="4.1.85", name="power_status", data_type="bool"),
            power_attr=AttrSpec(name="2.130.32913", id="4.1.85", data_type="bool"),
        )
        descriptors = [desc]
        await enrich_light_effects(
            client, "test_token",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
            descriptors=descriptors,
        )
        assert descriptors[0].effects == ()
        assert any("Effect fetch failed" in rec.message for rec in caplog.records)


    @pytest.mark.asyncio
    async def test_skips_non_light_descriptors(self):
        """Non-LightDescriptor entries are left untouched and no cloud calls are made."""
        from custom_components.aqara_lanlink.device.attrs import AttrSpec
        from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor
        from custom_components.aqara_lanlink.hub.cloud_client import (
            AqaraCloudClient,
            enrich_light_effects,
        )
        from unittest.mock import AsyncMock

        sw = SwitchDescriptor(key="sw", attr=AttrSpec(name="x", id="4.1.85", data_type="bool"))
        descriptors = [sw]
        client = AqaraCloudClient(region="EU", session=None)
        client.query_custom_actions = AsyncMock()  # type: ignore[method-assign]
        await enrich_light_effects(
            client, "test_token",
            device_model="lumi.light.agl003",
            user_device_id="lumi.TESTZIG0000000C",
            descriptors=descriptors,
        )
        assert descriptors[0] is sw
        client.query_custom_actions.assert_not_called()


# =============================================================================
# query_collection_panels - GET /app/v1.0/lumi/app/layout/collection/panels
# =============================================================================


class TestQueryCollectionPanels:
    async def test_query_collection_panels_returns_endpoint_path_map(self):
        """Happy path: fixture response yields EndpointPanel objects per endpoint id."""
        fixture = _load_fixture("cloud_collection_panels.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        result = await client.query_collection_panels(
            token="tok-1", did="lumi.TESTDEV0000000001",
        )

        assert 2 in result and 3 in result
        ep2, ep3 = result[2], result[3]
        assert ep2.endpoint_id == 2
        assert ep2.paths == (
            "2.130.32915", "2.130.32913", "2.164.20370", "2.164.33007",
            "2.130.32919", "2.164.20371", "2.164.20372", "2.164.20373",
            "2.164.20374", "2.164.20375", "2.164.20364", "2.164.20376",
            "2.164.20377", "2.130.33012",
        )
        assert ep3.endpoint_id == 3
        assert ep3.paths == (
            "3.130.32919", "3.130.32929", "3.130.33012", "3.155.32990",
            "3.130.32915", "3.130.32913",
        )

    async def test_query_collection_panels_sends_get_to_correct_url(self):
        """GET request sent to the correct endpoint URL with bare query params."""
        fixture = _load_fixture("cloud_collection_panels.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        await client.query_collection_panels(
            token="tok-1", did="lumi.TESTDEV0000000001",
        )

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "GET"
        assert url == (
            "https://rpc-ger.aqara.com"
            "/app/v1.0/lumi/app/layout/collection/panels"
            "?subjectIds=lumi.TESTDEV0000000001&types=device_endpoint_panel"
        )

    async def test_query_collection_panels_sign_source_uses_bare_strings(self):
        """Sign source is bare query string (not JSON-encoded), alpha order."""
        fixture = _load_fixture("cloud_collection_panels.json")
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps(fixture)))
        client = AqaraCloudClient(region="EU", session=session)

        await client.query_collection_panels(
            token="tok-1", did="lumi.TESTDEV0000000001",
        )

        headers = session.request.call_args.kwargs["headers"]
        # Sign source: subjectIds=<did>&types=device_endpoint_panel (alpha order)
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            "&subjectIds=lumi.TESTDEV0000000001&types=device_endpoint_panel"
            f"&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        assert headers["Token"] == "tok-1"

    async def test_query_collection_panels_empty_result_returns_empty_dict(self):
        """When the cloud returns an empty result list, the helper returns {}."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [], "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)

        result = await client.query_collection_panels(
            token="tok-1", did="lumi.TESTDEV0000000001",
        )
        assert result == {}

    async def test_query_collection_panels_raises_on_server_error(self):
        """A non-zero code in the cloud response raises AqaraAuthError."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 100, "message": "auth fail",
        })))
        client = AqaraCloudClient(region="EU", session=session)

        with pytest.raises(AqaraAuthError, match="auth fail"):
            await client.query_collection_panels(
                token="tok-1", did="lumi.TESTDEV0000000001",
            )


# =============================================================================
# query_resources_by_rid - POST /app/v1.0/lumi/res/query/by/resourceId
# =============================================================================


class TestQueryResourcesByRid:
    async def test_parses_result_into_rid_value_map(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [
                {"resourceId": "4.4.85", "value": "1"},
                {"resourceId": "8.0.2032", "value": "0"},
            ],
            "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)

        result = await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["4.4.85", "8.0.2032"],
        )
        assert result == {"4.4.85": "1", "8.0.2032": "0"}

    async def test_sends_post_to_correct_url_with_rids_in_body(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [{"resourceId": "8.0.2032", "value": "0"}], "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["8.0.2032"],
        )

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "POST"
        assert url.endswith("/app/v1.0/lumi/res/query/by/resourceId")
        assert url == "https://rpc-ger.aqara.com/app/v1.0/lumi/res/query/by/resourceId"

        body = session.request.call_args.kwargs["data"]
        body_dict = json.loads(body)
        assert body_dict == {
            "data": [{"options": ["8.0.2032"], "subjectId": "lumi.54ef"}],
        }

    async def test_sign_source_is_json_body_verbatim(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [{"resourceId": "8.0.2032", "value": "0"}], "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)
        await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["8.0.2032"],
        )

        body = session.request.call_args.kwargs["data"]
        headers = session.request.call_args.kwargs["headers"]
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            f"&{body}&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        assert headers["Token"] == "tok-1"

    async def test_partial_result_omits_missing_rid(self):
        """A rid absent from the result is simply absent from the returned dict."""
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [{"resourceId": "4.4.85", "value": "1"}], "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)
        result = await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["4.4.85", "8.0.2032"],
        )
        assert result == {"4.4.85": "1"}

    async def test_empty_result_returns_empty_dict(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [], "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)
        result = await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["4.4.85"],
        )
        assert result == {}

    async def test_skips_items_lacking_resource_id_or_value(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "result": [
                {"resourceId": "4.4.85", "value": "1"},
                {"resourceId": "8.0.2032"},      # no value
                {"value": "9"},                   # no resourceId
            ],
            "code": 0,
        })))
        client = AqaraCloudClient(region="EU", session=session)
        result = await client.query_resources_by_rid(
            token="tok-1", did="lumi.54ef", rids=["4.4.85", "8.0.2032"],
        )
        assert result == {"4.4.85": "1"}

    async def test_server_error_raises(self):
        session = MagicMock()
        session.request = MagicMock(return_value=_fake_response(json.dumps({
            "code": 106, "message": "Invalid sign",
        })))
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="Invalid sign"):
            await client.query_resources_by_rid(
                token="tok-1", did="lumi.54ef", rids=["4.4.85"],
            )

    async def test_http_error_raises(self):
        session = MagicMock()

        def _raise(*_a, **_kw):
            raise aiohttp.ClientError("connection refused")

        session.request = MagicMock(side_effect=_raise)
        client = AqaraCloudClient(region="EU", session=session)
        with pytest.raises(AqaraAuthError, match="HTTP error"):
            await client.query_resources_by_rid(
                token="tok-1", did="lumi.54ef", rids=["4.4.85"],
            )


# =============================================================================
# Auth-failure code classification: _raise_if_error must raise the narrow
# AqaraCloudAuthError subclass for codes Aqara's docs flag as token-invalid,
# so callers can trigger HA's re-auth flow specifically.
# Source: https://opendoc.aqara.cn/en/docs/developmanual/apiIntroduction/errorCode.html
# =============================================================================


class TestAuthFailureClassification:
    @pytest.mark.parametrize("code,desc", [
        (108, "Token has expired"),
        (109, "Token is absence"),
        (802, "Account not login"),
        (804, "Token failed"),
    ])
    def test_documented_auth_codes_raise_cloud_auth_error(self, code, desc):
        """Each documented auth-failure code raises AqaraCloudAuthError
        (subclass of AqaraAuthError) so callers can match the narrow
        type and trigger HA's re-auth flow."""
        with pytest.raises(AqaraCloudAuthError):
            AqaraCloudClient._raise_if_error({"code": code, "message": desc})

    @pytest.mark.parametrize("code", [302, 403, 500, 601, 803, 810, 1003])
    def test_non_auth_codes_raise_generic_auth_error_not_subclass(self, code):
        """Non-auth failures (param errors, server errors, scope-permission
        codes) raise the generic AqaraAuthError, NOT the narrow auth
        subclass -- otherwise the re-auth flow would fire on every
        unrelated cloud failure."""
        with pytest.raises(AqaraAuthError) as exc_info:
            AqaraCloudClient._raise_if_error({"code": code, "message": "x"})
        assert not isinstance(exc_info.value, AqaraCloudAuthError), (
            f"code={code} must not raise the auth-failure subclass"
        )

    def test_code_zero_does_not_raise(self):
        """Sanity: zero is success and short-circuits cleanly."""
        AqaraCloudClient._raise_if_error({"code": 0})  # must not raise

    def test_documented_codes_constant_matches_docs(self):
        """Pin the documented set so future edits stay aligned with
        Aqara's error-code table."""
        assert _AUTH_FAILURE_CODES == frozenset({108, 109, 802, 804})

