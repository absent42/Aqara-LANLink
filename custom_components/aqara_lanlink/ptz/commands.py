"""PTZ command constants, action vocabulary, and IOCTL body builders.

Ported verbatim from tools/ptz_poc/ptz_poc.py: the lumi IOCTL type enum
(PpcsIotType), the PTZ action vocabulary, and the zoom/position body builders.
The PTZ-feature name constants and zoom range consts are added here so the
controller and entity layers share a single source of truth.
"""
from __future__ import annotations

# IOCTL types on the wire (verbatim from the decompiled app's PpcsIotType enum,
# com.lumi.module.p2p.entity.PpcsIotType). The *_REQ goes phone->cam; the camera
# answers with the matching *_RESP (though in practice we just read the next DRW).
AUTH_IOTYPE = 4096        # AUTH request
AUTH_REPLY_IOTYPE = 4097  # AUTH reply ({"code":0} == M2 gate)
PTZ_IOTYPE = 4138         # CTRL_PTZ_REQ      -> {"action": "<dir>"}
PTZ_REPLY_IOTYPE = 4139   # CTRL_PTZ_RESP
POSITION_IOTYPE = 4140    # PTZ_POSITION_REQ  -> {"position": "<str>"}
POSITION_REPLY_IOTYPE = 4141  # PTZ_POSITION_RESP
ZOOM_IOTYPE = 4142        # ZOOM_REQ          -> {"zoom": "<%.1f>"}
ZOOM_REPLY_IOTYPE = 4144  # ZOOM_RESP

# PTZ action vocabulary, verbatim from the decompiled app
# (com.lumi.module.camera ControllerViewModel.Direction + ControllerFragment):
#   - single tap  -> "<dir>"         : one momentary nudge
#   - long-press  -> "<dir>_always"  : continuous motion until released
#   - release     -> "stop"          : ends a continuous move
# changeCameraDirection() passes the string straight into IOTYPE 4138, no
# transform, so these are the exact on-wire action strings.
NUDGE_ACTIONS = ("left", "right", "up", "down")
CONTINUOUS_ACTIONS = ("left_always", "right_always", "up_always", "down_always")
STOP_ACTION = "stop"

# Accepted `command` values for the single `aqara_lanlink.ptz` service. The
# four directions and `stop` map straight to wire actions; `zoom_in`/`zoom_out`
# step by ZOOM_BUTTON_STEP; `zoom` sets an absolute magnification; `preset`
# recalls a saved position. The first six match the AlexxIT WebRTC card's PTZ
# buttons (left/right/up/down/zoom_in/zoom_out) so the card can drive PTZ.
PTZ_COMMANDS = (*NUDGE_ACTIONS, STOP_ACTION, "zoom_in", "zoom_out", "zoom", "preset")


def zoom_body(scale: float) -> dict[str, str]:
    """Zoom IOCTL body: P2pCameraApiV2.U() -> {"zoom": String.format("%.1f", f)}."""
    return {"zoom": f"{scale:.1f}"}


def position_body(position: str) -> dict[str, str]:
    """Preset-recall IOCTL body: P2pCameraApiV2.S() -> {"position": "<str>"}."""
    return {"position": position}


# PTZ sub-feature names (mirror the CAPABILITIES["ptz"] declarations in each
# model's overrides.py; see the ptz/ package and device/catalog.py).
PAN_TILT = "pan_tilt"
ZOOM = "zoom"
PRESETS = "presets"
PTZ_FEATURES = frozenset({PAN_TILT, ZOOM, PRESETS})

# Zoom magnification range (the app's zoom slider: 1.0x .. 9.0x).
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP, ZOOM_BUTTON_STEP = 1.0, 9.0, 0.1, 1.0
