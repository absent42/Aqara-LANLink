# Changelog

All notable changes to this project will be documented in this file.

## [0.5.3] - 2026-08-07

### Fixed

- Hub: entity state no longer stalls after an HA-initiated write. Self-echo suppression dropped the hub's echo report, which is the only state source for report-driven entities (lights and wire-path traits set no optimistic state), leaving state frozen at the pre-write value. A toggle automation re-sent turn_on on every trigger instead of turning off

### Removed

- Hub: self-echo report suppression via src origin (added in 0.5.0)

## [0.5.2] - 2026-07-08

### Added

- Composite entities: packed and JSON-blob rids exposed as native HA number, switch, text, and time entities via a codec registry and CompositeController read-modify-write
- Time platform for schedule-style rids
- Codecs for packed periods, brightness, schedule JSON, region unwrap, gesture bounding box, and PTZ preset
- Camera: composites for agl003, agl004, acn007, and acn010; packed and schedule rids moved to composite entities

## [0.5.1] - 2026-07-02

### Added

- Camera: expand G350 (agl010) settings block to 90 ResourceIDs

## [0.5.0] - 2026-07-01

### Added

- Camera: local LANLink settings control for relayed cameras
- Camera: settings block for G400 (agl013), G100 (agl005) and G350 (agl010)
- Hub: self-echo report suppression via src origin

## [0.4.2] - 2026-06-25

### Fixed

- Cloud signing rows corrected for US, HMT, OTHER, AU, and ME regions; added code-106 auto-probe fallback

## [0.4.1] - 2026-06-22

### Fixed

- Catalogue: .111 status channel reclassified as sensor; single-option commands exposed as buttons; lunar.acn01 entries curated

## [0.4.0] - 2026-06-19

### Added

- Text platform for string and packed rid-settings
- Catalogue: rid-settings regenerated across all models
- Catalogue: friendly attribute names and resource_code metadata
- Catalogue: settings and dropped_rids generated for aeu002, acn132, and additional models
- Device: per-model dropped_rids loaded and indexed

### Fixed

- Number: no-range rid-settings rendered as BOX input with wide bounds instead of slider
- Number: no-range numbers default to HA 0-100 range; packed values routed to text
- Catalogue: acn132 name-only dual settings removed via overrides
- Catalogue: enum-bearing dual settings deduplicated; description text-bleed stopped
- Text: cached state capped at 255 characters to prevent HA state ValueError

## [0.3.0] - 2026-06-17

### Added

- Settings: local rid-keyed device settings exposed as HA entities
- scan_device: owner-side wire_path to rid pairs exported as RESOURCE_IDS

### Fixed

- Rearm: sweep now runs on the event loop; re-arm triggered at setup

## [0.2.0] - 2026-06-15

### Added

- Activation: standalone Wi-Fi device relay activation (FP2 support)
- Entities: friendly names disambiguated across endpoints

### Fixed

- LANLink: stable per-install PhoneId; bound wedge re-arm on reconnect
- Activation: :443 probe removed before relay poke
- Catalogue: OccupancySensing spec bleed removed; S1 Plus name added
- Doorbell: ring event_type preserved for multi-press doorbells

## [0.1.0] - 2026-06-07

Initial public release.

- Hub discovery and LANLink session with local push for device state
- Device catalogue spanning sensors, switches, lights, locks, covers, climate, and more
- Camera streaming (RTSP) and go2rtc two-way audio (backchannel)
- PTZ control for supported cameras
- HACS-compatible custom integration

[0.5.2]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.5.2
[0.5.1]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.5.1
[0.5.0]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.5.0
[0.4.2]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.4.2
[0.4.1]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.4.1
[0.4.0]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.4.0
[0.3.0]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.3.0
[0.2.0]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.2.0
[0.1.0]: https://github.com/absent42/Aqara-LANLink/releases/tag/v0.1.0
