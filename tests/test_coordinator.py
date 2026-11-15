"""Tests for current emergency outage updates."""

from __future__ import annotations

import aiohttp
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.dtek_dem_outages.const import DOMAIN, EVENT_BUS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import HOUSE, FakeDtek, make_house, setup_entry


async def test_setup_reports_power_without_schedule(
    hass: HomeAssistant,
    fake_dtek: FakeDtek,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """A successful empty response means power is available."""
    await setup_entry(hass, mock_config_entry)
    data = mock_config_entry.runtime_data.data
    assert data.address_ok
    assert data.address_outage is None
    assert data.connection == "ok"
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert (
        "address_outage"
        in hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"]
    )


async def test_emergency_outage_event_is_address_only(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """A current emergency outage creates one address event."""
    fake_dtek.houses[HOUSE] = make_house(
        reason="Аварійні ремонтні роботи",
        start="09:48 18.10.2026",
        end="13:48 18.10.2026",
    )
    await setup_entry(hass, mock_config_entry)
    events = async_capture_events(hass, EVENT_BUS)
    fake_dtek.houses = {}
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert [event.data["type"] for event in events] == ["address_outage"]
    assert events[0].data["status"] == "cleared"


async def test_result_false_means_power(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """An omitted DTEK address is treated as a successful no-outage result."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "settlement": "м. Слов'янськ",
            "street": "вул. Неіснуюча",
            "house": "12",
        },
    )
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.runtime_data.data.address_ok
    assert mock_config_entry.runtime_data.data.address_outage is None
    assert mock_config_entry.runtime_data.data.connection == "ok"


async def test_transport_error_makes_address_unavailable(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """A transport failure is not reported as power being available."""
    await setup_entry(hass, mock_config_entry)
    fake_dtek.ajax_error = aiohttp.ClientConnectionError()
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    data = mock_config_entry.runtime_data.data
    assert not data.address_ok
    assert not data.fetch_ok
    assert data.connection == "offline"


async def test_first_transport_error_retries_setup(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """An entry without a baseline cannot start without DTEK."""
    fake_dtek.ajax_error = aiohttp.ClientConnectionError()
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_refresh_only_current_data(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """Refresh sends no schedule version or mirror request."""
    await setup_entry(hass, mock_config_entry)
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert all("updateFact" not in form for form in fake_dtek.forms)
