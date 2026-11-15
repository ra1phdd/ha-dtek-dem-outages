"""Config flow for DTEK DEM Outages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .api import DtekError, DtekNotFoundError, async_get_client
from .const import (
    CONF_HOUSE,
    CONF_LANGUAGE,
    CONF_SETTLEMENT,
    CONF_STREET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_LANGUAGE,
    DEFAULT_OPTIONS,
    DOMAIN,
    LANGUAGES,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
_UK_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_SETTLEMENT_PREFIX = re.compile(r"^(м\.|с\.|смт|с-ще|селище|хут\.|х\.)\s*")
_STREET_PREFIX = re.compile(
    r"^(вулиця|вул\.?|провулок|пров\.|проспект|просп\.|бульвар|бульв\.|бул\.|"
    r"площа|пл\.|узвіз|шосе|туп\.)\s*",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Casefold and collapse whitespace."""
    return " ".join(text.split()).casefold()


def place_name(settlement: str) -> str:
    """Return a settlement without its type prefix."""
    return _SETTLEMENT_PREFIX.sub("", _normalize(settlement))


def _street_name(street: str) -> str:
    """Return a street without its type prefix."""
    return _STREET_PREFIX.sub("", _normalize(street))


def _collation_key(text: str) -> tuple[int, ...]:
    return tuple(
        1000 + _UK_ALPHABET.index(char) if char in _UK_ALPHABET else ord(char)
        for char in text.casefold()
    )


def settlement_sort_key(name: str) -> tuple[tuple[int, ...], str]:
    """Sort settlements by name."""
    return _collation_key(_SETTLEMENT_PREFIX.sub("", name)), name


def house_sort_key(number: str) -> list[Any]:
    """Sort house numbers naturally."""
    return [
        int(part) if part.isdigit() else _collation_key(part)
        for part in re.split(r"(\d+)", number)
    ]


def match_choice(value: Any, choices: Iterable[str]) -> str | None:
    """Match a choice ignoring case and whitespace."""
    if not isinstance(value, str):
        return None
    wanted = _normalize(value)
    return next((choice for choice in choices if _normalize(choice) == wanted), None)


def _custom_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def match_settlement(value: Any, streets: Mapping[str, list[str]]) -> str | None:
    """Match a settlement, allowing its type prefix to be omitted."""
    if settlement := match_choice(value, streets):
        return settlement
    if not isinstance(value, str):
        return None
    wanted = place_name(value)
    candidates = [name for name in streets if place_name(name) == wanted]
    return max(candidates, key=lambda name: len(streets[name]), default=None)


def street_choices(
    streets: Mapping[str, list[str]], settlement: str
) -> dict[str, tuple[str, str]]:
    """Return street labels and their canonical settlement/street values."""
    choices = {street: (settlement, street) for street in streets.get(settlement, [])}
    name = place_name(settlement)
    for other, names in streets.items():
        if other != settlement and place_name(other) == name:
            for street in names:
                choices[f"{street} ({other})"] = (other, street)
    return choices


def match_street(value: Any, choices: Mapping[str, tuple[str, str]]) -> str | None:
    """Match a street label or an unprefixed street name."""
    if label := match_choice(value, choices):
        return label
    if not isinstance(value, str) or not (wanted := _street_name(value)):
        return None
    candidates = [
        label
        for label, (_, street) in choices.items()
        if _street_name(street) == wanted
    ]
    return candidates[0] if len(candidates) == 1 else None


def build_unique_id(settlement: str, street: str, house: str) -> str:
    """Return the unique ID of an address."""
    return f"{settlement}|{street}|{house}"


def build_title(settlement: str, street: str, house: str) -> str:
    """Return an address title."""
    if street.casefold() == "без вулиці":
        return f"{settlement}, {house}"
    return f"{settlement}, {street}, {house}"


def _select(options: list[str]) -> SelectSelector:
    """Build a searchable selector that accepts custom text."""
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=True,
            sort=False,
        )
    )


def _language_select() -> SelectSelector:
    """Build the entity-language selector."""
    return SelectSelector(
        SelectSelectorConfig(options=list(LANGUAGES), mode=SelectSelectorMode.DROPDOWN)
    )


def _options_schema(options: Mapping[str, Any]) -> dict[vol.Marker, Any]:
    values = {**DEFAULT_OPTIONS, **options}
    return {
        vol.Required(
            CONF_UPDATE_INTERVAL, default=values[CONF_UPDATE_INTERVAL]
        ): vol.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=MIN_UPDATE_INTERVAL,
                    max=MAX_UPDATE_INTERVAL,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Coerce(int),
        ),
        vol.Required(CONF_LANGUAGE, default=values[CONF_LANGUAGE]): vol.All(
            _language_select(), vol.In(LANGUAGES)
        ),
    }


class DtekDemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one settlement, street and house."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize an empty address flow."""
        self._streets: dict[str, list[str]] | None = None
        self._settlement: str | None = None
        self._street: str | None = None
        self._houses: dict[str, Any] | None = None
        self._house: str | None = None
        self._name: str | None = None
        self._language = DEFAULT_LANGUAGE

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DtekDemOptionsFlow:
        """Return the options flow."""
        return DtekDemOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a settlement."""
        errors: dict[str, str] = {}
        if self._streets is None:
            try:
                self._streets = await (
                    await async_get_client(self.hass)
                ).async_get_streets()
            except DtekError as err:
                _LOGGER.debug("Cannot load settlements from DTEK: %s", err)
                return await self.async_step_unreachable()
        if user_input is not None:
            settlement = match_settlement(
                user_input.get(CONF_SETTLEMENT), self._streets
            ) or _custom_value(user_input.get(CONF_SETTLEMENT))
            if settlement is None:
                errors[CONF_SETTLEMENT] = "unknown_settlement"
            else:
                if settlement != self._settlement:
                    self._street = None
                    self._houses = None
                self._settlement = settlement
                return await self.async_step_street()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SETTLEMENT,
                        description={"suggested_value": self._settlement},
                    ): _select(sorted(self._streets, key=settlement_sort_key))
                }
            ),
            errors=errors,
        )

    async def async_step_unreachable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer retry when DTEK cannot provide the settlement list."""
        return self.async_show_menu(step_id="unreachable", menu_options=["user"])

    async def async_step_street(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a street."""
        choices = street_choices(self._streets or {}, self._settlement or "")
        errors: dict[str, str] = {}
        if user_input is not None:
            label = match_street(user_input.get(CONF_STREET), choices)
            street = _custom_value(user_input.get(CONF_STREET))
            if label is not None:
                self._settlement, street = choices[label]
            if street is None:
                errors[CONF_STREET] = "unknown_street"
            else:
                self._street = street
                self._houses = None
                return await self.async_step_house()
        return self.async_show_form(
            step_id="street",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_STREET, description={"suggested_value": self._street}
                    ): _select(sorted(choices, key=_collation_key))
                }
            ),
            errors=errors,
            description_placeholders={"settlement": self._settlement or ""},
        )

    async def async_step_house(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a house number."""
        errors: dict[str, str] = {}
        if self._houses is None:
            try:
                street = await (await async_get_client(self.hass)).async_get_street(
                    self._settlement or "", self._street or ""
                )
            except DtekNotFoundError:
                self._houses = {}
            except DtekError:
                errors["base"] = "cannot_connect"
            else:
                self._houses = street.houses
        if self._houses is None:
            return self.async_show_form(
                step_id="house",
                data_schema=vol.Schema({}),
                errors=errors,
            )
        if user_input is not None:
            house = match_choice(user_input.get(CONF_HOUSE), self._houses)
            house = house or _custom_value(user_input.get(CONF_HOUSE))
            if house is None:
                errors[CONF_HOUSE] = "unknown_house"
            else:
                self._house = house
                self._name = (user_input.get(CONF_NAME) or "").strip() or None
                if user_input.get(CONF_LANGUAGE) in LANGUAGES:
                    self._language = user_input[CONF_LANGUAGE]
                return await self._async_save()
        schema: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_HOUSE, description={"suggested_value": self._house}
            ): _select(sorted(self._houses, key=house_sort_key)),
            vol.Optional(
                CONF_NAME, description={"suggested_value": self._name}
            ): TextSelector(),
        }
        if self.source != SOURCE_RECONFIGURE:
            schema[vol.Required(CONF_LANGUAGE, default=self._language)] = (
                _language_select()
            )
        return self.async_show_form(
            step_id="house",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "settlement": self._settlement or "",
                "street": self._street or "",
            },
        )

    async def _async_save(self) -> ConfigFlowResult:
        """Create or update the address entry."""
        data = {
            CONF_SETTLEMENT: self._settlement or "",
            CONF_STREET: self._street or "",
            CONF_HOUSE: self._house or "",
        }
        unique_id = build_unique_id(
            data[CONF_SETTLEMENT], data[CONF_STREET], data[CONF_HOUSE]
        )
        reconfigured = (
            self._get_reconfigure_entry() if self.source == SOURCE_RECONFIGURE else None
        )
        if any(
            other.unique_id == unique_id
            and (reconfigured is None or other.entry_id != reconfigured.entry_id)
            for other in self._async_current_entries(include_ignore=False)
        ):
            return self.async_abort(reason="already_configured")
        if reconfigured is None:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._name or build_title(**data),
                data=data,
                options={**DEFAULT_OPTIONS, CONF_LANGUAGE: self._language},
            )
        title = self._name or reconfigured.title
        if not self._name and reconfigured.title == build_title(**reconfigured.data):
            title = build_title(**data)
        return self.async_update_reload_and_abort(
            reconfigured, unique_id=unique_id, title=title, data=data
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start address reconfiguration."""
        entry = self._get_reconfigure_entry()
        self._settlement = entry.data.get(CONF_SETTLEMENT)
        self._street = entry.data.get(CONF_STREET)
        self._house = entry.data.get(CONF_HOUSE)
        return await self.async_step_user(user_input)


class DtekDemOptionsFlow(OptionsFlowWithReload):
    """Manage polling interval and entity language."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the options."""
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_UPDATE_INTERVAL: int(
                        user_input.get(
                            CONF_UPDATE_INTERVAL,
                            options.get(
                                CONF_UPDATE_INTERVAL,
                                DEFAULT_OPTIONS[CONF_UPDATE_INTERVAL],
                            ),
                        )
                    ),
                    CONF_LANGUAGE: user_input.get(
                        CONF_LANGUAGE, options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
                    ),
                }
            )
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(_options_schema(options))
        )
