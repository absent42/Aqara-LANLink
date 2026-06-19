"""Entity descriptors -- one per HA platform.

Each descriptor is a frozen, kw-only dataclass extending the corresponding
HA `*EntityDescription` subclass. The descriptors capture the binding
between a `TraitSpec` / `AttrSpec` from the catalogs and the HA entity
type that should represent it.

Cameras intentionally do NOT have a descriptor here. HA's
`CameraEntityDescription` is rarely subclassed because cameras carry
imperative state (RTSP source URL, talk-protocol wiring, go2rtc bridge)
that doesn't fit the small frozen-description model. Instead, devices that
expose a camera implement `Device.async_setup_camera()` (see `base.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union
from collections.abc import Callable, Mapping

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.event import EventEntityDescription
from homeassistant.components.light import LightEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.components.text import TextEntityDescription

from .attrs import AttrSpec
from .traits import TraitSpec
from .units import normalize_unit

# Type alias for the per-device extra config (a frozen view of subentry.data
# narrowed to whatever fields the device's EXTRA_CONFIG_SCHEMA validated).
ExtraConfig = Mapping[str, Any]


@dataclass(frozen=True)
class Effect:
    """One named scene/effect from Aqara's customaction/query.

    A static scene sets color_temp + brightness directly via LANLink at
    runtime; the seq_id field is None. A dynamic scene invokes via cloud
    sequence/run; seq_id is the cloud-assigned id. The two paths share
    one dataclass; AqaraLight.async_turn_on dispatches on `seq_id is None`.
    """

    name: str
    seq_id: str | None = None
    color_temp_kelvin: int | None = None
    brightness_pct: int | None = None


@dataclass(frozen=True, kw_only=True)
class BinarySensorDescriptor(BinarySensorEntityDescription):
    """A binary sensor backed by a single trait id.

    `on_values` is the set of string values from the trait that map to
    "on"; anything else is "off". `auto_clear_seconds` is an optional
    HA-side timer that resets the entity to off after the given delay --
    useful for momentary events like motion-detected pulses where the
    firmware doesn't emit an explicit clear.

    `on_any_value` overrides the on_values match: when True, any non-empty,
    non-"0" wire value flips the sensor to on. Used for recognition-report
    traits (e.g. HumanRecognition.HumanRecognitionReport) where the
    firmware emits varying payload codes per detection -- any payload at
    all means "detected just now".
    """

    trait: TraitSpec
    on_values: frozenset[str] = frozenset({"1"})
    on_any_value: bool = False
    auto_clear_seconds: float | None = None


@dataclass(frozen=True, kw_only=True)
class SwitchDescriptor(SwitchEntityDescription):
    """A two-state switch backed by an attr name."""

    attr: AttrSpec
    attr_write: AttrSpec | None = None
    on_value: str = "1"
    off_value: str = "0"
    optimistic: bool = False


@dataclass(frozen=True, kw_only=True)
class SelectDescriptor(SelectEntityDescription):
    """A multi-state selector backed by an attr name.

    ``options_map`` is an ordered tuple of ``(label, wire_value)`` pairs.
    The label is the user-facing string; the wire value is what gets
    serialised over LANLink. Iteration order is preserved end-to-end so
    the UI orders options the same way auto-derive emitted them.

    The shape is a tuple-of-pairs (rather than a ``Mapping``) because
    frozen dataclasses don't deep-freeze field values: a mutable
    ``dict`` field would make instances unhashable, which breaks
    ``Device.seed_initial_value``'s ``setdefault(descriptor, [])`` and
    ``Device._pending_initial_values`` set membership. Use
    ``descriptor.options_dict()`` when you want the dict shape.
    """

    attr: AttrSpec
    attr_write: AttrSpec | None = None
    options_map: tuple[tuple[str, str], ...]
    optimistic: bool = False

    def options_dict(self) -> dict[str, str]:
        """Return ``options_map`` as a fresh dict for callers that need it."""
        return dict(self.options_map)


@dataclass(frozen=True, kw_only=True)
class NumberDescriptor(NumberEntityDescription):
    """A numeric input backed by an attr name.

    `transform_in` converts the wire string to a float/int when applying
    a report; `transform_out` does the reverse for writes. Both default
    to identity-with-int-coercion when None.
    """

    attr: AttrSpec
    attr_write: AttrSpec | None = None
    min_value: int | float
    max_value: int | float
    step: int | float = 1
    transform_in: Callable[[str], int | float] | None = None
    transform_out: Callable[[int | float], str] | None = None
    scale: float | None = None
    optimistic: bool = False

    def __post_init__(self) -> None:
        """Normalize the unit so HA NumberEntity device-class validation passes."""
        object.__setattr__(
            self, "native_unit_of_measurement",
            normalize_unit(self.native_unit_of_measurement),
        )


@dataclass(frozen=True, kw_only=True)
class TextDescriptor(TextEntityDescription):
    """A free-text input backed by an attr name.

    Used for string/packed rid-settings (e.g. hex-encoded light segment
    paragraphs) that have no clean enum/range, so they cannot be a
    switch/select/number. The bare rid is carried on `attr` for the write
    path, mirroring the other setting descriptors. Optimistic by default:
    settings receive no push reports, so the entity sets its own state.
    """

    attr: AttrSpec
    attr_write: AttrSpec | None = None
    optimistic: bool = False


@dataclass(frozen=True, kw_only=True)
class SensorDescriptor(SensorEntityDescription):
    """A read-only sensor.

    Bound to either an `AttrSpec` (when sourced from a wire-named attr)
    or a `TraitSpec` (when sourced from a numeric trait id with no
    writable counterpart). Auto-derive picks this descriptor type for
    traits that don't fit any of the writable categories.
    """

    attr: AttrSpec | None = None
    trait: TraitSpec | None = None
    transform_in: Callable[[str], Any] | None = None
    scale: float | None = None

    def __post_init__(self) -> None:
        """Normalize the unit so HA SensorEntity device-class validation passes."""
        object.__setattr__(
            self, "native_unit_of_measurement",
            normalize_unit(self.native_unit_of_measurement),
        )


@dataclass(frozen=True, kw_only=True)
class EventDescriptor(EventEntityDescription):
    """A one-shot event entity.

    Two trigger sources are supported:

    - `trigger_trait` -- a trait id whose appearance in a LANLink report
      fires the event.
    - `trigger_source` -- a named non-LANLink source whose listener is
      owned by the device's imperative `async_setup`, which calls
      `Device.fire_event(key, ...)` when the source fires. No shipping
      device currently uses this path.

    Exactly one of the two should be set, but the descriptor doesn't
    enforce this -- the device base class skips indexing for events that
    only declare `trigger_source`.
    """

    trigger_trait: TraitSpec | None = None
    trigger_source: str | None = None
    event_types: tuple[str, ...] = ("ring",)
    # When set, every fired trait-driven event carries the raw wire value
    # under this payload key. Used for ID-style reports (e.g. FaceRecognition
    # .FaceIDReport) whose value identifies *which* subject was recognised --
    # the id can exceed 2**53 so it is forwarded as the raw string, never
    # coerced to int.
    value_payload_key: str | None = None
    # When set, an enum-mapped trait that receives a wire value absent from
    # its enum_values fires this fallback event_type (which must appear in
    # event_types) instead of being dropped. Recognition/detection traits
    # under-declare their enum in the cloud spec, so the fallback both avoids
    # losing real detections and -- with value_payload_key -- surfaces the
    # unmapped code for discovery. Strict events (buttons/doorbells) leave
    # this None and keep dropping unknown values.
    unknown_event_type: str | None = None


@dataclass(frozen=True, kw_only=True)
class LightDescriptor(LightEntityDescription):
    """Composite descriptor for an Aqara light entity.

    Power is mandatory; brightness, color_temp, and xy color are optional.
    HA's color_mode is computed at entity-construction time from which
    optional traits are present.

    Color temperature is stored in KELVIN throughout the descriptor and
    entity API, matching HA's modern LightEntity contract (mireds is
    deprecated and scheduled for removal in HA 2026.1). The cloud's
    qlink/trait/read response may report the trait in mireds OR Kelvin
    depending on firmware; the pattern detector converts to Kelvin via
    K = 1_000_000 / mired when needed. Wire-format writes go back to
    whatever unit the cloud reported (mireds or Kelvin), with the
    LightDescriptor remembering the wire unit so async_turn_on can
    convert back at write time.
    """

    power_trait: TraitSpec
    power_attr: AttrSpec
    brightness_trait: TraitSpec | None = None
    brightness_attr: AttrSpec | None = None
    color_temp_trait: TraitSpec | None = None
    color_temp_attr: AttrSpec | None = None
    color_temp_min_kelvin: int | None = None
    color_temp_max_kelvin: int | None = None
    color_temp_wire_unit: Literal["K", "mired"] = "K"
    color_xy_trait: TraitSpec | None = None
    color_xy_attr: AttrSpec | None = None
    effects: tuple[Effect, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ButtonDescriptor(ButtonEntityDescription):
    """A press-to-trigger entity backed by a writable trait id.

    Stateless: the entity has no state to mirror. Pressing it writes
    `press_value` to the trait, which fires a one-shot device action
    (canonical example: writing to Identify.IdentifyType makes the
    physical device blink its LED for visual identification).
    """

    attr: AttrSpec
    press_value: str = "1"


# Convenience union for callers that accept any descriptor.
AnyDescriptor = Union[
    BinarySensorDescriptor,
    ButtonDescriptor,
    SwitchDescriptor,
    SelectDescriptor,
    NumberDescriptor,
    SensorDescriptor,
    TextDescriptor,
    EventDescriptor,
    LightDescriptor,
]


__all__ = [
    "AnyDescriptor",
    "BinarySensorDescriptor",
    "ButtonDescriptor",
    "Effect",
    "EventDescriptor",
    "ExtraConfig",
    "LightDescriptor",
    "NumberDescriptor",
    "SelectDescriptor",
    "SensorDescriptor",
    "SwitchDescriptor",
    "TextDescriptor",
]
