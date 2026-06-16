"""Aqara Legacy RPC cloud client.

Hits the same cloud endpoints the mobile app itself uses. Provides:

- :meth:`AqaraCloudClient.login` - username/password login
  (`/app/v1.0/lumi/user/login`). Returns a userId + token pair that works
  unchanged as the `user` and `aid` fields in the LANLink checkin.
- :meth:`AqaraCloudClient.query_device_list` - full device inventory
  across all homes (`/app/v1.0/lumi/app/position/device/query`). GET;
  sign source is the alphabetically-sorted query string.
- :meth:`AqaraCloudClient.query_device_traits` - per-device per-trait
  introspection with current values + metadata
  (`/app/v1.0/lumi/app/qlink/trait/read`). POST; sign source is the JSON
  body verbatim.
- :meth:`AqaraCloudClient.query_device_detail` - per-device extended
  metadata (mac, firmwareVersion, parentDeviceId, homeId, etc.)
  (`/app/v1.0/lumi/app/dev/query/detail`). GET; sign source is the
  alphabetically-sorted query string with the JSON-encoded `dids` list
  (NOT URL-percent-encoded in the sign source).
- :meth:`AqaraCloudClient.query_custom_actions` - returns the cloud's
  `result` dict for a named-scene action_id query
  (`/app/v1.0/lumi/app/customaction/query`). POST; sign source is the
  JSON body verbatim. Used at setup to fetch effect lists per Light.
  Note the action_id semantics gotcha (verified empirically against a
  live T2 RGB CCT capture): `scene_mode` returns DYNAMIC scenes,
  `new_dynamic_scene_mode` returns STATIC scenes -- the names are
  REVERSED from what they suggest.
- :meth:`AqaraCloudClient.run_sequence` - activate a dynamic scene by
  seq_id (`/app/v1.0/lumi/app/sequence/run`). POST; sign source is the
  JSON body verbatim.
- :meth:`AqaraCloudClient.query_collection_panels` - fetch per-endpoint
  path lists for one device
  (`/app/v1.0/lumi/app/layout/collection/panels`). GET; sign source is
  the alphabetically-sorted bare query string (subjectIds and types).
  Returns dict[int, list[str]] keyed by endpointId.

This is NOT the Aqara Open API v3 (which needs developer console signup).
The Legacy RPC uses embedded per-region app credentials that are documented
across multiple open-source projects (Wh1terat's gists, sdavides/AqaraPOST,
niceboygithub/AqaraGateway, etc).

Request format (verbatim from captured Android app + cross-referenced with
the sdavides AqaraPOST-tokenGenerator.py reference):

    POST {server}/app/v1.0/lumi/user/login
    Headers:
        Area:  <region code>
        Appid: <per-region AppID>
        Nonce: md5(uuid4())
        Time:  <epoch ms>
        Sign:  md5("Appid=X&Nonce=Y&Time=Z&<json_body>&<appkey>")
        Content-Type: application/json
    Body:
        {"account": <email>, "encryptType": 2,
         "password": base64(RSA-PKCS1v15(md5(password).hexdigest()))}

The `RequestBody` component of the sign is the JSON body serialised with
Python's default `json.dumps` spacing (space after `,` and `:`). Matching
that default is important -- the server rejects the sign if the body bytes
don't exactly match what was signed.

Response (login):
    {"code": 0, "result": {"userId": "...", "token": "...", ...}}

Backward compatibility:
    The legacy class name :class:`AqaraCloudAuth` remains available as an
    alias for :class:`AqaraCloudClient` so existing imports keep working.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..device.descriptors import Effect, LightDescriptor

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Region map -- server + app credentials per region.
# Mirrors sdavides/AqaraPOST-tokenGenerator.py verbatim.
# =============================================================================

@dataclass(frozen=True)
class _AreaConfig:
    server: str
    appid: str
    appkey: str


AREAS: dict[str, _AreaConfig] = {
    "CN": _AreaConfig(
        server="https://aiot-rpc.aqara.cn",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "EU": _AreaConfig(
        server="https://rpc-ger.aqara.com",
        appid="7be1984f0556276133336839",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "RU": _AreaConfig(
        server="https://rpc-ru.aqara.com",
        appid="94549908487478b220992a70",
        appkey="euGhPe2rcmxwculATNj45eEtnd50zp0I",
    ),
    "KR": _AreaConfig(
        server="https://rpc-kr.aqara.com",
        appid="94549908487478b220992a70",
        appkey="euGhPe2rcmxwculATNj45eEtnd50zp0I",
    ),
    "JP": _AreaConfig(
        server="https://rpc-kr.aqara.com",
        appid="94549908487478b220992a70",
        appkey="euGhPe2rcmxwculATNj45eEtnd50zp0I",
    ),
    "AF": _AreaConfig(
        server="https://rpc-ger.aqara.com",
        appid="7be1984f0556276133336839",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "US": _AreaConfig(
        server="https://aiot-rpc-usa.aqara.com",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "HMT": _AreaConfig(
        server="https://aiot-rpc-usa.aqara.com",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "OTHER": _AreaConfig(
        server="https://aiot-rpc-usa.aqara.com",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "AU": _AreaConfig(
        server="https://rpc-au.aqara.com",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
    "ME": _AreaConfig(
        server="https://rpc-au.aqara.com",
        appid="94549908487478b220992a70",
        appkey="Jddz01kIORDYrBzqGYgpUXKBnIHfW8E3",
    ),
}


# Hardcoded RSA public key used to encrypt the password hash. Extracted from
# the Android app; present across all current cross-platform Aqara builds.
_AQARA_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCG46slB57013JJs4Vvj5cVyMpR
9b+B2F+YJU6qhBEYbiEmIdWpFPpOuBikDs2FcPS19MiWq1IrmxJtkICGurqImRUt
4lP688IWlEmqHfSxSRf2+aH0cH8VWZ2OaZn5DWSIHIPBF2kxM71q8stmoYiV0oZs
rZzBHsMuBwA4LQdxBwIDAQAB
-----END PUBLIC KEY-----"""


_LOGIN_PATH = "/app/v1.0/lumi/user/login"
_DEVICE_LIST_PATH = "/app/v1.0/lumi/app/position/device/query"
_TRAIT_READ_PATH = "/app/v1.0/lumi/app/qlink/trait/read"
_RES_QUERY_BY_RID_PATH = "/app/v1.0/lumi/res/query/by/resourceId"
_DEV_QUERY_DETAIL_PATH = "/app/v1.0/lumi/app/dev/query/detail"
_CUSTOM_ACTION_QUERY_PATH = "/app/v1.0/lumi/app/customaction/query"
_SEQUENCE_RUN_PATH = "/app/v1.0/lumi/app/sequence/run"
_COLLECTION_PANELS_PATH = "/app/v1.0/lumi/app/layout/collection/panels"
_CAMERA_P2P_INFO_PATH = "/app/v1.0/lumi/devex/camera/p2p/info"
_CAMERA_P2P_SIGN_PATH = "/app/v1.0/lumi/devex/camera/p2p/sign"
_CAMERA_PTZ_POSITION_LIST_PATH = "/app/v1.0/lumi/devex/camera/ptz/position/list"

@dataclass(frozen=True)
class EndpointPanel:
    """Per-endpoint slice of a cloud `collection/panels` response.

    Carries the metadata the v3a synth pass needs to bind cold wire paths
    to catalogue pids:

      - endpoint_id, endpoint_name: cloud's user-facing label
      - endpoint_icon_id:           icon hint
      - device_name:                English device label (FP300 user-facing)
      - device_types:               Aqara device-class string
                                    (OccupancySensor, TemperatureSensor, ...)
      - position_name:              cloud-side room name
      - model_type:                 1=Hub, 2=Subdevice, 8=Camera
      - obj_properties:             canonical pids declared on this endpoint
      - paths:                      probe-list wire paths for this endpoint
    """

    endpoint_id: int
    endpoint_name: str
    endpoint_icon_id: str
    device_name: str
    device_types: str
    position_name: str
    model_type: int
    obj_properties: tuple[str, ...]
    paths: tuple[str, ...]


_UNIVERSAL_PATHS: tuple[str, ...] = (
    "0.128.32901", "0.129.32906", "0.129.32907", "0.129.32909",
    "0.129.32912", "0.129.33013", "0.130.32913", "0.130.32914",
    "0.130.33016", "1.147.32969",
)



# =============================================================================
# Errors + return type
# =============================================================================


class AqaraAuthError(RuntimeError):
    """Raised on any auth-related failure (bad creds, network, server error)."""


class AqaraCloudAuthError(AqaraAuthError):
    """Raised when the cloud explicitly rejects the stored token / session.

    Subclass of :class:`AqaraAuthError` so existing broad catches keep
    working; new catch sites can match this narrower type and trigger
    HA's re-auth flow via ``entry.async_start_reauth()``.

    Distinguished from generic AqaraAuthError by matching one of the
    documented auth-failure codes (see ``_AUTH_FAILURE_CODES``).
    """


# Aqara Open Cloud "Account Authorization Mode" error codes that indicate
# the stored token is no longer valid and the user must re-authenticate.
# Source: https://opendoc.aqara.cn/en/docs/developmanual/apiIntroduction/errorCode.html
#
#   108  Token has expired           -- the canonical case (token TTL elapsed)
#   109  Token is absence            -- missing/blank token (config corruption)
#   802  Account not login           -- server-side session ended (e.g. user
#                                       changed account password elsewhere)
#   804  Token failed                -- generic token validation failure
#
# Codes intentionally NOT in this set:
#   803  User permission denied      -- scope error, not auth-state
#   810  Password incorrect          -- only fires on the login step itself
#   2xxx (RefreshToken / AccessToken codes) -- Developer App authorization mode
#                                              which we don't use
_AUTH_FAILURE_CODES: frozenset[int] = frozenset({108, 109, 802, 804})


@dataclass
class AqaraTokens:
    """Result of a successful login.

    The Legacy RPC API doesn't return a refresh token or expiry, so we store
    only what the server actually gave us. Tokens appear stable for months
    in practice; the caller should re-login if a request is later rejected.
    """

    user_id: str
    token: str
    raw_result: dict[str, Any]


# =============================================================================
# Public client
# =============================================================================


class AqaraCloudClient:
    """Aqara Legacy RPC cloud client.

    Provides username/password login plus a small set of authenticated
    cloud queries used by the integration during enrollment / discovery:

    - :meth:`login` - exchange email + password for an (user_id, token)
      pair.
    - :meth:`query_device_list` - fetch the user's full device inventory
      across all homes.
    - :meth:`query_device_traits` - fetch per-device per-trait metadata
      and current values for one device.
    - :meth:`query_device_detail` - fetch extended per-device metadata
      (mac, firmwareVersion, parentDeviceId, homeId, etc.) for one
      device.
    - :meth:`query_custom_actions` - fetch the cloud's `result` dict for
      a named-scene action_id query, used at setup to fetch effect lists
      per Light. Note the action_id semantics gotcha (verified empirically
      against a live T2 RGB CCT capture): `scene_mode` returns DYNAMIC
      scenes, `new_dynamic_scene_mode` returns STATIC scenes -- the names
      are REVERSED from what they suggest.
    - :meth:`run_sequence` - activate a dynamic scene by seq_id.
    - :meth:`query_collection_panels` - fetch per-endpoint path lists
      for one device from the layout/collection/panels endpoint. Returns
      a dict[int, list[str]] keyed by endpointId.
    """

    #: Client headers aligned with a captured Android-app request, so our
    #: traffic looks like the real app's.
    _CLIENT_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": "okhttp/4.12.0",
        "App-Version": "6.1.6",
        "Sys-Type": "1",          # 0 = iOS, 1 = Android
        "Lang": "en",
        "Phone-Model": "Home Assistant##Server",
    }

    def __init__(
        self,
        region: str = "EU",
        *,
        session: aiohttp.ClientSession | None = None,
        hass: Any = None,
        phone_id: str | None = None,
    ) -> None:
        if region not in AREAS:
            raise ValueError(f"unknown Aqara region {region!r}")
        self._region = region
        self._area = AREAS[region]
        self._session = session
        # When no explicit session is given but a HomeAssistant instance is,
        # _request lazily borrows HA's shared aiohttp session (pooled TLS,
        # reused across this client's calls) instead of opening and tearing
        # down a fresh session per request.
        self._hass = hass
        # Stable per-instance PhoneId. Aqara's cloud namespaces push-subscription
        # state by (user, PhoneId); regenerating it per request orphans every
        # subscription on the hub and accumulates dead policy_resset entries
        # across reloads. A caller-provided value (persisted in ConfigEntry.data)
        # wins; the UUID4 fallback at least stays stable for this instance.
        self._phone_id = phone_id or str(uuid.uuid4()).upper()

    # -------------------------------------------------------------------------
    # Password encryption (RSA-PKCS1v15 over the hex MD5)
    # -------------------------------------------------------------------------

    @staticmethod
    def _encrypt_password(password: str) -> str:
        """Return `base64(RSA-PKCS1v15(md5(password).hexdigest().encode()))`.

        The server expects `encryptType: 2` alongside this value -- it tells
        the server the password has been pre-hashed+encrypted by the client.
        """
        pubkey = serialization.load_pem_public_key(_AQARA_PUBLIC_KEY_PEM)
        md5_hex = hashlib.md5(password.encode()).hexdigest().encode()
        ciphertext = pubkey.encrypt(md5_hex, padding.PKCS1v15())
        return base64.b64encode(ciphertext).decode()

    # -------------------------------------------------------------------------
    # Header + signing
    # -------------------------------------------------------------------------

    def _sign(
        self,
        nonce: str,
        ts_ms: str,
        body_str: str,
        token: str | None = None,
    ) -> str:
        """MD5 sign of the canonical header+body+appkey string.

        SECURITY: the ``source`` string below concatenates the cloud Token and
        the region appkey (a shared signing secret). Never log ``source``, the
        request headers, or this function's inputs - doing so leaks both the
        session token and the appkey.
        """
        if token:
            source = (
                f"Appid={self._area.appid}&Nonce={nonce}&Time={ts_ms}"
                f"&Token={token}&{body_str}&{self._area.appkey}"
            )
        else:
            source = (
                f"Appid={self._area.appid}&Nonce={nonce}&Time={ts_ms}"
                f"&{body_str}&{self._area.appkey}"
            )
        return hashlib.md5(source.encode()).hexdigest()

    def _build_headers(self, body_str: str, token: str | None = None) -> dict[str, str]:
        nonce = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()
        ts_ms = str(round(time.time() * 1000))
        headers = {
            **self._CLIENT_HEADERS,
            "PhoneId": self._phone_id,
            "Area": self._region,
            "Appid": self._area.appid,
            "Nonce": nonce,
            "Time": ts_ms,
            "Content-Type": "application/json",
            "Sign": self._sign(nonce, ts_ms, body_str, token),
        }
        if token:
            headers["Token"] = token
        return headers

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def login(self, email: str, password: str) -> AqaraTokens:
        """Log in and return a usable (user_id, token) pair.

        Raises :class:`AqaraAuthError` on credential, network, or server
        errors.
        """
        body_dict = {
            "account": email,
            "encryptType": 2,
            "password": self._encrypt_password(password),
        }
        # Server expects the body to be signed with default json.dumps spacing
        # (space after `,` and `:`). Using separators=(",", ":") breaks the sign.
        body_str = json.dumps(body_dict)

        headers = self._build_headers(body_str)
        url = f"{self._area.server}{_LOGIN_PATH}"

        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)
        result = payload.get("result") or {}
        user_id = result.get("userId")
        token = result.get("token")
        if not user_id or not token:
            raise AqaraAuthError(
                f"Aqara login returned no userId/token (keys: {sorted(result)})"
            )
        return AqaraTokens(user_id=user_id, token=token, raw_result=result)

    async def query_device_list(self, token: str) -> list[dict[str, Any]]:
        """Return the user's full device inventory across all homes.

        Calls ``GET /app/v1.0/lumi/app/position/device/query?size=300&startIndex=0``.

        The sign source is the alphabetically-sorted query string
        (``size=300&startIndex=0``), passed verbatim into ``_sign`` as the
        body component. The query string is sent on the URL unchanged
        (no URL-percent-encoding of values is required for these
        parameters).

        Returns the raw ``result.devices`` list. Each entry is the cloud's
        per-device record (``did``, ``model``, ``deviceName``,
        ``parentDeviceId``, ``positionId``, ``state``,
        ``firmwareVersion``, etc.).

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        (``code != 0``) responses.
        """
        # Alphabetically-sorted query string. Order matters: the server
        # validates the sign against this exact byte sequence. Keep the
        # parameters in alpha order and serialise without URL encoding.
        query = "size=300&startIndex=0"
        url = f"{self._area.server}{_DEVICE_LIST_PATH}?{query}"
        headers = self._build_headers(query, token=token)

        payload = await self._request("GET", url, headers)
        self._raise_if_error(payload)
        result = payload.get("result") or {}
        devices = result.get("devices")
        if not isinstance(devices, list):
            raise AqaraAuthError(
                f"Aqara device-list response missing 'devices' "
                f"(keys: {sorted(result)})"
            )
        return devices

    async def query_device_traits(
        self, token: str, did: str, paths: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Return per-trait introspection for a single device.

        Calls ``POST /app/v1.0/lumi/app/qlink/trait/read`` with body
        containing the requested paths as trait objects. Builds the
        request body's traits array from `paths` rather than sending
        an empty list.

        Args:
            token: Authentication token from login.
            did: Device ID.
            paths: Iterable of path strings (e.g., ["2.132.32920", "2.133.32923"]).
                   Empty paths raises ValueError before any HTTP call.

        Sign source is the JSON body verbatim (default ``json.dumps``
        spacing - space after ``,`` and ``:``). Matching that default is
        important since the server validates the sign against the bytes
        on the wire.

        Returns the raw ``result[0].traits`` list. Each entry has
        ``path``, ``propertyId``, ``enums``, ``unit``, ``min``, ``max``,
        ``step``, ``defaultValue``, ``value``, ``traitTime`` and a few
        more fields.

        Raises ValueError if paths is empty (programmer error).
        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        paths_list = list(paths)
        if not paths_list:
            raise ValueError("paths cannot be empty")

        body_dict = {
            "devices": [{
                "deviceId": did,
                "traits": [{"path": p, "needSubscribe": True} for p in paths_list],
            }],
            "needParam": True,
        }
        # Use default json.dumps spacing - matches the byte sequence the
        # server expects when computing the sign.
        body_str = json.dumps(body_dict)
        headers = self._build_headers(body_str, token=token)
        url = f"{self._area.server}{_TRAIT_READ_PATH}"

        _LOGGER.debug(
            "query_device_traits: POST %s body=%s",
            _TRAIT_READ_PATH, body_str,
        )
        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)
        result = payload.get("result")
        if not isinstance(result, list) or not result:
            raise AqaraAuthError(
                f"Aqara trait-read response missing 'result' list (got {type(result).__name__})"
            )
        traits = result[0].get("traits")
        if not isinstance(traits, list):
            raise AqaraAuthError(
                f"Aqara trait-read entry missing 'traits' "
                f"(keys: {sorted(result[0])})"
            )
        return traits

    async def query_resources_by_rid(
        self, token: str, did: str, rids: Iterable[str],
    ) -> dict[str, str]:
        """Return current setting values keyed by resource ID for one device.

        Calls ``POST /app/v1.0/lumi/res/query/by/resourceId`` with body
        ``{"data": [{"options": [<rids>], "subjectId": <did>}]}``.

        Args:
            token: Authentication token from login.
            did: Device ID (subjectId).
            rids: Iterable of resource-id strings (e.g., ["4.4.85", "8.0.2032"]).

        Sign source is the JSON body verbatim (default ``json.dumps``
        spacing - space after ``,`` and ``:``). Matching that default is
        important since the server validates the sign against the bytes
        on the wire.

        Parses the response ``result`` list into ``{resourceId: value}``.
        Items lacking either key are skipped; a partial or empty result
        simply yields fewer (or no) entries.

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        rids_list = list(rids)
        body_dict = {
            "data": [{"options": rids_list, "subjectId": did}],
        }
        # Use default json.dumps spacing - matches the byte sequence the
        # server expects when computing the sign.
        body_str = json.dumps(body_dict)
        headers = self._build_headers(body_str, token=token)
        url = f"{self._area.server}{_RES_QUERY_BY_RID_PATH}"

        _LOGGER.debug(
            "query_resources_by_rid: POST %s body=%s",
            _RES_QUERY_BY_RID_PATH, body_str,
        )
        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)
        result = payload.get("result")
        values: dict[str, str] = {}
        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                rid = item.get("resourceId")
                value = item.get("value")
                if rid is None or value is None:
                    continue
                values[rid] = value
        return values

    async def query_device_detail(
        self, token: str, did: str,
    ) -> dict[str, Any]:
        """Return extended metadata for a single device.

        Calls ``GET /app/v1.0/lumi/app/dev/query/detail?area=<region>&dids=["<did>"]``.

        The ``area`` query parameter is taken from the client's configured
        region (``self._region``) - no need for the caller to repeat it.

        Sign source is the alphabetically-sorted query string with the
        JSON-encoded ``dids`` list (NOT URL-percent-encoded in the sign
        source). The same string is sent on the URL unencoded so the
        wire bytes match the sign source exactly.

        Returns the raw ``result[0]`` dict for the single requested
        device. Common fields: ``mac``, ``model``, ``manualUrl``,
        ``parentDeviceId``, ``parentDeviceName``, ``parentModel``,
        ``homeId``, ``positionId``, ``positionName``,
        ``firmwareVersion``, ``timeZoneId``.

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        # JSON-encode the dids list with default spacing. This is the
        # exact byte sequence used both in the sign source and on the
        # URL. Aqara's sign convention here does NOT use URL percent
        # encoding, so the wire URL must carry the raw JSON brackets.
        dids_json = json.dumps([did])
        # Alphabetical order: 'area' before 'dids'.
        query = f"area={self._region}&dids={dids_json}"
        url = f"{self._area.server}{_DEV_QUERY_DETAIL_PATH}?{query}"
        headers = self._build_headers(query, token=token)

        payload = await self._request("GET", url, headers)
        self._raise_if_error(payload)
        result = payload.get("result")
        if not isinstance(result, list) or not result:
            raise AqaraAuthError(
                f"Aqara dev/query/detail response missing 'result' list (got {type(result).__name__})"
            )
        return result[0]

    async def query_custom_actions(
        self,
        token: str,
        action_id: str,
        *,
        device_model: str,
        user_device_id: str,
    ) -> dict[str, Any]:
        """Return the cloud's `result` dict for an action_id query.

        Calls ``POST /app/v1.0/lumi/app/customaction/query`` with body
        ``{"actionId": <action_id>, "deviceModel": <device_model>,
           "userDeviceId": <user_device_id>}``.

        Sign source is the JSON body verbatim (default ``json.dumps``
        spacing). Raises :class:`AqaraAuthError` on auth, HTTP, or
        server-error responses.

        Action IDs (semantics REVERSED from naming -- verified empirically
        against a T2 RGB CCT bulb on 2026-05-05):

        - ``"scene_mode"``: returns DYNAMIC scenes (each entry has a seqId).
        - ``"new_dynamic_scene_mode"``: returns STATIC scenes (each entry's
          `value` field is a JSON-encoded `{"colour_temperature": ...,
          "light_level": ...}` dict; seqId is None).

        Returns the cloud's ``result`` dict with shape:
            {"UserCustomActions": [...], "DefaultCustomActions": [...]}
        Both lists carry entries with the same shape; the merge happens in
        `_parse_effects`.
        """
        body_dict = {
            "actionId": action_id,
            "deviceModel": device_model,
            "userDeviceId": user_device_id,
        }
        body_str = json.dumps(body_dict)
        headers = self._build_headers(body_str, token=token)
        url = f"{self._area.server}{_CUSTOM_ACTION_QUERY_PATH}"

        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise AqaraAuthError(
                f"Aqara customaction/query response 'result' is not a dict "
                f"(got {type(result).__name__})"
            )
        return result

    async def run_sequence(
        self,
        token: str,
        *,
        did: str,
        seq_id: str,
    ) -> None:
        """Trigger a dynamic scene by seq_id.

        Calls ``POST /app/v1.0/lumi/app/sequence/run`` with body
        ``{"actionId": "", "deviceId": <did>, "seqId": <seq_id>}``.

        Sign source: JSON body verbatim. Returns None on success; raises
        :class:`AqaraAuthError` on auth, HTTP, or server-error responses.
        """
        body_dict = {
            "actionId": "",
            "deviceId": did,
            "seqId": seq_id,
        }
        body_str = json.dumps(body_dict)
        headers = self._build_headers(body_str, token=token)
        url = f"{self._area.server}{_SEQUENCE_RUN_PATH}"

        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)

    async def query_collection_panels(
        self, token: str, did: str,
    ) -> dict[int, EndpointPanel]:
        """Return endpoint_id -> ordered paths for a device.

        Maps to GET /app/v1.0/lumi/app/layout/collection/panels with
        query parameters subjectIds=<did> and types=device_endpoint_panel.

        Sign source is the alphabetically-sorted bare query string
        (``subjectIds=<did>&types=device_endpoint_panel``). Both keys and
        values are passed verbatim -- no JSON encoding and no
        URL-percent-encoding in the sign source. The same string appears
        on the wire URL unchanged.

        The real cloud response carries endpoint data under
        ``result[*].subjects[*]`` (not ``result[*].views[*]`` as the
        idealised spec describes). Each subject has an ``endpointId``
        (int) and a ``paths`` list. Multiple subjects per panel entry are
        collapsed into a single dict keyed by ``endpointId``; if two
        subjects share an endpointId the last one wins.

        Returns a dict keyed by endpoint id, values are EndpointPanel objects
        carrying the cloud's per-endpoint metadata (objProperties, endpointName,
        deviceTypes, etc.) plus the existing paths array. Empty dict if the
        cloud returns no panels.
        """
        # Alphabetical key order: subjectIds before types.
        # Values are bare strings -- NOT JSON-encoded (contrast with
        # query_device_detail which JSON-encodes its dids list).
        query = f"subjectIds={did}&types=device_endpoint_panel"
        url = f"{self._area.server}{_COLLECTION_PANELS_PATH}?{query}"
        headers = self._build_headers(query, token=token)

        payload = await self._request("GET", url, headers)
        self._raise_if_error(payload)

        result = payload.get("result")
        if not isinstance(result, list):
            raise AqaraAuthError(
                f"Aqara collection/panels response 'result' is not a list "
                f"(got {type(result).__name__})"
            )

        out: dict[int, EndpointPanel] = {}
        for panel in result:
            for subject in panel.get("subjects") or []:
                ep = self._parse_endpoint_subject(subject)
                if ep is not None:
                    out[ep.endpoint_id] = ep
        return out

    @staticmethod
    def _parse_endpoint_subject(subject: dict) -> EndpointPanel | None:
        """Coerce one collection/panels subject into an EndpointPanel.

        Returns None when the subject lacks an endpointId or a paths list (the
        skip case). If two subjects share an endpointId the caller keeps the
        last one.
        """
        ep_id = subject.get("endpointId")
        paths = subject.get("paths")
        if ep_id is None or not isinstance(paths, list):
            return None
        obj_props = subject.get("objProperties") or []
        if not isinstance(obj_props, list):
            obj_props = []
        return EndpointPanel(
            endpoint_id=int(ep_id),
            endpoint_name=str(subject.get("endpointName") or ""),
            endpoint_icon_id=str(subject.get("endpointIconId") or ""),
            device_name=str(subject.get("deviceName") or ""),
            device_types=str(subject.get("deviceTypes") or ""),
            position_name=str(subject.get("endpointPositionName") or ""),
            model_type=int(subject.get("modelType") or 0),
            obj_properties=tuple(str(p) for p in obj_props),
            paths=tuple(str(p) for p in paths),
        )

    async def query_camera_p2p_info(
        self, token: str, did: str,
    ) -> dict[str, Any]:
        """Return the camera's PPPP/P2P bootstrap info for one device.

        Calls ``GET /app/v1.0/lumi/devex/camera/p2p/info?did=<did>``.

        Sign source is the alphabetically-sorted bare query string
        (``did=<did>``), passed verbatim into ``_sign``. The same string is
        sent on the URL unchanged (no URL-percent-encoding required).

        Returns the raw ``result`` dict, with shape
        ``{"initStringApp": ..., "devP2pPublicKey": ..., "p2pId": ...}``.

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        query = f"did={did}"
        url = f"{self._area.server}{_CAMERA_P2P_INFO_PATH}?{query}"
        headers = self._build_headers(query, token=token)

        payload = await self._request("GET", url, headers)
        self._raise_if_error(payload)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise AqaraAuthError(
                f"Aqara camera/p2p/info response 'result' is not a dict "
                f"(got {type(result).__name__})"
            )
        return result

    async def request_camera_p2p_sign(
        self, token: str, did: str, p2p_app_public_key_hex: str,
    ) -> dict[str, Any]:
        """Return a per-session P2P signature for the app's public key.

        Calls ``POST /app/v1.0/lumi/devex/camera/p2p/sign`` with body
        ``{"devPwd": "", "did": <did>,
           "p2pAppPublicKey": <p2p_app_public_key_hex>}``.

        Sign source is the JSON body verbatim (default ``json.dumps``
        spacing - space after ``,`` and ``:``). Matching that default is
        important since the server validates the sign against the bytes
        on the wire.

        Returns the raw ``result`` dict, with shape
        ``{"sign": ..., "p2pDevPublicKey": ..., "time": ...}``.

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        body_dict = {
            "devPwd": "",
            "did": did,
            "p2pAppPublicKey": p2p_app_public_key_hex,
        }
        body_str = json.dumps(body_dict)
        headers = self._build_headers(body_str, token=token)
        url = f"{self._area.server}{_CAMERA_P2P_SIGN_PATH}"

        payload = await self._request("POST", url, headers, data=body_str)
        self._raise_if_error(payload)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise AqaraAuthError(
                f"Aqara camera/p2p/sign response 'result' is not a dict "
                f"(got {type(result).__name__})"
            )
        return result

    async def query_ptz_positions(
        self, token: str, did: str,
    ) -> list[dict[str, Any]]:
        """Return the saved PTZ preset positions for a camera (cloud-stored).

        Calls ``GET /app/v1.0/lumi/devex/camera/ptz/position/list?did=<did>``.

        Each entry has shape
        ``{"id": int, "name": str, "position": str, "imgUrl": str, ...}``. The
        ``"position"`` string is the opaque recall token passed to the local
        4140 IOCTL. Presets live in the cloud account, not the camera.

        Sign source is the bare query string (``did=<did>``), passed verbatim
        into ``_build_headers`` - identical construction to
        :meth:`query_camera_p2p_info`.

        Returns the raw ``result`` list (empty list when ``result`` is absent
        or not a list).

        Raises :class:`AqaraAuthError` on auth, HTTP, or server-error
        responses.
        """
        query = f"did={did}"
        url = f"{self._area.server}{_CAMERA_PTZ_POSITION_LIST_PATH}?{query}"
        headers = self._build_headers(query, token=token)

        payload = await self._request("GET", url, headers)
        self._raise_if_error(payload)
        result = payload.get("result")
        return list(result) if isinstance(result, list) else []

    @staticmethod
    def _raise_if_error(payload: dict[str, Any]) -> None:
        """Raise on a non-zero code; pick the right exception type.

        Auth-failure codes (see ``_AUTH_FAILURE_CODES``) raise the narrow
        :class:`AqaraCloudAuthError` so callers can trigger HA's re-auth
        flow specifically. Every other non-zero code raises the generic
        :class:`AqaraAuthError`.
        """
        code = payload.get("code")
        if str(code) == "0":
            return
        msg = (
            f"Aqara cloud rejected request (code={code} "
            f"msg={payload.get('message')!r})"
        )
        try:
            code_int = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_int = None
        if code_int is not None and code_int in _AUTH_FAILURE_CODES:
            raise AqaraCloudAuthError(msg)
        raise AqaraAuthError(msg)

    async def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        data: str | None = None,
    ) -> dict[str, Any]:
        """Issue an HTTP request and return the parsed JSON response.

        ``method`` is ``"GET"`` or ``"POST"``. For POST, pass the body
        as ``data``. For GET, leave ``data=None`` (any query string
        belongs in ``url``). Raises :class:`AqaraAuthError` on HTTP
        errors or non-JSON responses.
        """
        close_session = False
        session = self._session
        if session is None and self._hass is not None:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            session = async_get_clientsession(self._hass)
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True
        try:
            async with session.request(method, url, data=data, headers=headers) as resp:
                text = await resp.text()
        except aiohttp.ClientError as exc:
            raise AqaraAuthError(f"Aqara HTTP error: {exc}") from exc
        finally:
            if close_session:
                await session.close()

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AqaraAuthError(
                f"Aqara non-JSON response: {text[:200]!r}"
            ) from exc


# =============================================================================
# Backward-compat alias
# =============================================================================
#
# The class was originally named ``AqaraCloudAuth`` (login-only). It has
# since grown additional cloud query methods, so the canonical name is now
# :class:`AqaraCloudClient`. The old name is kept as a deprecated alias so
# any code (and external integrations) still importing ``AqaraCloudAuth``
# keeps working unchanged.
AqaraCloudAuth = AqaraCloudClient


# =============================================================================
# Light-effect helpers
# =============================================================================
#
# These module-level helpers consume cloud customaction/query responses and
# produce a tuple[Effect, ...] suitable for setting on a LightDescriptor's
# `effects` field. They live here next to the cloud RPCs they wrap so the
# request/response shape stays close to the parsing logic.


def _parse_effects(
    static_result: dict[str, Any],
    dynamic_result: dict[str, Any],
) -> tuple[Effect, ...]:
    """Walk UserCustomActions+DefaultCustomActions in both results.

    Each `result` is the cloud's `{"UserCustomActions": [...],
    "DefaultCustomActions": [...]}` dict. Both lists carry entries with
    the same shape; classification is by `seqId` presence (NOT by which
    result the entry came from):

    - seqId is None -> static scene; the entry's `value` field is a
      JSON-encoded `{"colour_temperature": <mireds>, "light_level": <pct>}`
      dict. Convert mireds to Kelvin so the in-memory Effect carries
      Kelvin (AqaraLight converts back at write time).
    - seqId is set -> dynamic scene; record name + str(seqId) only.
      The `is None` check is deliberate: `seqId == 0` is valid.
    """
    out: list[Effect] = []

    def _walk(result: dict[str, Any]) -> None:
        ucas = result.get("UserCustomActions") or []
        dcas = result.get("DefaultCustomActions") or []
        for entry in ucas + dcas:
            seq_id = entry.get("seqId")
            name = entry.get("customName") or ""
            if seq_id is None:
                # Static scene: parse value JSON.
                try:
                    value_dict = json.loads(entry.get("value") or "{}")
                except (json.JSONDecodeError, ValueError):
                    value_dict = {}
                ct_raw = value_dict.get("colour_temperature")
                # Defensive: 0 or non-numeric ct must not divide-by-zero.
                try:
                    ct_float = float(ct_raw) if ct_raw is not None else 0.0
                except (TypeError, ValueError):
                    ct_float = 0.0
                ct_kelvin = int(round(1_000_000 / ct_float)) if ct_float > 0 else None
                bright_raw = value_dict.get("light_level")
                try:
                    bright_pct = int(bright_raw) if bright_raw is not None else None
                except (TypeError, ValueError):
                    bright_pct = None
                out.append(Effect(
                    name=name,
                    seq_id=None,
                    color_temp_kelvin=ct_kelvin,
                    brightness_pct=bright_pct,
                ))
            else:
                out.append(Effect(name=name, seq_id=str(seq_id)))

    _walk(static_result)
    _walk(dynamic_result)
    return tuple(out)


async def enrich_light_effects(
    client: "AqaraCloudClient",
    token: str,
    *,
    device_model: str,
    user_device_id: str,
    descriptors: list[Any],
) -> None:
    """Enrich every LightDescriptor in `descriptors` with effects from the cloud.

    Mutates `descriptors` in place: each LightDescriptor is replaced
    (via dataclasses.replace) with an enriched copy carrying parsed
    Effect entries. Cloud failures are logged at WARNING and leave the
    affected descriptor with effects=() so the light still works locally.

    Note on action_id semantics (verified empirically against a live
    T2 RGB CCT bulb on 2026-05-05): the names are misleading.
        - "new_dynamic_scene_mode" -> STATIC scenes (no seqId)
        - "scene_mode" -> DYNAMIC scenes (seqId set)
    Both calls are needed; _parse_effects classifies each entry by
    seqId presence rather than which call it came from.

    Wire-up: called for each device that has light descriptors during
    setup, after descriptors are built and before entities are forwarded.
    """
    for i, desc in enumerate(descriptors):
        if not isinstance(desc, LightDescriptor):
            continue
        try:
            static_result = await client.query_custom_actions(
                token, "new_dynamic_scene_mode",
                device_model=device_model,
                user_device_id=user_device_id,
            )
            dynamic_result = await client.query_custom_actions(
                token, "scene_mode",
                device_model=device_model,
                user_device_id=user_device_id,
            )
            effects = _parse_effects(static_result, dynamic_result)
        except AqaraCloudAuthError:
            # Token expired/invalid -- propagate so the caller can trigger
            # HA's re-auth flow once for the whole setup pass, rather than
            # silently swallowing it per-device.
            raise
        except Exception as exc:  # noqa: BLE001 -- broad catch is intentional
            _LOGGER.warning(
                "Effect fetch failed for %s: %s; light works but effect_list "
                "will be empty until next setup",
                user_device_id, exc,
            )
            effects = ()
        descriptors[i] = dataclasses.replace(desc, effects=effects)
