"""Per-camera warm-session manager for local PTZ control.

Owns one warm :class:`PtzSession` per camera: lazy connect + AUTH on first
command, in-memory creds, idle teardown, per-camera command serialization,
optimistic zoom tracking, and one re-bootstrap retry on AUTH failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from homeassistant.exceptions import HomeAssistantError

from . import commands as c
from .bootstrap import bootstrap as _real_bootstrap
from .session import PptzTimeout, PtzSession

_LOGGER = logging.getLogger(__name__)


class PtzController:
    def __init__(self, *, cloud_client, token, user_id, did, camera_ip_provider, features,
                 bootstrap_fn=None, session_factory=None, idle_timeout=180.0):
        self._cloud = cloud_client
        self._token = token
        self._user_id = user_id
        self._did = did
        self.features = frozenset(features)
        # camera_ip_provider: async () -> str. The camera LAN IP is resolved
        # lazily (it is not known at integration-setup time - see Task 6.1), so
        # we await it on each (re)connect rather than holding a static IP.
        self._ip_provider = camera_ip_provider
        self._bootstrap_fn = bootstrap_fn or _real_bootstrap
        self._session_factory = session_factory or (lambda key, did_blob, ip: PtzSession(key, did_blob, ip))
        self._idle_timeout = idle_timeout
        self._creds = None
        self._session = None
        self._lock = asyncio.Lock()
        self._idle_handle = None
        self._idle_task = None
        self._reconnect_task = None
        self._closing = False
        self._current_zoom = c.ZOOM_MIN

    async def _ensure_creds(self):
        if self._creds is None:
            self._creds = await self._bootstrap_fn(self._cloud, self._token, self._user_id, self._did)
        return self._creds

    async def _connect_and_auth(self, creds):
        """Build, connect and AUTH a session. Return it on success, else None.

        On any non-success AUTH reply the session is closed and None returned;
        the caller decides whether to retry with fresh creds.
        """
        ip = await self._ip_provider()
        if not ip:
            raise HomeAssistantError(
                "PTZ unavailable: could not resolve the camera's LAN IP")
        _LOGGER.debug("PTZ: connecting to camera %s (did=%s)", ip, self._did)
        sess = self._session_factory(creds.cipher_key, creds.did_blob, ip)
        await sess.connect()
        await sess.send_ioctl(c.AUTH_IOTYPE, {
            "app_public_key": creds.app_pub_hex, "app_sign": creds.sign,
            "device_id": self._did, "timestamp": creds.time})
        reply = await sess.recv_ioctl(timeout=5.0)
        if not reply or reply[0] != c.AUTH_REPLY_IOTYPE or reply[2].get("code") != 0:
            _LOGGER.debug("PTZ: AUTH rejected (reply=%s)", reply)
            await sess.close()
            return None
        _LOGGER.debug("PTZ: session established (peer=%s)",
                      getattr(sess, "peer", None))
        return sess

    async def _ensure_session(self):
        if self._session is not None and getattr(self._session, "ready", False):
            _LOGGER.debug("PTZ: reusing warm session (peer=%s)",
                          getattr(self._session, "peer", None))
            return self._session
        _LOGGER.debug("PTZ: no live session, establishing a new one")
        creds = await self._ensure_creds()
        sess = await self._connect_and_auth(creds)
        if sess is None:
            # one retry with fresh creds (stale sign)
            self._creds = None
            creds = await self._ensure_creds()
            sess = await self._connect_and_auth(creds)
            if sess is None:
                raise HomeAssistantError("PTZ authentication failed")
        self._session = sess
        return sess

    async def send_command(self, iotype, body, *, recycle_after=False):
        async with self._lock:
            try:
                sess = await self._ensure_session()
                _LOGGER.debug("PTZ: send iotype=%s body=%s", iotype, body)
                await sess.send_ioctl(iotype, body)
                ack = await sess.recv_ioctl(timeout=3.0)
                if ack is None:
                    _LOGGER.warning(
                        "PTZ: iotype=%s got NO ACK within 3s -- camera silent; "
                        "the warm session may be stale. It will be rebuilt on "
                        "the next command / idle teardown.", iotype)
                else:
                    _LOGGER.debug("PTZ: iotype=%s ack=%s", iotype, ack)
                if recycle_after:
                    # A 4140 preset recall poisons the camera's P2P session: the
                    # camera keeps ACKing (4097 success) but STOPS executing
                    # commands on this session until a fresh one is established
                    # (verified live -- recovery only came on the idle teardown's
                    # reconnect). The poison is undetectable from the ack, so we
                    # proactively recycle the session after a preset; the next
                    # command reconnects (~1.5s) instead of silently failing for
                    # up to idle_timeout. The preset itself already executed.
                    _LOGGER.debug("PTZ: recycling session after iotype=%s", iotype)
                    await self._discard_session()
                    # Pre-warm a fresh session in the background. The user is
                    # typically looking at the recalled view for a few seconds
                    # before touching PTZ again, so the ~1.5s reconnect completes
                    # during that pause and the next move is instant. If it
                    # fails (camera busy mid-move), the next command reconnects.
                    self._schedule_eager_reconnect()
                else:
                    self._reset_idle()
                return ack
            except (PptzTimeout, OSError) as exc:
                _LOGGER.warning("PTZ command failed (iotype=%s): %s", iotype, exc)
                raise HomeAssistantError(f"PTZ command failed: {exc}") from exc

    async def _discard_session(self):
        """Close + drop the current session (and cancel the idle timer) so the
        next command establishes a fresh one. Caller must hold self._lock."""
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _schedule_eager_reconnect(self):
        """Pre-warm a fresh session in the background (after a preset recycle)."""
        if self._closing:
            return
        # Skip if a pre-warm is already in flight; if the previous one finished we
        # overwrite the slot. That is safe ONLY because _eager_reconnect swallows
        # its own non-cancel exceptions -- a completed task therefore never holds
        # an unretrieved exception. Keep that contract if this is ever refactored.
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self._eager_reconnect())

    async def _eager_reconnect(self):
        try:
            async with self._lock:
                if self._closing or self._session is not None:
                    return
                _LOGGER.debug("PTZ: eager reconnect (pre-warming after preset)")
                await self._ensure_session()
                self._reset_idle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "PTZ: eager reconnect failed (next command will retry): %s", exc)

    def _reset_idle(self):
        if self._idle_handle:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()

        def _fire():
            self._idle_task = asyncio.ensure_future(self._idle_teardown())

        self._idle_handle = loop.call_later(self._idle_timeout, _fire)

    async def _idle_teardown(self):
        try:
            if self._closing:
                return
            async with self._lock:
                if self._session is not None:
                    _LOGGER.debug(
                        "PTZ: idle teardown after %.0fs -- closing session",
                        self._idle_timeout)
                    await self._session.close()
                    self._session = None
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("PTZ idle teardown failed", exc_info=True)

    async def async_shutdown(self):
        self._closing = True
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        for attr in ("_idle_task", "_reconnect_task"):
            task = getattr(self, attr)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            setattr(self, attr, None)
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def current_zoom(self):
        return self._current_zoom

    async def ptz_move(self, direction, *, continuous=False, duration=None):
        if c.PAN_TILT not in self.features:
            raise HomeAssistantError("Pan/tilt is not supported on this camera")
        if not continuous:
            await self.send_command(c.PTZ_IOTYPE, {"action": direction})
            return
        await self.send_command(c.PTZ_IOTYPE, {"action": f"{direction}_always"})
        if duration is not None:
            await asyncio.sleep(duration)
            await self.ptz_stop()

    async def ptz_stop(self):
        await self.send_command(c.PTZ_IOTYPE, {"action": c.STOP_ACTION})

    async def ptz_zoom(self, scale):
        if c.ZOOM not in self.features:
            raise HomeAssistantError("Zoom is not supported on this camera")
        scale = max(c.ZOOM_MIN, min(c.ZOOM_MAX, float(scale)))
        await self.send_command(c.ZOOM_IOTYPE, c.zoom_body(scale))
        self._current_zoom = round(scale, 1)

    async def zoom_step(self, delta):
        await self.ptz_zoom(self._current_zoom + delta)

    async def goto_preset(self, position):
        if c.PRESETS not in self.features:
            raise HomeAssistantError("Presets are not supported on this camera")
        # recycle_after: a local 4140 recall leaves the session acked-but-dead;
        # rebuild on the next command rather than waiting out the idle timeout.
        await self.send_command(
            c.POSITION_IOTYPE, c.position_body(position), recycle_after=True)
