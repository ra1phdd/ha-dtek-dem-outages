"""Data update coordinator for current DTEK DEM emergency outages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import random
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AddressOutage,
    DtekBlockedError,
    DtekClient,
    DtekError,
    DtekNotFoundError,
    find_house,
    format_iso,
    parse_iso,
)
from .const import (
    CONF_HOUSE,
    CONF_LANGUAGE,
    CONF_SETTLEMENT,
    CONF_STREET,
    CONF_UPDATE_INTERVAL,
    CONNECTION_OFFLINE,
    CONNECTION_OK,
    DEFAULT_LANGUAGE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_ADDRESS_OUTAGE,
    EVENT_BUS,
    LANGUAGES,
    MIN_UPDATE_INTERVAL,
    REFRESH_COOLDOWN,
    RETRY_JITTER,
    RETRY_MAX_INTERVAL,
    STORAGE_VERSION,
    signal_event,
    storage_key,
)

_LOGGER = logging.getLogger(__name__)
type DtekConfigEntry = ConfigEntry[DtekCoordinator]


@dataclass(slots=True, kw_only=True)
class DtekData:
    """Snapshot of current address and connection state."""

    address_outage: AddressOutage | None = None
    address_ok: bool = False
    fetch_ok: bool = False
    last_success: datetime | None = None
    last_error: str | None = None
    connection: str = CONNECTION_OFFLINE
    consecutive_failures: int = 0
    last_dtek_success: datetime | None = None
    blocked: bool = False
    external_operator: bool = False


class DtekCoordinator(DataUpdateCoordinator[DtekData]):
    """Fetch and publish current emergency outage data for one address."""

    config_entry: DtekConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: DtekConfigEntry, client: DtekClient
    ) -> None:
        """Initialize the coordinator."""
        options = entry.options
        minutes = max(
            int(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
            MIN_UPDATE_INTERVAL,
        )
        self._base_interval = timedelta(minutes=minutes)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self._base_interval,
        )
        self.client = client
        self.tz = client.tz
        self.settlement = str(entry.data.get(CONF_SETTLEMENT, ""))
        self.street = str(entry.data.get(CONF_STREET, ""))
        self.house = str(entry.data.get(CONF_HOUSE, ""))
        self.has_address = bool(self.settlement and self.street and self.house)
        language = options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE
        self.translations: dict[str, str] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self._address_outage: AddressOutage | None = None
        self._address_ok = False
        self._fetch_ok = False
        self._external_operator = False
        self._last_success: datetime | None = None
        self._last_dtek_success: datetime | None = None
        self._last_error: str | None = None
        self._connection = CONNECTION_OFFLINE
        self._blocked = False
        self._failures = 0
        self._has_baseline = False
        self._pending_events: list[dict[str, Any]] = []
        self._dispatch_enabled = False
        self._last_attempt: datetime | None = None

    async def _async_setup(self) -> None:
        """Load the last current outage from storage."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict) or stored.get("address") != self.address_key:
            return
        self._address_outage = AddressOutage.from_dict(stored.get("address_outage"))
        self._address_ok = bool(stored.get("address_ok", False))
        self._last_success = parse_iso(stored.get("last_success"))
        self._last_dtek_success = parse_iso(stored.get("last_dtek_success"))
        self._external_operator = bool(stored.get("external_operator", False))
        self._has_baseline = True

    @property
    def address_key(self) -> str:
        """Return the address represented by this coordinator."""
        return f"{self.settlement}|{self.street}|{self.house}"

    def _store_data(self) -> dict[str, Any]:
        return {
            "address": self.address_key,
            "address_outage": (
                self._address_outage.as_dict() if self._address_outage else None
            ),
            "address_ok": self._address_ok,
            "external_operator": self._external_operator,
            "last_success": format_iso(self._last_success),
            "last_dtek_success": format_iso(self._last_dtek_success),
        }

    def refresh_due(self, now: datetime) -> bool:
        """Return whether the refresh cooldown has elapsed."""
        return self._last_attempt is None or now - self._last_attempt >= timedelta(
            seconds=REFRESH_COOLDOWN
        )

    async def _async_update_data(self) -> DtekData:
        """Fetch the current outage from DTEK."""
        now = self._last_attempt = dt_util.utcnow()
        self._blocked = False
        error: str | None = None
        changed = False
        try:
            street = await self.client.async_get_street(self.settlement, self.street)
        except DtekNotFoundError:
            # DEM uses result=false when the address has no current outage.
            self._address_ok = True
            self._external_operator = False
            changed = self._set_outage(None, now)
        except DtekError as err:
            self._address_ok = False
            self._fetch_ok = False
            self._blocked = isinstance(err, DtekBlockedError)
            error = f"DTEK: {err}"
            self._failures += 1
            self._connection = CONNECTION_OFFLINE
            self.update_interval = self._next_interval()
            self._last_error = error
            if not self._has_baseline:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="update_failed",
                    translation_placeholders={"error": error},
                ) from err
            return self._snapshot()
        else:
            self._address_ok = True
            house = find_house(street.houses, self.house)
            changed = self._set_outage(house.outage if house else None, now)
            self._external_operator = house.external if house else False

        self._failures = 0
        self.update_interval = self._base_interval
        self._fetch_ok = True
        self._connection = CONNECTION_OK
        self._last_success = now
        self._last_dtek_success = now
        self._last_error = error
        if not self._has_baseline:
            self._has_baseline = True
            changed = True
        if changed:
            await self._store.async_save(self._store_data())
        else:
            self._store.async_delay_save(self._store_data, 30)
        snapshot = self._snapshot()
        self._dispatch_events()
        return snapshot

    def _set_outage(self, outage: AddressOutage | None, now: datetime) -> bool:
        """Apply an outage and queue one event after the silent baseline."""
        old = self._address_outage
        self._address_outage = outage
        if old == outage or not self._has_baseline:
            return old != outage
        status = "cleared" if outage is None else "new" if old is None else "updated"
        shown = outage or old
        self._pending_events.append(
            {
                "type": EVENT_ADDRESS_OUTAGE,
                "status": status,
                "reason": shown.reason if shown else None,
                "kind": shown.kind if shown else None,
                "start": format_iso(shown.start) if shown else None,
                "end": format_iso(shown.end) if shown else None,
                "settlement": self.settlement,
                "street": self.street,
                "house": self.house,
                "config_entry_id": self.config_entry.entry_id,
                "entry_title": self.config_entry.title,
            }
        )
        return True

    def _next_interval(self) -> timedelta:
        """Back off polling after a failed request."""
        longest = max(self._base_interval, timedelta(minutes=RETRY_MAX_INTERVAL))
        delay = min(self._base_interval * 2 ** min(self._failures - 1, 8), longest)
        return delay * random.uniform(1 - RETRY_JITTER, 1 + RETRY_JITTER)  # noqa: S311

    def _snapshot(self) -> DtekData:
        return DtekData(
            address_outage=self._address_outage,
            address_ok=self._address_ok,
            fetch_ok=self._fetch_ok,
            last_success=self._last_success,
            last_error=self._last_error,
            connection=self._connection,
            consecutive_failures=self._failures,
            last_dtek_success=self._last_dtek_success,
            blocked=self._blocked,
            external_operator=self._external_operator,
        )

    async def async_load_translations(self) -> None:
        """Load entity names in the configured language."""
        self.translations = await async_get_translations(
            self.hass, self.language, "entity", {DOMAIN}
        )

    async def async_shutdown(self) -> None:
        """Save state and stop polling."""
        if self._has_baseline:
            await self._store.async_save(self._store_data())
        await super().async_shutdown()

    @callback
    def async_enable_event_dispatch(self) -> CALLBACK_TYPE:
        """Dispatch queued events after Home Assistant starts."""

        @callback
        def _started(_hass: HomeAssistant) -> None:
            self._dispatch_enabled = True
            self._dispatch_events()

        return async_at_started(self.hass, _started)

    @callback
    def _dispatch_events(self) -> None:
        if not self._dispatch_enabled or not self._pending_events:
            return
        signal = signal_event(self.config_entry.entry_id)
        pending, self._pending_events = self._pending_events, []
        for payload in pending:
            self.hass.bus.async_fire(EVENT_BUS, payload)
            async_dispatcher_send(self.hass, signal, payload["type"], payload)

    def diagnostics_state(self) -> dict[str, Any]:
        """Return coordinator internals safe for diagnostics."""
        return {
            "has_baseline": self._has_baseline,
            "pending_events": len(self._pending_events),
            "last_attempt": format_iso(self._last_attempt),
        }


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete an entry's stored state."""
    await Store[dict[str, Any]](
        hass, STORAGE_VERSION, storage_key(entry_id)
    ).async_remove()
