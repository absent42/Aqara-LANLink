"""Tests for the auto-generated reference library under docs/catalogue/."""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOGUE_DIR = _REPO_ROOT / "docs" / "catalogue"
_BY_MODEL_DIR = _CATALOGUE_DIR / "by-model"
_INDEX_MD = _CATALOGUE_DIR / "index.md"
_README_MD = _CATALOGUE_DIR / "README.md"
_MODELS_DIR = (
    _REPO_ROOT / "custom_components" / "aqara_lanlink" / "device" / "models"
)


def test_index_md_exists():
    assert _INDEX_MD.exists(), "docs/catalogue/index.md missing -- regen needed"


def test_readme_md_exists():
    assert _README_MD.exists(), (
        "docs/catalogue/README.md missing -- hand-written, must survive regens"
    )


def test_by_model_dir_has_one_file_per_model():
    """Every model directory should have a corresponding markdown file."""
    model_dirs = {
        p.name for p in _MODELS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("__")
    }
    md_files = {p.stem for p in _BY_MODEL_DIR.glob("*.md")}
    missing = model_dirs - md_files
    orphans = md_files - model_dirs
    assert not missing, f"models without markdown: {sorted(missing)[:5]}"
    assert not orphans, f"orphan markdown files: {sorted(orphans)[:5]}"


def test_index_has_expected_columns():
    text = _INDEX_MD.read_text(encoding="utf-8")
    # V3-native index columns produced by tools/render_catalogue_docs.py.
    assert "| Model |" in text
    assert "| Display name |" in text
    assert "| Authored |" in text
    assert "| Diagnostic |" in text
    assert "| Dropped |" in text


def test_per_model_content_spot_check():
    """A known-authored model has at least one V3 trait listed in
    'Supported traits' AND a 'Dropped traits' section describing what
    trait_policy filtered. light_acn003 is a representative -- it has a
    full V3 spec capture plus the usual BasicInformation drop set.
    """
    md = _BY_MODEL_DIR / "light_acn003.md"
    text = md.read_text(encoding="utf-8")
    # Supported section is populated.
    assert "## Supported traits" in text
    # A canonical V3 power trait shows up under the Light composer.
    assert "`Output.OnOff`" in text
    # The dropped-section + at least one known DROP_FUNCTIONS member.
    assert "## Dropped traits" in text
    assert "BasicInformation" in text


@pytest.mark.skip(reason="regen-stability check; run only after a fresh regen")
def test_regen_byte_stability():
    """Regenerating produces byte-identical output (no timestamp drift).

    Skipped by default; run manually with -m or unskip when triaging
    drift. Implementer: run `python tools/extract_plugins.py` twice
    back-to-back and confirm `git diff docs/catalogue/` is empty after
    the second run.
    """
    pass
