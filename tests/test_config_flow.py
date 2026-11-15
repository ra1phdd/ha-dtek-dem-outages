"""Tests for the DEM address config flow."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import aiohttp
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.dtek_dem_outages.config_flow import (
    build_title,
    house_sort_key,
    match_choice,
    match_settlement,
    match_street,
    settlement_sort_key,
    street_choices,
)
from custom_components.dtek_dem_outages.const import (
    CONF_HOUSE,
    CONF_LANGUAGE,
    CONF_SETTLEMENT,
    CONF_STREET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from .conftest import HOUSE, SETTLEMENT, STREET, TITLE, FakeDtek, setup_entry


async def start(hass: HomeAssistant) -> dict[str, Any]:
    """Start a user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def submit(hass: HomeAssistant, result: dict[str, Any], data: dict) -> dict:
    """Submit a flow step."""
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


def fields_of(result: dict[str, Any]) -> list[str]:
    """Return form field names."""
    schema = result["data_schema"]
    if isinstance(schema, list):
        return [item["name"] for item in schema]
    return [str(marker) for marker in schema.schema]


def options_of(result: dict[str, Any], key: str) -> list[Any]:
    """Return selector options."""
    for marker, selector in result["data_schema"].schema.items():
        if marker == key:
            return selector.config["options"]
    raise KeyError(key)


def test_helpers() -> None:
    """Address helper functions normalize user input."""
    names = ["с. Яблунівка", "м. Ірпінь", "смт Гостомель"]
    assert sorted(names, key=settlement_sort_key) == [
        "смт Гостомель",
        "м. Ірпінь",
        "с. Яблунівка",
    ]
    assert sorted(["10", "2", "10/2", "10 а", "1"], key=house_sort_key) == [
        "1",
        "2",
        "10",
        "10 а",
        "10/2",
    ]
    assert match_choice("  м.  бориспіль ", [SETTLEMENT]) == SETTLEMENT
    assert match_choice("Київ", [SETTLEMENT]) is None
    assert match_settlement("Бориспіль", {SETTLEMENT: [STREET]}) == SETTLEMENT
    assert build_title("с. Буча", "Без вулиці", "5") == "с. Буча, 5"


def test_street_matching() -> None:
    """Street suggestions match their canonical values."""
    choices = street_choices({"м. Київ": ["вул. Миру", "пров. Миру"]}, "м. Київ")
    assert match_street("пров. миру", choices) == "пров. Миру"
    assert match_street("миру", choices) is None


async def test_address_flow_creates_entry(
    hass: HomeAssistant, fake_dtek: FakeDtek
) -> None:
    """Settlement, street and house create an address entry."""
    result = await start(hass)
    assert result["step_id"] == "user"
    assert options_of(result, CONF_SETTLEMENT)
    result = await submit(hass, result, {CONF_SETTLEMENT: SETTLEMENT})
    result = await submit(hass, result, {CONF_STREET: STREET})
    result = await submit(hass, result, {CONF_HOUSE: HOUSE})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {
        CONF_SETTLEMENT: SETTLEMENT,
        CONF_STREET: STREET,
        CONF_HOUSE: HOUSE,
    }
    assert result["options"] == DEFAULT_OPTIONS


async def test_custom_address_values_are_accepted(
    hass: HomeAssistant, fake_dtek: FakeDtek
) -> None:
    """Non-empty values absent from suggestions can be entered."""
    result = await start(hass)
    result = await submit(hass, result, {CONF_SETTLEMENT: "м. Слов'янськ"})
    result = await submit(hass, result, {CONF_STREET: "ул. Левадна (Первокінна)"})
    result = await submit(hass, result, {CONF_HOUSE: "12"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOUSE] == "12"


async def test_empty_address_values_are_rejected(
    hass: HomeAssistant, fake_dtek: FakeDtek
) -> None:
    """Blank address values remain invalid."""
    result = await start(hass)
    result = await submit(hass, result, {CONF_SETTLEMENT: ""})
    assert result["errors"] == {CONF_SETTLEMENT: "unknown_settlement"}
    result = await submit(hass, result, {CONF_SETTLEMENT: SETTLEMENT})
    result = await submit(hass, result, {CONF_STREET: ""})
    assert result["errors"] == {CONF_STREET: "unknown_street"}
    result = await submit(hass, result, {CONF_STREET: STREET})
    result = await submit(hass, result, {CONF_HOUSE: ""})
    assert result["errors"] == {CONF_HOUSE: "unknown_house"}


async def test_empty_house_response_allows_custom_house(
    hass: HomeAssistant, fake_dtek: FakeDtek
) -> None:
    """An empty outage response still allows a custom house number."""
    fake_dtek.houses = {}
    result = await start(hass)
    result = await submit(hass, result, {CONF_SETTLEMENT: SETTLEMENT})
    result = await submit(hass, result, {CONF_STREET: STREET})
    result = await submit(hass, result, {CONF_HOUSE: "12"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_only_polling_settings(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """Options contain no schedule or warning settings."""
    await setup_entry(hass, mock_config_entry)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert fields_of(result) == [CONF_UPDATE_INTERVAL, CONF_LANGUAGE]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 10, CONF_LANGUAGE: "en"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_UPDATE_INTERVAL: 10, CONF_LANGUAGE: "en"}


async def test_connection_errors_offer_retry(
    hass: HomeAssistant, fake_dtek: FakeDtek
) -> None:
    """Loading the settlement list can be retried."""
    fake_dtek.ajax_error = aiohttp.ClientConnectionError()
    result = await start(hass)
    assert result["step_id"] == "unreachable"
    assert result["menu_options"] == ["user"]


async def test_duplicate_address(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """The same address cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await start(hass)
    result = await submit(hass, result, {CONF_SETTLEMENT: SETTLEMENT})
    result = await submit(hass, result, {CONF_STREET: STREET})
    result = await submit(hass, result, {CONF_HOUSE: HOUSE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_forms_reach_frontend(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_dtek: FakeDtek
) -> None:
    """Address and options forms serialize through Home Assistant's API."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()

    async def post(path: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await client.post(f"/api/config/config_entries/{path}", json=data)
        assert response.status == HTTPStatus.OK, await response.text()
        await hass.async_block_till_done()
        return await response.json()

    result = await post("flow", {"handler": DOMAIN})
    flow = f"flow/{result['flow_id']}"
    result = await post(flow, {CONF_SETTLEMENT: SETTLEMENT})
    result = await post(flow, {CONF_STREET: STREET})
    result = await post(flow, {CONF_HOUSE: HOUSE})
    assert result["type"] == FlowResultType.CREATE_ENTRY.value
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await post("options/flow", {"handler": entry.entry_id})
    assert fields_of(result) == [CONF_UPDATE_INTERVAL, CONF_LANGUAGE]


async def test_reconfigure_address(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure changes the stored address."""
    await setup_entry(hass, mock_config_entry)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await submit(hass, result, {CONF_SETTLEMENT: SETTLEMENT})
    result = await submit(hass, result, {CONF_STREET: STREET})
    result = await submit(hass, result, {CONF_HOUSE: "2"})
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOUSE] == "2"
