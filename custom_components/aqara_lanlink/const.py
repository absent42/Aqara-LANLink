"""Domain-level constants for the Aqara LANLink integration.

Protocol-level constants live in hub/protocol.py. Device-specific trait
and attribute identifiers live in device/traits.py and device/attrs.py.
This file is for things that are genuinely global: the domain name,
the list of platforms, and domain-wide timeouts.
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "aqara_lanlink"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.LIGHT,
]

# Config entry data keys (hub entry)
# Several keys intentionally mirror the existing aqara_doorbell integration's
# names (CONF_AQARA_*, CONF_HUB_*). The two integrations are independent
# config-entry namespaces, so collision is harmless; keeping the names
# aligned simplifies anyone familiar with the existing code.
CONF_HUB_IP = "hub_ip"
CONF_HUB_PORT = "hub_port"
CONF_HUB_DID = "hub_did"
CONF_HUB_MODEL = "hub_model"
CONF_AQARA_ACCOUNT = "aqara_account"
CONF_AQARA_PASSWORD = "aqara_password"
CONF_AQARA_REGION = "aqara_region"
CONF_AQARA_USER_ID = "aqara_user_id"
CONF_AQARA_TOKEN = "aqara_token"
# Stable per-install identity sent as the cloud "PhoneId" header. Aqara
# namespaces push-subscription state by (user, PhoneId); persisting one value
# keeps the hub's subscription stable across reloads instead of orphaning it.
CONF_PHONE_ID = "phone_id"

# Subentry data keys (device subentry)
CONF_DEVICE_DID = "did"
CONF_DEVICE_MODEL = "model"

# Standalone Wi-Fi device relay-activation endpoint, persisted on the
# subentry so a later re-arm step knows where to reconnect. Underscore
# prefix follows the ``_cloud_metadata`` convention so these never collide
# with user-supplied per-device extras fields.
CONF_ACTIVATION_HOST = "_activation_host"
CONF_ACTIVATION_PORT = "_activation_port"

# Hub connection defaults
DEFAULT_HUB_PORT = 59703

# Default hub model identifier used when a config entry doesn't carry an
# explicit model. Matches the G3 base hub firmware id.
DEFAULT_HUB_MODEL = "lumi.gateway.agl004"

# Push-liveness watchdog TTL (seconds). A connected hub with a populated
# topology that goes silent past this is treated as wedged-forwarding and its
# subscription is re-armed. Also surfaced by the tunnel-forwarding diagnostic
# binary sensor as its staleness threshold.
PUSH_STALL_TTL_SECONDS = 300.0

# Aqara cloud regions, in the order the Aqara apps display them. The
# legacy RPC `cloud_client.AREAS` table is the authoritative mapping;
# this tuple is here so the config flow can offer a closed selector
# without having to import the cloud client (which has heavy
# cryptography dependencies).
AQARA_REGIONS: tuple[str, ...] = (
    "CN",
    "EU",
    "US",
    "HMT",
    "OTHER",
    "AF",
    "RU",
    "AU",
    "ME",
    "KR",
    "JP",
)
