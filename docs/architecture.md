# Architecture

This document is the deep "how it works" reference for the Aqara LANLink integration. It is aimed at contributors and power users who want to understand the system end to end.

**Disclaimer:** This integration is unofficial and reverse-engineered. Firmware updates to the hub may break it at any time. It is not affiliated with or endorsed by Aqara.

---

## 1. Overview

Aqara LANLink is a local-push integration. At steady-state runtime, device state is pushed from the hub to Home Assistant over a persistent, encrypted TCP connection. There is no polling: HA reacts to what the hub sends.

The cloud is contacted at three points: during config-flow setup, on every HA startup or entry reload, and on every tunnel reconnect or topology change. At startup and on reconnect the integration calls the cloud to re-arm the hub's push subscription, seed current trait values, and fetch light effects. What is fully local is the steady state: once connected and subscribed, individual device state changes are pushed over the LAN with no cloud round-trip.

The one-line trade-off: the cloud is required at setup and on every reconnect/reload, but individual device state changes never leave the local network.

The integration does not claim to expose the complete Aqara feature set. Coverage is limited by the reverse-engineered catalogue and the traits that the LANLink protocol surfaces for each model.

---

## 2. Setup time (the cloud part)

### Config-flow steps

Adding a hub proceeds through these steps:

1. **user** -- mDNS discovery runs automatically. If one or more Aqara hubs are found and verified, a picker is shown. If discovery finds nothing, a manual form requests the hub IP and DID.

2. **credentials** -- Accepts either (a) an Aqara account email + password + region (the flow calls the cloud login endpoint to exchange them for a `user_id` and `token`) or (b) a directly pasted `user_id` + `token` that bypasses cloud login entirely. Either path is then validated by performing a one-shot LANLink checkin against the hub (10 s budget). Only the token is carried forward; the password is discarded after the exchange.

3. **confirm** -- A cloud device-count preview is fetched and displayed. The user confirms to create the config entry. The entry stores the hub IP, DID, model, account email hint, region, `user_id`, and `token`.

Adding individual devices uses a separate subentry flow (`pick_device`). The flow queries the cloud for the hub's sub-device list, classifies each device as supported or unsupported, and presents a picker. If a selected device's model is not yet in the catalogue, the flow routes through a `bootstrap_review` step that performs a cloud scan and lets the user accept traits into the per-install overlay before the subentry is committed.

The picker also offers **standalone Wi-Fi devices** (cloud devicetype 8, e.g. the Aqara FP2) that are bound to the account but not yet relayed by the hub. These are matched from the cloud device list against on-LAN mDNS discovery and shown labelled "needs activation"; a manual-IP fallback (`manual_activate`) lets the user type a device's LAN IP when mDNS misses it. When such a device is added it is *activated* (see section 3) so the hub begins relaying it over the existing LANLink tunnel, and its activation endpoint (host/port) is persisted on the subentry for later re-arming.

### What is cached on disk

When a subentry is created it stores `did`, `model`, and `_cloud_metadata` (the raw cloud device record). It does not store trait metadata. The entity set is reconstructed at every startup by running `build_descriptors` against the shipped `data.json` catalogue (plus `overrides.py` and the per-install overlay); no cloud call is needed for that step. The cloud is consulted on every startup to re-arm the push subscription, seed current trait values, and fetch light effects (see section 3).

### Credential storage

Only the `token` (not the password) is persisted in the config entry. If the token is rejected at runtime, HA raises a re-auth Repair issue that guides the user through the `reauth_confirm` step to supply fresh credentials. A `reconfigure` flow is also available for user-initiated credential updates.

---

## 3. Runtime (the local part)

### The LANLink session

At startup, `HubCoordinator` opens a persistent encrypted TCP connection to the hub (`EncryptedTunnel`: ECC key exchange followed by a symmetric tunnel). After the handshake, it sends a checkin message that authenticates the session using the stored `user_id` and `token`.

The coordinator runs a background task that loops indefinitely:
- Opens the tunnel and completes the checkin.
- Listens for inbound frames and dispatches them to registered per-DID report handlers.
- On any error or disconnect, closes the tunnel and waits using exponential backoff (2 s minimum, 60 s maximum, with per-client jitter) before reconnecting.

### Pushed trait reports

The hub pushes a frame for each trait state change. Each frame carries a sub-device DID and one or more wire-path/value pairs. The coordinator routes each frame to the `Device` instance registered for that DID. The device translates the wire-path key to an entity descriptor and calls `async_write_ha_state` on the relevant entity. No polling takes place during normal operation.

### Push subscription and initial seeding

The hub's push subscription is per-tunnel-connection: it is lost on every reconnect and must be re-armed. At setup, and again after every reconnect and after topology growth, the integration calls a cloud trait-read endpoint for each catalogued wire path, requesting subscription at the same time. The response also carries each trait's current value, which is seeded into the device so entities show state immediately rather than waiting for the next push.

### Topology

The hub periodically pushes a topology frame listing the DIDs of all reachable sub-devices. The coordinator exposes the current set as `lanlink_topology_dids`. The integration uses topology-growth events to trigger subscription re-arming: the hub forwards nothing until its mesh is ready, and a cold-start subscribe issued before topology is populated would produce no pushes.

### Standalone Wi-Fi device activation

A standalone Wi-Fi device (e.g. the FP2) is bound to the hub's account but is not relayed over LANLink until a controller *activates* it: open TLS to the device's local `:443` service and send a single LANLink handshake frame. The hub then adopts the device into its relay cluster -- it appears in the topology push and its trait reports flow over the existing tunnel like any sub-device, with no cloud round-trip in steady state. The handshake itself is expected to reset; sending the frame is the trigger and the response is irrelevant, so activation is best-effort and never raises.

The poke must reach a *quiescent* device: the firmware ignores an activation poke that arrives shortly after another `:443` connection to the device. The integration therefore must not probe the device's `:443` port (for example a TLS reachability/cert check) immediately before poking it -- the poke itself is the only `:443` connection the activation path makes, and it doubles as the reachability check (a still-booting device simply fails the connect and is retried).

Activation is not durable across a hub reboot or a device power-cycle; either drops the device from the topology. `RearmManager` watches topology pushes and re-pokes a configured standalone device when it falls out of the relay cluster, using an absence debounce and a per-device cooldown (repeated pokes can themselves destabilise the relay) plus a periodic sweep for the case where a returning device emits no topology push.

### Reconnect and the push-liveness watchdog

When the tunnel drops, the coordinator reconnects with exponential backoff and fires `on_session_up`, which re-arms the push subscription. A separate push-liveness watchdog runs on every keepalive acknowledgement (approximately every 10 s). If the hub is connected and reports a non-empty topology but no trait report has arrived within 300 s, the watchdog re-arms the subscription. After three consecutive failed re-arms without a report resuming, the watchdog escalates to a Repair issue (`push_stalled`) advising the user to reboot the hub or check that LAN Control is enabled in the Aqara app.

---

## 4. The catalogue-to-entity pipeline

The integration translates per-model catalogue data into HA entity descriptors through a deterministic, pure pipeline that runs once at setup time per device.

```
data.json + overrides.py   per-install overlay
(shipped catalogue)        (HA .storage file)
        |                        |
        v                        v
   catalog module           overlay module
        |                        |
        +----------+-------------+
                   |
                   v
         build_descriptors(model, overlay)
         [pure, deterministic; merges with override-on-top semantics]
                   |
                   v
            classify_v3(model, endpoints, merged_traits)
            [groups traits by endpoint; dispatches each endpoint's
             deviceType to its composer in device/device_types/]
                   |
                   v
         list[AnyDescriptor]
         (BinarySensorDescriptor, LightDescriptor, SensorDescriptor, ...)
                   |
                   v
         AutoDerivedDevice.async_setup()
         [iterates descriptors; registers one HA entity per descriptor
          on each platform in PLATFORMS]
                   |
                   v
         HA platform entities
         (binary_sensor, button, camera, event, light,
          number, select, sensor, switch)
```

The pipeline steps in detail:

- `catalog.all_traits_for_model(model)` loads the per-model `TraitSpec` dict from the shipped `data.json` files (via the model package index in `registry.py`). `catalog.endpoints_for_model(model)` loads the endpoint map.
- `overlay.traits_for_model(model)` returns the per-install overlay traits for the same model (empty dict if none exist).
- `build_descriptors` merges the two with override-on-top semantics: an overlay entry replaces the catalogue entry at the same wire path; a `None` overlay entry removes a catalogue entry. The merge result is passed to `classify_v3`.
- `classify_v3` groups traits by endpoint ID, looks up each endpoint's `deviceType`, and calls the matching composer from `device/device_types/`. Composers such as `Light` fuse multiple traits (OnOff, LevelControl, ColorControl) into a single `LightDescriptor`. A `_fallback` composer handles endpoints with unrecognised device types by producing one entity per trait.
- The resulting `list[AnyDescriptor]` is deterministic: identical catalogue + overlay inputs always produce identical output.

For the data-model vocabulary (TraitSpec fields, descriptor types, wire paths, trait policies, dropped paths), see [catalogue-and-traits.md](catalogue-and-traits.md).

---

## 5. The per-install overlay

The overlay is a local extension of the shipped catalogue stored in HA's `.storage` directory (`aqara_lanlink.overlay`). It is read once at integration startup and passed into `build_descriptors` for every device. Its merge semantics give it precedence: overlay entries replace or remove catalogue entries at the same wire path.

Only two code paths write the overlay:
- The `scan_review` Repair accept handler in `repairs.py` (`_ScanReviewFlow._accept`), which is triggered after the user clicks Fix on the Repair issue raised by the `scan_device` service. The service itself raises the Repair and writes nothing; the overlay write happens when the user reviews and accepts the discovered traits.
- The `bootstrap_review` step of the subentry flow (`config_flow.py` `_write_bootstrap_acceptance`), when the user first adds an uncatalogued model.

The overlay is never written during normal setup, push handling, or entity operation. Across a normal HA session it is immutable unless the user explicitly accepts a discovery.

See [overrides.md](overrides.md) for how to use the overlay to correct shipped catalogue data for your installation.

---

## 6. Data-first design

Device support is driven by data, not by Python code per model. The shipped catalogue (`data.json` files in each model package under `device/models/`) defines traits; `overrides.py` in each package carries baked-in corrections to labels, enum values, and display names that are too model-specific to belong in the generator workspace.

`AutoDerivedDevice` is the stock device class used for all models today. It takes the descriptor list produced by `build_descriptors`, iterates it, and registers one HA entity per descriptor. `AutoDerivedCameraDevice` is a specialised variant for camera models that adds RTSP stream management on top of the same derived-entity base.

The `@register_device` decorator in `device/registry.py` exists for future per-model Python override classes. No override classes ship today; `get_device_class` returns `None` for every model, and the integration falls back to `AutoDerivedDevice` (or `AutoDerivedCameraDevice` for cameras). The mechanism is in place for cases where a model needs behaviour that cannot be expressed in the catalogue alone.

For the contributor workflow for adding model support, see [adding-device-support.md](adding-device-support.md).

---

## 7. Entity behaviour and naming

### Diagnostic and disabled-by-default entities

The `trait_policy` applied at catalogue generation time classifies each trait. Traits classified as diagnostic are exposed as `EntityCategory.DIAGNOSTIC` entities. Traits the policy drops entirely (administrative protocol chatter such as `ZigbeeNetworkDiagnostics` or `BasicInformation.Mac`) are recorded in the model package's `DROPPED_PATHS` set and never create entities; the integration silently consumes push reports for those paths without surfacing them.

### Generic names

Because entity names come from the catalogue's `name` field (baked in from the Aqara V3 spec display name at generation time), some entities carry generic names such as "On off" or "Level control". These can be overridden at the HA entity level using the standard HA rename feature. Catalogue label corrections for a specific model can also be contributed via `overrides.py` in the model package.

### The dropped_paths concept

If a trait is visible on the wire (in push reports) but absent from HA, it is either in `DROPPED_PATHS` (intentionally excluded by policy), absent from the catalogue for that model, or present in the catalogue but producing no descriptors after classification. The `candidate_paths` Repair issue (see section 8 below) surfaces newly observed wire paths not yet in the catalogue. See [catalogue-and-traits.md](catalogue-and-traits.md) and [troubleshooting.md](troubleshooting.md) for diagnosis steps.

---

## 8. Repair issues

The integration raises three categories of HA Repair issues:

- **`push_stalled`** -- "Aqara hub stopped sending updates." The hub is connected and reports a non-empty topology, but no trait report has arrived for an extended period. Probable causes: the LAN Control toggle in the Aqara app was disabled, or the hub needs a reboot. The watchdog re-arms the subscription automatically; the Repair is raised after three consecutive failed re-arms.

- **`candidate_paths`** -- "New device capabilities observed." Wire paths arrived in push reports that are not in the catalogue or overlay for the relevant model. The Fix action invokes the `aqara_lanlink.scan_device` service to perform a cloud scan and surface a review flow. See [services.md](services.md).

- **`scan_review_<entry>_<did>`** -- Raised after `scan_device` completes. The user reviews the discovered traits and accepts them into the per-install overlay.

---

## 9. Region notes

The Aqara cloud is region-partitioned. The region selected at config-flow time determines which cloud endpoint is used for all cloud calls (login, device enumeration, trait reads). The correct region is the one associated with the Aqara account; using the wrong region will result in an authentication failure.

The shipped catalogue records which region's cloud spec a model's trait data was scraped from in a `source_region` field on the model package. The scrape order is EU first, then CN. A model whose catalogue originates from the CN region may carry trait labels in Chinese or in PascalCase identifiers that read oddly in English. Corrections to labels, enum values, or display names that should apply to everyone can be contributed via `overrides.py` in the model package (a shipped, contributor-authored file). A per-installation trait addition that only applies to one install goes via the overlay (written through the scan_device Repair accept flow).

---

## 10. Security and privacy

### What is stored

Only the `token` (not the password) is persisted in the HA config entry. The password is exchanged once for a token at config-flow time and never written to disk.

### What leaves the local network and when

- **Config-flow time:** cloud login (email/password path only) and device enumeration/preview. These happen when the user runs the config-flow wizard to add the hub or a device subentry.
- **Every HA startup / reload / reconnect:** per-device subscribe+seed (re-arming the push subscription and fetching current trait values) and light-effects enrichment. These calls happen on every entry load and after every tunnel reconnect or topology change, not only during the initial config-flow.
- **Steady-state runtime:** nothing leaves the local network. Individual device state changes are pushed from the hub over the LAN; write commands are sent directly over the LANLink TCP session with no cloud relay.
- **If `scan_device` is invoked:** one additional cloud round-trip per scan.

### Unofficial status

This integration is built on a reverse-engineered protocol. Aqara does not document the LANLink wire format or the local API. A hub firmware update may change the protocol in ways that break this integration without warning. Use it with that understanding.

---

## 11. Camera PTZ

Camera PTZ (pan, tilt, zoom) is a separate local P2P control plane built on
the reverse-engineered TUTK/PPPP/CS2 protocol. It is entirely distinct from
LANLink and is not represented in the V3 trait catalogue. PTZ capability is
declared per-model in the camera's `overrides.py` via a `CAPABILITIES` dict;
the model loader indexes the declared sub-features and exposes them through
`catalog.ptz_features_for_model()`. Session authentication for PTZ is
cloud-assisted, so a valid cloud login is required even though movement
commands are sent directly over the LAN. See [ptz.md](ptz.md) for the full
protocol description.

---

*Related documentation:*
- [catalogue-and-traits.md](catalogue-and-traits.md) -- data model vocabulary: TraitSpec, descriptors, wire paths, trait policy
- [overrides.md](overrides.md) -- correcting catalogue data for your installation
- [adding-device-support.md](adding-device-support.md) -- contributor workflow for new models
- [services.md](services.md) -- `aqara_lanlink.scan_device` and other services
- [troubleshooting.md](troubleshooting.md) -- diagnosing missing entities, stalled pushes, and other issues
