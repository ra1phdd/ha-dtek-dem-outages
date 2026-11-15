# CLAUDE.md

Інструкції для роботи в цьому репозиторії.

## Правила

- Не робити `git commit`, `git push`, теги, релізи або PR без прямого прохання користувача.
- PATCH-версія піднімається при кожній зміні інтеграції. Спершу перевіряй незакомічені зміни в `manifest.json`; усі зміни поточної незакоміченої версії залишаються в ній.
- Версія живе лише в `custom_components/dtek_dem_outages/manifest.json`. Разом із нею має бути секція тієї ж версії в `CHANGELOG.md`.
- MINOR/MAJOR піднімати лише на прохання користувача.

## Проєкт

**DTEK DEM Outages** — кастомна інтеграція Home Assistant для поточних аварійних відключень ДТЕК Донецькі електромережі: <https://www.dtek-dem.com.ua/ua/shutdowns>.

Домен — `dtek_dem_outages`; мінімальна HA — `2025.8.0`; зовнішніх requirements немає. Інтеграція отримує населений пункт, вулицю та номер будинку. Вона не підтримує і не створює погодинні/тижневі графіки, черги, календарі графіків, попередження до початку/кінця графікових відключень, дзеркало графіків або `get_schedule`.

У `getHomeNum` непорожній `sub_type` аварійного характеру означає поточне аварійне відключення. Відсутність запису або `result: false` — успішний стан «світло є». Транспортна/API-помилка — дані недоступні, не «світло є».

## Структура

```
custom_components/dtek_dem_outages/
  __init__.py        setup/unload/remove, service registration
  api.py             DTEK ajax client, CSRF, address lookup, emergency parser
  coordinator.py     DataUpdateCoordinator, Store, outage events, retry backoff
  config_flow.py     settlement → street → house, custom values, options
  entity.py          shared device entity
  sensor.py          address status, restoration, last update, connection
  binary_sensor.py   current emergency address outage
  event.py           current address outage event entity
  services.py        refresh service
  diagnostics.py     diagnostics with street and house redacted
  const.py           shared constants
  strings.json, translations/*, services.yaml, icons.json, manifest.json
  brand/             integration icons
tests/               pytest + pytest-homeassistant-custom-component
.github/workflows/   tests, validation and release workflows
```

## API та стан

- Сторінка може бути захищена Incapsula, але її `POST /ua/ajax` використовується без обходу захисту, з чесним User-Agent і Yii2 CSRF cookie/header.
- Основні методи API: `getStreets` та `getHomeNum(city, street)`; до `getHomeNum` не додається `updateFact`.
- Mirror не використовується.
- Перший запуск мовчки записує базовий стан; зміни поточного аварійного відключення надсилаються на `dtek_dem_outages_event` та event-сутність як `address_outage`.
- Store містить лише адресу, поточне аварійне відключення, ознаку доступності адреси, оператора та час останніх успішних відповідей.
- Після помилок інтервал опитування збільшується з jitter і повертається до базового після успіху.

## Команди

```bash
pip install -r requirements_test.txt
ruff check .
ruff format --check .
pytest -q
```

CI перевіряє Python 3.14 і мінімальний HA 2025.8.0. Не використовувати API HA новіші за мінімальну версію.

## Конвенції

- Код, коментарі, docstrings і logs — англійською; `from __future__ import annotations` у кожному модулі.
- Спільні константи — у `const.py` з `Final`.
- `strings.json` і `translations/en.json` ідентичні; `uk.json` і `ru.json` мають той самий набір ключів.
- Нова поведінка має тестуватися через `FakeDtek` у `tests/conftest.py`.
- Не зберігати credentials, cookies, токени або API-ключі.
