"""Tests for Aqara Doorbell talk client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.aqara_lanlink.device.camera.const import (
    AUDIO_PORT,
    TYPE_ACK,
    TYPE_START_VOICE,
)
from custom_components.aqara_lanlink.device.camera.protocol import build_packet
from custom_components.aqara_lanlink.device.camera.talk import AqaraLanTalkClient


@pytest.fixture
def mock_tcp_connection():
    """Mock asyncio.open_connection and loop.create_datagram_endpoint."""
    reader = AsyncMock()
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    ack = build_packet(TYPE_ACK, 0)
    reader.read = AsyncMock(return_value=ack)

    mock_udp_transport = MagicMock()
    mock_udp_protocol = MagicMock()

    async def fake_create_datagram_endpoint(protocol_factory, **kwargs):
        return mock_udp_transport, mock_udp_protocol

    with patch(
        "custom_components.aqara_lanlink.device.camera.talk.asyncio.open_connection",
        return_value=(reader, writer),
    ):
        with patch(
            "asyncio.get_event_loop",
            return_value=MagicMock(
                create_datagram_endpoint=AsyncMock(
                    return_value=(mock_udp_transport, mock_udp_protocol)
                )
            ),
        ):
            with patch(
                "asyncio.get_running_loop",
                return_value=MagicMock(
                    create_datagram_endpoint=AsyncMock(
                        return_value=(mock_udp_transport, mock_udp_protocol)
                    )
                ),
            ):
                yield reader, writer


class TestAqaraLanTalkClient:
    async def test_connect_sends_start_voice(self, mock_tcp_connection):
        reader, writer = mock_tcp_connection
        client = AqaraLanTalkClient("10.1.20.150")
        assert await client.connect() is True
        writer.write.assert_called_once()
        sent = writer.write.call_args[0][0]
        assert sent[0:2] == b"\xFE\xEF"
        assert sent[2] == TYPE_START_VOICE
        await client.disconnect()

    async def test_connect_fails_on_rejected_ack(self, mock_tcp_connection):
        reader, writer = mock_tcp_connection
        reader.read = AsyncMock(return_value=build_packet(TYPE_ACK, 1))
        client = AqaraLanTalkClient("10.1.20.150")
        assert await client.connect() is False

    async def test_connect_fails_on_timeout(self):
        with patch(
            "custom_components.aqara_lanlink.device.camera.talk.asyncio.open_connection",
            side_effect=asyncio.TimeoutError,
        ):
            client = AqaraLanTalkClient("10.1.20.150")
            assert await client.connect() is False

    async def test_disconnect_sends_stop_voice(self, mock_tcp_connection):
        reader, writer = mock_tcp_connection
        client = AqaraLanTalkClient("10.1.20.150")
        await client.connect()
        await client.disconnect()
        assert writer.write.call_count >= 2

    async def test_send_audio_frame(self, mock_tcp_connection):
        reader, writer = mock_tcp_connection
        client = AqaraLanTalkClient("10.1.20.150")
        await client.connect()
        mock_transport = MagicMock()
        client._udp_transport = mock_transport
        client.send_audio_frame(b"\xFF\xF1" + b"\x00" * 50, 0)
        mock_transport.sendto.assert_called_once()
        sent_data, addr = mock_transport.sendto.call_args[0]
        assert addr == ("10.1.20.150", AUDIO_PORT)
        assert len(sent_data) == 12 + 52  # RTP header + frame
        await client.disconnect()

    async def test_is_connected_property(self, mock_tcp_connection):
        reader, writer = mock_tcp_connection
        client = AqaraLanTalkClient("10.1.20.150")
        assert client.is_connected is False
        await client.connect()
        assert client.is_connected is True
        await client.disconnect()
        assert client.is_connected is False
