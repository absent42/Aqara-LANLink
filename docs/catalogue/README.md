# Catalogue reference library

This directory documents the Aqara device catalogue shipped with the
`aqara_lanlink` integration.

- `index.md` - one row per device model with three trait counts:
  Authored (HA entities, enabled-by-default plus press-to-trigger
  buttons), Diagnostic (HA entities under the diagnostic category,
  default-disabled), and Dropped (pids excluded from the runtime
  catalogue by `trait_policy.py`).
- `by-model/<model_dir>.md` - per-device-model markdown with one section
  per category: Supported traits, Diagnostic traits, press-to-trigger
  (Button) traits, and Dropped traits (grouped by drop reason).

The per-model and index files are auto-generated; do not hand-edit them.
This README is hand-written and survives regenerations.

## Finding support for your device

If a feature on your Aqara device is not appearing in Home Assistant:

1. Look up your device in `index.md` to find its model directory name.
2. Open `by-model/<model_dir>.md`. Pids under "Supported traits" and
   "Press-to-trigger (Button) traits" are already exposed as Home
   Assistant entities; pids under "Diagnostic traits" are exposed but
   default-disabled. Pids under "Dropped traits" are known to the V3
   spec but deliberately excluded from the runtime catalogue.
3. If the feature you want corresponds to a dropped pid, open an issue
   on the integration's repository naming the device model and the pid -
   the maintainers can use that information to reassess whether it should
   be surfaced.
