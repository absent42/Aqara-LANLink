"""LANLink JSON message protocol serialization layer.

Builds and parses the JSON messages exchanged with Aqara devices over the
LANLink local TCP session (mDNS service _aqara-setup._tcp.local.).

Message envelope:
    {
        "seq":  <int>,
        "type": <"session" | "device">,
        "cmd":  <command string>,
        "data": { ... }
    }
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
import json
from typing import Any

# LANLink protocol commands
LANLINK_CMD_CHECKIN = "checkin"
LANLINK_CMD_CHECKIN_DONE = "checkin_done"
LANLINK_CMD_KEEPALIVE = "keepalive"
LANLINK_CMD_KEEPALIVE_DONE = "keepalive_done"
LANLINK_CMD_READ = "read"
LANLINK_CMD_READ_DONE = "read_done"
LANLINK_CMD_WRITE = "write"
LANLINK_CMD_WRITE_DONE = "write_done"
LANLINK_CMD_REPORT = "report"
LANLINK_CMD_EVENT = "event"
LANLINK_CMD_TOPOLOGY = "topology"
LANLINK_TYPE_SESSION = "session"
LANLINK_TYPE_DEVICE = "device"

# Model string for the Aqara G400 doorbell camera.
G400_MODEL = "lumi.camera.agl013"



# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class LANEntity:
    """Parsed LANLink message envelope."""

    seq: int
    type: str
    cmd: str
    data: dict[str, Any] | None = field(default=None)


@dataclass
class LANReport:
    """Parsed device report payload (cmd == "report").

    A report carries a `values` dict mapping trait IDs (e.g. `"5.160.33000.1"`)
    to string values. Multiple traits can appear in one report, though the
    captured G400 firmware sends one trait per report.
    """

    did: str
    sdid: str
    src: str
    time: int
    values: dict[str, Any]

    @classmethod
    def from_entity(cls, entity: LANEntity) -> LANReport | None:
        """Construct a LANReport from a LANEntity.

        Returns None if the entity is not a report, data is missing, or the
        `value` field is not a dict (old-style path/value reports are no
        longer produced by current firmware).
        """
        if entity.cmd != LANLINK_CMD_REPORT:
            return None
        if not isinstance(entity.data, dict):
            return None
        data = entity.data
        raw_values = data.get("value")
        if not isinstance(raw_values, dict):
            return None
        return cls(
            did=data.get("did", ""),
            sdid=data.get("sdid", ""),
            src=data.get("src", ""),
            time=data.get("time", 0),
            values=dict(raw_values),
        )


# =============================================================================
# Message builders
# =============================================================================


def build_checkin(seq: int, user_id: str, device_id: str, token: str) -> dict:
    """Build a session check-in message.

    Parameters
    ----------
    seq:        Message sequence number.
    user_id:    Aqara cloud user ID (returned by the Open API login).
    device_id:  Target device DID (hub for G400 via-hub reports).
    token:      Aqara cloud session token. Sent in the `aid` field.
    """
    return {
        "seq": seq,
        "type": LANLINK_TYPE_SESSION,
        "cmd": LANLINK_CMD_CHECKIN,
        "data": {
            "user": user_id,
            "did": device_id,
            "aid": token,
        },
    }


def build_keepalive(seq: int) -> dict:
    """Build a session keepalive message with the current epoch-ms timestamp.

    Captured wire format uses a `timestamp` field (in milliseconds) in the
    data object.
    """
    return {
        "seq": seq,
        "type": LANLINK_TYPE_SESSION,
        "cmd": LANLINK_CMD_KEEPALIVE,
        "data": {
            "timestamp": int(time.time() * 1000),
        },
    }


def build_read(
    seq: int,
    device_id: str,
    model: str,
    attrs: list[str],
    *,
    sdid: str | None = None,
) -> dict:
    """Build a device trait read request.

    Two framing modes:

    - Hub-scoped / direct (default): ``sdid`` is omitted, so ``data.sdid``
      equals ``data.did`` (``device_id``). Used when the read targets the
      hub itself or a WiFi device that does not require gateway
      indirection.
    - Gateway-relay (sub-device): ``sdid`` is provided as the sub-device
      DID. ``device_id`` becomes the hub DID and ``model`` becomes the
      hub model -- the hub then routes the read to the sub-device.

    Parameters
    ----------
    seq:       Message sequence number.
    device_id: ``did`` field. Hub DID for sub-device reads, sub-device
               DID for hub-scoped reads.
    model:     Device model string. Hub model for sub-device reads.
    attrs:     List of attribute names to read.
    sdid:      Optional sub-device DID for gateway-relay framing. When
               ``None`` (default) ``data.sdid`` is set to ``device_id``.
    """
    return {
        "seq": seq,
        "type": LANLINK_TYPE_DEVICE,
        "cmd": LANLINK_CMD_READ,
        "data": {
            "did": device_id,
            "sdid": sdid if sdid is not None else device_id,
            "model": model,
            "value": attrs,
        },
    }


def build_write(
    seq: int,
    device_id: str,
    model: str,
    attrs: dict[str, Any],
    *,
    sdid: str | None = None,
) -> dict:
    """Build a device trait write request.

    Two framing modes:

    - Hub-scoped / direct (default): ``sdid`` is omitted, so ``data.sdid``
      equals ``data.did`` (``device_id``). Used when the write targets
      the hub itself or a WiFi device that does not require gateway
      indirection.
    - Gateway-relay (sub-device): ``sdid`` is provided as the sub-device
      DID. ``device_id`` becomes the hub DID and ``model`` becomes the
      hub model -- the hub then routes the write to the sub-device.

    Parameters
    ----------
    seq:       Message sequence number.
    device_id: ``did`` field. Hub DID for sub-device writes, sub-device
               DID for hub-scoped writes.
    model:     Device model string. Hub model for sub-device writes.
    attrs:     Mapping of attribute name to the value to write.
    sdid:      Optional sub-device DID for gateway-relay framing. When
               ``None`` (default) ``data.sdid`` is set to ``device_id``.
    """
    return {
        "seq": seq,
        "type": LANLINK_TYPE_DEVICE,
        "cmd": LANLINK_CMD_WRITE,
        "data": {
            "did": device_id,
            "sdid": sdid if sdid is not None else device_id,
            "model": model,
            "src": f"3,,{int(time.time() * 1000)},,",
            "value": attrs,
        },
    }


# =============================================================================
# Message parser
# =============================================================================


def parse_message(raw_json_str: str) -> LANEntity | None:
    """Parse a raw JSON string into a LANEntity.

    Returns None if the input is not valid JSON, not a dict, or is missing
    any of the required envelope fields (seq, type, cmd).

    Parameters
    ----------
    raw_json_str: Raw JSON string received from the device.
    """
    try:
        obj = json.loads(raw_json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    if "seq" not in obj or "type" not in obj or "cmd" not in obj:
        return None

    return LANEntity(
        seq=obj["seq"],
        type=obj["type"],
        cmd=obj["cmd"],
        data=obj.get("data"),
    )
