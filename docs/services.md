# Services

Aqara LAN Link registers three services and one audio path that is not a service.
The three services are developer and power-user tools; they are not needed for
routine day-to-day use of the integration.

---

## aqara_lanlink.scan_device

**Purpose.** Runs a one-shot cloud scan against a single Aqara device to
discover traits that the shipped V3 catalogue does not yet cover. The service
itself does not modify entities or write anything to disk. Instead it compares
the cloud-reported trait list against the catalogue and, when gaps are found,
raises a "scan_review" Repair issue in Settings > Devices & Services > Repairs
where you review the discovered traits and choose which to accept.

**When to use.** Only needed for the small number of models not yet in the
catalogue, or when a firmware update has added new traits to a model that is
already catalogued. Not required for normal use.

**Fields.**

| Field | Required | Description |
|-------|----------|-------------|
| `device_id` | Yes | The Home Assistant device ID for an Aqara LAN Link device. Use the device picker in Developer Tools > Actions. |

**What happens next.**

1. The service contacts the Aqara cloud, retrieves the device's trait collection,
   and compares it against the local catalogue.
2. If gaps are found, a Repair issue named "scan_review" appears under
   Settings > Devices & Services > Repairs.
3. Open the Repair issue. A checklist shows each discovered trait with its
   property ID, wire path, name, platform, and a sample cloud value where
   available. All entries are pre-selected; untick any you do not want.
4. Click Fix. Accepted traits are written to the per-install overlay and the
   config entry reloads automatically.
5. If no gaps are found the service logs an info message and removes any prior
   scan_review Repair for that device.

Independently of the gap report, every successful scan also posts a Persistent
Notification titled "Aqara LAN Link RID discovery: &lt;model&gt;". Its body is a
`RESOURCE_IDS` dict of `wire_path -> resource_id` pairs read from the device's
`propertyId` (authoritative), enriched over the model's catalogue trait wire
paths. This is a contributor aid for authoring local device settings or adding
a `resource_id` to a trait, and can be ignored for ordinary trait additions.
This step is best-effort and additive: any failure is logged and never breaks
the trait scan. See
[adding-device-support.md](adding-device-support.md#adding-local-device-settings-rid-keyed).

If the cloud authentication token is expired, the service triggers the standard
re-authentication flow instead of raising a scan-failure Repair.

See [adding-device-support.md](adding-device-support.md) for the full workflow
for uncatalogued models, and [troubleshooting.md](troubleshooting.md) if the
scan fails unexpectedly.

**Example action call.**

```yaml
action: aqara_lanlink.scan_device
data:
  device_id: <device id>
```

---

## aqara_lanlink.export_overlay

**Purpose.** Renders the traits you have accepted into the local overlay as a
Python `TraitSpec` snippet, formatted ready to paste into a model package's
`overrides.py` `OVERRIDES` dict. The result is posted as a Persistent
Notification (bell icon, top right in the HA UI, or Notifications in the
sidebar). You copy the snippet and attach it to a GitHub PR or issue so that it
can be folded into the shipped catalogue.

For what a `TraitSpec` is and how model packages are structured, see
[catalogue-and-traits.md](catalogue-and-traits.md).

**When to use.** After accepting traits via a scan_review Repair and verifying
that the resulting entities behave correctly, use this service to produce the
PR-ready snippet. See [adding-device-support.md](adding-device-support.md) for
the full PR submission workflow.

**Fields.**

| Field | Required | Description |
|-------|----------|-------------|
| `model` | No | Restrict the export to one model identifier (e.g. `lumi.camera.acn003`). Omit to export every model currently in the overlay. |

**What happens next.**

A Persistent Notification titled "Aqara LAN Link overlay export" appears. Its
body is a pasteable Python block with one `OVERRIDES` dict per model. Each
entry includes a provenance comment showing when and how the trait was
discovered. Review each entry for correctness and usefulness before submitting
a PR; the renderer does not filter out low-quality or redundant traits.

**Example action calls.**

Export all models in the overlay:

```yaml
action: aqara_lanlink.export_overlay
data: {}
```

Export a single model:

```yaml
action: aqara_lanlink.export_overlay
data:
  model: lumi.camera.acn003
```

---

## aqara_lanlink.play_audio_file

**Purpose.** Streams a local AAC-ADTS audio file to a camera or doorbell
speaker over the LAN link. Useful for playing custom chimes, announcements, or
alerts through an Aqara device that has a built-in speaker.

**Fields.**

| Field | Required | Description |
|-------|----------|-------------|
| `device_id` | Yes | The Home Assistant device ID for the target Aqara camera or doorbell. |
| `file_path` | Yes | Absolute path to an AAC-ADTS audio file accessible to the Home Assistant process (e.g. `/media/chime.aac`). |

**Example action call.**

```yaml
action: aqara_lanlink.play_audio_file
data:
  device_id: <device id>
  file_path: /media/chime.aac
```

---

## PTZ service (cameras)

A single camera entity service, `aqara_lanlink.ptz`, provides pan, tilt, zoom,
and preset recall for supported camera models. It targets a camera entity
directly (not a device) and only works when the camera model declares the `ptz`
capability and the camera's IP address is configured in the integration options.

A single command-style service (rather than four narrow ones) keeps the surface
small and lets dashboards such as the [AlexxIT WebRTC card](https://github.com/AlexxIT/WebRTC)
drive every PTZ action through one `service:` key. For per-action manual control
you can also use the camera's button (directions/stop, zoom in/out), number
(absolute zoom), and select (presets) entities.

See [ptz.md](ptz.md) for the full picture, including the WebRTC card config, how
PTZ works, and which models support it.

**Prerequisites.**
- The camera model must declare `ptz` in its `CAPABILITIES` dict
  (`overrides.py`).
- The camera IP must be set in the integration options for that subentry.
- A valid cloud login is required (PTZ session auth is cloud-assisted).

### aqara_lanlink.ptz

| Field | Required | Description |
|-------|----------|-------------|
| `entity_id` | Yes | The camera entity. |
| `command` | Yes | One of `up`, `down`, `left`, `right`, `stop`, `zoom_in`, `zoom_out`, `zoom`, `preset`. |
| `value` | For `zoom`/`preset` | `zoom`: magnification `1.0` (wide) to `9.0` (telephoto). `preset`: preset name or id, matching an option in the preset select entity. Ignored otherwise. |
| `continuous` | No | For `up`/`down`/`left`/`right`: `true` to move until `stop` (or `duration`) instead of a single nudge. |
| `duration` | No | For a continuous directional move: seconds before auto-stop. Omit to move until `stop`. |

```yaml
action: aqara_lanlink.ptz
target:
  entity_id: camera.g350_front_door
data:
  command: left
  continuous: true
  duration: 2
```

```yaml
action: aqara_lanlink.ptz
target:
  entity_id: camera.g350_front_door
data:
  command: preset
  value: Front door
```

---

## Two-way audio (not a service)

Two-way audio - talking back through a camera or doorbell speaker while
watching the live stream - is not implemented as a service. It uses the go2rtc
backchannel mechanism.

**How it works.** When the integration loads, it registers a go2rtc stream for
each camera. The stream name is derived from the camera's IP address:

```
aqara_lanlink_<camera IP with dots replaced by underscores>
```

For example, a camera at `192.168.1.50` produces the stream name
`aqara_lanlink_192_168_1_50`.

**WebRTC card requirement.** To use the backchannel you must use the
[AlexxIT WebRTC custom card](https://github.com/AlexxIT/WebRTC) and reference
the stream by name using the `url:` key. Using `entity:` does not enable the
backchannel.

```yaml
type: custom:webrtc-camera
url: aqara_lanlink_192_168_1_50
```

Replace `192_168_1_50` with the underscored IP of your camera.

If two-way audio is not working, see [troubleshooting.md](troubleshooting.md).
