"""Validation for host values (IP or hostname) that flow into commands/URLs.

Camera IPs and hub addresses arrive from device traits, the cloud, and user
input, and are later interpolated into go2rtc exec/RTSP source strings and
socket targets. A value containing whitespace or shell/URL metacharacters can
corrupt those sinks - argument injection into a spawned process, malformed
URLs, or an unexpected connection target. These helpers constrain a host to a
literal IP address or a strict hostname so untrusted data cannot break out of
its sink.
"""

from __future__ import annotations

import ipaddress
import re

# Total host string length limit (RFC 1035).
_MAX_HOST_LEN = 253

# Strict hostname: dot-separated labels of [A-Za-z0-9-], each 1-63 chars and
# not starting or ending with a hyphen. No whitespace, no shell/URL
# metacharacters, no path or scheme separators.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


def is_safe_host(value: str) -> bool:
    """Return True if `value` is a literal IP address or a strict hostname.

    Rejects anything containing whitespace or metacharacters, so the value is
    safe to interpolate into a command line, URL, or socket target.
    """
    if not value or not isinstance(value, str) or len(value) > _MAX_HOST_LEN:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return bool(_HOSTNAME_RE.match(value))


def validate_host(value: str) -> str:
    """Return `value` unchanged if it is a safe host, else raise ValueError."""
    if not is_safe_host(value):
        raise ValueError(f"unsafe host value: {value!r}")
    return value


__all__ = ["is_safe_host", "validate_host"]
