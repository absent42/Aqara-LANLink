"""Parser for a camera's CameraRTSPURL trait (trait 20368).

The trait value carries the camera's RTSP stream details. The exact
document shape is firmware-dependent, so this parser is deliberately
shape-tolerant: it locates every rtsp:// URL in the value (walking a
JSON structure if the value is JSON, or treating the value as a bare
URL) and reads the IP and credentials out of the URL itself.

A typical camera returns a JSON object keyed by resolution label, e.g.
{"480p": "rtsp://.../ch3", "960p": "rtsp://.../ch2", "1536p": "rtsp://.../ch1"}.
The parser picks the highest-resolution stream by scoring each URL with
the largest integer found in the dict key directly containing it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ...host_validation import is_safe_host

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RtspDetails:
    """Structured RTSP details extracted from trait 20368."""

    camera_ip: str | None
    username: str | None
    password: str | None
    stream_url: str | None  # the chosen (highest-resolution) RTSP URL


def _resolution_score(key: str) -> int:
    """Largest integer embedded in a dict key (e.g. '1536p' -> 1536).

    Used to rank per-resolution stream URLs. Keys with no digits score 0.
    """
    digits = re.findall(r"\d+", key)
    return max((int(d) for d in digits), default=0)


def _collect_rtsp_urls(raw: str) -> list[tuple[int, str]]:
    """Return (resolution_score, url) for every rtsp:// URL in `raw`.

    Handles a bare rtsp URL or a JSON structure of arbitrary nesting.
    The score comes from the dict key directly containing each URL.
    """
    text = raw.strip()
    if text.lower().startswith("rtsp://"):
        return [(0, text)]
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    found: list[tuple[int, str]] = []

    def _walk(node: object, key_hint: str) -> None:
        if isinstance(node, str):
            if node.lower().startswith("rtsp://"):
                found.append((_resolution_score(key_hint), node))
        elif isinstance(node, dict):
            for key, value in node.items():
                _walk(value, str(key))
        elif isinstance(node, list):
            for item in node:
                _walk(item, key_hint)

    _walk(parsed, "")
    return found


def parse_camera_rtsp_url(raw: str | None) -> RtspDetails | None:
    """Parse the trait-20368 value into structured RTSP details.

    Returns None when the value is empty or contains no rtsp:// URL.
    When several URLs are present (one per resolution channel) the
    highest-resolution one is chosen, ranked by the integer in its key;
    ties and unkeyed URLs fall back to document order.
    """
    if not raw or not isinstance(raw, str):
        return None
    found = _collect_rtsp_urls(raw)
    if not found:
        return None
    # max() returns the first item among equal-scoring ties, so unkeyed
    # URLs (all score 0) collapse to document order.
    chosen = max(found, key=lambda pair: pair[0])[1]
    parts = urlsplit(chosen)
    # urlsplit preserves whitespace and metacharacters in .hostname, which
    # would later be interpolated into a go2rtc exec command line and an RTSP
    # URL. Reject anything that is not a literal IP or strict hostname so a
    # malicious/compromised camera cannot inject into those sinks.
    if not parts.hostname or not is_safe_host(parts.hostname):
        _LOGGER.debug(
            "Rejecting RTSP URL with unsafe host %r", parts.hostname,
        )
        return None
    return RtspDetails(
        camera_ip=parts.hostname,
        username=unquote(parts.username) if parts.username else None,
        password=unquote(parts.password) if parts.password else None,
        stream_url=chosen,
    )


__all__ = ["RtspDetails", "parse_camera_rtsp_url"]
