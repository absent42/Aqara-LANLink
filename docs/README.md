# Aqara LANLink - Documentation

This directory contains the in-depth technical documentation for the Aqara LANLink Home Assistant integration - an unofficial, local-push integration that controls Aqara devices through a local hub over the reverse-engineered LANLink protocol. The cloud is used only during initial setup and at restart to obtain credentials and the device catalogue; all steady-state control and state updates are local. For installation instructions and a high-level overview of the integration, start with the top-level project README at [../README.md](../README.md).

---

## Contents - recommended reading order

| Document | Description |
|---|---|
| [architecture.md](architecture.md) | How the integration works end to end: cloud setup vs local steady-state, the LANLink session, and the catalogue-to-entity pipeline. **START HERE.** |
| [catalogue-and-traits.md](catalogue-and-traits.md) | The data model and vocabulary: `data.json`, `TraitSpec`, wire paths, endpoints, `deviceTypes`, composers, and a full glossary. |
| [overrides.md](overrides.md) | The per-model `overrides.py` mechanism for correcting labels, units, and entity types without regenerating the catalogue. |
| [adding-device-support.md](adding-device-support.md) | How to add or fix support for a device and submit a PR, covering all three contribution tiers. |
| [services.md](services.md) | The integration's custom services (`scan_device`, `export_overlay`, `play_audio_file`) and the two-way-audio backchannel. |
| [ptz.md](ptz.md) | Camera pan/tilt/zoom: requirements, entities, services, and enabling it per model. |
| [troubleshooting.md](troubleshooting.md) | Symptom-to-fix guide covering connection problems, missing entities, camera issues, and Repair notifications. |
| [faq.md](faq.md) | Short answers to the most common questions. |
| [catalogue/](catalogue/) | Auto-generated per-model reference library. See [catalogue/index.md](catalogue/index.md) for a list of every catalogued model. |
