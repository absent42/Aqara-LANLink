"""Async LAN talk client for Aqara Doorbell.

Manages TCP control session (port 54324) and UDP audio stream (port 54323).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from .const import (
    AUDIO_PORT,
    CONTROL_PORT,
    HEARTBEAT_INTERVAL,
    RTP_PAYLOAD_TYPE,
    TCP_CONNECT_TIMEOUT,
    TYPE_ACK,
    TYPE_HEARTBEAT,
    TYPE_START_VOICE,
    TYPE_STOP_VOICE,
)
from .protocol import build_packet, build_rtp_header, parse_packet

_LOGGER = logging.getLogger(__name__)


class _UDPProtocol(asyncio.DatagramProtocol):
    """Minimal UDP protocol for sending audio."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        pass


class AqaraLanTalkClient:
    """Async client for Aqara camera LAN talk protocol."""

    def __init__(self, camera_ip: str) -> None:
        self._camera_ip = camera_ip
        self._session_ts = int(time.time() * 1000)
        self._ssrc = random.randint(1, 2147483647)
        self._seq_num = 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._connected = False
        self._connect_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """Start a voice session with the camera."""
        async with self._connect_lock:
            if self._connected:
                return True
            return await self._connect_inner()

    async def _connect_inner(self) -> bool:
        """Internal connect logic, called under _connect_lock."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._camera_ip, CONTROL_PORT),
                timeout=TCP_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.error("Connect failed %s:%s: %s", self._camera_ip, CONTROL_PORT, err)
            return False

        self._writer.write(build_packet(TYPE_START_VOICE, self._session_ts))
        await self._writer.drain()

        try:
            resp = await asyncio.wait_for(self._reader.read(1024), timeout=TCP_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.error("No ACK from camera")
            await self._close_tcp()
            return False

        if not resp:
            _LOGGER.error("Connection closed before ACK")
            await self._close_tcp()
            return False

        parsed = parse_packet(resp)
        if not parsed or parsed["type"] != TYPE_ACK or parsed["value"] != 0:
            _LOGGER.error("Voice session rejected: %s", parsed)
            await self._close_tcp()
            return False

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            _UDPProtocol, local_addr=("0.0.0.0", 0)
        )
        self._udp_transport = transport

        self._connected = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        _LOGGER.debug("Voice session active with %s", self._camera_ip)
        return True

    def send_audio_frame(self, aac_frame: bytes, timestamp_samples: int) -> None:
        """Send an AAC-ADTS frame via RTP."""
        if not self._udp_transport:
            return
        header = build_rtp_header(
            RTP_PAYLOAD_TYPE, timestamp_samples, self._ssrc, self._seq_num
        )
        self._seq_num += 1
        self._udp_transport.sendto(header + aac_frame, (self._camera_ip, AUDIO_PORT))

    async def disconnect(self) -> None:
        """Stop the voice session and clean up."""
        self._connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(build_packet(TYPE_STOP_VOICE, self._session_ts))
                await self._writer.drain()
            except (OSError, ConnectionError):
                pass

        await self._close_tcp()

        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None

    async def _close_tcp(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (OSError, ConnectionError):
                pass
            self._writer = None
            self._reader = None

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats every 5s to keep the session alive."""
        failures = 0
        while self._connected:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if not self._connected or not self._writer:
                break
            try:
                self._writer.write(build_packet(TYPE_HEARTBEAT, self._session_ts))
                await self._writer.drain()
                resp = await asyncio.wait_for(
                    self._reader.read(1024), timeout=TCP_CONNECT_TIMEOUT
                )
                if not resp:
                    _LOGGER.warning("Connection closed during heartbeat")
                    self._connected = False
                    await self._close_tcp()
                    if self._udp_transport:
                        self._udp_transport.close()
                        self._udp_transport = None
                    break
                parsed = parse_packet(resp)
                if parsed and parsed["type"] == TYPE_ACK and parsed["value"] == 0:
                    failures = 0
                    continue
                failures += 1
            except (asyncio.TimeoutError, OSError, ConnectionError):
                failures += 1

            if failures > 5:
                _LOGGER.warning("Voice channel dead after %d heartbeat failures", failures)
                self._connected = False
                await self._close_tcp()
                if self._udp_transport:
                    self._udp_transport.close()
                    self._udp_transport = None
                break
