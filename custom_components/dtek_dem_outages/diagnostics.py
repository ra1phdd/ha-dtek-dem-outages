"""Diagnostics for DTEK DEM Outages."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOUSE, CONF_STREET
from .coordinator import DtekConfigEntry

TO_REDACT = {CONF_STREET, CONF_HOUSE, "street", "house", "entry_title", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DtekConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without exposing the street or house number."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            **coordinator.diagnostics_state(),
        },
        "state": async_redact_data(
            {
                "street": coordinator.street,
                "house": coordinator.house,
                "address_ok": data.address_ok,
                "fetch_ok": data.fetch_ok,
                "connection": data.connection,
                "consecutive_failures": data.consecutive_failures,
                "external_operator": data.external_operator,
                "last_success": data.last_success.isoformat()
                if data.last_success
                else None,
                "last_dtek_success": data.last_dtek_success.isoformat()
                if data.last_dtek_success
                else None,
                "last_error": data.last_error,
                "address_outage": data.address_outage.as_dict()
                if data.address_outage
                else None,
            },
            TO_REDACT,
        ),
    }
