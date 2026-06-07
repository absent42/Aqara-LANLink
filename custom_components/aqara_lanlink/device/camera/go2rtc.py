"""go2rtc stream registration for Aqara Doorbell backchannel.

Registers a go2rtc stream with two producers: RTSP for camera output and
an exec bridge for backchannel audio input. Registration is done via the
go2rtc.yaml config file because go2rtc blocks exec sources via REST API
for security (prevents remote code execution).

The integration works fully without go2rtc (camera, doorbell, talk services).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

from ...host_validation import validate_host
from .const import RTSP_PORT

_LOGGER = logging.getLogger(__name__)

GO2RTC_CONFIG_FILE = "go2rtc.yaml"


class _QuotedDumper(yaml.SafeDumper):
    """YAML dumper that quotes strings containing # to prevent Go yaml
    parser from treating them as comments."""



def _quoted_str_representer(dumper, data):
    if "#" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_QuotedDumper.add_representer(str, _quoted_str_representer)


def _dump_config(config: dict) -> str:
    """Dump go2rtc config to YAML, quoting strings that contain #."""
    return yaml.dump(config, Dumper=_QuotedDumper, default_flow_style=False, width=4096)


def build_exec_source(config_dir: str, camera_ip: str) -> str:
    """Build the go2rtc exec source URL for the backchannel bridge.

    The exec source tells go2rtc to spawn the bridge script when a browser
    microphone connects. HA's bundled go2rtc uses stdin.NewClient which
    hardcodes PCMA/8kHz for backchannel audio (the #audio= parameter is
    ignored despite the binary reporting as v1.9.14).

    go2rtc uses exec.Command (not a shell), so we pass the absolute script
    path. go2rtc's ParseQuery splits on # (not &) for multiple parameters.

    Raises ValueError if camera_ip is not a literal IP or strict hostname:
    go2rtc tokenizes this string on whitespace, so an unsafe host could inject
    extra argv tokens into the spawned command.
    """
    validate_host(camera_ip)
    python = sys.executable
    bridge_script = str(
        Path(config_dir)
        / "custom_components"
        / "aqara_lanlink"
        / "device"
        / "camera"
        / "bridge.py"
    )
    return (
        f"exec:{python} {bridge_script} "
        f"{camera_ip}#backchannel=1"
    )


def build_rtsp_source(
    camera_ip: str, username: str, password: str, channel: int = 1
) -> str:
    """Build the RTSP source URL for the camera stream on a given channel.

    Raises ValueError if camera_ip is not a literal IP or strict hostname.
    """
    validate_host(camera_ip)
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return f"rtsp://{user}:{pwd}@{camera_ip}:{RTSP_PORT}/ch{channel}"


def register_stream(
    config_dir: str,
    stream_name: str,
    sources: list[str],
) -> bool:
    """Add a stream to go2rtc.yaml config file.

    go2rtc blocks exec sources via REST API for security, so we write
    directly to the config file. Returns True on success.
    A go2rtc restart is needed for changes to take effect.
    """
    config_path = Path(config_dir) / GO2RTC_CONFIG_FILE

    try:
        config: dict = {}
        if config_path.exists():
            raw = config_path.read_text()
            config = yaml.safe_load(raw) or {}

        streams = config.setdefault("streams", {})
        existing = streams.get(stream_name, [])

        # Only write if our sources aren't already there
        if set(sources) == set(existing):
            _LOGGER.debug("go2rtc stream %s already configured", stream_name)
            return True

        streams[stream_name] = sources
        config_path.write_text(_dump_config(config))

        _LOGGER.info(
            "go2rtc stream '%s' written to %s. "
            "Restart HA for backchannel to take effect. "
            "Test at http://localhost:1984/stream.html?src=%s",
            stream_name,
            config_path,
            stream_name,
        )
        return True

    except Exception:
        _LOGGER.warning("Failed to write go2rtc config", exc_info=True)
        return False


def remove_stream(config_dir: str, stream_name: str) -> None:
    """Remove a stream from go2rtc.yaml. Best-effort, never raises."""
    config_path = Path(config_dir) / GO2RTC_CONFIG_FILE

    try:
        if not config_path.exists():
            return
        raw = config_path.read_text()
        config = yaml.safe_load(raw) or {}
        streams = config.get("streams", {})
        if stream_name in streams:
            del streams[stream_name]
            config_path.write_text(_dump_config(config))
            _LOGGER.info("go2rtc stream '%s' removed from %s", stream_name, config_path)
    except Exception:
        _LOGGER.warning("Failed to update go2rtc config", exc_info=True)
