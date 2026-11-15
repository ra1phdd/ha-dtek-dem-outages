"""Tests for the refresh service and diagnostics."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dtek_dem_outages.const import (
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
    REFRESH_COOLDOWN,
)
from custom_components.dtek_dem_outages.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import ENTRY_ID, FakeDtek, setup_entry


async def test_refresh(
    hass: HomeAssistant,
    fake_dtek: FakeDtek,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """Refresh fetches current address state and reports failures."""
    await setup_entry(hass, mock_config_entry)
    count = len(fake_dtek.forms)
    frozen_now.tick(timedelta(seconds=REFRESH_COOLDOWN))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert len(fake_dtek.forms) == count + 1

    fake_dtek.ajax_error = aiohttp.ClientConnectionError()
    frozen_now.tick(timedelta(seconds=REFRESH_COOLDOWN))
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)


async def test_refresh_cooldown(
    hass: HomeAssistant,
    fake_dtek: FakeDtek,
    mock_config_entry: MockConfigEntry,
    frozen_now: FrozenDateTimeFactory,
) -> None:
    """Calls within a minute do not ask DTEK again."""
    await setup_entry(hass, mock_config_entry)
    count = len(fake_dtek.forms)
    for _ in range(5):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert len(fake_dtek.forms) == count
    frozen_now.tick(timedelta(seconds=REFRESH_COOLDOWN))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert len(fake_dtek.forms) == count + 1


async def test_service_errors(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """Unknown, unloaded and missing entries are reported."""
    await setup_entry(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh",
            {ATTR_CONFIG_ENTRY_ID: "missing"},
            blocking=True,
        )
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "refresh",
            {ATTR_CONFIG_ENTRY_ID: ENTRY_ID},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)


async def test_diagnostics_redact_address(
    hass: HomeAssistant, fake_dtek: FakeDtek, mock_config_entry: MockConfigEntry
) -> None:
    """The street and house are redacted from diagnostics."""
    await setup_entry(hass, mock_config_entry)
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result["entry"]["data"]["street"] == "**REDACTED**"
    assert result["entry"]["data"]["house"] == "**REDACTED**"
    assert result["entry"]["title"] == "**REDACTED**"
    assert result["state"]["street"] == "**REDACTED**"
    assert result["state"]["house"] == "**REDACTED**"
