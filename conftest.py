"""Project-level pytest configuration.

`pytest_plugins` must be declared in the rootdir conftest (per pytest rules),
not in nested test directories. This file keeps that single declaration in one
place so both the aqara_lanlink test tree (tracked) and any local-only test
trees in tests/ can share the HA test harness.
"""

pytest_plugins = "pytest_homeassistant_custom_component"
