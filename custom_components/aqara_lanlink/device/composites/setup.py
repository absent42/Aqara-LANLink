"""Shared per-platform enumerator for composite sub-entities.

A composite rid packs several logical fields into one wire value; each field
is surfaced as its own native HA entity bound to a shared
:class:`CompositeController`. This module produces the entities for one
platform (time / number / switch / text) across a single device's
controllers, applying the display naming derived from the model's composites
catalog block.
"""

from __future__ import annotations

from .entities import CompositeNumber, CompositeSwitch, CompositeText, CompositeTime

_ENTITY_BY_PLATFORM = {
    "switch": CompositeSwitch,
    "number": CompositeNumber,
    "text": CompositeText,
    "time": CompositeTime,
}


def composite_entities_for_platform(hub, device, subentry, controllers, platform, decls):
    """One entity per codec field whose ``field.platform == platform``, across this
    device's controllers. ``controllers`` is the (did, rid)-keyed flat dict.
    ``decls`` maps rid -> {"codec","name"} (from catalog.composites_for_model) for
    entity display naming."""
    out = []
    cls = _ENTITY_BY_PLATFORM[platform]
    for (did, rid), controller in controllers.items():
        if did != device.did:
            continue
        display = decls.get(rid, {}).get("name", rid)
        for f in controller.codec.fields:
            if f.platform != platform:
                continue
            ent = cls(hub, device, subentry, controller, f)
            ent._attr_name = f"{display} {f.label}"   # _attr_has_entity_name=True so this is the entity name
            out.append(ent)
    return out


__all__ = ["composite_entities_for_platform"]
