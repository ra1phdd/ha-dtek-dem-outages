"""Constants for the DTEK DEM Outages integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dtek_dem_outages"
NAME: Final = "DTEK DEM Outages"
ATTRIBUTION: Final = "Data provided by DTEK Donetsk Electric Networks"
MANUFACTURER: Final = "DTEK DEM"
CONFIGURATION_URL: Final = "https://www.dtek-dem.com.ua/ua/shutdowns"
PROJECT_URL: Final = "https://github.com/ra1phdd/ha-dtek-dem-outages"

SITE_BASE_URL: Final = "https://www.dtek-dem.com.ua"
SITE_AJAX_PATH: Final = "/ua/ajax"
SITE_REFERER_PATH: Final = "/ua/shutdowns"
CSRF_COOKIE: Final = "_csrf-dtek-dem"
API_TIMEOUT: Final = 20
TIMEZONE: Final = "Europe/Kyiv"

CONF_SETTLEMENT: Final = "settlement"
CONF_STREET: Final = "street"
CONF_HOUSE: Final = "house"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_LANGUAGE: Final = "language"

DEFAULT_UPDATE_INTERVAL: Final = 5
MIN_UPDATE_INTERVAL: Final = 2
MAX_UPDATE_INTERVAL: Final = 60
RETRY_MAX_INTERVAL: Final = 15
RETRY_JITTER: Final = 0.1

LANGUAGES: Final[dict[str, str]] = {
    "uk": "Українська",
    "en": "English",
    "ru": "Русский",
}
DEFAULT_LANGUAGE: Final = "uk"
DEFAULT_OPTIONS: Final[dict[str, object]] = {
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    CONF_LANGUAGE: DEFAULT_LANGUAGE,
}
REFRESH_COOLDOWN: Final = 60

EVENT_BUS: Final = f"{DOMAIN}_event"
EVENT_ADDRESS_OUTAGE: Final = "address_outage"
EVENT_TYPES: Final = (EVENT_ADDRESS_OUTAGE,)

OUTAGE_KIND_EMERGENCY: Final = "emergency"
ADDRESS_STATUS_NONE: Final = "none"
ADDRESS_STATUSES: Final = (ADDRESS_STATUS_NONE, OUTAGE_KIND_EMERGENCY)

CONNECTION_OK: Final = "ok"
CONNECTION_OFFLINE: Final = "offline"
CONNECTION_STATES: Final = (CONNECTION_OK, CONNECTION_OFFLINE)

ADDRESS_ENTITIES: Final = (
    ("binary_sensor", "address_outage"),
    ("sensor", "address_status"),
    ("sensor", "restoration"),
)

STORAGE_VERSION: Final = 1
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"


def signal_event(entry_id: str) -> str:
    """Return the dispatcher signal used for one entry's events."""
    return f"{DOMAIN}_event_{entry_id}"


def storage_key(entry_id: str) -> str:
    """Return the Store key for one entry."""
    return f"{DOMAIN}.{entry_id}"
