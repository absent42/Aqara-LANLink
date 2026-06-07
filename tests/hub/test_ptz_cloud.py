"""Tests for the PTZ preset-list cloud call (query_ptz_positions).

Mirrors tests/hub/test_cloud_client.py: an AqaraCloudClient is
built over a MagicMock aiohttp session whose `request` returns a fake async
context manager, exactly as the GET helpers (e.g. query_camera_p2p_info) are
exercised there.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.hub.cloud_client import (
    AREAS,
    AqaraAuthError,
    AqaraCloudClient,
)


def _fake_response(text):
    resp = MagicMock()
    resp.text = AsyncMock(return_value=text)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


_PRESET_PAYLOAD = {
    "code": 0,
    "result": [
        {
            "id": 1,
            "name": "Door",
            "position": "POS-A",
            "imgUrl": "https://example/img.jpg",
            "did": "lumi3.x",
        }
    ],
}


class TestQueryPtzPositions:
    async def test_returns_preset_list(self):
        session = MagicMock()
        session.request = MagicMock(
            return_value=_fake_response(json.dumps(_PRESET_PAYLOAD))
        )
        client = AqaraCloudClient(region="EU", session=session)

        result = await client.query_ptz_positions("tok-1", "lumi3.x")

        assert result == _PRESET_PAYLOAD["result"]
        assert result[0]["position"] == "POS-A"

    async def test_sends_get_to_correct_url(self):
        session = MagicMock()
        session.request = MagicMock(
            return_value=_fake_response(json.dumps(_PRESET_PAYLOAD))
        )
        client = AqaraCloudClient(region="EU", session=session)

        await client.query_ptz_positions("tok-1", "lumi3.x")

        method = session.request.call_args.args[0]
        url = session.request.call_args.args[1]
        assert method == "GET"
        assert url == (
            "https://rpc-ger.aqara.com"
            "/app/v1.0/lumi/devex/camera/ptz/position/list"
            "?did=lumi3.x"
        )

    async def test_sign_source_uses_bare_query(self):
        session = MagicMock()
        session.request = MagicMock(
            return_value=_fake_response(json.dumps(_PRESET_PAYLOAD))
        )
        client = AqaraCloudClient(region="EU", session=session)

        await client.query_ptz_positions("tok-1", "lumi3.x")

        headers = session.request.call_args.kwargs["headers"]
        expected = hashlib.md5(
            f"Appid={AREAS['EU'].appid}&Nonce={headers['Nonce']}"
            f"&Time={headers['Time']}&Token=tok-1"
            "&did=lumi3.x"
            f"&{AREAS['EU'].appkey}".encode()
        ).hexdigest()
        assert headers["Sign"] == expected
        assert headers["Token"] == "tok-1"

    async def test_non_list_result_returns_empty(self):
        session = MagicMock()
        session.request = MagicMock(
            return_value=_fake_response(json.dumps({"code": 0, "result": {}}))
        )
        client = AqaraCloudClient(region="EU", session=session)

        result = await client.query_ptz_positions("tok-1", "lumi3.x")
        assert result == []

    async def test_raises_on_server_error(self):
        session = MagicMock()
        session.request = MagicMock(
            return_value=_fake_response(json.dumps({
                "code": 100, "message": "auth fail",
            }))
        )
        client = AqaraCloudClient(region="EU", session=session)

        with pytest.raises(AqaraAuthError, match="auth fail"):
            await client.query_ptz_positions("tok-1", "lumi3.x")
