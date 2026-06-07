"""Regression guard: registry discovery must skip _-prefixed modules
in device/models/, since those are private helpers (e.g. _loader.py),
not model packages.
"""
from __future__ import annotations


def test_loader_module_not_indexed_as_model_package():
    """Importing the integration must not raise on _loader.py because the
    registry skips _-prefixed modules in device/models/."""
    from custom_components.aqara_lanlink.device import registry
    # Force discovery; should NOT raise.
    registry._ensure_discovered()
    # The _loader module exists but is not a model — no model code should
    # be claimed for "_loader".
    from custom_components.aqara_lanlink.device.models import _loader  # noqa: F401
