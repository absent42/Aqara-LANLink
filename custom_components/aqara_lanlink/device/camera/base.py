"""Shared base class for all Aqara camera and doorbell device models.

Carries the imperative infrastructure common to every camera model:

    - EXTRA_CONFIG_SCHEMA for camera IP + RTSP credentials.
    - async_prefill_extras: best-effort RTSP URL pre-fill via hub relay.
    - async_setup_camera: builds AqaraCameraEntity + go2rtc registration.
    - async_setup / async_unload: talk client lifecycle, service registry.
    - play_audio_file service wiring via a per-hass _camera_devices map.

Model subclasses declare MODEL and EVENTS, optionally override
RTSP_URL_TRAIT_PATH if their camera deviates from the universal default,
and add any model-specific overrides; they do not re-implement the
imperative methods.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Any

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..base import Device, SelfDeviceContext, resolve_subentry_metadata
from ..attrs import AttrSpec
from ...camera import AqaraCameraEntity
from ...const import DOMAIN
from . import go2rtc as _go2rtc
from .const import CHANNELS
from .number_entities import DetectionClearDelayNumber
from .protocol import extract_adts_frames
from .rtsp import parse_camera_rtsp_url
from .talk import AqaraLanTalkClient

_LOGGER = logging.getLogger(__name__)

# Per-hass tracker so the integration knows when to remove the shared
# play_audio_file service. Keyed by DID -> CameraDevice. Lives under
# hass.data[DOMAIN][_CAMERA_REGISTRY_KEY]. One shared service spans every
# camera model, so all cameras register here regardless of model.
_CAMERA_REGISTRY_KEY = "_camera_devices"

_PLAY_AUDIO_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("file_path"): str,
    }
)

# AAC frame pacing: 1024 samples per frame at 16kHz -> ~64ms wall-clock.
_AAC_FRAME_SAMPLES = 1024
_AAC_FRAME_SLEEP_S = 0.064


class CameraDevice(Device):
    """Base class for Aqara camera and doorbell device models.

    Subclasses must declare MODEL. They may override RTSP_URL_TRAIT_PATH if
    their camera deviates from the universal default, and may also declare
    EVENTS, AUGMENT_AUTO_DERIVE, and any other model-specific class attributes.
    """

    # Subclasses override this with the wire path for the camera's RTSP URL
    # trait. An empty string disables the relay/self-device read. Every known
    # Aqara camera model uses 7.136.20368, so it is the base default.
    RTSP_URL_TRAIT_PATH: str = "7.136.20368"

    EXTRA_CONFIG_SCHEMA = vol.Schema(
        {
            vol.Optional("camera_ip"): str,
            vol.Optional("rtsp_username"): str,
            vol.Optional("rtsp_password"): str,
            vol.Optional("backchannel_channel", default=1): vol.All(
                vol.Coerce(int), vol.In((1, 2, 3)),
            ),
        }
    )

    @classmethod
    async def async_prefill_extras(
        cls, hass: Any, device_record: dict, hub_coordinator: Any,
    ) -> dict | None:
        """Best-effort cloud read of the RTSP URL trait to pre-fill the extras form.

        Reads the camera's RTSP URL trait via the cloud's qlink/trait/read
        endpoint (the same path setup uses for trait seeding) and parses
        out the IP + RTSP credentials. Returns a dict of form field
        defaults on success, or None on any failure (config flow then
        renders a blank manual form). Never raises.

        Why cloud and not LANLink relay: Aqara hubs (M3, etc.) silently
        time out the LANLink trait-read message for CameraRTSPURL even
        though the V3 spec lists it as readable. The cloud round-trip
        reliably returns the value because the cloud reads it via a
        different RPC the hub does answer.

        Each failure mode logs at WARNING so the integration log surfaces
        the actual cause rather than silently rendering a blank form.
        """
        did = device_record.get("did", "")
        model = device_record.get("model", "")
        if not did:
            _LOGGER.warning(
                "async_prefill_extras: no did in device_record; "
                "form will render blank",
            )
            return None
        if hub_coordinator is None:
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s -- hub_coordinator is None; "
                "form will render blank",
                did, model,
            )
            return None
        from custom_components.aqara_lanlink.device import catalog
        known_paths = set(catalog.all_traits_for_model(model).keys())
        known_paths |= catalog.dropped_paths_for_model(model)
        synthetic_candidates = cls._synthetic_rtsp_candidates(model)
        # Keep only candidates the catalogue actually knows about. Some camera
        # models (e.g. G100/agl005) have a Camera endpoint but no CameraRTSPURL
        # trait, so the synthetic <ep>.136.20368 isn't in the spec -> blank form.
        candidate_paths = [p for p in synthetic_candidates if p in known_paths]
        if not candidate_paths:
            _LOGGER.info(
                "async_prefill_extras: did=%s model=%s -- catalogue exposes "
                "no CameraRTSPURL trait for this model (synthetic candidates=%s "
                "not in known paths). Manual entry required; form will render blank.",
                did, model, synthetic_candidates,
            )
            return None
        cloud = getattr(hub_coordinator, "cloud_client", None)
        token = getattr(hub_coordinator, "token", "")
        if cloud is None or not token:
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s -- "
                "hub_coordinator missing cloud_client (%s) or token (%s); "
                "form will render blank",
                did, model, cloud is None, not token,
            )
            return None
        try:
            traits = await cloud.query_device_traits(token, did, candidate_paths)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s candidate_paths=%s -- "
                "cloud query_device_traits raised %s; form renders blank",
                did, model, candidate_paths, exc,
                exc_info=True,
            )
            return None
        raw, trait_path = cls._extract_rtsp_value(traits, candidate_paths)
        if raw is None:
            returned = [
                (e.get("path"), e.get("value"))
                for e in (traits or ()) if isinstance(e, dict)
            ]
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s candidate_paths=%s -- "
                "cloud returned no non-empty value for any candidate "
                "(returned=%s); form renders blank. Check "
                "docs/dev/generated/catalogue_full/%s.json for the actual "
                "CameraRTSPURL wire path.",
                did, model, candidate_paths, returned, model,
            )
            return None
        return cls._build_rtsp_prefill(raw, trait_path, did, model)

    @classmethod
    def _synthetic_rtsp_candidates(cls, model: str) -> list[str]:
        """Unfiltered CameraRTSPURL wire-path candidates for ``model``.

        Function 136 is the Camera cluster, trait 20368 is CameraRTSPURL; the
        endpoint number varies per model (G400/agl013 is ep 7/8, most others
        ep 2), so offer the configured RTSP_URL_TRAIT_PATH plus
        ``<ep>.136.20368`` for every Camera endpoint.
        """
        from custom_components.aqara_lanlink.device import catalog
        candidates: list[str] = []
        if cls.RTSP_URL_TRAIT_PATH:
            candidates.append(cls.RTSP_URL_TRAIT_PATH)
        for ep_id, ep_meta in catalog.endpoints_for_model(model).items():
            if ep_meta.get("deviceType") != "Camera":
                continue
            wp = f"{ep_id}.136.20368"
            if wp not in candidates:
                candidates.append(wp)
        return candidates

    @staticmethod
    def _extract_rtsp_value(
        traits: Any, candidate_paths: list[str],
    ) -> tuple[Any, str | None]:
        """First (value, path) among ``traits`` whose path is a candidate and
        whose value is non-empty, else (None, None)."""
        for entry in traits or ():
            if not isinstance(entry, dict):
                continue
            entry_path = entry.get("path")
            entry_value = entry.get("value")
            if entry_path in candidate_paths and entry_value:
                return entry_value, entry_path
        return None, None

    @staticmethod
    def _build_rtsp_prefill(
        raw: Any, trait_path: str | None, did: str, model: str,
    ) -> dict | None:
        """Parse a CameraRTSPURL value into extras-form defaults, or None."""
        details = parse_camera_rtsp_url(raw if isinstance(raw, str) else str(raw))
        if details is None:
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s trait_path=%s -- "
                "parse_camera_rtsp_url returned None for raw=%r; form renders blank",
                did, model, trait_path, raw,
            )
            return None
        if not details.camera_ip:
            _LOGGER.warning(
                "async_prefill_extras: did=%s model=%s trait_path=%s -- "
                "parse_camera_rtsp_url succeeded but extracted no camera_ip "
                "from raw=%r; form renders blank",
                did, model, trait_path, raw,
            )
            return None
        prefill: dict = {"camera_ip": details.camera_ip}
        if details.username:
            prefill["rtsp_username"] = details.username
        if details.password:
            prefill["rtsp_password"] = details.password
        _LOGGER.info(
            "async_prefill_extras: did=%s model=%s -- pre-filled form with "
            "camera_ip=%s rtsp_username=%s",
            did, model, details.camera_ip,
            "<set>" if details.username else "<none>",
        )
        return prefill

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Set when async_setup_camera registers a stream so async_unload
        # can call go2rtc.remove_stream with the same name.
        self.go2rtc_stream_name: str | None = None
        # Set in async_setup, torn down in async_unload.
        self._talk_client: AqaraLanTalkClient | None = None
        self._hass: Any = None
        # Set by async_setup_extra_numbers; read by resolve_auto_clear_seconds.
        self._detection_delay: DetectionClearDelayNumber | None = None
        # Resolved once, lazily, by _ensure_camera_endpoint.
        self._camera_ip: str | None = None
        self._rtsp_username: str | None = None
        self._rtsp_password: str | None = None
        self._backchannel_channel: int = 1
        self._endpoint_resolved: bool = False

    async def async_setup_extra_numbers(
        self, hub: Any, subentry: Any,
    ) -> list[Any]:
        """Build the per-camera detection-clear-delay number entity.

        Created only when the device has at least one auto-clearing binary
        sensor; otherwise the number would be an inert config control.
        """
        if not any(
            getattr(d, "auto_clear_seconds", None) for d in self.descriptors
        ):
            return []
        if self._detection_delay is not None:
            return [self._detection_delay]
        self._detection_delay = DetectionClearDelayNumber(hub, self, subentry)
        return [self._detection_delay]

    async def _resolve_camera_endpoint(
        self, coordinator: Any, subentry: Any,
    ) -> tuple[str | None, str | None, str | None]:
        """Resolve (camera_ip, rtsp_username, rtsp_password) for this camera.

        Sub-devices, picker devices, and an entry hub with options set carry
        the values in subentry.data. An entry hub with no configured IP reads
        its RTSP URL trait live via the coordinator. Best-effort: any failure
        resolves to (None, None, None) with no exception raised.
        """
        camera_ip = subentry.data.get("camera_ip")
        if camera_ip:
            return (
                camera_ip,
                subentry.data.get("rtsp_username"),
                subentry.data.get("rtsp_password"),
            )
        if not isinstance(subentry, SelfDeviceContext):
            return (None, None, None)
        if not self.RTSP_URL_TRAIT_PATH or coordinator is None:
            return (None, None, None)
        try:
            # The entry hub reads its OWN trait through its own coordinator,
            # so self.MODEL is passed (unlike the sub-device relay read in
            # async_prefill_extras, which passes "").
            result = await coordinator.async_read(
                self.did, self.MODEL, [AttrSpec(name=self.RTSP_URL_TRAIT_PATH)],
            )
        except Exception:  # noqa: BLE001
            return (None, None, None)
        raw = (
            result.get(self.RTSP_URL_TRAIT_PATH)
            if isinstance(result, dict) else None
        )
        details = parse_camera_rtsp_url(raw)
        if details is None or not details.camera_ip:
            return (None, None, None)
        return (details.camera_ip, details.username, details.password)

    async def _ensure_camera_endpoint(
        self, coordinator: Any, subentry: Any,
    ) -> None:
        """Resolve and cache the camera endpoint once, on first use."""
        if self._endpoint_resolved:
            return
        (
            self._camera_ip,
            self._rtsp_username,
            self._rtsp_password,
        ) = await self._resolve_camera_endpoint(coordinator, subentry)
        try:
            self._backchannel_channel = int(
                subentry.data.get("backchannel_channel", 1)
            )
        except (TypeError, ValueError):
            self._backchannel_channel = 1
        self._endpoint_resolved = True

    def resolve_auto_clear_seconds(self, descriptor: Any) -> float | None:
        """Return the live, user-tuned detection-clear delay.

        Falls back to the descriptor's baked value when the number entity
        is not set up yet (platform setup order is not guaranteed).
        """
        delay = self._detection_delay
        if delay is not None and delay.native_value is not None:
            return float(delay.native_value)
        return getattr(descriptor, "auto_clear_seconds", None)

    async def async_resolve_camera_ip(
        self, coordinator: Any, subentry: Any,
    ) -> str | None:
        """Resolve (lazily, once) and return the camera's LAN IP for PTZ P2P.

        Reuses the same endpoint resolution the camera platform performs, so
        the PTZ controller's `camera_ip_provider` can defer the IP lookup to
        first command. The hub's own camera (self_device) has no synchronous
        IP at setup -- it comes from an async RTSP-URL trait read -- so this
        must be awaited, not read from a config field.

        Returns the resolved IP string, or None when no endpoint could be
        resolved (mirrors `_resolve_camera_endpoint`'s best-effort contract).
        """
        await self._ensure_camera_endpoint(coordinator, subentry)
        return self._camera_ip

    async def async_setup_camera(
        self, coordinator: Any, subentry: Any,
    ) -> list[AqaraCameraEntity]:
        """Build one AqaraCameraEntity per RTSP channel; register go2rtc.

        Three camera entities (high/medium/low quality) stream direct RTSP.
        A single go2rtc stream, on the configured backchannel channel, adds
        the WebRTC two-way-audio backchannel. go2rtc registration is
        best-effort: failures log and the entities are returned anyway.
        """
        await self._ensure_camera_endpoint(coordinator, subentry)
        camera_ip = self._camera_ip
        if not camera_ip:
            _LOGGER.debug(
                "Camera %s: no camera_ip resolved; skipping camera entities",
                self.did,
            )
            return []
        username = self._rtsp_username or ""
        password = self._rtsp_password or ""

        cameras = [
            AqaraCameraEntity(
                coordinator, self, subentry,
                stream_source=_go2rtc.build_rtsp_source(
                    camera_ip, username, password, channel=channel,
                ),
                channel=channel,
            )
            for channel in CHANNELS
        ]

        hass = getattr(coordinator, "hass", None)
        if hass is None:
            _LOGGER.debug(
                "Camera %s: skipping go2rtc registration (no hass on coordinator)",
                camera_ip,
            )
            return cameras

        stream_name = f"aqara_lanlink_{camera_ip.replace('.', '_')}"
        self.go2rtc_stream_name = stream_name

        rtsp_source = _go2rtc.build_rtsp_source(
            camera_ip, username, password, channel=self._backchannel_channel,
        )
        # ffmpeg source references the stream by name and re-emits it with
        # video passed through unchanged (video=copy avoids re-encoding),
        # AAC preserved (audio=copy keeps MSE compatibility) plus an opus
        # mix (WebRTC compatibility).
        ffmpeg_source = f"ffmpeg:{stream_name}#video=copy#audio=opus#audio=copy"
        exec_source = _go2rtc.build_exec_source(hass.config.config_dir, camera_ip)

        await hass.async_add_executor_job(
            _go2rtc.register_stream,
            hass.config.config_dir,
            stream_name,
            [rtsp_source, ffmpeg_source, exec_source],
        )
        return cameras

    async def async_setup(self, coordinator: Any, subentry: Any) -> None:
        """Wire imperative side-channels: talk client + services.

        Called once per camera subentry. Creates the per-device talk client,
        registers the device under hass.data[DOMAIN][_CAMERA_REGISTRY_KEY],
        and (if not already done by a sibling camera) registers the shared
        play_audio_file service.
        """
        hass = getattr(coordinator, "hass", None)
        self._hass = hass

        await self._ensure_camera_endpoint(coordinator, subentry)
        self._talk_client = AqaraLanTalkClient(camera_ip=self._camera_ip)

        if hass is None:
            return
        self._register_in_hass_data(hass)
        self._register_services_if_first(hass)

    async def async_unload(self) -> None:
        """Reverse async_setup. Idempotent."""
        if self._talk_client is not None:
            try:
                if self._talk_client.is_connected:
                    await self._talk_client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error disconnecting talk client")
            self._talk_client = None

        hass = self._hass
        if hass is None:
            return

        if self.go2rtc_stream_name:
            try:
                await hass.async_add_executor_job(
                    _go2rtc.remove_stream,
                    hass.config.config_dir,
                    self.go2rtc_stream_name,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error removing go2rtc stream")
            self.go2rtc_stream_name = None

        registry_map = self._registry_map(hass)
        registry_map.pop(self.did, None)
        if not registry_map:
            self._unregister_services(hass)
        self._hass = None

    # ------------------------------------------------------------------
    # Service wiring helpers.
    # ------------------------------------------------------------------

    def _registry_map(self, hass: Any) -> dict[str, "CameraDevice"]:
        domain_data = hass.data.setdefault(DOMAIN, {})
        return domain_data.setdefault(_CAMERA_REGISTRY_KEY, {})

    def _register_in_hass_data(self, hass: Any) -> None:
        self._registry_map(hass)[self.did] = self

    def _register_services_if_first(self, hass: Any) -> None:
        if hass.services.has_service(DOMAIN, "play_audio_file"):
            return

        async def handle_play_audio_file(call: Any) -> None:
            file_path = call.data["file_path"]
            if not hass.config.is_allowed_path(file_path):
                raise ServiceValidationError(
                    f"Path not allowed: {file_path}. "
                    "Add the directory to allowlist_external_dirs.",
                )
            device = _resolve_camera_for_call(hass, call)
            client = device._talk_client
            if client is None:
                raise HomeAssistantError("Camera device is not initialised")
            if not client.is_connected:
                if not await client.connect():
                    raise HomeAssistantError(
                        "Failed to connect to camera — the voice channel "
                        "may be in use by browser two-way audio or another "
                        "session",
                    )
            await _send_aac_file(hass, client, file_path)

        hass.services.async_register(
            DOMAIN, "play_audio_file", handle_play_audio_file,
            schema=_PLAY_AUDIO_SCHEMA,
        )

    def _unregister_services(self, hass: Any) -> None:
        for service in ("play_audio_file",):
            hass.services.async_remove(DOMAIN, service)


def _resolve_camera_for_call(hass: Any, call: Any) -> "CameraDevice":
    """Map a service call's `device_id` to the CameraDevice instance.

    Service calls arrive with `device_id` (HA device-registry id). The HA
    device's `identifiers` set contains `(DOMAIN, did)` for every device
    this integration owns; the DID then keys the camera registry stashed
    under hass.data.
    """
    device_id = call.data.get("device_id")
    if not device_id:
        raise ServiceValidationError("Missing device_id in service call")

    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    ha_device = dev_reg.async_get(device_id)
    if ha_device is None:
        raise ServiceValidationError(f"Unknown device_id: {device_id}")

    domain_data = hass.data.get(DOMAIN, {})
    registry_map: dict[str, "CameraDevice"] = domain_data.get(_CAMERA_REGISTRY_KEY, {})

    for domain, ident in ha_device.identifiers:
        if domain != DOMAIN:
            continue
        device = registry_map.get(ident)
        if device is not None:
            return device
    raise ServiceValidationError(
        f"device_id {device_id} is not an Aqara camera",
    )


async def _send_aac_file(
    hass: Any, client: AqaraLanTalkClient, file_path: str,
) -> None:
    """Stream an AAC-ADTS file's frames through the talk client."""
    path = pathlib.Path(file_path)
    audio_data = await hass.async_add_executor_job(path.read_bytes)
    frames = extract_adts_frames(audio_data)
    ts_samples = 0
    for frame in frames:
        if not client.is_connected:
            break
        client.send_audio_frame(frame, ts_samples)
        ts_samples += _AAC_FRAME_SAMPLES
        await asyncio.sleep(_AAC_FRAME_SLEEP_S)


class AutoDerivedCameraDevice(CameraDevice):
    """Stock CameraDevice for V3-catalogue camera models without a hand-authored subclass.

    Same as AutoDerivedDevice (sets MODEL / MANUFACTURER / DISPLAY_NAME from
    cloud + catalogue at instance construction), but extends CameraDevice so
    `async_setup_camera`, `async_setup_extra_numbers`, and the talk-client
    lifecycle are available. Used by `__init__.py` when
    `catalog.is_camera_model(model)` is True and no `@register_device` class
    is registered for the model.

    RTSP_URL_TRAIT_PATH stays at CameraDevice's default ("7.136.20368") --
    every documented Aqara camera reports its RTSP URL at that wire path.
    """

    def __init__(self, *args, **kwargs) -> None:
        # CameraDevice.__init__ takes the same (coordinator, subentry, derived)
        # signature it inherits from Device. resolve_subentry_metadata reads
        # the subentry's cloud metadata and catalogue to fill in the naming
        # attributes that hand-authored subclasses set as class constants.
        subentry = kwargs.get("subentry") if "subentry" in kwargs else (
            args[1] if len(args) >= 2 else None
        )
        if subentry is not None:
            model, parent_did, manufacturer, display_name = (
                resolve_subentry_metadata(subentry)
            )
            self.MODEL = model
            self.PARENT_DID = parent_did
            self.MANUFACTURER = manufacturer
            self.DISPLAY_NAME = display_name
        super().__init__(*args, **kwargs)


__all__ = ["AutoDerivedCameraDevice", "CameraDevice"]
