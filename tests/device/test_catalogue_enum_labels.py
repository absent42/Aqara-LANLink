"""Invariant: shipped enum labels are human-readable.

Enum value labels are humanized at catalogue-generation time (PascalCase
``DoorForcedOpen`` becomes ``Door forced open``) and baked into each
model's data.json, mirroring how trait *names* are baked. This guards
that the catalogue stays humanized -- a regen that dropped the transform,
or a hand-edit reintroducing PascalCase, fails here.

Enum sensors and selects surface these labels verbatim as the entity
state / option text, so a raw PascalCase label is a user-visible defect.
(Events re-tokenize the label via _snake_case, so they are unaffected
either way.)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.aqara_lanlink.device.humanize import humanize_name

_MODELS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "custom_components" / "aqara_lanlink" / "device" / "models"
)


def _enum_labels():
    """Yield (model, wire_path, label) for every enum value in every model."""
    for data_json in sorted(_MODELS_ROOT.glob("*/data.json")):
        raw = json.loads(data_json.read_text())
        for wp, trait in (raw.get("traits") or {}).items():
            ev = trait.get("enum_values")
            if isinstance(ev, dict):
                for label in ev.values():
                    yield data_json.parent.name, wp, label


def test_all_enum_labels_are_humanized():
    """Every enum label equals its humanized form (idempotency check)."""
    offenders = [
        (model, wp, label, humanize_name(label))
        for model, wp, label in _enum_labels()
        if label != humanize_name(label)
    ]
    assert not offenders, (
        "Non-humanized enum labels found in data.json (showing up to 20):\n"
        + "\n".join(
            f"  {m} {wp}: {label!r} -> should be {want!r}"
            for m, wp, label, want in offenders[:20]
        )
    )
