"""Tests for the DTEK DEM client and current-outage parser."""

from __future__ import annotations

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.dtek_dem_outages.api import (
    AddressOutage,
    DtekBlockedError,
    DtekClient,
    DtekConnectionError,
    DtekNotFoundError,
    DtekResponseError,
    House,
    async_get_client,
    build_user_agent,
    classify_reason,
    extract_csrf_token,
    find_house,
    mask_csrf_token,
)
from custom_components.dtek_dem_outages.const import (
    CONFIGURATION_URL,
    CSRF_COOKIE,
    OUTAGE_KIND_EMERGENCY,
    SITE_BASE_URL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    AJAX_URL,
    HOUSE,
    KYIV,
    SETTLEMENT,
    STREET,
    TODAY,
    FakeDtek,
    kyiv,
    make_house,
    unmask,
)


@pytest.fixture
async def client(hass: HomeAssistant, fake_dtek: FakeDtek) -> DtekClient:
    """Return a client using the mocked session."""
    return await async_get_client(hass)


def test_csrf_helpers() -> None:
    """CSRF cookies are extracted and tokens are masked."""
    fake = FakeDtek.__new__(FakeDtek)
    assert extract_csrf_token(fake.cookie_value) == FakeDtek.token
    masked = mask_csrf_token(FakeDtek.token)
    assert unmask(masked) == FakeDtek.token
    assert mask_csrf_token("ab", b"\x01\x02") == "AQJgYA=="


def test_site_and_user_agent() -> None:
    """The client targets DTEK DEM."""
    assert SITE_BASE_URL == "https://www.dtek-dem.com.ua"
    assert CONFIGURATION_URL == "https://www.dtek-dem.com.ua/ua/shutdowns"
    assert CSRF_COOKIE == "_csrf-dtek-dem"
    assert "ha-dtek-dem-outages/0.2.4" in build_user_agent("0.2.4")


def test_emergency_parser() -> None:
    """Emergency records parse; planned records are intentionally ignored."""
    raw = make_house(
        reason="Аварійні ремонтні роботи",
        start="09:48 18.10.2026",
        end="13:48 18.10.2026",
    )
    house = House.from_api(HOUSE, raw, KYIV)
    assert house is not None
    assert house.outage == AddressOutage(
        reason="Аварійні ремонтні роботи",
        kind=OUTAGE_KIND_EMERGENCY,
        start=kyiv(TODAY, "09:48"),
        end=kyiv(TODAY, "13:48"),
        type="2",
    )
    planned = make_house(reason="Планові ремонтні роботи")
    assert House.from_api(HOUSE, planned, KYIV).outage is None
    assert classify_reason("Аварійні ремонтні роботи") == OUTAGE_KIND_EMERGENCY


def test_find_house() -> None:
    """House numbers tolerate spaces and Cyrillic lookalikes."""
    houses = {number: House(number=number) for number in ("8 а", "10")}
    assert find_house(houses, "8а").number == "8 а"
    assert find_house(houses, "12") is None


def test_address_outage_round_trip() -> None:
    """Emergency outage storage round-trips and reports activity."""
    outage = AddressOutage(
        reason="Аварійні ремонтні роботи",
        start=kyiv(TODAY, "10:00"),
        end=kyiv(TODAY, "12:00"),
    )
    assert AddressOutage.from_dict(outage.as_dict()) == outage
    assert not outage.is_active(kyiv(TODAY, "09:59"))
    assert outage.is_active(kyiv(TODAY, "10:00"))
    assert not outage.is_active(kyiv(TODAY, "12:00"))


async def test_get_streets_bootstraps_csrf(
    client: DtekClient, fake_dtek: FakeDtek, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first request gets a cookie and later requests reuse the token."""
    streets = await client.async_get_streets()
    assert streets[SETTLEMENT] == [STREET, "вул. Київський Шлях"]
    assert fake_dtek.issued_tokens == 1
    assert aioclient_mock.call_count == 2
    assert "X-CSRF-Token" not in aioclient_mock.mock_calls[0][3]
    assert aioclient_mock.mock_calls[1][3]["X-Requested-With"] == "XMLHttpRequest"
    await client.async_get_streets()
    assert fake_dtek.issued_tokens == 1


async def test_get_street_has_no_schedule_request(
    client: DtekClient, fake_dtek: FakeDtek
) -> None:
    """Address lookups contain only city and street fields."""
    data = await client.async_get_street(SETTLEMENT, STREET)
    assert set(data.houses) == {HOUSE}
    assert fake_dtek.forms[-1] == {
        "method": "getHomeNum",
        "data[0][name]": "city",
        "data[0][value]": SETTLEMENT,
        "data[1][name]": "street",
        "data[1][value]": STREET,
    }


async def test_not_found_means_no_current_outage(client: DtekClient) -> None:
    """DEM's result=false is a normal no-outage address result."""
    with pytest.raises(DtekNotFoundError):
        await client.async_get_street(SETTLEMENT, "вул. Неіснуюча")


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ({"ajax_error": aiohttp.ClientConnectionError()}, DtekConnectionError),
        ({"ajax_error": TimeoutError()}, DtekConnectionError),
        ({"ajax_status": 503}, DtekResponseError),
        ({"ajax_text": "not json"}, DtekResponseError),
        (
            {"ajax_text": '<script src="/_Incapsula_Resource?x=1"></script>'},
            DtekBlockedError,
        ),
    ],
)
async def test_ajax_errors(
    client: DtekClient,
    fake_dtek: FakeDtek,
    setup: dict[str, object],
    error: type[Exception],
) -> None:
    """Transport and malformed responses remain errors."""
    for key, value in setup.items():
        setattr(fake_dtek, key, value)
    with pytest.raises(error):
        await client.async_get_street(SETTLEMENT, STREET)


async def test_no_csrf_cookie(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bootstrap response without a cookie is rejected."""
    aioclient_mock.post(AJAX_URL, status=400, text="Bad Request")
    client = DtekClient(async_get_clientsession(hass), KYIV)
    with pytest.raises(DtekResponseError, match="CSRF"):
        await client.async_get_streets()


async def test_closed_session_is_replaced(
    hass: HomeAssistant, client: DtekClient, fake_dtek: FakeDtek
) -> None:
    """A closed Home Assistant session is replaced."""
    assert await client.async_get_streets()
    await client._session.close()
    assert await client.async_get_streets()
    assert not client._session.closed
