"""Sensors for DTEK DEM Outages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import format_iso
from .const import (
    ADDRESS_STATUS_NONE,
    ADDRESS_STATUSES,
    CONNECTION_STATES,
    OUTAGE_KIND_EMERGENCY,
)
from .coordinator import DtekConfigEntry, DtekCoordinator
from .entity import DtekEntity

PARALLEL_UPDATES = 0


def _address_status(coordinator: DtekCoordinator, now: datetime) -> str:
    """Return the current outage status reported for the address."""
    outage = coordinator.data.address_outage
    if outage is None:
        return ADDRESS_STATUS_NONE
    return OUTAGE_KIND_EMERGENCY


def _address_attributes(coordinator: DtekCoordinator, now: datetime) -> dict[str, Any]:
    """Return the current address outage details."""
    outage = coordinator.data.address_outage
    data = outage.as_dict() if outage else {}
    return {
        "reason": data.get("reason"),
        "start": data.get("start"),
        "end": data.get("end"),
        "active": outage is not None and outage.is_active(now),
        "external_operator": coordinator.data.external_operator,
    }


def _connection_attributes(
    coordinator: DtekCoordinator, now: datetime
) -> dict[str, Any]:
    """Return diagnostic details about the last address request."""
    data = coordinator.data
    return {
        "last_success": format_iso(data.last_success),
        "last_dtek_success": format_iso(data.last_dtek_success),
        "consecutive_failures": data.consecutive_failures,
        "update_interval_minutes": round(
            coordinator.update_interval.total_seconds() / 60, 1
        )
        if coordinator.update_interval
        else None,
        "blocked": data.blocked,
        "last_error": data.last_error,
    }


@dataclass(frozen=True, kw_only=True)
class DtekSensorDescription(SensorEntityDescription):
    """Describe a DTEK DEM Outages sensor."""

    value_fn: Any
    attributes_fn: Any = lambda coordinator, now: None
    address: bool = False


SENSORS: tuple[DtekSensorDescription, ...] = (
    DtekSensorDescription(
        key="address_status",
        translation_key="address_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(ADDRESS_STATUSES),
        value_fn=_address_status,
        attributes_fn=_address_attributes,
        address=True,
    ),
    DtekSensorDescription(
        key="restoration",
        translation_key="restoration",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, now: (
            outage.end if (outage := c.data.address_outage) else None
        ),
        attributes_fn=lambda c, now: {
            "reason": outage.reason if (outage := c.data.address_outage) else None,
        },
        address=True,
    ),
    DtekSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, now: c.data.last_success,
        attributes_fn=lambda c, now: {"last_error": c.data.last_error},
    ),
    DtekSensorDescription(
        key="connection",
        translation_key="connection",
        device_class=SensorDeviceClass.ENUM,
        options=list(CONNECTION_STATES),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, now: c.data.connection,
        attributes_fn=_connection_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DtekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up current-outage and connection sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        DtekSensor(coordinator, description)
        for description in SENSORS
        if coordinator.has_address or not description.address
    )


class DtekSensor(DtekEntity, SensorEntity):
    """A sensor computed from current address data."""

    entity_description: DtekSensorDescription
    _unrecorded_attributes = frozenset({"last_success", "last_dtek_success"})

    def __init__(
        self, coordinator: DtekCoordinator, description: DtekSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return false for address data that is not current."""
        return super().available and (
            not self.entity_description.address or self.coordinator.data.address_ok
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor state."""
        return self.entity_description.value_fn(self.coordinator, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return sensor attributes."""
        return self.entity_description.attributes_fn(self.coordinator, dt_util.utcnow())
