"""Regression guard: strings.json and translations/en.json must stay in sync.

HA serves UI strings to the frontend from ``translations/<lang>.json`` at
runtime; ``strings.json`` is the source-of-truth English template that
maintainers edit. If a new step/field/error/abort/service key is added
to ``strings.json`` without mirroring it to ``translations/en.json``,
the UI silently falls back to rendering the raw translation keys
("config.step.reconfigure.title") instead of the human-readable text.

This test pins exact equality so the mismatch is caught in CI rather
than at HA-reload time when a user opens the affected dialog.

Translated locales (``translations/<other>.json``) are intentionally
out of scope here -- they're allowed to lag and HA falls back to the
English key on missing entries. Only the English pair is locked.
"""
from __future__ import annotations

import json
from pathlib import Path

_INTEGRATION = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "aqara_lanlink"
)


def test_strings_and_en_translations_match():
    """Every key (and value) in strings.json must appear identically in
    translations/en.json. The test compares the parsed JSON structures
    directly so ordering and whitespace differences don't trip it up.

    On failure: copy missing entries from strings.json into
    translations/en.json (the en.json is the runtime-served file). If
    you intentionally diverged the English wording, update strings.json
    to match -- it's the maintainer-facing source.
    """
    strings = json.loads((_INTEGRATION / "strings.json").read_text())
    en = json.loads(
        (_INTEGRATION / "translations" / "en.json").read_text()
    )
    assert strings == en, (
        "strings.json and translations/en.json have drifted -- HA's UI "
        "loads from translations/, so any string in strings.json that "
        "isn't mirrored to translations/en.json will render as a raw "
        "translation key. Sync the two files before merging."
    )
