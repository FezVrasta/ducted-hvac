"""Ducted HVAC — zoned ducted air conditioning for Home Assistant.

Manages N zone climate entities and one central motor unit.
Each zone controls a vent switch based on temperature and mode,
and a coordinator syncs the motor's mode/temperature from all active zones.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ducted_hvac"

CONF_MOTOR = "motor"
CONF_MODES = "modes"
CONF_ZONES = "zones"
CONF_VENT = "vent"
CONF_SENSOR = "sensor"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TEMP_STEP = "temp_step"
CONF_TOLERANCE = "tolerance"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_FAN_MODES = "fan_modes"

DEFAULT_MIN_TEMP = 16.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_TEMP_STEP = 0.5
DEFAULT_TOLERANCE = 0.3
DEFAULT_FAN_MODES = ["auto", "low", "medium", "high"]

DEFAULT_MODES = [
    HVACMode.OFF,
    HVACMode.HEAT,
    HVACMode.COOL,
    HVACMode.FAN_ONLY,
    HVACMode.DRY,
]

# Modes that require temperature comparison for vent control
TEMP_CONTROLLED_MODES = {HVACMode.HEAT, HVACMode.COOL}

# Mode priority for coordinator (highest priority first, excluding OFF)
MODE_PRIORITY = [HVACMode.COOL, HVACMode.HEAT, HVACMode.DRY, HVACMode.FAN_ONLY]

VALID_MODES = [
    HVACMode.OFF,
    HVACMode.HEAT,
    HVACMode.COOL,
    HVACMode.FAN_ONLY,
    HVACMode.DRY,
]

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_UNIQUE_ID): cv.string,
        vol.Required(CONF_VENT): cv.entity_id,
        vol.Required(CONF_SENSOR): cv.entity_id,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_MOTOR): cv.entity_id,
                vol.Optional(CONF_MODES, default=DEFAULT_MODES): vol.All(
                    cv.ensure_list,
                    [lambda v: HVACMode.OFF.value if isinstance(v, bool) and not v else v],
                    [vol.In([m.value for m in VALID_MODES])],
                ),
                vol.Required(CONF_ZONES): vol.All(cv.ensure_list, [ZONE_SCHEMA]),
                vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): vol.Coerce(float),
                vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): vol.Coerce(float),
                vol.Optional(CONF_TEMP_STEP, default=DEFAULT_TEMP_STEP): vol.Coerce(float),
                vol.Optional(CONF_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(float),
                vol.Optional(CONF_MIN_CYCLE_DURATION): cv.time_period,
                vol.Optional(CONF_FAN_MODES, default=DEFAULT_FAN_MODES): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the shared DeviceInfo for all entities in this config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Ducted HVAC",
        model="Zoned Ducted System",
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML backward compatibility: auto-import YAML config as a config entry."""
    domain_config = config.get(DOMAIN)
    if domain_config is None:
        return True

    # Normalize modes: HVACMode enums → string values for JSON serialization
    raw_modes = domain_config[CONF_MODES]
    modes_str = [m.value if isinstance(m, HVACMode) else str(m) for m in raw_modes]
    if "off" not in modes_str:
        modes_str.insert(0, "off")

    # Normalize zones to JSON-safe dict format
    zones = []
    for z in domain_config[CONF_ZONES]:
        zones.append(
            {
                CONF_NAME: z[CONF_NAME],
                "unique_id": z[CONF_UNIQUE_ID],
                CONF_VENT: z[CONF_VENT],
                CONF_SENSOR: z[CONF_SENSOR],
            }
        )

    # Serialize timedelta → seconds (config entry data must be JSON-serializable)
    mcd = domain_config.get(CONF_MIN_CYCLE_DURATION)
    mcd_seconds = mcd.total_seconds() if mcd is not None else 0

    import_data = {
        CONF_NAME: "Ducted HVAC",
        CONF_MOTOR: domain_config[CONF_MOTOR],
        CONF_MODES: modes_str,
        CONF_ZONES: zones,
        CONF_MIN_TEMP: domain_config[CONF_MIN_TEMP],
        CONF_MAX_TEMP: domain_config[CONF_MAX_TEMP],
        CONF_TEMP_STEP: domain_config[CONF_TEMP_STEP],
        CONF_TOLERANCE: domain_config[CONF_TOLERANCE],
        CONF_MIN_CYCLE_DURATION: mcd_seconds,
        CONF_FAN_MODES: domain_config.get(CONF_FAN_MODES, DEFAULT_FAN_MODES),
    }

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=import_data,
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ducted_hvac from a config entry."""
    data = entry.data

    modes = [HVACMode(m) for m in data[CONF_MODES]]
    if HVACMode.OFF not in modes:
        modes.insert(0, HVACMode.OFF)

    coordinator = MotorCoordinator(hass, data[CONF_MOTOR])

    fan_modes: list[str] = data.get(CONF_FAN_MODES, [])

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": data,
        "modes": modes,
        "fan_modes": fan_modes,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class MotorCoordinator:
    """Watches all zone entities and syncs the central motor unit.

    Zones register themselves during async_added_to_hass. Whenever any zone's
    vent state or mode changes, it calls async_zone_changed() which schedules
    a debounced motor sync.
    """

    def __init__(self, hass: HomeAssistant, motor_entity_id: str) -> None:
        self._hass = hass
        self._motor_entity_id = motor_entity_id
        self._zones: list = []  # list[DuctedHVACZone], typed loosely to avoid circular import
        self._pending_sync: asyncio.Task | None = None

        # State tracked for coordinator sensor
        self._last_active_mode: str | None = None
        self._last_motor_temp: float | None = None
        self._last_fan_mode: str | None = None  # last fan mode set by any zone
        self._sensor = None  # MotorCoordinatorSensor, injected via register_sensor()

    @property
    def zones(self) -> list:
        """All registered zone entities."""
        return self._zones

    @property
    def last_active_mode(self) -> str | None:
        """Mode last commanded to the motor, or None if motor is off."""
        return self._last_active_mode

    @property
    def last_motor_temp(self) -> float | None:
        """Temperature last commanded to the motor, or None."""
        return self._last_motor_temp

    @property
    def last_fan_mode(self) -> str | None:
        """Fan mode last commanded to the motor, or None."""
        return self._last_fan_mode

    def register_zone(self, zone) -> None:
        """Register a zone. Called by each DuctedHVACZone in async_added_to_hass."""
        self._zones.append(zone)

    def register_sensor(self, sensor) -> None:
        """Register the coordinator status sensor for push notifications."""
        self._sensor = sensor

    @callback
    def async_fan_mode_changed(self, fan_mode: str) -> None:
        """Record the latest fan mode and schedule a motor sync."""
        self._last_fan_mode = fan_mode
        self.async_zone_changed()

    @callback
    def async_zone_changed(self) -> None:
        """Schedule a debounced motor sync.

        If a sync task is already pending (not yet started), skip scheduling a new one.
        The pending task will pick up the latest zone state when it runs.
        """
        if self._pending_sync is not None and not self._pending_sync.done():
            return
        self._pending_sync = self._hass.async_create_task(self._async_sync_motor())

    async def _async_sync_motor(self) -> None:
        """Determine the correct motor state from all zones and apply it."""
        # Yield to let any in-flight async_write_ha_state calls complete
        await asyncio.sleep(0)

        open_zones = [z for z in self._zones if z.vent_is_open]

        if not open_zones:
            # All vents closed — turn off the motor
            self._last_active_mode = None
            self._last_motor_temp = None
            try:
                await self._hass.services.async_call(
                    "climate",
                    "turn_off",
                    {"entity_id": self._motor_entity_id},
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to turn off motor %s", self._motor_entity_id)
            self._notify_sensor()
            return

        # Determine the winning mode by priority
        open_modes = {z.hvac_mode for z in open_zones}
        target_mode = next(
            (m for m in MODE_PRIORITY if m in open_modes),
            HVACMode.FAN_ONLY,
        )

        # Determine temperature setpoint from open zones with temp-controlled modes
        open_temps = [
            z.target_temperature
            for z in open_zones
            if z.target_temperature is not None and z.hvac_mode in TEMP_CONTROLLED_MODES
        ]

        if target_mode == HVACMode.COOL:
            target_temp = min(open_temps) if open_temps else None
        elif target_mode == HVACMode.HEAT:
            target_temp = max(open_temps) if open_temps else None
        else:
            target_temp = None

        self._last_active_mode = target_mode.value
        self._last_motor_temp = target_temp

        try:
            await self._hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": self._motor_entity_id, "hvac_mode": target_mode.value},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to set motor %s mode to %s", self._motor_entity_id, target_mode
            )
            self._notify_sensor()
            return

        if target_temp is not None:
            try:
                await self._hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": self._motor_entity_id, "temperature": target_temp},
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to set motor %s temperature to %s",
                    self._motor_entity_id,
                    target_temp,
                )

        if self._last_fan_mode is not None:
            try:
                await self._hass.services.async_call(
                    "climate",
                    "set_fan_mode",
                    {"entity_id": self._motor_entity_id, "fan_mode": self._last_fan_mode},
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Failed to set motor %s fan mode to %s",
                    self._motor_entity_id,
                    self._last_fan_mode,
                )

        self._notify_sensor()

    def _notify_sensor(self) -> None:
        """Push state update to the coordinator sensor if registered."""
        if self._sensor is not None:
            self._sensor.async_update_sensor()
