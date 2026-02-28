"""Config flow and options flow for Ducted HVAC."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import (
    CONF_FAN_MODES,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_TEMP,
    CONF_MODES,
    CONF_MOTOR,
    CONF_NAME,
    CONF_SENSOR,
    CONF_TEMP_STEP,
    CONF_TOLERANCE,
    CONF_VENT,
    CONF_ZONES,
    DEFAULT_FAN_MODES,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_TEMP_STEP,
    DEFAULT_TOLERANCE,
    DOMAIN,
    VALID_MODES,
)

# String value of "off" excluded from mode picker (it's always included implicitly)
_SELECTABLE_MODES = [m.value for m in VALID_MODES if m != HVACMode.OFF]
_MODE_LABELS = {
    "heat": "Heat",
    "cool": "Cool",
    "fan_only": "Fan only",
    "dry": "Dry",
}

_SELECTABLE_FAN_MODES = ["auto", "low", "medium", "high", "turbo"]
_FAN_MODE_LABELS = {
    "auto": "Auto",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "turbo": "Turbo",
}


def _slug(name: str) -> str:
    """Convert a display name to a slug suitable for use as unique_id."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _global_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Ducted HVAC")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(CONF_MOTOR, default=defaults.get(CONF_MOTOR, "")): EntitySelector(
                EntitySelectorConfig(domain="climate")
            ),
            vol.Required(
                CONF_MODES,
                default=defaults.get(CONF_MODES, _SELECTABLE_MODES),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": m, "label": _MODE_LABELS[m]} for m in _SELECTABLE_MODES],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_FAN_MODES,
                default=defaults.get(CONF_FAN_MODES, DEFAULT_FAN_MODES),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": m, "label": _FAN_MODE_LABELS[m]} for m in _SELECTABLE_FAN_MODES],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_MIN_TEMP, default=defaults.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=40, step=0.5, mode=NumberSelectorMode.BOX, unit_of_measurement="°"
                )
            ),
            vol.Optional(
                CONF_MAX_TEMP, default=defaults.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=50, step=0.5, mode=NumberSelectorMode.BOX, unit_of_measurement="°"
                )
            ),
            vol.Optional(
                CONF_TEMP_STEP, default=defaults.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.1, max=5.0, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="°"
                )
            ),
            vol.Optional(
                CONF_TOLERANCE, default=defaults.get(CONF_TOLERANCE, DEFAULT_TOLERANCE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.1,
                    max=5.0,
                    step=0.1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            ),
            vol.Optional(
                CONF_MIN_CYCLE_DURATION,
                default=defaults.get(CONF_MIN_CYCLE_DURATION, 0),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=3600,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
    )


def _zone_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(CONF_VENT, default=defaults.get(CONF_VENT, "")): EntitySelector(
                EntitySelectorConfig(domain="switch")
            ),
            vol.Required(CONF_SENSOR, default=defaults.get(CONF_SENSOR, "")): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
        }
    )


def _build_zone_entry(user_input: dict[str, Any]) -> dict[str, Any]:
    name = user_input[CONF_NAME].strip()
    return {
        CONF_NAME: name,
        "unique_id": _slug(name),
        CONF_VENT: user_input[CONF_VENT],
        CONF_SENSOR: user_input[CONF_SENSOR],
    }


def _validate_global(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if user_input[CONF_MIN_TEMP] >= user_input[CONF_MAX_TEMP]:
        errors["base"] = "temp_range_invalid"
    if not user_input.get(CONF_MODES):
        errors[CONF_MODES] = "no_modes_selected"
    return errors


class DuctedHVACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for Ducted HVAC.

    Step sequence:
      user (global settings) → zone (first zone) →
      add_another (menu: add zone / finish) → [loop back to zone] →
      finish (create entry)
    """

    VERSION = 1

    def __init__(self) -> None:
        self._global: dict[str, Any] = {}
        self._zones: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Step 1: global settings
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_global(user_input)
            if not errors:
                modes = list(user_input[CONF_MODES])
                if "off" not in modes:
                    modes.insert(0, "off")
                self._global = {**user_input, CONF_MODES: modes}
                self._zones = []
                return await self.async_step_zone()

        return self.async_show_form(
            step_id="user",
            data_schema=_global_schema(self._global),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2+: zone configuration
    # ------------------------------------------------------------------

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            if not name:
                errors[CONF_NAME] = "zone_name_required"
            else:
                new_uid = _slug(name)
                existing_uids = [_slug(z[CONF_NAME]) for z in self._zones]
                if new_uid in existing_uids:
                    errors[CONF_NAME] = "zone_name_duplicate"

            if not errors:
                self._zones.append(_build_zone_entry(user_input))
                return await self.async_step_add_another()

        return self.async_show_form(
            step_id="zone",
            data_schema=_zone_schema({}),
            errors=errors,
            description_placeholders={"zone_number": str(len(self._zones) + 1)},
        )

    # ------------------------------------------------------------------
    # Step 3: add another zone?
    # ------------------------------------------------------------------

    async def async_step_add_another(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Menu: add another zone or finish."""
        return self.async_show_menu(
            step_id="add_another",
            menu_options=["zone", "finish"],
            description_placeholders={"zones_summary": self._build_zones_summary()},
        )

    # ------------------------------------------------------------------
    # Step 4: create entry
    # ------------------------------------------------------------------

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if not self._zones:
            return await self.async_step_zone()

        title = self._global.get(CONF_NAME, "Ducted HVAC")
        await self.async_set_unique_id(f"{DOMAIN}_{_slug(title)}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title,
            data={**self._global, CONF_ZONES: self._zones},
        )

    # ------------------------------------------------------------------
    # YAML import step
    # ------------------------------------------------------------------

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle YAML-based auto-import triggered by async_setup."""
        if not user_input:
            return self.async_abort(reason="no_data")

        motor = user_input.get(CONF_MOTOR, "")
        unique_id = f"{DOMAIN}_yaml_{_slug(motor)}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=user_input, reload_on_update=True)

        title = user_input.get(CONF_NAME, "Ducted HVAC")
        return self.async_create_entry(title=title, data=user_input)

    # ------------------------------------------------------------------
    # Options flow hook
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "DuctedHVACOptionsFlow":
        return DuctedHVACOptionsFlow(config_entry)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_zones_summary(self) -> str:
        if not self._zones:
            return "none"
        return ", ".join(z[CONF_NAME] for z in self._zones)


class DuctedHVACOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit global settings and manage zones.

    Menu:
      global_settings  → edit motor, modes, temperature bounds, etc.
      add_zone         → add a new zone
      delete_zone      → remove an existing zone
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    # ------------------------------------------------------------------
    # Entry point — show menu
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["global_settings", "add_zone", "delete_zone"],
        )

    # ------------------------------------------------------------------
    # Edit global settings
    # ------------------------------------------------------------------

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self._entry.data

        if user_input is not None:
            errors = _validate_global(user_input)
            if not errors:
                modes = list(user_input[CONF_MODES])
                if "off" not in modes:
                    modes.insert(0, "off")
                updated = {**current, **user_input, CONF_MODES: modes}
                self.hass.config_entries.async_update_entry(self._entry, data=updated)
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry.entry_id)
                )
                return self.async_create_entry(title="", data={})

        # Pre-fill with current values; exclude "off" from the mode picker
        current_modes = [m for m in current.get(CONF_MODES, _SELECTABLE_MODES) if m != "off"]
        defaults = {
            CONF_NAME: current.get(CONF_NAME, ""),
            CONF_MOTOR: current.get(CONF_MOTOR, ""),
            CONF_MODES: current_modes,
            CONF_FAN_MODES: current.get(CONF_FAN_MODES, DEFAULT_FAN_MODES),
            CONF_MIN_TEMP: current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP),
            CONF_MAX_TEMP: current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP),
            CONF_TEMP_STEP: current.get(CONF_TEMP_STEP, DEFAULT_TEMP_STEP),
            CONF_TOLERANCE: current.get(CONF_TOLERANCE, DEFAULT_TOLERANCE),
            CONF_MIN_CYCLE_DURATION: current.get(CONF_MIN_CYCLE_DURATION, 0),
        }
        return self.async_show_form(
            step_id="global_settings",
            data_schema=_global_schema(defaults),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Add a zone
    # ------------------------------------------------------------------

    async def async_step_add_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self._entry.data

        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            if not name:
                errors[CONF_NAME] = "zone_name_required"
            else:
                existing_uids = [z["unique_id"] for z in current.get(CONF_ZONES, [])]
                if _slug(name) in existing_uids:
                    errors[CONF_NAME] = "zone_name_duplicate"

            if not errors:
                zones = list(current.get(CONF_ZONES, []))
                zones.append(_build_zone_entry(user_input))
                updated = {**current, CONF_ZONES: zones}
                self.hass.config_entries.async_update_entry(self._entry, data=updated)
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry.entry_id)
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="add_zone",
            data_schema=_zone_schema({}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Delete a zone
    # ------------------------------------------------------------------

    async def async_step_delete_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        current = self._entry.data
        zones: list[dict] = list(current.get(CONF_ZONES, []))

        if not zones:
            return self.async_abort(reason="no_zones_to_delete")

        errors: dict[str, str] = {}

        if user_input is not None:
            uid_to_delete = user_input.get("zone_uid")
            updated_zones = [z for z in zones if z["unique_id"] != uid_to_delete]
            if len(updated_zones) == len(zones):
                errors["base"] = "zone_not_found"
            else:
                updated = {**current, CONF_ZONES: updated_zones}
                self.hass.config_entries.async_update_entry(self._entry, data=updated)
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry.entry_id)
                )
                return self.async_create_entry(title="", data={})

        zone_options = [
            {"value": z["unique_id"], "label": z[CONF_NAME]}
            for z in zones
        ]

        return self.async_show_form(
            step_id="delete_zone",
            data_schema=vol.Schema(
                {
                    vol.Required("zone_uid"): SelectSelector(
                        SelectSelectorConfig(
                            options=zone_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )
