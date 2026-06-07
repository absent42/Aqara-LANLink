# Camera PTZ (Pan/Tilt/Zoom)

Pan/tilt/zoom control for supported Aqara cameras.

PTZ runs over a separate local peer-to-peer (P2P) control plane using a
reverse-engineered TUTK/PPPP/CS2 protocol. It is entirely independent of the
LANLink tunnel used for all other device control, and PTZ is not part of the
V3 trait catalogue. The protocol has been fully reverse-engineered and is
documented internally for maintainers.

Session auth is cloud-assisted: establishing a PTZ session exchanges credentials
with the Aqara cloud once. The actual pan, tilt, and zoom commands then flow
locally to the camera over the P2P connection without further cloud involvement.


## Requirements

Three things must be in place before PTZ entities and services become active:

1. **A supported camera model.** The camera's model package must declare the
   `ptz` capability. See [Supported models](#supported-models) below.

2. **Camera IP address configured.** The camera's LAN IP address must be set in
   the integration options (via the integration's options / camera-IP form; it
   is stored as the `camera_ip` option). Without it the PTZ controller cannot
   reach the camera and all PTZ calls will fail with an error.

3. **A working Aqara cloud login on the config entry.** The cloud credential
   stored in your config entry is used for the one-time cloud-assisted PTZ auth.
   If the cloud login is invalid or expired, PTZ session establishment will fail.


## Sub-features

A camera's `ptz` capability declaration is a set of sub-features. Each
sub-feature gates a specific group of entities and services:

| Sub-feature | What it enables |
|---|---|
| `pan_tilt` | Directional movement buttons (Up, Down, Left, Right, Stop) and the `up`/`down`/`left`/`right`/`stop` commands of the `ptz` service. |
| `zoom` | Zoom In and Zoom Out buttons and the `zoom_in`/`zoom_out`/`zoom` commands of the `ptz` service. |
| `presets` | A "PTZ preset" select entity listing saved positions and the `preset` command of the `ptz` service. |

A camera may declare any combination of sub-features. For example, a camera
with only `pan_tilt` will have directional buttons but no zoom or preset
entities.


## What you get in Home Assistant

### Buttons

Created when `pan_tilt` is in the camera's sub-features:

- PTZ Up
- PTZ Down
- PTZ Left
- PTZ Right
- PTZ Stop

Created when `zoom` is in the camera's sub-features:

- PTZ Zoom In (one step = 1.0x magnification)
- PTZ Zoom Out (one step = 1.0x magnification)

### Select entity

Created when `presets` is in the camera's sub-features:

- **PTZ preset** - lists the saved positions fetched from your Aqara account.
  Selecting a position recalls it on the camera.

### Service

A single service, `aqara_lanlink.ptz`, performs every PTZ action. It targets a
camera entity (`domain: camera`) and only works on PTZ-capable cameras; calling
it on a non-PTZ camera raises an error. The `command` field selects the action;
`value`, `continuous`, and `duration` refine it.

| Field | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | One of: `up`, `down`, `left`, `right`, `stop`, `zoom_in`, `zoom_out`, `zoom`, `preset`. |
| `value` | string | for `zoom`/`preset` | For `zoom`: the magnification, 1.0 (widest) to 9.0 (tele). For `zoom_in`/`zoom_out`: the step size in magnification (optional, default 1.0). For `preset`: the saved position's name (as set in the Aqara app) or its numeric id. Ignored by other commands. |
| `continuous` | boolean | no (default: false) | For `up`/`down`/`left`/`right`: move continuously instead of a single nudge. |
| `duration` | float (seconds) | no | For a continuous directional move: auto-stop after this many seconds. Omit to move until `stop`. |

Command reference:

| `command` | Effect |
|---|---|
| `up` / `down` / `left` / `right` | A single momentary nudge, or (with `continuous: true`) a sustained move until `stop`/`duration`. |
| `stop` | Halt a continuous pan/tilt move. |
| `zoom_in` / `zoom_out` | Step the optical zoom by `value` magnification (default 1.0x). |
| `zoom` | Set an absolute magnification from `value` (1.0–9.0). |
| `preset` | Recall the saved position named (or id'd) by `value`. |

Example - continuous tilt down for 2 seconds:

```yaml
service: aqara_lanlink.ptz
target:
  entity_id: camera.hallway_camera
data:
  command: down
  continuous: true
  duration: 2.0
```

Example - recall a saved preset:

```yaml
service: aqara_lanlink.ptz
target:
  entity_id: camera.hallway_camera
data:
  command: preset
  value: "Front door"
```

#### Using the WebRTC card

The single-service design lets the [AlexxIT WebRTC card](https://github.com/AlexxIT/WebRTC)
drive PTZ from its on-image overlay. The card's PTZ feature allows exactly one
`service:` with per-button `data_*` payloads, so each button maps to a `command`:

```yaml
type: custom:webrtc-camera
url: hallway_camera          # the go2rtc stream name
ptz:
  service: aqara_lanlink.ptz
  data_left:     {entity_id: camera.hallway_camera, command: left}
  data_right:    {entity_id: camera.hallway_camera, command: right}
  data_up:       {entity_id: camera.hallway_camera, command: up}
  data_down:     {entity_id: camera.hallway_camera, command: down}
  data_zoom_in:  {entity_id: camera.hallway_camera, command: zoom_in}
  data_zoom_out: {entity_id: camera.hallway_camera, command: zoom_out}
```

To change the per-click zoom step, add a `value` (magnification delta) to the
zoom buttons — e.g. a finer 0.5x step:

```yaml
  data_zoom_in:  {entity_id: camera.hallway_camera, command: zoom_in,  value: 0.5}
  data_zoom_out: {entity_id: camera.hallway_camera, command: zoom_out, value: 0.5}
```


## Supported models

The following camera models currently declare PTZ support:

| Model package | Sub-features |
|---|---|
| `camera_acn007` | `pan_tilt`, `presets` |
| `camera_agl010` | `pan_tilt`, `zoom`, `presets` |
| `camera_gwpagl01` | `pan_tilt`, `presets` |
| `camera_gwpgl1` | `pan_tilt`, `presets` |

These are internal package directory names; each maps to a specific Aqara
camera model. More models can be added by contributors — see
[Enabling PTZ for a new model](#enabling-ptz-for-a-new-model) below.


## Enabling PTZ for a new model

PTZ is declared per-model by a maintainer or contributor. To add PTZ support
for a new camera, add a `CAPABILITIES` dict to that model's `overrides.py`:

```python
CAPABILITIES = {
    "ptz": frozenset({"pan_tilt", "zoom", "presets"}),
}
```

Only list the sub-features the camera actually supports. The `CAPABILITIES`
dict sits alongside the existing `OVERRIDES` dict in the same `overrides.py`
file. See `device/models/camera_agl010/overrides.py` for a live example.

Because PTZ is off-catalogue and uses a protocol that is specific to Aqara's
P2P stack, you should confirm the camera actually speaks the P2P PTZ protocol
before declaring the capability. Cameras that do not support the protocol will
silently fail or produce errors on every PTZ call.

For the general overrides mechanism and file layout, see
[overrides.md](overrides.md). For the broader contribution workflow, see
[adding-device-support.md](adding-device-support.md).


## Troubleshooting

If PTZ entities do not appear, or PTZ services do nothing (or raise errors),
consult [troubleshooting.md](troubleshooting.md) for a full symptom-to-fix
guide. The most common causes are:

- The camera IP address is not set in the integration options.
- The Aqara cloud login on the config entry is invalid or expired.
- The camera model does not have the `ptz` capability declared.
