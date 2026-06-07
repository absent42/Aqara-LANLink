"""Shared constants for the Aqara LANLink camera and doorbell subsystem.

These constants are used across camera model packages for the two-way-audio
protocol, RTSP streaming, and RTP framing. Model-specific wire paths (trait
IDs such as CameraRTSPURL or ButtonEvent) belong in each model package, not
here. Hub/topology constants live in the top-level `aqara_lanlink/const.py`
module instead.
"""

from __future__ import annotations

# Network ports
RTSP_PORT = 8554
CONTROL_PORT = 54324
AUDIO_PORT = 54323

# RTSP channels exposed by Aqara cameras (high, medium, low quality).
CHANNELS = (1, 2, 3)

# Protocol constants
MAGIC = b"\xFE\xEF"
TYPE_START_VOICE = 0
TYPE_STOP_VOICE = 1
TYPE_ACK = 2
TYPE_HEARTBEAT = 3
RTP_PAYLOAD_TYPE = 97  # Dynamic PT for AAC

# Timing
HEARTBEAT_INTERVAL = 5.0  # seconds
TCP_CONNECT_TIMEOUT = 3.0  # seconds
