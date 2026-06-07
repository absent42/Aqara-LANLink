"""Camera platform (imperative, not descriptor-driven).

Cameras carry per-instance state (RTSP URL, talk-protocol wiring, go2rtc
bridge) that doesn't fit the small frozen-description model. The platform
delegates entity construction to `Device.async_setup_camera`; devices that
don't expose a camera return an empty list and are skipped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import AqaraEntity
from .ptz import commands as _ptz_cmd

if TYPE_CHECKING:
    from . import AqaraLanLinkRuntimeData
    from .ptz.controller import PtzController


# hass.data sentinel guarding the one-shot PTZ entity-service registration.
# async_register_entity_service raises on a duplicate service name, and
# async_setup_entry runs once per config entry, so a second Aqara hub would
# crash without this guard. Entity services live for the process lifetime, so
# the flag is never reset on unload (a re-add reuses the existing services).
_PTZ_SERVICES_FLAG = f"{DOMAIN}_ptz_services_registered"


def _register_ptz_services(
    hass: HomeAssistant, platform: Any | None = None,
) -> None:
    """Register the single PTZ entity service on the camera platform once.

    One `ptz` service dispatches every action via its `command` field, rather
    than four narrow services. This keeps the surface small and, crucially,
    lets the AlexxIT WebRTC card drive PTZ -- its overlay allows only one
    `service:` key with per-button `data_*` payloads. Per-action granular
    control is still available through the button/number/select entities.

    Idempotent across config entries via the hass.data sentinel. The platform
    defaults to the current platform (resolved lazily, only when registration
    actually needs to happen) so callers can pass an explicit platform in tests.
    """
    if hass.data.get(_PTZ_SERVICES_FLAG):
        return
    hass.data[_PTZ_SERVICES_FLAG] = True
    if platform is None:
        platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "ptz",
        {
            vol.Required("command"): vol.In(list(_ptz_cmd.PTZ_COMMANDS)),
            vol.Optional("value"): cv.string,
            vol.Optional("continuous", default=False): cv.boolean,
            vol.Optional("duration"): vol.Coerce(float),
        },
        "async_ptz",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Ask each device for its camera entities; skip those that return none."""
    data: "AqaraLanLinkRuntimeData" = entry.runtime_data
    cloud = getattr(data, "cloud_client", None)
    token = getattr(data, "cloud_token", "")
    ptz_controllers = getattr(data, "ptz_controllers", {})

    def _wire_ptz(cameras: list["AqaraCameraEntity"], device: Any) -> None:
        """Stash the device's PTZ controller + cloud creds on each camera."""
        controller = ptz_controllers.get(device.did)
        for cam in cameras:
            cam._ptz_controller = controller
            cam._cloud_client = cloud
            cam._cloud_token = token
            cam._did = device.did

    for subentry_id, device in data.devices.items():
        subentry = entry.subentries[subentry_id]
        cameras = await device.async_setup_camera(data.hub, subentry)
        if cameras:
            _wire_ptz(cameras, device)
            async_add_entities(cameras, config_subentry_id=subentry_id)
    self_device = data.self_device
    if self_device is not None:
        cameras = await self_device.async_setup_camera(
            data.hub, self_device.subentry,
        )
        if cameras:
            _wire_ptz(cameras, self_device)
            async_add_entities(cameras)

    # Register the PTZ entity services on this platform (once per process).
    _register_ptz_services(hass)


class AqaraCameraEntity(AqaraEntity, Camera):
    """Stream-source-backed camera entity.

    Not descriptor-driven: the device's `async_setup_camera` factory
    constructs this with whatever stream URL it has resolved
    (RTSP-direct, go2rtc-fronted, etc.). Overriding HA's `stream_source`
    is enough for HA to wire up the stream/preview machinery.
    """

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, hub, device, subentry, stream_source: str, channel: int = 1):
        # Camera has its own `__init__` taking no required args; calling
        # both supers ensures both AqaraEntity-style metadata and
        # Camera's internal state get initialised.
        AqaraEntity.__init__(
            self, hub, device, subentry,
            descriptor=None, unique_id_suffix=f"camera_ch{channel}",
        )
        Camera.__init__(self)
        self._channel = channel
        self._attr_translation_key = f"camera_ch{channel}"
        self._stream_source = stream_source
        # PTZ wiring, populated by async_setup_entry for PTZ-capable cameras.
        # None for non-PTZ cameras; the service handlers raise in that case.
        self._ptz_controller: "PtzController | None" = None
        self._cloud_client: Any = None
        self._cloud_token: str = ""
        self._did: str = ""
        # Cache of the cloud-stored preset list, fetched lazily on first recall.
        self._ptz_presets: list[dict] | None = None

    async def stream_source(self) -> str:
        """Return the resolved stream URL passed at construction."""
        return self._stream_source

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None,
    ) -> bytes | None:
        """Grab a single JPEG frame from the RTSP source via ffmpeg.

        Returns None on failure (no hass yet, or ffmpeg error) rather
        than raising, so a transient stream hiccup never crashes HA's
        snapshot/preview path.
        """
        if self.hass is None:
            return None
        try:
            return await async_get_image(
                self.hass, self._stream_source, width=width, height=height,
            )
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # PTZ entity-service handlers. Each delegates to the per-camera
    # PtzController; a camera with no controller (non-PTZ model) raises a
    # clear error rather than silently no-op'ing.
    # ------------------------------------------------------------------

    def _require_controller(self) -> "PtzController":
        if self._ptz_controller is None:
            raise HomeAssistantError(
                f"Camera {self.entity_id or self._did} does not support PTZ",
            )
        return self._ptz_controller

    async def async_ptz(
        self, command: str, value: str | None = None,
        continuous: bool = False, duration: float | None = None,
    ) -> None:
        """Dispatch a single `ptz` service call to the controller by command.

        Directional commands (up/down/left/right) honour `continuous`/`duration`;
        `zoom_in`/`zoom_out` step by `value` (a magnification delta, default
        `ZOOM_BUTTON_STEP` when omitted); `zoom` sets an absolute magnification
        from `value`; `preset` recalls a saved position by name/id from `value`.
        Bad/missing arguments raise HomeAssistantError.
        """
        controller = self._require_controller()
        if command in _ptz_cmd.NUDGE_ACTIONS:
            await controller.ptz_move(
                command, continuous=continuous, duration=duration,
            )
        elif command == _ptz_cmd.STOP_ACTION:
            await controller.ptz_stop()
        elif command == "zoom_in":
            await controller.zoom_step(self._coerce_step(value))
        elif command == "zoom_out":
            await controller.zoom_step(-self._coerce_step(value))
        elif command == "zoom":
            await controller.ptz_zoom(self._coerce_zoom(value))
        elif command == "preset":
            if not value:
                raise HomeAssistantError("ptz command 'preset' requires 'value'")
            await self._resolve_and_recall(value)
        else:  # pragma: no cover - schema vol.In already rejects others
            raise HomeAssistantError(f"Unknown ptz command: {command!r}")

    @staticmethod
    def _coerce_zoom(value: str | None) -> float:
        """Validate the `value` of a `zoom` command as a magnification float."""
        if value is None:
            raise HomeAssistantError("ptz command 'zoom' requires 'value'")
        try:
            scale = float(value)
        except (TypeError, ValueError) as exc:
            raise HomeAssistantError(
                f"ptz zoom value must be a number, got {value!r}",
            ) from exc
        if not _ptz_cmd.ZOOM_MIN <= scale <= _ptz_cmd.ZOOM_MAX:
            raise HomeAssistantError(
                f"ptz zoom value {scale} out of range "
                f"{_ptz_cmd.ZOOM_MIN}-{_ptz_cmd.ZOOM_MAX}",
            )
        return scale

    @staticmethod
    def _coerce_step(value: str | None) -> float:
        """Validate the `value` of a zoom_in/zoom_out command as a step delta.

        Omitted -> the default ZOOM_BUTTON_STEP. The magnitude is always
        positive (the in/out direction supplies the sign); the controller
        clamps the resulting magnification to the zoom range.
        """
        if value is None:
            return _ptz_cmd.ZOOM_BUTTON_STEP
        try:
            step = float(value)
        except (TypeError, ValueError) as exc:
            raise HomeAssistantError(
                f"ptz zoom step must be a number, got {value!r}",
            ) from exc
        if step <= 0:
            raise HomeAssistantError(
                f"ptz zoom step must be positive, got {step}",
            )
        return step

    async def _resolve_and_recall(self, preset: str) -> None:
        """Resolve a preset name/id to its opaque position string and recall it.

        Presets live in the cloud account (not the camera), so the position
        token is fetched via query_ptz_positions and matched against the
        preset's name or stringified id. Raises HomeAssistantError when the
        controller is missing or the preset cannot be resolved.
        """
        controller = self._require_controller()
        if self._cloud_client is None:
            raise HomeAssistantError("Cloud client unavailable for preset recall")
        if self._ptz_presets is None:
            self._ptz_presets = await self._cloud_client.query_ptz_positions(
                self._cloud_token, self._did,
            )
        match = self._match_preset(preset)
        if match is None:
            # Self-heal: a preset added in the Aqara app after the cache was
            # last fetched is unrecallable until reload otherwise. Refetch once
            # and retry before giving up.
            self._ptz_presets = await self._cloud_client.query_ptz_positions(
                self._cloud_token, self._did,
            )
            match = self._match_preset(preset)
        if match is None or not match.get("position"):
            raise HomeAssistantError(f"Unknown PTZ preset: {preset!r}")
        await controller.goto_preset(match["position"])

    def _match_preset(self, preset: str) -> dict | None:
        """Match a preset name/id against the cached position list, or None."""
        for entry in self._ptz_presets or ():
            if preset in (entry.get("name"), str(entry.get("id"))):
                return entry
        return None


__all__ = ["AqaraCameraEntity", "async_setup_entry"]
