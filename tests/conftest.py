"""Fixtures for DTEK DEM Outages tests."""

from __future__ import annotations

import base64
from datetime import date, datetime
import json
from typing import Any
from urllib.parse import quote

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.dtek_dem_outages.const import (
    CONF_HOUSE,
    CONF_SETTLEMENT,
    CONF_STREET,
    CSRF_COOKIE,
    DEFAULT_OPTIONS,
    DOMAIN,
    SITE_AJAX_PATH,
    SITE_BASE_URL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

AJAX_URL = f"{SITE_BASE_URL}{SITE_AJAX_PATH}"
ENTRY_ID = "01TESTENTRY"
KYIV = dt_util.get_time_zone("Europe/Kyiv")
SETTLEMENT = "м. Бориспіль"
STREET = "вул. Нечая Івана"
HOUSE = "43"
TITLE = f"{SETTLEMENT}, {STREET}, {HOUSE}"
NOW = datetime(2026, 10, 18, 7, 10, tzinfo=dt_util.UTC)
TODAY = date(2026, 10, 18)


def kyiv(day: date, time: str) -> datetime:
    """Return a Kyiv datetime."""
    hours, minutes = (int(part) for part in time.split(":"))
    return datetime(day.year, day.month, day.day, hours, minutes, tzinfo=KYIV)


def make_house(
    reason: str = "",
    start: str = "",
    end: str = "",
    external: bool = False,
) -> dict[str, Any]:
    """Return a raw emergency house record."""
    return {
        "sub_type": reason,
        "start_date": start,
        "end_date": end,
        "type": "2" if reason else "",
        "sub_type_reason": [],
        "voluntarily": None,
        "cek": external,
    }


def unmask(masked: str) -> str:
    """Undo Yii2 token masking."""
    raw = base64.urlsafe_b64decode(masked)
    half = len(raw) // 2
    return bytes(a ^ b for a, b in zip(raw[:half], raw[half:], strict=True)).decode()


class FakeDtek:
    """Fake DTEK ajax endpoint."""

    token = "Fz3qUb1L0aW7xN2cR5tY8vK4mH6pJ9sD"  # noqa: S105

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Initialize a fake with address suggestions and no outage."""
        self._mock = aioclient_mock
        self.streets = {
            SETTLEMENT: [STREET, "вул. Київський Шлях"],
            "с. Буча": ["Без вулиці"],
        }
        self.houses: dict[str, Any] = {HOUSE: make_house()}
        self.ajax_error: Exception | None = None
        self.ajax_status = 200
        self.ajax_text: str | None = None
        self.issued_tokens = 0
        self.forms: list[dict[str, str]] = []

    @property
    def cookie_value(self) -> str:
        """Return a Yii2-style CSRF cookie."""
        serialized = (
            f'a:2:{{i:0;s:{len(CSRF_COOKIE)}:"{CSRF_COOKIE}";'
            f'i:1;s:{len(self.token)}:"{self.token}";}}'
        )
        return quote("0" * 64 + serialized)

    async def ajax(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Answer a POST to the ajax endpoint."""
        if self.ajax_error is not None:
            raise self.ajax_error
        if self.ajax_text is not None:
            return AiohttpClientMockResponse(
                method, url, status=self.ajax_status, text=self.ajax_text
            )
        headers = self._mock.mock_calls[-1][3] or {}
        masked = headers.get("X-CSRF-Token")
        if masked is None or unmask(masked) != self.token:
            self.issued_tokens += 1
            return AiohttpClientMockResponse(
                method,
                url,
                status=400,
                text="Bad Request",
                cookies={CSRF_COOKIE: self.cookie_value},
            )
        form = dict(data)
        self.forms.append(form)
        if self.ajax_status != 200:
            return AiohttpClientMockResponse(method, url, status=self.ajax_status)
        if form["method"] == "getStreets":
            return self._json(method, url, {"result": True, "streets": self.streets})
        city = form.get("data[0][value]")
        street = form.get("data[1][value]")
        if city not in self.streets or street not in self.streets[city]:
            return self._json(method, url, {"result": False, "text": "Error"})
        return self._json(
            method,
            url,
            {"result": True, "data": self.houses},
        )

    @staticmethod
    def _json(method: str, url: Any, payload: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            method,
            url,
            text=json.dumps(payload, ensure_ascii=False),
            headers={"Content-Type": "application/json"},
        )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations."""


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Freeze time for deterministic outage tests."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def fake_dtek(aioclient_mock: AiohttpClientMocker) -> FakeDtek:
    """Route DTEK requests to the fake endpoint."""
    fake = FakeDtek(aioclient_mock)
    aioclient_mock.post(AJAX_URL, side_effect=fake.ajax)
    return fake


@pytest.fixture
def options() -> dict[str, Any]:
    """Return default entry options."""
    return dict(DEFAULT_OPTIONS)


@pytest.fixture
def entry_data() -> dict[str, Any]:
    """Return default address data."""
    return {
        CONF_SETTLEMENT: SETTLEMENT,
        CONF_STREET: STREET,
        CONF_HOUSE: HOUSE,
    }


@pytest.fixture
def mock_config_entry(
    options: dict[str, Any], entry_data: dict[str, Any]
) -> MockConfigEntry:
    """Return a config entry for the test address."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        unique_id=f"{SETTLEMENT}|{STREET}|{HOUSE}",
        entry_id=ENTRY_ID,
        data=entry_data,
        options=options,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up a config entry."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def get_entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    """Return the entity ID for a test entry."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{ENTRY_ID}_{key}"
    )
    assert entity_id is not None
    return entity_id
