"""Tests for the rid-data renderers in aqara_lanlink.export_overlay.

Covers Task C: render the owner-discovered wire_path<->rid map (Part 1) as a
pasteable Python snippet.
"""
from __future__ import annotations

import ast

from custom_components.aqara_lanlink.services.export_overlay import (
    render_resource_id_map,
)


# --- Part 1: wire_path -> resource_id map --------------------------------


def test_render_resource_id_map_sorts_and_quotes():
    rendered = render_resource_id_map(
        "lumi.test",
        {"4.21.85": "9.1.85", "2.163.20237": "14.35.85"},
    )
    # header mentions the model and the authoritative source
    assert "lumi.test" in rendered
    assert "RESOURCE_IDS: dict[str, str] = {" in rendered
    # keys sorted: 2.163.20237 before 4.21.85
    assert rendered.index("'2.163.20237'") < rendered.index("'4.21.85'")
    # both key and value quoted via repr
    assert "'2.163.20237': '14.35.85'" in rendered
    assert "'4.21.85': '9.1.85'" in rendered
    # valid Python
    ast.parse(rendered)


def test_render_resource_id_map_empty_emits_comment():
    rendered = render_resource_id_map("lumi.test", {})
    assert rendered.startswith("#")
    assert "lumi.test" in rendered
    assert "no wire_path" in rendered.lower()
    assert "RESOURCE_IDS" not in rendered
