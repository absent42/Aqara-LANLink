# Aqara LANLink

A local-push Home Assistant integration for Aqara hubs,
their sub-devices, and cameras over the LAN, using the reverse-engineered Aqara LANLink and P2P protocols.

Device state is pushed from the hub to Home Assistant over a persistent encrypted TCP LAN connection; no polling or cloud roundtrip.

Aqara does not endorse or support this integration. The LANLink protocol was reverse-engineered from Aqara app and firmware; future firmware updates may break compatibility without warning.

## Features

- **~379 Aqara device models supported** out of the box, from a shipped device catalogue.
- **Local push, no polling** - device state is pushed from the hub over a persistent encrypted LANLink tunnel; state changes reach Home Assistant with no cloud round-trip.
- **Multiple Aqara hubs in a cluster**, and mutliple hubs bound to different regions.
- **Multiple entity types** - switches, sensors, binary sensors, lights, numbers, selects, buttons, and events are derived from each device's catalogue with names, values and labels.
- **Local device settings** - sub-device configuration that the trait catalogue does not cover (child lock, indicator light, button mode, power-off memory, max power, find/restart) is exposed as switch, select, number, and button entities and written fully locally over LANLink by resource ID.
- **Cameras and doorbells** - RTSP streaming via go2rtc, doorbell ring events, motion/occupancy/gesture etc detection events and sensors, and two-way audio (talk-back) through the go2rtc backchannel.
- **Local camera PTZ** - pan/tilt/zoom and saved-position presets for supported cameras, as buttons, a preset selector, and services.
- **Rich lights** - brightness, colour temperature, and full colour fused into a single light entity, plus Aqara and user created dynamic and static effects in the effect picker.
- **Privacy-conscious** - only a session token is stored, never your password; steady-state operation stays on your LAN.
- **Contributor-friendly device support** - per-model overrides for labels, entity types and more, plus manual cloud discovery (`scan_device`) service that exports both new traits and resource-ID pairs for a PR.
- **Self-healing and guided** - automatic reconnect, push-liveness recovery, and Home Assistant Repairs that flag stalled hubs or newly discovered device capabilities.

## How it works

The integration creates an encrypted tunnel between Home Assistant and an Aqara hub using a reverse engineered version of Aqara's LANLink protocol.

Once the tunnel is in place individual sub-device state changes and automations operate fully locally with every read and write going over the LAN tunnel to the hub which then relays them locally to the sub-device, with no roundtrip via the Aqara cloud servers.

The cloud is still required at two points:

- **Setup** - Adding a hub or a device makes a small number of HTTPS calls to
  Aqara's regional API: a login (to exchange credentials for a session token),
  sub-device enumeration (to build the device picker), and a light-effects fetch
  for lights.
- **Each restart and reconnect** - On every Home Assistant restart, the
  integration re-arms the local push subscription and seeds current device
  values from the cloud before switching to local-only steady-state operation.

If Aqara's cloud is unreachable when Home Assistant restarts, devices may come
up without freshly seeded state until the first push from the device. Once
connected and subscribed, cloud availability has no effect on state updates or
control.

Only a session token (not your password) is stored in the config entry.

For a deeper look at the workings and protocol details, see
[docs/architecture.md](docs/architecture.md).

## Requirements

- Home Assistant 2025.4.0 or later.
- An Aqara hub that supports LAN Control. The **M3 (firmware 4.5.50_0019)** has been tested; other `lumi1.` hubs may work but are unvalidated.
- **LAN Control enabled on the hub.** In the Aqara Home app, open the hub's
  settings and enable the LAN Control / local control option. Without it the hub
  will not accept the encrypted LANLink session.
- An Aqara account (email + password), or a user ID + session token
  obtained externally. Only the resulting session token is stored; credentials
  are not retained.
- go2rtc (optional, required for camera streaming and two-way audio). The
  integration declares it as an `after_dependency`.

## Installation

### HACS (custom repository)

[![Open HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=absent42&repository=Aqara-LANLink&category=Integration)

The integration is not yet in the default HACS catalogue. Add it as a custom
repository:

1. Click the **Open HACS** button above and confirm the custom repository
   prompt. Alternatively, in HACS open the three-dot menu, choose **Custom
   repositories**, paste `https://github.com/absent42/Aqara-LANLink`, and
   select **Integration** as the type.
2. Search HACS for "Aqara LANLink" and install it.
3. Restart Home Assistant.
4. Proceed to Configuration below.

### Manual install

1. Copy the `custom_components/aqara_lanlink/` directory into your Home
   Assistant configuration directory:

   ```
   <config>/
     custom_components/
       aqara_lanlink/
         __init__.py
         manifest.json
         ...
   ```

2. Restart Home Assistant.
3. Proceed to Configuration below.

## Configuration

Configuration is a two-step process: add the hub first, then add devices as
sub-entries.

### Step 1 - Add the hub

1. Go to **Settings > Devices & Services > Add Integration**.
2. Search for and select **Aqara LANLink**.
3. Click **Add hub**. If your hub is on the same LAN as Home Assistant it will appear in a
   discovered list. Select it. If it does not appear, select **Enter manually**
   and type the hub's IP address and DID (visible in the
   Aqara app under hub settings; starts with `lumi1.`).
5. On the credentials step, enter either:
   - Your Aqara account email, password, and region (exchanged for a session
     token; credentials are not stored), or
   - A user ID and token pasted directly, to skip the cloud login step.
6. Confirm the hub details. A preview of how many devices belong to this hub is
   shown if the cloud is reachable.

The hub entry is created and Home Assistant connects immediately.

### Step 2 - Add devices

1. On the integration card click **Add device** (or open the hub entry and click
   **Add device** from there).
2. The integration queries the cloud for the list of sub-devices paired to this
   hub. Supported devices appear in a picker.
3. Select a device and click **Next**. Cameras prompt for RTSP credentials in a
   follow-up step.
4. Repeat for each device you want to add.
5. Reload the integration to create the device entities, or if adding a device 
   with go2rtc streams restart HA.

Re-authentication: if the Aqara cloud no longer accepts the stored session
token (e.g. during the subscribe and seed re-arm), Home Assistant surfaces a
re-authentication prompt on the integration card. Enter fresh credentials
there; the hub entry is updated and reloaded automatically.

## Usage overview

Once a device is added, entities appear automatically. About 379 device models
are catalogued and will light up with friendly names and labelled controls out
of the box. See [docs/catalogue/index.md](docs/catalogue/index.md) for the live
list.

Cameras support RTSP streaming and two-way audio via the
[AlexxIT WebRTC custom card](https://github.com/AlexxIT/WebRTC) with a `url:`
stream reference (not `entity:`). The integration auto-registers a go2rtc
stream named `aqara_lanlink_<camera-IP-with-dots-as-underscores>` (e.g. camera
at 192.168.1.50 -> `aqara_lanlink_192_168_1_50`). PTZ-capable cameras
additionally get pan/tilt/zoom buttons, a preset selector, and a single
`aqara_lanlink.ptz` service (one `command` field covers move/stop/zoom/preset,
so the WebRTC card can drive PTZ); this requires the
camera IP to be set in the integration options - see
[docs/ptz.md](docs/ptz.md). For all available services
(`scan_device`, `export_overlay`, `play_audio_file`, and the PTZ service) see
[docs/services.md](docs/services.md).

If a device is added but missing expected traits, use the `scan_device` service
to trigger a re-scan without removing and re-adding the device. See
[docs/adding-device-support.md](docs/adding-device-support.md) for details.

## Device support

Around 379 device models are catalogued. Catalogued devices render with friendly
entity names, labelled enum options, and correct units immediately on add. 
Uncatalogued devices may still work through auto-discovery: entities appear with
names derived from the wire trait id and raw enum values rather than friendly
labels.

The live device list is at [docs/catalogue/index.md](docs/catalogue/index.md).

Most of these devices are currently untested with the integration and may create 
incorrect or non-functioning entities. An override mechanism is provided to correct 
or improve the auto-created entities. In time the device catalogue is hoped to be 
improved through user contributions.

The integration does not claim to expose the complete Aqara feature set. Coverage is limited by the reverse-engineered catalogue and the traits that Aqara's LANLink protocol supports for each model. 

To add or improve support for a device, see
[docs/adding-device-support.md](docs/adding-device-support.md).

## Documentation

Full documentation lives under [docs/](docs/README.md):

- [docs/architecture.md](docs/architecture.md) - protocol and session lifecycle
- [docs/catalogue/index.md](docs/catalogue/index.md) - supported device list
- [docs/services.md](docs/services.md) - available HA services
- [docs/adding-device-support.md](docs/adding-device-support.md) - adding or
  improving a device
- [docs/faq.md](docs/faq.md) - frequently asked questions
- [docs/troubleshooting.md](docs/troubleshooting.md) - troubleshooting and known limimitations

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a new device, author
traits, write tests, and submit a pull request.

## License

MIT. See [LICENSE](LICENSE).

## Support

_If you want to support this project please_

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png)](https://www.buymeacoffee.com/absent42)
