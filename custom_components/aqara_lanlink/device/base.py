"""`Device` abstract base class + `AutoDerivedDevice` stock subclass.

Each Aqara device represented in HA is backed by a `Device` instance. The
class is "abstract" only in spirit; it has a fully usable default
implementation, and most devices end up using `AutoDerivedDevice` (the
stock subclass that pulls metadata from the cloud-supplied subentry).

Per-device override classes live under `device/models/<model>/__init__.py`
and register via the `@register_device` decorator in `registry.py`. They
override class-level descriptor lists or the imperative
`async_setup` / `async_setup_camera` / `async_unload` hooks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from . import catalog
from .descriptors import (
    AnyDescriptor,
    BinarySensorDescriptor,
    EventDescriptor,
    LightDescriptor,
    NumberDescriptor,
    SelectDescriptor,
    SensorDescriptor,
    SwitchDescriptor,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelfDeviceContext:
    """Minimal context for a tunnel host's own (self) device.

    Carries just enough information to stand in for a HA config subentry
    when constructing a Device and AqaraEntity for the tunnel host's OWN
    traits (e.g. built-in sensors, IR, AC). There is no subentry for this
    device; unique-ids are derived from the tunnel-host DID, which is
    globally unique.

    Attributes:
        did: The tunnel host's device id (globally unique, stable across
            restarts -- derived from MAC address in Aqara's scheme).
        model: The tunnel host's model string (e.g. ``lumi.gateway.m3``).
        camera_ip: The camera's RTSP endpoint IP address, populated from
            ``entry.options`` when the entry hub is itself a camera; empty
            string when unset.
        rtsp_username: The camera's RTSP username, populated from
            ``entry.options`` when the entry hub is itself a camera; empty
            string when unset.
        rtsp_password: The camera's RTSP password, populated from
            ``entry.options`` when the entry hub is itself a camera; empty
            string when unset.
        backchannel_channel: The RTSP channel the go2rtc two-way-audio
            backchannel routes through (1 high, 2 medium, 3 low); defaults
            to 1.

    The ``subentry_id`` property returns a stable namespace string of the
    form ``self_<did>``. This is what AqaraEntity uses as the unique-id
    prefix (``subentry_id + '_' + descriptor.key``), so self-device entity
    unique_ids are of the form ``self_<did>_<key>`` -- guaranteed to be
    distinct from any subentry entity, whose prefix is a UUID4 string.

    The ``data`` property returns a mapping compatible with
    ``subentry.data`` for the fields that Device and AqaraEntity read:
    ``did`` and ``model``. When ``camera_ip`` is set it also includes the
    four camera keys: ``camera_ip``, ``rtsp_username``, ``rtsp_password``,
    and ``backchannel_channel``.
    """

    did: str
    model: str
    camera_ip: str = ""
    rtsp_username: str = ""
    rtsp_password: str = ""
    backchannel_channel: int = 1

    @property
    def subentry_id(self) -> str:
        """Stable unique-id namespace derived from the tunnel-host DID."""
        return f"self_{self.did}"

    @property
    def data(self) -> dict[str, Any]:
        """Mapping compatible with subentry.data for the fields code reads."""
        d: dict[str, Any] = {"did": self.did, "model": self.model}
        if self.camera_ip:
            d["camera_ip"] = self.camera_ip
            d["rtsp_username"] = self.rtsp_username
            d["rtsp_password"] = self.rtsp_password
            d["backchannel_channel"] = self.backchannel_channel
        return d


def _canonicalize_report_key(key: str) -> str:
    """Strip a trailing `.<digits>` LANLink resource-index suffix.

    Live push reports key state by 4-part path (e.g. `2.132.32920.1`)
    while cloud trait paths -- and our descriptors -- use 3-part
    `<endpoint>.<group>.<res>`. Strip the trailing segment when the
    key has more than 3 dotted parts and the trailing segment is a
    plain integer; pass anything else through unchanged.
    """
    parts = key.split(".")
    if len(parts) > 3 and parts[-1].isdigit():
        return ".".join(parts[:-1])
    return key


_LIGHT_COMPONENTS: frozenset[str] = frozenset(
    {"power", "brightness", "color_temp", "color_xy"}
)
# Single source of truth for a LightDescriptor's absorbed components:
# (component_name, trait_field, attr_field). Both _descriptor_wire_paths and
# _build_indices walk this, so adding a 5th component only touches one place.
_LIGHT_COMPONENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("power", "power_trait", "power_attr"),
    ("brightness", "brightness_trait", "brightness_attr"),
    ("color_temp", "color_temp_trait", "color_temp_attr"),
    ("color_xy", "color_xy_trait", "color_xy_attr"),
)


def _index(table: dict[str, list], key: str, desc: Any) -> None:
    """Append ``desc`` to the list stored under ``key`` (creating it if absent)."""
    table.setdefault(key, []).append(desc)


def _descriptor_wire_paths(
    descriptor: Any,
) -> Iterator[tuple[str, str | None]]:
    """Yield (wire_path, component) for every trait this descriptor exposes.

    Atomic descriptors yield exactly one entry with component=None.
    LightDescriptor yields one entry per absorbed component (power,
    brightness, color_temp, color_xy), each tagged with its component
    name so register_entity can route the value into the right field
    of the light entity.
    """
    trait = getattr(descriptor, "trait", None)
    if trait is not None:
        path = getattr(trait, "id", None)
        if path:
            yield (path, None)
        return
    if isinstance(descriptor, EventDescriptor):
        trigger = descriptor.trigger_trait
        path = getattr(trigger, "id", None)
        if path:
            yield (path, None)
        return
    # Attr-backed descriptors (Switch, Select, Number): the attr name IS the
    # wire path. These don't carry a trait field; yield the attr name directly.
    attr = getattr(descriptor, "attr", None)
    if attr is not None:
        path = getattr(attr, "name", None)
        if path:
            yield (path, None)
        return
    # LightDescriptor: walk each absorbed component.
    for component, trait_field, _attr_field in _LIGHT_COMPONENT_FIELDS:
        t = getattr(descriptor, trait_field, None)
        if t is not None:
            path = getattr(t, "id", None)
            if path:
                yield (path, component)


def _is_numeric_path(key: str) -> bool:
    """True when key has 3+ parts and every part is a non-empty digit run.

    Filters out attr-name keys (`device_i_am`) and other non-path tokens
    from the LANLink push handler so ObservedPathCache only stores wire
    paths.
    """
    parts = key.split(".")
    if len(parts) < 3:
        return False
    return all(p.isdigit() for p in parts)


def _should_replay_seed(descriptor: Any) -> bool:
    """Whether to replay seeded initial values into an entity on register.

    On integration reload, `_subscribe_and_seed_traits` re-reads every
    trait's last cloud-stored value and seeds it into the device. For
    stateful traits (switch on/off, light level, battery percent) that's
    correct -- the cloud value IS the entity's current state. For
    event-shaped or momentary traits the cloud's last value is whatever
    the last activation emitted, kept sticky until the next sensor
    cycle; replaying that into `apply_value` would treat it as
    "happening now" and refire automations on every reload.

    The two markers for "do not replay":
      - Any EventDescriptor by construction (events are notifications).
      - BinarySensorDescriptor with `auto_clear_seconds` set OR
        `on_any_value=True` -- both signal the trait is momentary.
    Other binary sensors (door contact, leak, presence) remain stateful
    and seed normally.
    """
    if isinstance(descriptor, EventDescriptor):
        return False
    if isinstance(descriptor, BinarySensorDescriptor):
        if getattr(descriptor, "auto_clear_seconds", None):
            return False
        if getattr(descriptor, "on_any_value", False):
            return False
    return True


class Device:
    """Base class for every device the integration manages.

    Class-level slots declare the device's static descriptor lists and
    metadata. Subclasses populate them at class-definition time. Instance
    state (entities registered, pending initial values, the coordinator
    handle) lives on `self`.

    The constructor accepts a `derived` argument carrying auto-derived
    descriptors from the cloud-trait pipeline. Three merge modes are
    supported, controlled by `AUGMENT_AUTO_DERIVE`:

    1. ``AUGMENT_AUTO_DERIVE = True`` -- auto-derived descriptors PLUS the
       class-level lists are both included. Use when the override class
       adds a few extras on top of what auto-derive produces (G400 case).
    2. ``AUGMENT_AUTO_DERIVE = False`` (default) AND class lists are
       non-empty -- the class lists fully replace auto-derive output.
       Use when the override class wants total control over what entities
       appear.
    3. ``AUGMENT_AUTO_DERIVE = False`` AND class lists are empty -- only
       auto-derived descriptors are used. The default for
       `AutoDerivedDevice` and any plain `Device` instance constructed
       without an override.
    """

    # ------------------------------------------------------------------
    # Class-level metadata slots (subclasses override).
    # ------------------------------------------------------------------

    MODEL: str = ""
    MANUFACTURER: str = "Aqara"
    DISPLAY_NAME: str = ""
    # Cluster parent hub DID for this device. Used by coordinator.async_write
    # to route writes via the cluster mesh -- when a sub-device's parent is a
    # peer hub (not the hub we connected to), we must address the parent or
    # the write silently no-ops. Empty string means "this device IS its own
    # parent" (i.e. a hub being written to directly). Override classes
    # generally leave this empty; AutoDerivedDevice loads it from the cloud
    # metadata's `parentDeviceId` field at construction time.
    PARENT_DID: str = ""
    EXTRA_CONFIG_SCHEMA: Any = None

    # Class-level descriptor lists. Subclasses override.
    BINARY_SENSORS: ClassVar[list[BinarySensorDescriptor]] = []
    SWITCHES: ClassVar[list[SwitchDescriptor]] = []
    SELECTS: ClassVar[list[SelectDescriptor]] = []
    NUMBERS: ClassVar[list[NumberDescriptor]] = []
    SENSORS: ClassVar[list[SensorDescriptor]] = []
    EVENTS: ClassVar[list[EventDescriptor]] = []
    # Class-level LightDescriptor list. Hand-authored override classes
    # populate this for unusual lights (e.g. T1M segment-strip controls in
    # v2). Auto-derived LightDescriptors flow through `derived`, not LIGHTS.
    LIGHTS: ClassVar[list[LightDescriptor]] = []

    # When True, auto-derived descriptors augment the class-level lists.
    # When False (default), non-empty class-level lists fully replace
    # auto-derive output.
    AUGMENT_AUTO_DERIVE: bool = False

    # ------------------------------------------------------------------
    # Construction.
    # ------------------------------------------------------------------

    def __init__(
        self,
        coordinator: Any,
        subentry: Any,
        derived: Iterable[AnyDescriptor] = (),
    ) -> None:
        self._coordinator = coordinator
        self._subentry = subentry
        derived_list = list(derived)
        class_list = self._class_descriptors()
        if self.AUGMENT_AUTO_DERIVE:
            self._effective_descriptors: list[AnyDescriptor] = (
                derived_list + class_list
            )
        elif class_list:
            self._effective_descriptors = class_list
        else:
            self._effective_descriptors = derived_list

        # Per-descriptor entity registry. A descriptor is the key; the
        # value is the bound entity (most platforms) or a list when
        # multiple entities may share a descriptor (rare).
        self._entities_by_descriptor: dict[AnyDescriptor, Any] = {}
        # Initial values seeded before the entity registers; delivered
        # exactly once when register_entity wires the matching entity in.
        # Keyed by wire_path (the 3-part canonical form on TraitSpec.id)
        # used for push routing, so descriptor replacement (e.g. via
        # dataclasses.replace for renames) cannot orphan the seed. For
        # LightDescriptor the absorbed-component paths are seeded
        # individually; register_entity replays each.
        self._pending_initial_values: dict[str, list[Any]] = {}
        # Wire paths whose seed has been delivered, to keep
        # seed_initial_value idempotent.
        self._delivered_initial_values: set[str] = set()

        self._by_trait_id: dict[str, list[AnyDescriptor]] = {}
        self._by_attr_name: dict[str, list[AnyDescriptor]] = {}
        self._build_indices()

        # Wire paths present in either the shipped catalogue or the
        # local overlay for THIS device's model. handle_report uses this
        # set to skip recording paths that are already understood; only
        # truly novel paths enter ObservedPathCache.
        self._known_wire_paths: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Public properties.
    # ------------------------------------------------------------------

    @property
    def coordinator(self) -> Any:
        """The hub coordinator this device is bound to."""
        return self._coordinator

    @property
    def subentry(self) -> Any:
        """The HA subentry carrying this device's per-device config."""
        return self._subentry

    @property
    def did(self) -> str:
        """The device's DID, sourced from the subentry's data dict."""
        try:
            return self._subentry.data.get("did", "")
        except AttributeError:
            return ""

    @property
    def descriptors(self) -> list[AnyDescriptor]:
        """The merged descriptor list this device exposes."""
        return list(self._effective_descriptors)

    # ------------------------------------------------------------------
    # Index construction.
    # ------------------------------------------------------------------

    def _class_descriptors(self) -> list[AnyDescriptor]:
        """Concatenate every class-level descriptor list into one."""
        return [
            *self.BINARY_SENSORS,
            *self.SWITCHES,
            *self.SELECTS,
            *self.NUMBERS,
            *self.SENSORS,
            *self.EVENTS,
            *self.LIGHTS,
        ]

    def _build_indices(self) -> None:
        """Walk the effective descriptor list and populate the lookup tables."""
        self._by_trait_id.clear()
        self._by_attr_name.clear()
        for desc in self._effective_descriptors:
            if isinstance(desc, LightDescriptor):
                # Index every absorbed trait id + attr name so handle_report
                # routes any component report to this Light.
                for _component, trait_field, attr_field in _LIGHT_COMPONENT_FIELDS:
                    trait = getattr(desc, trait_field, None)
                    if trait is not None and getattr(trait, "id", None):
                        _index(self._by_trait_id, trait.id, desc)
                    attr = getattr(desc, attr_field, None)
                    if attr is not None and getattr(attr, "name", None):
                        _index(self._by_attr_name, attr.name, desc)
                continue

            # Trait-id indexing covers binary sensors, sensors with a
            # trait set, and events with `trigger_trait`. Events with
            # `trigger_source` only (and no trait) are NOT indexed --
            # they fire via Device.fire_event from imperative code.
            trait = (
                getattr(desc, "trait", None)
                or getattr(desc, "trigger_trait", None)
            )
            if trait is not None and getattr(trait, "id", None):
                _index(self._by_trait_id, trait.id, desc)
            # Attr-name indexing covers any descriptor with an `attr` --
            # switches, selects, numbers, attr-backed sensors. attr_write
            # adds an alias entry when it differs from attr.
            attr = getattr(desc, "attr", None)
            if attr is not None and getattr(attr, "name", None):
                _index(self._by_attr_name, attr.name, desc)
                attr_write = getattr(desc, "attr_write", None)
                if (
                    attr_write is not None
                    and getattr(attr_write, "name", None)
                    and attr_write.name != attr.name
                ):
                    _index(self._by_attr_name, attr_write.name, desc)

    # ------------------------------------------------------------------
    # Entity registration.
    # ------------------------------------------------------------------

    def register_entity(
        self, descriptor: AnyDescriptor, entity: Any,
    ) -> Callable[[], None]:
        """Bind an entity to a descriptor and return an unregister callable.

        All seeded initial values for every wire path this descriptor
        exposes are replayed once during registration in insertion
        order. For LightDescriptor seeds the component name is passed
        to apply_value so the right field of the light entity updates.
        """
        self._entities_by_descriptor[descriptor] = entity
        replay_allowed = _should_replay_seed(descriptor)
        for wire_path, component in _descriptor_wire_paths(descriptor):
            if wire_path in self._delivered_initial_values:
                continue
            seeds = self._pending_initial_values.pop(wire_path, None)
            if not seeds:
                continue
            self._delivered_initial_values.add(wire_path)
            # Gate: consume the seed (pop + mark delivered above) but skip
            # the apply pass for event/momentary descriptors so reload
            # doesn't refire automations. See _should_replay_seed.
            if not replay_allowed:
                continue
            for value in seeds:
                try:
                    self._apply(descriptor, value, trait_id=component or wire_path)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "initial-value delivery failed for wire_path=%s "
                        "value=%r component=%r",
                        wire_path, value, component,
                    )

        def _unregister() -> None:
            # Index entries are descriptor-scoped (built once at construction);
            # only the entity binding is removed here. _by_trait_id /
            # _by_attr_name stay populated so a future register_entity() call
            # for the same descriptor still finds the indices.
            current = self._entities_by_descriptor.get(descriptor)
            if current is entity:
                self._entities_by_descriptor.pop(descriptor, None)

        return _unregister

    def seed_initial_value(self, wire_path: str, value: Any) -> None:
        """Stage a value to be applied when an entity for a descriptor
        that references this wire path registers.

        `wire_path` is the 3-part canonical form (TraitSpec.id) used for
        push routing. For composite LightDescriptor seeds, seed each
        absorbed component's wire path separately; register_entity
        routes by component name based on the descriptor's structure.

        Calling after delivery is a no-op.
        """
        if not wire_path or wire_path in self._delivered_initial_values:
            return
        self._pending_initial_values.setdefault(wire_path, []).append(value)

    # ------------------------------------------------------------------
    # Report dispatch.
    # ------------------------------------------------------------------

    def handle_report(self, report: Any) -> None:
        """Dispatch one LANLink report to matching entities.

        Reports may key by trait id (live LANLink reports) or attr name
        (synthetic initial-read reports from the coordinator). Both indices
        can contain the same descriptor for the same key (notably
        LightDescriptor, whose absorbed paths share trait_id == attr_name);
        we de-duplicate per (key, descriptor) to avoid double-applying.

        Live LANLink push reports key by 4-part `<endpoint>.<group>.<res>.<idx>`
        (e.g. `2.132.32920.1`) while cloud-derived descriptors store the
        3-part canonical `<endpoint>.<group>.<res>`. Strip the trailing
        segment so descriptor lookup -- and the LightDescriptor component
        dispatch downstream -- match the descriptor's stored trait_id.
        Synthetic initial-read reports already use the 3-part form, so the
        canonicalize step is a no-op for them.
        """
        values = getattr(report, "values", None) or {}
        observed_cache = getattr(self._coordinator, "observed_path_cache", None)
        for raw_key, value in values.items():
            key = _canonicalize_report_key(raw_key)
            # Record observed wire paths that are not already covered by the
            # shipped catalogue or the local overlay. Only truly novel paths
            # (absent from _known_wire_paths) enter ObservedPathCache so that
            # the candidate-list issue fires only for genuinely unknown paths.
            if (
                observed_cache is not None
                and self.MODEL
                and self.did
                and _is_numeric_path(key)
                and key not in self._known_wire_paths
            ):
                observed_cache.record(self.did, self.MODEL, key)
            seen: set[int] = set()
            for desc in self._by_trait_id.get(key, []):
                if id(desc) in seen:
                    continue
                seen.add(id(desc))
                self._apply(desc, value, trait_id=key)
            for desc in self._by_attr_name.get(key, []):
                if id(desc) in seen:
                    continue
                seen.add(id(desc))
                self._apply(desc, value, trait_id=key)

    def _apply(
        self,
        descriptor: AnyDescriptor,
        value: Any,
        *,
        trait_id: str | None = None,
    ) -> None:
        """Forward a value to the entity bound to `descriptor`.

        For LightDescriptor, the trait_id selects which component the value
        belongs to; entity.apply_value is invoked with `component=<name>`.
        `trait_id` may be a wire path (from push routing) or a component
        name string (from initial-value replay via register_entity).
        For other descriptor types, apply_value is called positionally with
        no kwarg (existing behavior).
        """
        entity = self._entities_by_descriptor.get(descriptor)
        if entity is None:
            return
        try:
            if isinstance(descriptor, LightDescriptor):
                # trait_id is either a component name (from initial-value
                # replay) or a wire path (from push routing). The helper
                # accepts either.
                if trait_id in _LIGHT_COMPONENTS:
                    component = trait_id
                else:
                    component = self._light_component_for_trait(descriptor, trait_id)
                entity.apply_value(value, component=component)
            else:
                entity.apply_value(value)
        except Exception:  # noqa: BLE001
            # Per spec: log + continue. A misbehaving entity must not
            # cascade into report-handling for the rest of the device.
            _LOGGER.exception(
                "entity.apply_value raised for descriptor=%r value=%r",
                descriptor, value,
            )

    def _light_component_for_trait(
        self, desc: "LightDescriptor", trait_id: str | None,
    ) -> str | None:
        """Return 'power' / 'brightness' / 'color_temp' / 'color_xy' or None."""
        if trait_id is None:
            return None
        if desc.power_trait and desc.power_trait.id == trait_id:
            return "power"
        if desc.brightness_trait and desc.brightness_trait.id == trait_id:
            return "brightness"
        if desc.color_temp_trait and desc.color_temp_trait.id == trait_id:
            return "color_temp"
        if desc.color_xy_trait and desc.color_xy_trait.id == trait_id:
            return "color_xy"
        return None

    # ------------------------------------------------------------------
    # Writes + events.
    # ------------------------------------------------------------------

    async def async_write(self, attrs: dict[Any, Any]) -> None:
        """Forward a write to the coordinator using the device's DID + model.

        Passes ``self.PARENT_DID`` through so the coordinator can route the
        write to the device's actual parent in the cluster (necessary when
        the parent is a peer hub rather than the hub we're connected to).
        """
        await self._coordinator.async_write(
            self.did, self.MODEL, attrs, parent_did=self.PARENT_DID,
        )

    def fire_event(self, key: str, event_type: str, **payload: Any) -> None:
        """Fire an event on the EventDescriptor whose `key` matches.

        Used by imperative side-channel handlers (devices that declare an
        `EventDescriptor` with `trigger_source`) to deliver an event to HA
        without going through the LANLink report path.
        """
        for desc in self._effective_descriptors:
            if not isinstance(desc, EventDescriptor):
                continue
            if getattr(desc, "key", None) != key:
                continue
            entity = self._entities_by_descriptor.get(desc)
            if entity is None:
                _LOGGER.debug(
                    "fire_event: no entity registered for key=%r", key,
                )
                return
            try:
                entity.trigger_event(event_type, **payload)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "event entity raised on trigger_event(key=%r)", key,
                )
            return
        _LOGGER.debug("fire_event: no EventDescriptor with key=%r", key)

    # ------------------------------------------------------------------
    # Imperative lifecycle hooks (subclasses override as needed).
    # ------------------------------------------------------------------

    def resolve_auto_clear_seconds(
        self, descriptor: AnyDescriptor,
    ) -> float | None:
        """Resolve a binary sensor's auto-clear delay at arm time.

        Base implementation returns the descriptor's baked value. Device
        override classes may return a live, user-tunable value instead
        (see G400Device, which reads a per-camera number entity).
        """
        return getattr(descriptor, "auto_clear_seconds", None)

    async def async_setup(self, coordinator: Any, subentry: Any) -> None:
        """Subclass hook for non-descriptor wiring. Default no-op."""
        return None

    async def async_setup_camera(
        self, coordinator: Any, subentry: Any,
    ) -> list[Any]:
        """Subclass hook returning camera entities. Default empty list."""
        return []

    async def async_setup_extra_numbers(
        self, hub: Any, subentry: Any,
    ) -> list[Any]:
        """Subclass hook returning imperative (non-descriptor) number entities.

        Default empty. Mirrors `async_setup_camera`: lets a device model
        add HA-local number entities that don't fit the descriptor-driven
        model (e.g. a tunable preference with no backing wire trait).
        """
        return []

    async def async_unload(self) -> None:
        """Subclass hook reversing `async_setup`. Default no-op."""
        return None


def resolve_subentry_metadata(subentry: Any) -> tuple[str, str, str, str]:
    """Resolve MODEL / PARENT_DID / MANUFACTURER / DISPLAY_NAME for a subentry.

    Used by the auto-derived device subclasses (the stock dispatch targets when
    no hand-authored Device subclass is registered for a model). The precedence
    rules:

    - MODEL: cloud_metadata['model'], then subentry.data['model']
    - PARENT_DID: cloud_metadata['parentDeviceId'] (empty for top-level/hub)
    - MANUFACTURER: catalog.get_display_metadata, else "Aqara"
    - DISPLAY_NAME: cloud_metadata['deviceName'] (often English) > catalog
      display_name (often Chinese) > MODEL string
    """
    try:
        cloud_record = subentry.data.get("_cloud_metadata", {}) or {}
    except AttributeError:
        cloud_record = {}
    try:
        fallback_model = subentry.data.get("model", "")
    except AttributeError:
        fallback_model = ""
    model = cloud_record.get("model") or fallback_model
    parent_did = cloud_record.get("parentDeviceId") or ""
    catalog_metadata = catalog.get_display_metadata(model)
    cloud_name = cloud_record.get("deviceName") or ""
    manufacturer = (
        catalog_metadata.manufacturer if catalog_metadata else "Aqara"
    )
    if cloud_name:
        display_name = cloud_name
    elif catalog_metadata is not None:
        display_name = catalog_metadata.display_name
    else:
        display_name = model
    return model, parent_did, manufacturer, display_name


class AutoDerivedDevice(Device):
    """Stock subclass for devices without a registered override class.

    Pulls `MODEL` / `MANUFACTURER` / `DISPLAY_NAME` from the subentry's
    cloud metadata at construction time. All entities are sourced from
    the `derived` argument; no class-level descriptor lists are set.

    Unlike the class-level MODEL / MANUFACTURER / DISPLAY_NAME pattern
    used by hand-authored Device subclasses, this class sets them as
    INSTANCE attributes that shadow the class-level Device defaults.
    """

    def __init__(self, coordinator: Any, subentry: Any, derived: Iterable[AnyDescriptor]) -> None:
        model, parent_did, manufacturer, display_name = resolve_subentry_metadata(subentry)
        self.MODEL = model
        self.PARENT_DID = parent_did
        self.MANUFACTURER = manufacturer
        self.DISPLAY_NAME = display_name
        super().__init__(coordinator, subentry, derived=derived)


__all__ = [
    "AutoDerivedDevice",
    "Device",
    "SelfDeviceContext",
    "resolve_subentry_metadata",
]
