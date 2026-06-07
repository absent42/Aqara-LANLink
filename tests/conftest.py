"""Shared fixtures for Aqara LANLink tests.

pytest_plugins is declared in the project-root conftest.py (required by
pytest - the directive is only honored at rootdir).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield
