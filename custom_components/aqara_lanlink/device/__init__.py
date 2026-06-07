"""Device package init.

Device knowledge is scoped per-model and lives in per-model packages
under device/models/<model>/. These packages register their TraitSpec
entries at import time, scoped to the specific device model's catalog.
"""

from __future__ import annotations
