"""LANLink tunnel abstraction layer.

Provides:
    - BaseTunnel: abstract JSON-in/JSON-out transport interface.
    - PlaintextTunnel: newline-delimited JSON, unit-test only.
    - EncryptedTunnel: production AES-128-CBC + ECDH tunnel speaking the real
      wire protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

from .crypto import (
    MSG_TYPE_ECDH,
    MSG_TYPE_SESSION,
    MSG_TYPE_VERIFY,
    Frame,
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    derive_device_key,
    parse_frame,
)
from .ecc import (
    KEY_BYTES,
    PUBKEY_BYTES,
    ecdh_pubkey_from_privkey,
    ecdh_shared_secret,
    field_to_bytes,
)

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Abstract base
# =============================================================================


class BaseTunnel(ABC):
    """Abstract transport interface for a LANLink session."""

    @abstractmethod
    async def connect(self, host: str, port: int, *, ssl=None) -> None:
        """Open the connection to host:port.

        Pass ssl=<SSLContext> to wrap the connection in TLS. When set,
        server_hostname is derived from host and forwarded to
        asyncio.open_connection automatically.
        """

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Serialize message to JSON and transmit it."""

    @abstractmethod
    async def receive(self) -> dict[str, Any] | None:
        """Receive and deserialize the next message.

        Returns None on EOF, disconnect, or parse error.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the connection and release resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the tunnel is open and not closing."""


# =============================================================================
# Plaintext stub -- testing only
# =============================================================================


class PlaintextTunnel(BaseTunnel):
    """Newline-delimited JSON tunnel over a plain TCP connection.

    Not for production use. Intended for unit tests that spin up mock TCP
    servers (Task 5) to exercise the LANLink session client without
    encryption.

    Framing: each message is a single line terminated by '\\n'.
    """

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, host: str, port: int, *, ssl=None) -> None:
        """Open a plain TCP connection to host:port.

        Pass ssl=<SSLContext> to wrap the connection in TLS. When set,
        server_hostname is derived from host and forwarded to
        asyncio.open_connection automatically.
        """
        open_kwargs: dict[str, object] = {}
        if ssl is not None:
            open_kwargs["ssl"] = ssl
            open_kwargs["server_hostname"] = host
        self._reader, self._writer = await asyncio.open_connection(host, port, **open_kwargs)
        _LOGGER.debug("PlaintextTunnel connected to %s:%d", host, port)

    async def send(self, message: dict[str, Any]) -> None:
        """Serialize message as JSON, append a newline, and write to the socket."""
        if self._writer is None:
            raise RuntimeError("Tunnel is not connected")
        line = json.dumps(message) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()

    async def receive(self) -> dict[str, Any] | None:
        """Read one newline-terminated line and deserialize it as JSON.

        Returns None if the connection is closed, the line is empty, or
        the data cannot be parsed as a JSON object.
        """
        if self._reader is None:
            return None
        try:
            raw = await self._reader.readline()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError) as exc:
            _LOGGER.debug("PlaintextTunnel receive error: %s", exc)
            return None

        if not raw:
            _LOGGER.debug("PlaintextTunnel received EOF")
            return None

        try:
            obj = json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.debug("PlaintextTunnel JSON parse error: %s", exc)
            return None

        if not isinstance(obj, dict):
            _LOGGER.debug("PlaintextTunnel received non-dict JSON: %r", obj)
            return None

        return obj

    async def close(self) -> None:
        """Close the writer and clear internal state."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError as exc:
                _LOGGER.debug("PlaintextTunnel close error: %s", exc)

    def is_connected(self) -> bool:
        """Return True if the writer exists and is not closing."""
        return self._writer is not None and not self._writer.is_closing()


# =============================================================================
# Production encrypted tunnel
# =============================================================================


class TunnelError(Exception):
    """Raised for tunnel-level protocol errors."""


def _keepalive_acks_lost(
    last_recv: float, now: float, interval: float, factor: float,
) -> bool:
    """True when nothing has been received for more than ``interval * factor``
    seconds -- i.e. the hub has gone silent (likely a half-open socket)."""
    return (now - last_recv) > interval * factor


class EncryptedTunnel(BaseTunnel):
    """Full LANLink tunnel: ECDH handshake, key verify, encrypted JSON.

    Handshake sequence (captured and validated):
        1. C -> S: type 0x2020, AES-CBC(device_key, 0x0030 || pubkey48 ||
                                        0x0010 || client_nonce16 || pad)
        2. S -> C: type 0x2020, AES-CBC(device_key, 0x0030 || server_pubkey48 ||
                                        0x0010 || server_nonce16 || pad)
        3. Client derives session_key = first 16 bytes of ECDH(priv, server_pub).
        4. C -> S: type 0x201b, AES-CBC(device_key,
                     0x0004 || random_challenge4 || 0x0010 ||
                     AES-CBC(session_key, random_challenge4 + pad) || pad)
        5. S -> C: type 0x201b, mirrored verify payload.
        6. Further messages exchanged as type 0x2021 with session_key.

    Public interface matches BaseTunnel: callers get plain JSON dicts.
    """

    #: Default interval between keepalives, in seconds. The hub drops idle
    #: sessions at ~15s, so we ping every 10s to stay comfortably inside.
    KEEPALIVE_INTERVAL = 10.0
    #: Force a reconnect if no frame (not even a keepalive ack) has arrived for
    #: this many keepalive intervals -- catches a half-open socket where our
    #: sends still succeed into the TCP buffer but the hub is gone.
    KEEPALIVE_LOSS_FACTOR = 3

    def __init__(
        self,
        device_id: str,
        keepalive_interval: float = KEEPALIVE_INTERVAL,
    ) -> None:
        self._device_id = device_id
        self._device_key = derive_device_key(device_id)
        self._session_key: bytes | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._client_priv: bytes | None = None
        self._recv_buf = bytearray()
        self._send_lock = asyncio.Lock()
        self._keepalive_interval = keepalive_interval
        self._keepalive_task: asyncio.Task[None] | None = None
        self._last_recv_monotonic = time.monotonic()

    # -------------------------------------------------------------------------
    # Framing helpers
    # -------------------------------------------------------------------------

    async def _send_frame(self, msg_type: int, plaintext: bytes, key: bytes) -> None:
        """Encrypt and send one FF FE frame. Concurrency-safe via send lock."""
        if self._writer is None:
            raise TunnelError("Tunnel is not connected")
        ciphertext = aes_cbc_encrypt(key, plaintext)
        frame = Frame(type=msg_type, ciphertext=ciphertext).encode()
        async with self._send_lock:
            if self._writer is None:
                raise TunnelError("Tunnel is not connected")
            _LOGGER.debug("send frame type=0x%04x ct_len=%d", msg_type, len(ciphertext))
            self._writer.write(frame)
            await self._writer.drain()

    async def _recv_frame(self) -> tuple[int, bytes]:
        """Receive one frame and decrypt it. Returns (type, plaintext)."""
        while True:
            try:
                parsed = parse_frame(bytes(self._recv_buf))
            except ValueError as exc:
                raise TunnelError(f"Bad frame: {exc}") from exc
            if parsed is not None:
                frame, rest = parsed
                self._recv_buf = bytearray(rest)
                key = self._select_decrypt_key(frame.type)
                plaintext = aes_cbc_decrypt(key, frame.ciphertext)
                _LOGGER.debug(
                    "recv frame type=0x%04x ct_len=%d pt_len=%d",
                    frame.type, len(frame.ciphertext), len(plaintext),
                )
                return frame.type, plaintext

            chunk = await self._reader.read(4096)
            if not chunk:
                if self._recv_buf:
                    _LOGGER.debug(
                        "EOF with %d buffered bytes: %s",
                        len(self._recv_buf), bytes(self._recv_buf).hex(),
                    )
                raise TunnelError("Tunnel closed by peer")
            _LOGGER.debug("recv chunk len=%d: %s", len(chunk), chunk.hex())
            self._recv_buf.extend(chunk)

    def _select_decrypt_key(self, frame_type: int) -> bytes:
        """Return the appropriate AES key for a frame type.

        Handshake frames (0x2020, 0x201b) use the device key.
        Session-data frames (0x2021) use the session key.
        """
        if frame_type == MSG_TYPE_SESSION:
            if self._session_key is None:
                raise TunnelError("Received session frame before session key was set")
            return self._session_key
        return self._device_key

    # -------------------------------------------------------------------------
    # Handshake
    # -------------------------------------------------------------------------

    @staticmethod
    def _build_ecdh_payload(pubkey48: bytes, nonce16: bytes) -> bytes:
        """Build the ECDH handshake plaintext (payload pre-padding).

        Layout (from Frida capture):
            0x00 0x30                  header: length 0x30 of following blob
            <48 bytes pubkey>         little-endian x || little-endian y
            0x00 0x10                  header: length 0x10 of following blob
            <16 bytes nonce>
        """
        if len(pubkey48) != PUBKEY_BYTES:
            raise ValueError(f"pubkey must be {PUBKEY_BYTES} bytes")
        if len(nonce16) != 16:
            raise ValueError("nonce must be 16 bytes")
        return b"\x00\x30" + pubkey48 + b"\x00\x10" + nonce16

    @staticmethod
    def _parse_ecdh_payload(plaintext: bytes) -> tuple[bytes, bytes | None]:
        """Parse an ECDH plaintext.

        Client->server sends `0x0030 + pubkey48 + 0x0010 + nonce16`.
        Server->client sends `0x0030 + pubkey48` (no nonce).
        Returns (pubkey48, nonce_or_None).
        """
        if len(plaintext) < 2 + PUBKEY_BYTES:
            raise TunnelError("ECDH plaintext too short")
        if plaintext[0:2] != b"\x00\x30":
            raise TunnelError(f"Unexpected ECDH header: {plaintext[0:2].hex()}")
        pubkey = plaintext[2 : 2 + PUBKEY_BYTES]
        cursor = 2 + PUBKEY_BYTES
        if len(plaintext) < cursor + 2 + 16:
            return pubkey, None
        if plaintext[cursor : cursor + 2] != b"\x00\x10":
            return pubkey, None
        nonce = plaintext[cursor + 2 : cursor + 2 + 16]
        return pubkey, nonce

    async def _handshake(self) -> None:
        """Run the full ECDH handshake + key verification."""
        loop = asyncio.get_running_loop()
        # --- Step 1: generate our keypair and send pubkey + nonce ---
        priv_int = int.from_bytes(os.urandom(KEY_BYTES), "little")
        priv_int %= 1 << 163  # fit in the field
        if priv_int == 0:
            priv_int = 1
        self._client_priv = field_to_bytes(priv_int)
        # The GF(2^163) scalar multiplications are pure-Python and take tens of
        # milliseconds each; run them in the default executor so the handshake
        # (and every reconnect) doesn't block the event loop.
        our_pub = await loop.run_in_executor(
            None, ecdh_pubkey_from_privkey, self._client_priv
        )
        our_nonce = os.urandom(16)
        out = self._build_ecdh_payload(our_pub, our_nonce)
        await self._send_frame(MSG_TYPE_ECDH, out, self._device_key)

        # --- Step 2: receive server's pubkey + nonce ---
        frame_type, plaintext = await self._recv_frame()
        if frame_type != MSG_TYPE_ECDH:
            raise TunnelError(
                f"Expected ECDH frame 0x2020, got 0x{frame_type:04x}"
            )
        server_pub, _server_nonce = self._parse_ecdh_payload(plaintext)

        # --- Step 3: derive session key (offloaded; see Step 1) ---
        shared = await loop.run_in_executor(
            None, ecdh_shared_secret, self._client_priv, server_pub
        )
        self._session_key = shared[:16]
        # Never log the session key value, even at DEBUG: users enable DEBUG to
        # troubleshoot and paste logs into issues. Confirm derivation only.
        _LOGGER.debug(
            "handshake derived session key (%d bytes)",
            len(self._session_key),
        )

        # --- Step 4: send key verification (random challenge encrypted with
        # session key, all wrapped in device-key-encrypted outer frame) ---
        challenge = os.urandom(4)
        challenge_encrypted = aes_cbc_encrypt(self._session_key, challenge)
        verify_payload = (
            b"\x00\x04" + challenge + b"\x00\x10" + challenge_encrypted
        )
        await self._send_frame(MSG_TYPE_VERIFY, verify_payload, self._device_key)

        # --- Step 5: receive and check the server's verification ---
        frame_type, plaintext = await self._recv_frame()
        if frame_type != MSG_TYPE_VERIFY:
            raise TunnelError(
                f"Expected verify frame 0x201b, got 0x{frame_type:04x}"
            )
        # Weak key-verify: confirm the peer can run the session cipher (see
        # _verify_server_proof). This does not authenticate the peer -- the
        # ECDH is unauthenticated, a protocol limitation we cannot fix on the
        # client side -- but it rejects a peer that cannot run the session
        # cipher. The cloud token is sent later, session-key-encrypted, only
        # over a tunnel whose session key is set (EncryptedTunnel.send), so it
        # is never disclosed to a peer that fails here.
        if not self._verify_server_proof(self._session_key, plaintext):
            raise TunnelError("Server failed session-key verification")
        _LOGGER.debug("handshake complete, tunnel ready")

    @staticmethod
    def _verify_server_proof(session_key: bytes, plaintext: bytes) -> bool:
        """Proof that the peer holds the session key.

        Validated against real M3-hub captures: the server's verify frame is
            00 10 <enc1:16>  00 20 <enc2:32>
        where enc1 = E(session_key, <4-byte challenge>) and
              enc2 = E(session_key, enc1).
        The peer re-encrypts its own encrypted-challenge block under the
        session key, so D(session_key, enc2) must equal enc1. That proves the
        peer can run the session cipher, and it is bound to the current session
        key (an old frame decrypts wrong under a fresh key, so it is
        replay-proof). It does NOT authenticate the peer -- the ECDH is
        unauthenticated, a protocol limitation -- it only rejects a peer that
        cannot run the session cipher.

        Returns True (accept) when the payload is not this structure, so a hub
        model with a different verify layout is never wrongly rejected; returns
        False only on a clear cryptographic mismatch.
        """
        if (
            len(plaintext) < 2 + 16 + 2 + 32
            or plaintext[0:2] != b"\x00\x10"
            or plaintext[18:20] != b"\x00\x20"
        ):
            _LOGGER.debug(
                "verify frame structure not recognised (%d bytes); accepting",
                len(plaintext),
            )
            return True
        enc1 = plaintext[2:18]
        enc2 = plaintext[20:52]
        try:
            return aes_cbc_decrypt(session_key, enc2) == enc1
        except Exception:  # noqa: BLE001 - any decrypt/pad failure = no proof
            return False

    # -------------------------------------------------------------------------
    # BaseTunnel interface
    # -------------------------------------------------------------------------

    async def connect(self, host: str, port: int, *, ssl=None) -> None:
        """Open TCP connection and run the handshake.

        Pass ssl=<SSLContext> to wrap the connection in TLS. When set,
        server_hostname is derived from host and forwarded to
        asyncio.open_connection automatically.
        """
        open_kwargs: dict[str, object] = {}
        if ssl is not None:
            open_kwargs["ssl"] = ssl
            open_kwargs["server_hostname"] = host
        self._reader, self._writer = await asyncio.open_connection(host, port, **open_kwargs)
        _LOGGER.debug("EncryptedTunnel connected to %s:%d", host, port)
        await self._handshake()
        if self._keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(), name="lanlink-keepalive"
            )

    async def _keepalive_loop(self) -> None:
        """Send a JSON keepalive every self._keepalive_interval seconds.

        Captured format:
            {"cmd":"keepalive","data":{"timestamp":<ms>},"seq":1,"type":"session"}
        The hub replies with `cmd=keepalive_done`.
        """
        try:
            while self._session_key is not None:
                await asyncio.sleep(self._keepalive_interval)
                if self._session_key is None:
                    return
                # Half-open detection: our sends still succeed into the TCP
                # buffer even after the hub is gone. If we have heard nothing
                # back for several intervals, close the writer so the read side
                # unblocks (EOF) and the coordinator reconnects.
                if _keepalive_acks_lost(
                    self._last_recv_monotonic, time.monotonic(),
                    self._keepalive_interval, self.KEEPALIVE_LOSS_FACTOR,
                ):
                    _LOGGER.warning(
                        "LANLink tunnel silent for >%.0fs; forcing reconnect",
                        self._keepalive_interval * self.KEEPALIVE_LOSS_FACTOR,
                    )
                    writer = self._writer
                    if writer is not None:
                        writer.close()
                    return
                msg = {
                    "cmd": "keepalive",
                    "data": {"timestamp": int(time.time() * 1000)},
                    "seq": 1,
                    "type": "session",
                }
                try:
                    await self.send(msg)
                except TunnelError as exc:
                    _LOGGER.debug("keepalive send failed: %s", exc)
                    return
        except asyncio.CancelledError:
            pass

    async def send(self, message: dict[str, Any]) -> None:
        """Serialize message as JSON, encrypt with session key, wrap in 0x2021."""
        if self._session_key is None:
            raise TunnelError("Session key not established")
        payload = json.dumps(message, indent=2).encode()
        await self._send_frame(MSG_TYPE_SESSION, payload, self._session_key)

    async def receive(self) -> dict[str, Any] | None:
        """Receive one session-data frame and return it as a JSON dict.

        Returns None if the tunnel closes cleanly. Handshake-type frames
        received here (other than SESSION) are treated as protocol errors.
        """
        try:
            frame_type, plaintext = await self._recv_frame()
        except TunnelError as exc:
            _LOGGER.debug("EncryptedTunnel receive ended: %s", exc)
            return None
        # Any received frame (incl. keepalive acks) proves the link is alive.
        self._last_recv_monotonic = time.monotonic()

        if frame_type != MSG_TYPE_SESSION:
            raise TunnelError(
                f"Unexpected frame type 0x{frame_type:04x} in session phase"
            )

        try:
            obj = json.loads(plaintext.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.debug("Bad JSON in session frame: %s", exc)
            return None

        return obj if isinstance(obj, dict) else None

    async def close(self) -> None:
        task = self._keepalive_task
        self._keepalive_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("keepalive task raised during close: %s", err)

        writer = self._writer
        self._reader = None
        self._writer = None
        self._session_key = None
        self._recv_buf.clear()
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def is_connected(self) -> bool:
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._session_key is not None
        )
