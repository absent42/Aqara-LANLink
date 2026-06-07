"""Firmware attribute-name catalog.

An `AttrSpec` describes a single firmware attribute name -- the wire-level
identifier that LANLink read/write messages reference. Where `TraitSpec` is
keyed by a dotted numeric trait id, `AttrSpec` is keyed by the symbolic
`name` (e.g. auto-generated `"auto_2_133_32923"`).

The catalog ships empty in source. All entries are auto-derived runtime
registrations, populated by the auto-derive pipeline for writable traits.
Authored (hand-curated) knowledge lives in `catalog` and the per-model
packages under `device/models/`, not in this catalog.

The shape of `AttrSpec` mirrors `TraitSpec` deliberately: in many cases the
same metadata describes both the trait id and the firmware attribute. The
two catalogs stay separate because the *namespaces* are different (numeric
trait id vs. symbolic firmware name), even though the fields are similar.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable

# Module-level mutable catalog. Keyed by `name`.
BY_NAME: dict[str, "AttrSpec"] = {}

_change_listener: Callable[[], None] | None = None


@dataclass(frozen=True)
class AttrSpec:
    """A single firmware attribute's metadata.

    `name` is the wire identifier sent to the hub in read/write messages
    (e.g. `data.attrs[0]` for reads, the dict key for writes). `id` is the
    associated trait id when known -- mostly informational; `name` is the
    primary key for catalog lookup.
    """

    name: str
    id: str = ""
    description: str = ""
    data_type: str = "unknown"
    unit: str | None = None
    enum_values: tuple[str, ...] | None = None
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None


def register_attr(spec: AttrSpec) -> AttrSpec:
    """Register or merge `spec` into the catalog. See `traits.register_trait`."""
    existing = BY_NAME.get(spec.name)
    if existing is None:
        BY_NAME[spec.name] = spec
        _notify_change()
        return spec
    if existing == spec:
        return existing
    BY_NAME[spec.name] = spec
    _notify_change()
    return spec


def get(name: str) -> AttrSpec | None:
    """Return the registered `AttrSpec` for `name`, or None."""
    return BY_NAME.get(name)


def all_attrs() -> list[AttrSpec]:
    """Snapshot of every registered attr."""
    return list(BY_NAME.values())


def reset_for_tests() -> None:
    """Clear the catalog. Tests only."""
    BY_NAME.clear()
    global _change_listener
    _change_listener = None


def set_change_listener(listener: Callable[[], None] | None) -> None:
    """Install a callback fired after every catalog-mutating registration."""
    global _change_listener
    _change_listener = listener


def _notify_change() -> None:
    listener = _change_listener
    if listener is None:
        return
    try:
        listener()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "BY_NAME",
    "AttrSpec",
    "all_attrs",
    "get",
    "register_attr",
    "replace",
    "reset_for_tests",
    "set_change_listener",
]
