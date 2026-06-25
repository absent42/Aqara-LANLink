# Troubleshooting

This page covers the most common problems encountered when setting up or
running the Aqara LANLink integration. Each section describes the symptom,
the likely cause, and the steps to resolve it.

---

## Known limitations

- The LANLink protocol does not distinguish a rejected token from an unreachable
  hub. Both appear as `cannot_connect` in the config flow; if connection fails,
  check that LAN Control is enabled on the hub before assuming a credential
  problem.
- Sub-device enumeration requires the cloud to be reachable at config-flow time
  (Step 2 above).
- On each Home Assistant restart or tunnel reconnect the integration re-arms
  its push subscription and re-seeds device state via the cloud. If the cloud
  is unreachable at restart time, devices may show stale or unknown state until
  the first device push, or re-arm succeeds.
- A purely local credential path (no Aqara cloud account) is not currently
  supported. You can paste an externally obtained userId+token to avoid entering
  a password, but some cloud reachability at restart is still required.
- Integration is currently only tested against the Aqara EU server

---

## Known device issues

- Noise detection not working on G350
- ResouceID writes not working for IP based devices

---

## "Cannot connect" error at setup or re-authentication

**Symptom:** The config flow or re-auth dialog shows "Could not validate
against the hub" (`cannot_connect`) and will not proceed.

**Cause:** The LANLink protocol cannot distinguish a rejected token from an
unreachable hub. Both conditions look the same to the integration -- the hub
closes or never opens the encrypted session -- so both are reported as
`cannot_connect`.

**Resolution:** Work through the following checks in order:

1. Confirm the hub's IP address is correct and that Home Assistant can reach
   it on the local network. A quick `ping <hub-ip>` from the HA host is
   usually sufficient.
2. Open the Aqara app, navigate to the hub's settings, and confirm that
   "LAN Control" (sometimes labelled "Local Control") is enabled. If LAN
   Control is off, the hub refuses the encrypted session regardless of
   credentials.
3. Verify the credentials. If you used email and password in the flow, try
   logging in to the Aqara app with the same credentials to confirm they are
   valid. If you pasted a user ID and token directly, re-generate them from
   a fresh login.
4. If the hub was recently rebooted or switched network interfaces, its LAN
   IP may have changed. Update the IP in your router's DHCP settings or assign
   a static lease.

---

## Devices show no state updates after a successful connection

**Symptom:** The integration connects without error, but entity states remain
unavailable or do not update when you control the physical device.

**Cause:** Push reports from the hub only flow once the hub's internal device
topology is populated. A standalone hub with no sub-device topology, or a hub
whose mesh has not yet finished joining, will not forward any reports even
after a successful subscribe. This is a normal hub behaviour, not a bug in
the integration.

**What the integration does automatically:** A push-liveness watchdog monitors
report traffic on connected hubs. If no reports have arrived for approximately
300 seconds and the hub has a non-empty topology, the integration re-arms the
push subscription automatically. After three consecutive failed re-arm
attempts the integration raises a Repair issue (`push_stalled`) titled "Aqara
hub stopped sending updates" -- see the next section.

**Resolution:**

- Wait up to a minute after HA starts for the hub to deliver its topology push
  and for the subscription to seed initial values from the cloud.
- If the hub has no paired sub-devices, it will not forward reports.
  Pair at least one device to the hub in the Aqara app.
- If updates do not arrive after a few minutes, check that "LAN Control" is
  still enabled for the hub (the hub can lose this setting after a firmware
  update).

---

## Repair issue: "Aqara hub stopped sending updates" (push_stalled)

**Symptom:** A Repair notification appears with the title "Aqara hub stopped
sending updates". Entity states have not changed for an extended period despite
the hub appearing connected.

**Cause:** The integration's push-liveness watchdog re-armed the subscription
three times (roughly 15 minutes of silence) without receiving any reports from
a hub that has a non-empty topology. The hub's forwarding is wedged.

The hub stores its push relay/subscription table in persistent storage that
survives reboots and reconnects. If that table accumulates a large number of
stale entries, the hub keeps accepting tunnel connections and keeps sending
keepalives and topology pushes but stops forwarding device reports. Re-arming
the subscription (which the integration does automatically) does not clear the
stale entries, so the wedge can persist across both HA restarts and hub
reboots.

**Resolution:**

1. First confirm that "LAN Control" is enabled in the hub's app settings. A
   firmware update can disable it, which produces the same "no updates"
   symptom.
2. Reboot the Aqara hub from the Aqara app or by cycling its power, then wait a
   couple of minutes for it to re-deliver topology.
3. If reports still do not resume after a reboot, the hub's relay table is
   wedged with stale entries. A reboot does not clear it because the table is
   persisted. A **factory reset of the hub** is the only reliable way to clear
   the table; re-pair the hub and its sub-devices afterwards.
4. Once reports resume, the integration clears the Repair issue automatically.
   No manual dismissal is required.

**What accumulates the stale entries:** in normal use this is rare -- the table
is bounded by the set of devices you actually use. It builds up fastest under
repeated connect/disconnect churn against the same hub: removing and re-adding
the integration many times, or repeatedly triggering standalone-device relay
activation (for example, FP2 activation testing during development). If you are
doing that kind of repeated activation/reconnect testing, expect to need an
occasional hub factory reset to clear accumulated relay state. See the
developer notes for detail.

---

## State is stale or missing after a Home Assistant restart

**Symptom:** Entities come up in an unknown or outdated state after HA
restarts, then correct themselves after a short delay -- or never correct if
the Aqara cloud is unreachable.

**Cause:** On every HA restart and on every reconnect, the integration re-arms
the push subscription and seeds current entity values from the Aqara cloud. If
Aqara's cloud is unreachable at restart time, initial values cannot be seeded
and devices start with no freshly confirmed state. Steady-state local push is
unaffected once the subscription is re-armed and the hub begins forwarding
reports.

This is expected behavior. The integration requires a brief cloud round-trip
at startup to seed state and subscribe; it does not cache the last-known state
across restarts.

**Resolution:** Ensure that Home Assistant can reach the Aqara cloud API
(outbound HTTPS) at startup. If the cloud is only temporarily unavailable,
entity state will catch up as soon as the next push report arrives from the
hub or the next successful re-arm completes.

**Note on device-setting entities:** Settings such as child lock, indicator
light, button mode, power-off memory, max power, and find/restart are
controlled locally over LANLink. Their state is seeded from the cloud once at
load and updated optimistically whenever you change them from Home Assistant.
They do not receive live push updates, so a change you make in the Aqara app
while Home Assistant is running will not appear until you reload the
integration. Changes made from Home Assistant are reflected immediately.

---

## Repair issue: "New device capabilities observed" (candidate_paths)

**Symptom:** A Repair notification appears with the title "New device
capabilities observed", listing one or more devices and wire paths the
integration does not recognise.

**Cause:** The integration received push reports containing wire paths that are
not present in the shipped catalogue or your local overlay. This typically
happens when a firmware update adds new traits to a previously catalogued
device.

**Resolution:**

1. Run the `aqara_lanlink.scan_device` service (see [services.md](services.md))
   against each affected device listed in the Repair notification. The service
   queries the Aqara cloud for the device's current traits and produces a
   scan-review Repair.
2. In the scan-review Repair, select which traits to add as entities, then
   accept.
3. Reload the integration for the new entities to appear.

For guidance on contributing new traits back to the shipped catalogue, see
[adding-device-support.md](adding-device-support.md).

---

## Missing entities or unexpectedly named entities

**Symptom:** An entity you expect to see is absent, shows a generic numeric
name (such as `2.1.85`), or has an untranslated label in a foreign language.

**Possible causes and resolutions:**

- **Intentionally excluded trait.** Some traits are dropped by the integration's
  trait policy (for example, low-level diagnostic paths). A dropped trait will
  never produce an entity. If you believe a trait should be exposed, see
  [adding-device-support.md](adding-device-support.md) and the `dropped_paths`
  explanation in [catalogue-and-traits.md](catalogue-and-traits.md).

- **Diagnostic entity, disabled by default.** Several traits are classified as
  diagnostic and are created in a disabled state. To enable one, go to the
  device page in Home Assistant, click on the entity, open its settings, and
  enable it.

- **Uncatalogued trait.** The integration does not have a friendly label or
  type mapping for this wire path. A numeric name such as `2.1.85` means the
  catalogue lacks the entry. Run `aqara_lanlink.scan_device` and accept the
  discovered trait, or add a manual entry via `overrides.py` -- see
  [adding-device-support.md](adding-device-support.md).

- **Foreign-language label.** Labels sourced from a CN-region scrape may
  appear in Chinese. These are fixable via `overrides.py` for the affected
  model. See [adding-device-support.md](adding-device-support.md).

---

## Camera or two-way audio not working

**Symptom:** The camera entity appears but the stream is unavailable, or
two-way audio (speaking back through the doorbell) does not work.

**Cause:** The integration auto-registers a go2rtc stream for each camera.
The stream name is derived from the camera's IP address:
`aqara_lanlink_<camera-IP-with-dots-as-underscores>` (for example, a camera at
`192.168.1.50` produces `aqara_lanlink_192_168_1_50`). If go2rtc is not
running or its configuration has not been reloaded, the stream will not be
available.

Two-way audio requires the AlexxIT WebRTC custom card. It must be configured
with a `url:` field referencing the stream name above, not an `entity:` field.
Using `entity:` bypasses the backchannel and two-way audio will not function.

**Resolution:**

1. Verify go2rtc is running and that its configuration file includes the
   stream written by this integration. A go2rtc restart may be needed after
   the integration first registers the stream.
2. In your WebRTC card configuration, use `url: aqara_lanlink_192_168_1_50`
   (substituting the correct stream name for your camera's IP).
3. For additional service options such as `play_audio_file`, see
   [services.md](services.md).

---

## Re-authentication prompt on the integration card

**Symptom:** A "Re-authentication required" notification appears on the Aqara
LANLink integration card.

**Cause:** The stored session token was rejected by the hub or the Aqara
cloud. This can happen when the token expires, when the Aqara account password
is changed, or after a hub factory-reset.

**Resolution:** Click the notification or the "Authenticate" button on the
integration card. Enter fresh credentials in the re-auth dialog (either an
email/password pair for a new cloud login, or a freshly obtained user ID and
token). The integration reloads automatically on success.

---

## PTZ buttons or services do nothing

**Symptom:** Pan/tilt/zoom buttons or PTZ services appear to accept commands
but the camera does not move, or the buttons and services are absent entirely.

**Possible causes:**

- **Camera model does not declare the ptz capability.** PTZ is an off-catalogue
  feature declared per-model via a `CAPABILITIES` dict in `overrides.py`. If
  the model package does not include it, no PTZ entities or services are
  registered. Check whether the model's `overrides.py` exports `CAPABILITIES`.

- **Camera IP not set in integration options.** The PTZ control plane connects
  directly to the camera's IP address. If that address has not been entered in
  the integration options for the camera's subentry, PTZ cannot establish a
  session.

- **Cloud login invalid.** PTZ session authentication is cloud-assisted. If the
  stored session token has expired or been revoked, PTZ session setup will fail
  even though LANLink (which re-arms its own subscription) may still appear
  connected.

See [ptz.md](ptz.md) for the full protocol description and configuration
requirements.

---

## Still stuck?

If none of the above resolves the issue, open a bug report at
https://github.com/absent42/Aqara-LANLink/issues. Include the following to
help diagnose the problem:

- The device model string (visible on the device page in Home Assistant under
  the "Model" field, for example `lumi.gateway.agl004`).
- Relevant Home Assistant logs at DEBUG level. Enable debug logging for the
  integration by adding the following to your `configuration.yaml`, restarting
  HA, reproducing the issue, then attaching the resulting log:

```yaml
logger:
  default: warning
  logs:
    custom_components.aqara_lanlink: debug
```
