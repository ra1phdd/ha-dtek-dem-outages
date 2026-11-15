<img src="https://raw.githubusercontent.com/ra1phdd/ha-dtek-dem-outages/main/custom_components/dtek_dem_outages/brand/icon.png" alt="DTEK DEM Outages" width="96" align="right">

# DTEK DEM Outages

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz/)
[![Tests](https://github.com/ra1phdd/ha-dtek-dem-outages/actions/workflows/tests.yml/badge.svg)](https://github.com/ra1phdd/ha-dtek-dem-outages/actions/workflows/tests.yml)

**Українська** | [English](https://github.com/ra1phdd/ha-dtek-dem-outages/blob/main/doc/README_en.md)

Інтеграція Home Assistant для поточних аварійних відключень [ДТЕК Донецькі електромережі](https://www.dtek-dem.com.ua/ua/shutdowns). Під час налаштування потрібно вказати населений пункт, вулицю та номер будинку.

Інтеграція навмисно працює лише з аварійними відключеннями за адресою. Погодинних і тижневих графіків, черг, календарів графіків, дзеркала графіків та попереджень до початку або кінця графікового відключення немає.

## Встановлення

### HACS

Знайдіть **DTEK DEM Outages** у HACS та встановіть інтеграцію. Після цього перезапустіть Home Assistant.

### Вручну

```bash
cd /config
git clone https://github.com/ra1phdd/ha-dtek-dem-outages.git
mkdir -p custom_components
cp -r ha-dtek-dem-outages/custom_components/dtek_dem_outages custom_components/
```

Перезапустіть Home Assistant після копіювання.

## Налаштування

**Налаштування → Пристрої та служби → Додати інтеграцію → DTEK DEM Outages**.

1. Виберіть або введіть населений пункт.
2. Виберіть або введіть вулицю.
3. Виберіть або введіть номер будинку.
4. За потреби задайте назву запису та мову назв сутностей.

Якщо поточне відключення відсутнє, ДТЕК може не повернути населений пункт, вулицю або будинок у списку. Непорожні значення можна ввести вручну. Відповідь API без запису будинку означає, що світло є.

## Сутності

- `binary_sensor.…_emergency_outage` — `on`, поки ДТЕК повідомляє активне аварійне відключення.
- `sensor.…_address_status` — `none` або `emergency`; містить причину, початок, кінець та ознаку активності.
- `sensor.…_estimated_restoration` — час очікуваного відновлення, якщо ДТЕК його повернув.
- `sensor.…_last_update` — час останньої успішної відповіді.
- `sensor.…_dtek_connection` — `ok` або `offline`; містить технічний стан останнього запиту.
- `event.…_emergency_outage_events` — події `address_outage` зі статусом `new`, `updated` або `cleared`.

Якщо DTEK тимчасово недоступний, адресні сутності стають недоступними. Це відрізняється від нормальної відповіді без аварії, яка означає «світло є».

## Сервіс

`dtek_dem_outages.refresh` повторно запитує поточний стан. Поле `config_entry_id` необов'язкове; без нього оновлюються всі завантажені записи.

## Дані та конфіденційність

Дані надходять безпосередньо з `POST https://www.dtek-dem.com.ua/ua/ajax`, який використовує сайт ДТЕК. Інтеграція не обходить антибот-захист і не використовує стороннє дзеркало. У діагностиці вулиця та номер будинку приховуються.

> Data provided by [DTEK DEM](https://www.dtek-dem.com.ua/ua/shutdowns). Проєкт не пов'язаний з ДТЕК.
