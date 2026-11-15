"""Tests for the current-outage entities."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.dtek_dem_outages.const import ATTRIBUTION, DOMAIN, EVENT_BUS
from homeassistant.const import ATTR_ATTRIBUTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import (
    ENTRY_ID,
    HOUSE,
    TODAY,
    FakeDtek,
    get_entity_id,
    kyiv,
    make_house,
    setup_entry,
)


def state(hass: HomeAssistant, platform: str, key: str):
    """Return an entity state from the test entry."""
    entity_id = get_entity_id(hass, platform, key)
    return hass.states.get(entity_id)


async def test_only_current_outage_entities_are_created(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """The integration exposes address status, diagnostics and no schedules."""
    await setup_entry(hass, mock_config_entry)

    assert state(hass, "sensor", "address_status").state == "none"
    assert state(hass, "sensor", "restoration").state == "unknown"
    assert state(hass, "sensor", "last_update").state is not None
    assert state(hass, "sensor", "connection").state == "ok"
    assert state(hass, "binary_sensor", "address_outage").state == "off"
    assert state(hass, "event", "events").attributes["event_types"] == [
        "address_outage"
    ]

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("calendar", DOMAIN, f"{ENTRY_ID}_outages") is None
    )
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{ENTRY_ID}_next_outage")
        is None
    )
    assert (
        registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{ENTRY_ID}_scheduled_outage"
        )
        is None
    )


async def test_emergency_outage_is_reported(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """An emergency response updates the address entities."""
    fake_dtek.houses[HOUSE] = make_house(
        reason="Аварійні ремонтні роботи",
        start="09:50 18.10.2026",
        end="13:00 18.10.2026",
    )
    await setup_entry(hass, mock_config_entry)

    status = state(hass, "sensor", "address_status")
    assert status.state == "emergency"
    assert status.attributes["reason"] == "Аварійні ремонтні роботи"
    assert status.attributes["active"] is True
    restoration = state(hass, "sensor", "restoration")
    assert dt_util.parse_datetime(restoration.state) == kyiv(TODAY, "13:00")
    assert state(hass, "binary_sensor", "address_outage").state == "on"
    assert state(hass, "binary_sensor", "address_outage").attributes[ATTR_ATTRIBUTION]
    assert status.attributes[ATTR_ATTRIBUTION] == ATTRIBUTION


async def test_address_outage_event_only(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """Only the address outage event is exposed."""
    await setup_entry(hass, mock_config_entry)
    events = async_capture_events(hass, EVENT_BUS)
    fake_dtek.houses[HOUSE] = make_house(
        reason="Аварійні ремонтні роботи",
        start="09:50 18.10.2026",
        end="13:00 18.10.2026",
    )
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert [event.data["type"] for event in events] == ["address_outage"]


async def test_connection_diagnostics(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """The connection sensor reports polling failures without schedule fields."""
    await setup_entry(hass, mock_config_entry)
    fake_dtek.ajax_error = TimeoutError()
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    connection = state(hass, "sensor", "connection")
    assert connection.state == "offline"
    assert connection.attributes["consecutive_failures"] == 1
    assert "daily_schedule_shown" not in connection.attributes
    assert "weekly_schedule_shown" not in connection.attributes
