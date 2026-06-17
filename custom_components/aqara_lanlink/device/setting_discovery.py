"""Pure logic for deriving the wire-path -> rid bridge from a cloud scan.

One pure function, no HA / cloud / I/O:

- `extract_resource_id_map` derives the authoritative wire-path -> rid
  bridge from a qlink/trait/read scan response (the `propertyId` field).
"""

from __future__ import annotations

from typing import Any


def _canonical_path(path: str) -> str:
    """Strip a trailing `.<idx>` so a 4-part wire path becomes 3-part.

    Mirrors `device/base.py::_canonicalize_report_key`: cloud trait paths
    and our descriptors use the 3-part `<endpoint>.<group>.<res>` form,
    while LANLink sometimes carries a 4-part `....<idx>` variant. Only the
    4-part-with-digit-suffix case is stripped; anything else passes through.
    """
    parts = path.split(".")
    if len(parts) == 4 and parts[-1].isdigit():
        return ".".join(parts[:3])
    return path


def extract_resource_id_map(traits_response: Any) -> dict[str, str]:
    """Map canonical wire-path -> rid from a qlink/trait/read scan response.

    `traits_response` mirrors the real response shape::

        {"result": [
            {"traits": [
                {"path": "2.163.20237", "propertyId": ["14.35.85"], ...},
                {"path": "...", ...},  # may lack propertyId
            ], "deviceId": "...", ...},
            ...
        ], ...}

    For every trait carrying a non-empty `propertyId` list, map its
    canonicalised `path` to `propertyId[0]`. Traits without a usable path
    or propertyId are skipped. Defensive against missing keys and
    non-list propertyId values.
    """
    mapping: dict[str, str] = {}
    if not isinstance(traits_response, dict):
        return mapping
    devices = traits_response.get("result") or []
    if not isinstance(devices, list):
        return mapping
    for device in devices:
        if not isinstance(device, dict):
            continue
        traits = device.get("traits") or []
        if not isinstance(traits, list):
            continue
        for trait in traits:
            if not isinstance(trait, dict):
                continue
            path = trait.get("path")
            property_id = trait.get("propertyId")
            if not path or not isinstance(path, str):
                continue
            if not isinstance(property_id, list) or not property_id:
                continue
            rid = property_id[0]
            if not rid or not isinstance(rid, str):
                continue
            mapping[_canonical_path(path)] = rid
    return mapping


__all__ = ["extract_resource_id_map"]
