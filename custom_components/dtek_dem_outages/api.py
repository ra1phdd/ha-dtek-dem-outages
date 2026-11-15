"""Async client for current emergency outages reported by DTEK DEM."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
import re
import secrets
from typing import Any
from urllib.parse import unquote

import aiohttp

from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey
from homeassistant.util.json import json_loads

from .const import (
    API_TIMEOUT,
    CSRF_COOKIE,
    DOMAIN,
    OUTAGE_KIND_EMERGENCY,
    PROJECT_URL,
    SITE_AJAX_PATH,
    SITE_BASE_URL,
    SITE_REFERER_PATH,
    TIMEZONE,
)

DATA_CLIENT: HassKey[DtekClient] = HassKey(f"{DOMAIN}_client")
HOUSE_DATE_FORMAT = "%H:%M %d.%m.%Y"
_CSRF_TOKEN_RE = re.compile(r'i:1;s:\d+:"([^"]+)"')
_CHALLENGE_MARKER = "_Incapsula_Resource"


class DtekError(Exception):
    """Base error raised by the DTEK client."""


class DtekConnectionError(DtekError):
    """The DTEK server could not be reached."""


class DtekBlockedError(DtekConnectionError):
    """DTEK returned its anti-bot challenge."""


class DtekResponseError(DtekError):
    """DTEK returned an invalid response."""


class DtekNotFoundError(DtekError):
    """DTEK returned no current address record."""


def build_user_agent(version: str | None = None) -> str:
    """Return the integration User-Agent."""
    product = f"ha-dtek-dem-outages/{version}" if version else "ha-dtek-dem-outages"
    return f"HomeAssistant/{ha_version} {product} (+{PROJECT_URL})"


def extract_csrf_token(cookie_value: str) -> str | None:
    """Extract the raw Yii2 token from a CSRF cookie."""
    match = _CSRF_TOKEN_RE.search(unquote(cookie_value))
    return match.group(1) if match else None


def mask_csrf_token(token: str, mask: bytes | None = None) -> str:
    """Mask a token as Yii2 expects."""
    raw = token.encode()
    mask = mask or secrets.token_bytes(len(raw))
    return base64.urlsafe_b64encode(
        mask + bytes(a ^ b for a, b in zip(mask, raw, strict=True))
    ).decode()


def parse_local_datetime(value: Any, fmt: str, tz: tzinfo) -> datetime | None:
    """Parse a DTEK local datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.strptime(value.strip(), fmt)  # noqa: DTZ007
    except ValueError:
        return None
    return parsed.replace(tzinfo=tz)


def format_iso(value: datetime | None) -> str | None:
    """Format a datetime as ISO, or return None."""
    return value.isoformat() if value is not None else None


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO datetime saved in the Store."""
    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


def classify_reason(reason: str) -> str:
    """Classify an outage description."""
    text = reason.casefold()
    if "аварійн" in text or "екстрен" in text:
        return OUTAGE_KIND_EMERGENCY
    return "other"


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AddressOutage:
    """An emergency outage currently reported for an address."""

    reason: str
    kind: str = OUTAGE_KIND_EMERGENCY
    start: datetime | None = None
    end: datetime | None = None
    type: str = ""

    def is_active(self, now: datetime) -> bool:
        """Return whether the outage is active at ``now``."""
        if self.start is not None and now < self.start:
            return False
        return self.end is None or now < self.end

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable outage."""
        return {
            "reason": self.reason,
            "kind": self.kind,
            "start": format_iso(self.start),
            "end": format_iso(self.end),
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: Any) -> AddressOutage | None:
        """Restore an outage from the Store."""
        if not isinstance(data, Mapping) or not (reason := _as_str(data.get("reason"))):
            return None
        if classify_reason(reason) != OUTAGE_KIND_EMERGENCY:
            return None
        return cls(
            reason=reason,
            kind=OUTAGE_KIND_EMERGENCY,
            start=parse_iso(data.get("start")),
            end=parse_iso(data.get("end")),
            type=_as_str(data.get("type")),
        )


def _parse_outage(raw: Mapping[str, Any], tz: tzinfo) -> AddressOutage | None:
    """Parse an emergency outage record; ignore non-emergency records."""
    reason = _as_str(raw.get("sub_type"))
    if not reason or classify_reason(reason) != OUTAGE_KIND_EMERGENCY:
        return None
    return AddressOutage(
        reason=reason,
        start=parse_local_datetime(raw.get("start_date"), HOUSE_DATE_FORMAT, tz),
        end=parse_local_datetime(raw.get("end_date"), HOUSE_DATE_FORMAT, tz),
        type=_as_str(raw.get("type")),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class House:
    """A house record returned by DTEK."""

    number: str
    outage: AddressOutage | None = None
    external: bool = False

    @classmethod
    def from_api(cls, number: str, raw: Any, tz: tzinfo) -> House | None:
        """Build a house from one or several DTEK records."""
        records = [raw] if isinstance(raw, Mapping) else raw
        if not isinstance(records, list):
            return None
        records = [record for record in records if isinstance(record, Mapping)]
        if not records:
            return None
        outages = [
            outage
            for record in records
            if (outage := _parse_outage(record, tz)) is not None
        ]
        outage = outages[0] if outages else None
        return cls(
            number=number,
            outage=outage,
            external=any(bool(record.get("cek")) for record in records),
        )


# Latin letters typed instead of Cyrillic lookalikes.
_HOUSE_LOOKALIKES = str.maketrans("aceiopxkmhtby", "асеіорхкмнтву")


def _house_key(number: str) -> str:
    return "".join(number.split()).casefold().translate(_HOUSE_LOOKALIKES)


def find_house(houses: Mapping[str, House], number: str) -> House | None:
    """Find a house while tolerating spaces and Cyrillic lookalikes."""
    if house := houses.get(number):
        return house
    wanted = _house_key(number)
    matches = [house for key, house in houses.items() if _house_key(key) == wanted]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True, kw_only=True)
class StreetData:
    """The current-address response for one street."""

    houses: dict[str, House] = field(default_factory=dict)


def _form_pairs(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Encode fields like jQuery's ``serializeArray``."""
    form: dict[str, str] = {}
    for index, (name, value) in enumerate(pairs):
        form[f"data[{index}][name]"] = name
        form[f"data[{index}][value]"] = value
    return form


class DtekClient:
    """Async client for the DTEK DEM ajax endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tz: tzinfo,
        *,
        base_url: str = SITE_BASE_URL,
        user_agent: str | None = None,
        session_factory: Callable[[], aiohttp.ClientSession] | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._session_factory = session_factory
        self.tz = tz
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent or build_user_agent()
        self._csrf_token: str | None = None
        self._bootstrap_lock = asyncio.Lock()

    @property
    def _active_session(self) -> aiohttp.ClientSession:
        """Return a live session, recreating it after Home Assistant closes it."""
        if self._session.closed and self._session_factory is not None:
            self._session = self._session_factory()
            self._csrf_token = None
        return self._session

    async def async_get_streets(self) -> dict[str, list[str]]:
        """Return settlements and their streets."""
        payload = await self._async_ajax({"method": "getStreets"})
        streets = payload.get("streets")
        if not isinstance(streets, Mapping):
            raise DtekResponseError("Expected a streets object")
        result = {
            settlement: [name for name in names if isinstance(name, str)]
            for settlement, names in streets.items()
            if isinstance(settlement, str) and isinstance(names, list)
        }
        if not result:
            raise DtekResponseError("Empty streets list")
        return result

    async def async_get_street(self, settlement: str, street: str) -> StreetData:
        """Return current emergency records for a street."""
        payload = await self._async_ajax(
            {
                "method": "getHomeNum",
                **_form_pairs((("city", settlement), ("street", street))),
            }
        )
        houses: dict[str, House] = {}
        data = payload.get("data")
        if isinstance(data, Mapping):
            for number, raw in data.items():
                if isinstance(number, str) and (
                    house := House.from_api(number, raw, self.tz)
                ):
                    houses[number] = house
        return StreetData(houses=houses)

    async def _async_ajax(self, form: Mapping[str, str]) -> Mapping[str, Any]:
        """POST to DTEK with a Yii2 CSRF token."""
        token = await self._async_token()
        status, payload = await self._async_post(form, token)
        if status == 400:
            token = await self._async_token(stale=token)
            status, payload = await self._async_post(form, token)
        if status != 200:
            raise DtekResponseError(f"Unexpected HTTP status {status} from DTEK")
        if not isinstance(payload, Mapping):
            raise DtekResponseError("Expected an object from DTEK")
        if payload.get("result") is not True:
            raise DtekNotFoundError(str(payload.get("text") or "Unknown address"))
        return payload

    async def _async_token(self, stale: str | None = None) -> str:
        """Return a CSRF token, bootstrapping the DTEK cookie when needed."""
        async with self._bootstrap_lock:
            session = self._active_session
            if stale is not None and self._csrf_token == stale:
                self._csrf_token = None
                session.cookie_jar.clear(lambda cookie: cookie.key == CSRF_COOKIE)
            if self._csrf_token is None:
                await self._async_post({"method": "getHomeNum"}, None)
            if self._csrf_token is None:
                raise DtekResponseError("DTEK did not issue a CSRF cookie")
            return self._csrf_token

    async def _async_post(
        self, form: Mapping[str, str], token: str | None
    ) -> tuple[int, Any]:
        """POST a form and return status plus decoded JSON."""
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self._base_url,
            "Referer": f"{self._base_url}{SITE_REFERER_PATH}",
        }
        if token is not None:
            headers["X-CSRF-Token"] = mask_csrf_token(token)
        try:
            async with self._active_session.post(
                f"{self._base_url}{SITE_AJAX_PATH}",
                data=dict(form),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                if (cookie := response.cookies.get(CSRF_COOKIE)) is not None and (
                    new_token := extract_csrf_token(cookie.value)
                ):
                    self._csrf_token = new_token
                status = response.status
                text = await response.text()
        except TimeoutError as err:
            raise DtekConnectionError("Timeout while requesting DTEK") from err
        except aiohttp.ClientError as err:
            raise DtekConnectionError(f"Error requesting DTEK: {err}") from err
        if _CHALLENGE_MARKER in text:
            raise DtekBlockedError("DTEK answered with the anti-bot challenge")
        if status != 200:
            return status, None
        try:
            return status, json_loads(text)
        except ValueError as err:
            raise DtekResponseError("Invalid JSON from DTEK") from err


async def async_get_client(hass: HomeAssistant) -> DtekClient:
    """Return the client shared by config flows and entries."""
    if client := hass.data.get(DATA_CLIENT):
        return client
    integration = await async_get_integration(hass, DOMAIN)
    tz = await dt_util.async_get_time_zone(TIMEZONE)
    if tz is None:
        raise DtekError(f"Time zone {TIMEZONE} is not available")

    def create_session() -> aiohttp.ClientSession:
        return async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())

    client = DtekClient(
        create_session(),
        tz,
        user_agent=build_user_agent(str(integration.version)),
        session_factory=create_session,
    )
    hass.data[DATA_CLIENT] = client
    return client
