"""LANLink session client.

Manages the connection lifecycle for a LANLink local TCP session with an
Aqara device. Provides checkin, attribute read/write, and async report
handling over a BaseTunnel transport.

The client uses a single listen() loop that owns tunnel.receive() and
dispatches each frame either to a seq-keyed pending future (for
request/response correlation) or to the on_report callback (for async
report frames). Callers must ensure listen() is running as a background
task before invoking async_send_and_await.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from collections.abc import Callable

from .protocol import (
    LANEntity,
    LANReport,
    LANLINK_CMD_REPORT,
    build_checkin,
    build_read,
    build_write,
)
from .tunnel import BaseTunnel

_LOGGER = logging.getLogger(__name__)


class LANLinkClient:
    """Session client for the Aqara LANLink local protocol.

    Parameters
    ----------
    tunnel:    A BaseTunnel instance (connected or to be connected externally).
    device_id: The target device DID.
    user_id:   The Aqara cloud user ID for session check-in.
    model:     The device model string (e.g. 'lumi.camera.agl013').
    """

    def __init__(
        self,
        tunnel: BaseTunnel,
        device_id: str,
        user_id: str,
        model: str,
        token: str = "",
    ) -> None:
        self._tunnel = tunnel
        self.device_id = device_id
        self.user_id = user_id
        self.model = model
        self.token = token
        self._seq: int = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.on_report: Callable[[LANReport], None] | None = None
        # Optional callback invoked for async (non-pending-future, non-report)
        # frames. Used by the coordinator to capture spontaneous hub pushes
        # such as `cmd=topology` without coupling the session client to the
        # semantics of those frames.
        self.on_async: Callable[[dict[str, Any]], None] | None = None

    # -------------------------------------------------------------------------
    # Sequence counter
    # -------------------------------------------------------------------------

    def next_seq(self) -> int:
        """Allocate the next outgoing sequence number."""
        self._seq += 1
        return self._seq

    # Kept for backwards-compatible internal callers.
    def _next_seq(self) -> int:
        return self.next_seq()

    # -------------------------------------------------------------------------
    # Send-and-await (seq-keyed futures; requires listen() running)
    # -------------------------------------------------------------------------

    async def async_send_and_await(
        self, message: dict[str, Any], timeout: float = 10.0
    ) -> dict[str, Any]:
        """Send *message* and await the response with matching seq.

        Safe to call concurrently with listen() and with other
        async_send_and_await invocations. The caller is expected to have
        populated ``message["seq"]`` with a value from ``next_seq()``.

        Raises asyncio.TimeoutError if no matching response arrives within
        *timeout* seconds, or ConnectionError if the tunnel closes while we
        are waiting.
        """
        seq = message["seq"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        if seq in self._pending:
            # Programming error: duplicate seq reuse.
            raise RuntimeError(f"duplicate seq {seq} already pending")
        self._pending[seq] = future
        try:
            await self._tunnel.send(message)
            return await asyncio.wait_for(future, timeout)
        finally:
            # Cover every exit path: success, timeout, cancellation,
            # tunnel-closed (set_exception from listen). Drop the entry so
            # the seq slot is freed for future use.
            self._pending.pop(seq, None)

    # -------------------------------------------------------------------------
    # Async report dispatch
    # -------------------------------------------------------------------------

    def _dispatch_async(self, raw: dict[str, Any]) -> None:
        """Dispatch a raw message dict as an async notification.

        Handles report messages by constructing a LANReport and calling
        the on_report callback. Non-report frames are forwarded to the
        ``on_async`` callback (if set) so the coordinator can capture
        hub-level signals such as topology pushes; if no async callback
        is registered they are logged so we don't silently drop signals
        like errors or kick-outs.
        """
        cmd = raw.get("cmd")
        if cmd != LANLINK_CMD_REPORT:
            if self.on_async is not None:
                try:
                    self.on_async(raw)
                except Exception:
                    _LOGGER.exception("Exception in on_async callback")
            else:
                _LOGGER.info(
                    "LANLink non-report message cmd=%s type=%s seq=%s data=%s",
                    cmd, raw.get("type"), raw.get("seq"), raw.get("data"),
                )
            return
        if self.on_report is None:
            return
        # Build a minimal LANEntity so LANReport.from_entity can validate it.
        entity = LANEntity(
            seq=raw.get("seq", 0),
            type=raw.get("type", ""),
            cmd=raw.get("cmd", ""),
            data=raw.get("data"),
        )
        report = LANReport.from_entity(entity)
        if report is not None:
            try:
                self.on_report(report)
            except Exception:
                _LOGGER.exception("Exception in on_report callback")

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def checkin(self) -> bool:
        """Perform the session check-in handshake.

        Returns True if the hub accepted the session (`code == 0` in the
        checkin_done response), False otherwise. The real wire uses `code`,
        not `result` -- legacy callers still passing user_id as the token
        will get a 2 (auth fail) from the hub.
        """
        seq = self.next_seq()
        msg = build_checkin(seq, self.user_id, self.device_id, self.token)
        response = await self.async_send_and_await(msg)
        data = response.get("data") or {}
        return data.get("code", -1) == 0

    async def read_attrs(self, attrs: list[str]) -> dict[str, Any]:
        """Read one or more device attributes.

        Parameters
        ----------
        attrs: List of attribute name strings to read.

        Returns the response data dict from the device.
        """
        seq = self.next_seq()
        msg = build_read(seq, self.device_id, self.model, attrs)
        response = await self.async_send_and_await(msg)
        return response.get("data") or {}

    async def write_attrs(self, attrs: dict[str, Any]) -> None:
        """Write one or more device attributes.

        Parameters
        ----------
        attrs: Mapping of attribute name to the value to write.
        """
        seq = self.next_seq()
        msg = build_write(seq, self.device_id, self.model, attrs)
        await self.async_send_and_await(msg)

    async def listen(self) -> None:
        """Run the single receive loop until the tunnel closes.

        Intended for use as a background task. Owns tunnel.receive() so
        concurrent async_send_and_await calls never race for frames.
        Each incoming frame is dispatched as follows:

        * if its ``seq`` matches a pending future, pop and resolve it;
        * else if it is a report frame, forward to ``on_report``;
        * else log-and-discard (unsolicited non-report, e.g. error/kick).

        On tunnel EOF, any still-pending futures are resolved with a
        ConnectionError so their callers wake up instead of waiting for
        their timeout.
        """
        try:
            while True:
                raw = await self._tunnel.receive()
                if raw is None:
                    _LOGGER.debug("LANLinkClient: tunnel closed, exiting listen loop")
                    break
                # Full decoded-frame dump. Captures EVERY decrypted message,
                # including request-responses (read_done/write_done) that are
                # otherwise consumed silently by their pending future -- enable
                # DEBUG on this logger to observe all tunnel content (e.g. when
                # hunting an uncatalogued push path).
                _LOGGER.debug("LANLink frame recv: %s", raw)
                seq = raw.get("seq")
                future = self._pending.get(seq) if seq is not None else None
                if future is not None and not future.done():
                    # Pop first so a slow callback on done() can't race a
                    # second frame with the same seq.
                    self._pending.pop(seq, None)
                    future.set_result(raw)
                    continue
                self._dispatch_async(raw)
        except Exception as exc:
            _LOGGER.exception("LANLinkClient: unhandled error in listen loop")
            self._fail_pending(exc)
            return
        # Normal EOF path: wake any still-pending callers.
        self._fail_pending(ConnectionError("Tunnel closed"))

    def _fail_pending(self, exc: BaseException) -> None:
        """Resolve every pending future with *exc* (used on tunnel close)."""
        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)
