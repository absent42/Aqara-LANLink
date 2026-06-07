"""The shipped catalogue carries no untranslated CJK text.

The maintainer-side generator (tools/) translates CN-region labels and display
names to English at regen time via tools/catalogue_corrections.py. This guards
the OUTPUT -- the committed data.json -- directly, independent of that
gitignored tooling, so a regen that dropped the corrections, or a newly-scraped
CN-named model, fails here and prompts a correction + regen.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from custom_components.aqara_lanlink.device import models as _models_pkg

_MODELS = Path(_models_pkg.__file__).resolve().parent
# Common/Unified CJK ideographs -- enough to catch leaked CN labels/names.
_CJK = re.compile(r"[㐀-鿿]")


def _all_data_json() -> list[dict]:
    return [
        json.loads((d / "data.json").read_text())
        for d in _MODELS.iterdir()
        if (d / "data.json").is_file()
    ]


def test_no_cjk_in_shipped_display_names():
    leaked = [
        (data.get("model"), data.get("display_name"))
        for data in _all_data_json()
        if _CJK.search(data.get("display_name") or "")
    ]
    assert not leaked, (
        f"CJK display_name(s) in the catalogue -- add a correction to "
        f"tools/catalogue_corrections.py and regen: {leaked}"
    )


def test_no_cjk_in_shipped_enum_labels():
    leaked = [
        (data.get("model"), wp, v)
        for data in _all_data_json()
        for wp, t in data.get("traits", {}).items()
        for v in (t.get("enum_values") or {}).values()
        if _CJK.search(v)
    ]
    assert not leaked, (
        f"CJK enum label(s) in the catalogue -- add a correction to "
        f"tools/catalogue_corrections.py and regen: {leaked}"
    )
