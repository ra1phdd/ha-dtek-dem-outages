<img src="https://raw.githubusercontent.com/ra1phdd/ha-dtek-dem-outages/main/custom_components/dtek_dem_outages/brand/icon.png" alt="DTEK DEM Outages" width="96" align="right">

# DTEK DEM Outages

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz/)
[![Tests](https://github.com/ra1phdd/ha-dtek-dem-outages/actions/workflows/tests.yml/badge.svg)](https://github.com/ra1phdd/ha-dtek-dem-outages/actions/workflows/tests.yml)

[Українська](../README.md) | **English**

Home Assistant integration for current emergency outages from [DTEK Donetsk Electric Networks](https://www.dtek-dem.com.ua/ua/shutdowns). Setup asks for a settlement, street and house number.

This integration intentionally handles only emergency outages reported for the address. It has no hourly or weekly schedules, groups, schedule calendars, schedule mirror, or warnings before scheduled outage start/end.

## Setup

Install **DTEK DEM Outages** through HACS or copy `custom_components/dtek_dem_outages` into your Home Assistant `custom_components` directory, then restart Home Assistant.

Open **Settings → Devices & services → Add integration → DTEK DEM Outages** and enter a settlement, street and house number. DTEK may omit address suggestions when there is no current outage; non-empty values can be entered manually. A successful API response without an address record means power is available.

## Entities

- `binary_sensor.…_emergency_outage` — on while DTEK reports an active emergency outage.
- `sensor.…_address_status` — `none` or `emergency`, with reason and outage times.
- `sensor.…_estimated_restoration` — expected restoration time when DTEK provides one.
- `sensor.…_last_update` — last successful response.
- `sensor.…_dtek_connection` — `ok` or `offline` and request diagnostics.
- `event.…_emergency_outage_events` — `address_outage` events with `new`, `updated` or `cleared` status.

If DTEK is temporarily unavailable, address entities become unavailable. This is different from a successful no-outage response, which means power is available.

## Service

`dtek_dem_outages.refresh` refreshes the current address state. `config_entry_id` is optional; without it, all loaded entries are refreshed.

## Data source and privacy

Data comes directly from `POST https://www.dtek-dem.com.ua/ua/ajax`, using the same CSRF mechanism as the DTEK page. The integration does not bypass anti-bot protection and does not use a schedule mirror. Street and house number are redacted from diagnostics.

> Data provided by [DTEK DEM](https://www.dtek-dem.com.ua/ua/shutdowns). This project is not affiliated with DTEK.
