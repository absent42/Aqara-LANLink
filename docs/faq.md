# Frequently Asked Questions

## Do I need the Aqara cloud running all the time?

Not for steady-state operation. Once the integration is connected and subscribed, individual device state changes are pushed directly over the LAN with no cloud round-trip involved.

However, the cloud is required at setup (login and device enumeration) and again at every Home Assistant restart or reconnect. On each restart the integration re-arms the local push subscription and seeds current device values from the cloud. During that brief re-arm window, devices are unavailable until the cloud handshake completes.

See [architecture.md](architecture.md) for a full explanation of what runs locally versus what touches the cloud.

---

## Will it keep working if Aqara's servers go down?

While the integration is connected and subscribed, yes. Live state updates and automations driven by local push will continue unaffected by a cloud outage.

If Home Assistant restarts or the connection drops during a cloud outage, the re-arm step will fail. Devices may come back without freshly seeded state and will remain in an unknown state until the cloud becomes reachable again and the subscription is re-armed.

See [troubleshooting.md](troubleshooting.md) for guidance on diagnosing connectivity and subscription failures.

---

## Is my device supported?

Approximately 379 models are catalogued. Check the live model list at [catalogue/index.md](catalogue/index.md).

If your device is missing or only partially functional, see [adding-device-support.md](adding-device-support.md) for instructions on contributing support for new models.

---

## Why is one of my entities named oddly, or missing?

A few common causes:

- The catalogue is missing a friendly label for that trait. This is fixable by submitting an override; see [catalogue-and-traits.md](catalogue-and-traits.md).
- The entity is a diagnostic entity, which Home Assistant disables by default. Enable it from the device page in Settings.
- The trait is intentionally not exposed because it cannot be reliably controlled or read over the local protocol.
- Device functions that are inlcuded in the Aqara local API and whose wire path is known are supported. If a device function is missing or doesn't funciton correctly is may be because either the function isn't exposed in the Aqara local API, or the wire path for that funciton isn't currently known.

See [troubleshooting.md](troubleshooting.md) for steps to identify which case applies.

---

## Is this safe? Will Aqara firmware updates break it?

This integration is unofficial and built on a reverse-engineered local protocol. Aqara firmware updates may change that protocol and break compatibility without notice. There is no guarantee of forward compatibility.

On the credentials side, only a session token is stored locally - your Aqara account password is not retained after login.

See [architecture.md](architecture.md) for the security and privacy section.

---

## Can I pan, tilt, or zoom my camera?

Yes, for supported camera models. When the camera model declares the `ptz`
capability in its `overrides.py`, the integration registers pan/tilt/zoom
buttons, a preset selector entity, and a single `aqara_lanlink.ptz` camera
service (with a `command` field covering move/stop/zoom/preset). This requires the camera's IP
address to be set in the integration options for that subentry; a valid cloud
login is also needed because PTZ session authentication is cloud-assisted.

See [ptz.md](ptz.md) for supported models, configuration steps, and protocol
details.

---

## How do I get two-way audio through my doorbell?

Two-way audio (talk-back) is supported via the go2rtc backchannel. You will need the AlexxIT WebRTC card configured with a `url:` stream reference (not `entity:`). The `url:` form is required for the backchannel to function correctly.

See [services.md](services.md) for setup instructions and the required go2rtc configuration.
