"""Climate platform for ducted_hvac — one entity per zone."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import (
    CONF_FAN_MODES,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_TEMP,
    CONF_MODES,
    CONF_SENSOR,
    CONF_TEMP_STEP,
    CONF_TOLERANCE,
    CONF_UNIQUE_ID,
    CONF_VENT,
    CONF_ZONES,
    DOMAIN,
    TEMP_CONTROLLED_MODES,
    MotorCoordinator,
    build_device_info,
)
from homeassistant.const import CONF_NAME

_LOGGER = logging.getLogger(__name__)

ATTR_VENT_OPEN = "vent_open"
ATTR_VENT_ENTITY_ID = "vent_entity_id"
ATTR_SENSOR_ENTITY_ID = "sensor_entity_id"
ATTR_LAST_VENT_TOGGLE = "last_vent_toggle"

# HVACAction mapping per mode
_MODE_ACTION_OPEN: dict[HVACMode, HVACAction] = {
    HVACMode.HEAT: HVACAction.HEATING,
    HVACMode.COOL: HVACAction.COOLING,
    HVACMode.FAN_ONLY: HVACAction.FAN,
    HVACMode.DRY: HVACAction.DRYING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DuctedHVACZone entities from a config entry."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MotorCoordinator = domain_data["coordinator"]
    cfg = entry.data
    modes: list[HVACMode] = domain_data["modes"]

    min_temp: float = cfg["min_temp"]
    max_temp: float = cfg["max_temp"]
    temp_step: float = cfg["temp_step"]
    tolerance: float = cfg["tolerance"]
    mcd_seconds = cfg.get("min_cycle_duration") or 0
    min_cycle_duration = timedelta(seconds=mcd_seconds) if mcd_seconds else None
    fan_modes: list[str] = domain_data.get("fan_modes") or []
    device_info = build_device_info(entry)

    entities = []
    for zone_cfg in cfg["zones"]:
        entity = DuctedHVACZone(
            hass=hass,
            name=zone_cfg[CONF_NAME],
            unique_id=zone_cfg["unique_id"],
            vent_entity_id=zone_cfg[CONF_VENT],
            sensor_entity_id=zone_cfg[CONF_SENSOR],
            coordinator=coordinator,
            modes=modes,
            fan_modes=fan_modes,
            min_temp=min_temp,
            max_temp=max_temp,
            temp_step=temp_step,
            tolerance=tolerance,
            min_cycle_duration=min_cycle_duration,
            device_info=device_info,
        )
        entities.append(entity)

    async_add_entities(entities)


async def async_setup_platform(
    hass: HomeAssistant,
    config,
    async_add_entities,
    discovery_info=None,
) -> None:
    """Set up DuctedHVACZone entities from discovery_info (YAML path, legacy)."""
    if discovery_info is None:
        return

    domain_data = hass.data[DOMAIN]
    coordinator: MotorCoordinator = domain_data["coordinator"]
    cfg = discovery_info

    min_temp: float = cfg[CONF_MIN_TEMP]
    max_temp: float = cfg[CONF_MAX_TEMP]
    temp_step: float = cfg[CONF_TEMP_STEP]
    tolerance: float = cfg[CONF_TOLERANCE]
    modes: list[HVACMode] = cfg[CONF_MODES]
    min_cycle_duration: timedelta | None = cfg.get(CONF_MIN_CYCLE_DURATION)

    entities = []
    for zone_cfg in cfg[CONF_ZONES]:
        entity = DuctedHVACZone(
            hass=hass,
            name=zone_cfg[CONF_NAME],
            unique_id=zone_cfg[CONF_UNIQUE_ID],
            vent_entity_id=zone_cfg[CONF_VENT],
            sensor_entity_id=zone_cfg[CONF_SENSOR],
            coordinator=coordinator,
            modes=modes,
            min_temp=min_temp,
            max_temp=max_temp,
            temp_step=temp_step,
            tolerance=tolerance,
            min_cycle_duration=min_cycle_duration,
        )
        entities.append(entity)

    async_add_entities(entities)


class DuctedHVACZone(ClimateEntity, RestoreEntity):
    """A single zone climate entity for a ducted HVAC system.

    Controls one vent switch based on temperature and mode, and notifies
    a MotorCoordinator whenever the vent state changes.
    """

    _attr_should_poll = False
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        vent_entity_id: str,
        sensor_entity_id: str,
        coordinator: MotorCoordinator,
        modes: list[HVACMode],
        fan_modes: list[str],
        min_temp: float,
        max_temp: float,
        temp_step: float,
        tolerance: float,
        min_cycle_duration: timedelta | None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self.hass = hass
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._vent_entity_id = vent_entity_id
        self._sensor_entity_id = sensor_entity_id
        self._coordinator = coordinator
        self._tolerance = tolerance
        self._min_cycle_duration = min_cycle_duration

        if device_info is not None:
            self._attr_device_info = device_info

        # ClimateEntity attributes
        self._attr_hvac_modes = modes
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._attr_target_temperature_step = temp_step
        self._attr_temperature_unit = hass.config.units.temperature_unit

        # Only advertise TARGET_TEMPERATURE if at least one temp-controlled mode is present
        has_temp_mode = any(m in TEMP_CONTROLLED_MODES for m in modes)
        self._attr_supported_features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if has_temp_mode:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE

        # Fan mode support (optional — empty list disables the feature)
        if fan_modes:
            self._attr_fan_modes = fan_modes
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._fan_mode: str | None = fan_modes[0]
        else:
            self._attr_fan_modes = []
            self._fan_mode = None

        # Internal state
        self._hvac_mode: HVACMode = HVACMode.OFF
        self._target_temp: float = (min_temp + max_temp) / 2
        self._current_temp: float | None = None
        self._vent_open: bool = False
        self._last_vent_toggle: datetime | None = None

        # Listener unsubscribe handles
        self._unsub_sensor = None
        self._unsub_startup = None

    # ------------------------------------------------------------------
    # ClimateEntity properties
    # ------------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._vent_open:
            return _MODE_ACTION_OPEN.get(self._hvac_mode, HVACAction.IDLE)
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return self._current_temp

    @property
    def target_temperature(self) -> float | None:
        return self._target_temp

    @property
    def fan_mode(self) -> str | None:
        return self._fan_mode

    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_VENT_OPEN: self._vent_open,
            ATTR_VENT_ENTITY_ID: self._vent_entity_id,
            ATTR_SENSOR_ENTITY_ID: self._sensor_entity_id,
            ATTR_LAST_VENT_TOGGLE: (
                self._last_vent_toggle.isoformat() if self._last_vent_toggle else None
            ),
        }

    # ------------------------------------------------------------------
    # Public interface used by MotorCoordinator
    # ------------------------------------------------------------------

    @property
    def vent_is_open(self) -> bool:
        """Return True if the vent is currently open (in-memory state)."""
        return self._vent_open

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore state, subscribe to events, register with coordinator."""
        await super().async_added_to_hass()

        # 1. Restore previous state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            if last_state.state in [m.value for m in HVACMode]:
                self._hvac_mode = HVACMode(last_state.state)

            attrs = last_state.attributes
            if (temp := attrs.get(ATTR_TEMPERATURE)) is not None:
                try:
                    self._target_temp = float(temp)
                except (TypeError, ValueError):
                    pass

            if (vent := attrs.get(ATTR_VENT_OPEN)) is not None:
                self._vent_open = bool(vent)

            if (ts := attrs.get(ATTR_LAST_VENT_TOGGLE)) is not None:
                try:
                    self._last_vent_toggle = dt_util.parse_datetime(ts)
                except (TypeError, ValueError):
                    pass

            if self._fan_mode is not None:
                if (fm := attrs.get("fan_mode")) and fm in (self._attr_fan_modes or []):
                    self._fan_mode = fm

        # 2. Read current sensor value
        self._update_current_temp()

        # 3. Register with coordinator
        self._coordinator.register_zone(self)

        # 4. Subscribe to sensor state changes
        self._unsub_sensor = async_track_state_change_event(
            self.hass,
            [self._sensor_entity_id],
            self._async_sensor_changed,
        )

        # 5. Defer initial vent evaluation until HA is fully started
        self._unsub_startup = async_at_started(self.hass, self._async_startup_check)

        # 6. Write initial state
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe all listeners."""
        if self._unsub_sensor:
            self._unsub_sensor()
            self._unsub_sensor = None
        if self._unsub_startup:
            self._unsub_startup()
            self._unsub_startup = None

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode and immediately re-evaluate vent."""
        if hvac_mode not in self._attr_hvac_modes:
            _LOGGER.warning(
                "%s: mode %s not in configured modes %s",
                self.name,
                hvac_mode,
                self._attr_hvac_modes,
            )
            return
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._async_control_vent()
        self._coordinator.async_zone_changed()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set target temperature and re-evaluate vent."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._target_temp = float(temp)
            self.async_write_ha_state()
            await self._async_control_vent()
            self._coordinator.async_zone_changed()

        # Handle combined hvac_mode + temperature calls
        if hvac_mode := kwargs.get("hvac_mode"):
            await self.async_set_hvac_mode(HVACMode(hvac_mode))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode and notify the coordinator (last-set wins)."""
        if fan_mode not in (self._attr_fan_modes or []):
            _LOGGER.warning(
                "%s: fan mode %s not in configured fan modes %s",
                self.name,
                fan_mode,
                self._attr_fan_modes,
            )
            return
        self._fan_mode = fan_mode
        self.async_write_ha_state()
        self._coordinator.async_fan_mode_changed(fan_mode)

    async def async_turn_on(self) -> None:
        """Turn on: restore to last non-off mode, or cool as default."""
        mode = (
            self._hvac_mode
            if self._hvac_mode != HVACMode.OFF
            else (
                HVACMode.COOL
                if HVACMode.COOL in self._attr_hvac_modes
                else self._attr_hvac_modes[1]  # first non-off mode
            )
        )
        await self.async_set_hvac_mode(mode)

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_current_temp(self) -> None:
        """Read current temperature from the sensor entity."""
        sensor_state = self.hass.states.get(self._sensor_entity_id)
        if sensor_state is None or sensor_state.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            return
        try:
            self._current_temp = float(sensor_state.state)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "%s: could not parse sensor %s state: %s",
                self.name,
                self._sensor_entity_id,
                sensor_state.state,
            )

    @callback
    def _async_sensor_changed(self, event) -> None:
        """Handle temperature sensor state change."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        try:
            self._current_temp = float(new_state.state)
        except (TypeError, ValueError):
            return

        self.async_write_ha_state()
        self.hass.async_create_task(self._async_control_vent())

    @callback
    def _async_startup_check(self, _event=None) -> None:
        """Run initial vent evaluation after HA has fully started."""
        self._unsub_startup = None
        self._update_current_temp()
        self.hass.async_create_task(self._async_control_vent())

    def _should_vent_be_open(self) -> bool:
        """Determine desired vent state from current mode and temperature.

        Uses hysteresis: within the dead-band (target ± tolerance), the current
        vent state is preserved to avoid unnecessary toggling.
        """
        mode = self._hvac_mode

        if mode == HVACMode.OFF:
            return False

        # Non-temperature modes: vent always open
        if mode in (HVACMode.FAN_ONLY, HVACMode.DRY):
            return True

        # Temperature-controlled modes
        temp = self._current_temp
        if temp is None:
            return False  # fail-safe: don't open vent without a reading

        target = self._target_temp
        tol = self._tolerance

        if mode == HVACMode.HEAT:
            if temp < target - tol:
                return True   # too cold, need heating
            if temp >= target + tol:
                return False  # warm enough, stop
            return self._vent_open  # dead-band: no change

        if mode == HVACMode.COOL:
            if temp > target + tol:
                return True   # too hot, need cooling
            if temp <= target - tol:
                return False  # cool enough, stop
            return self._vent_open  # dead-band: no change

        return False

    def _is_min_cycle_respected(self, desired_open: bool) -> bool:
        """Return True if the min_cycle_duration has elapsed since last toggle."""
        if self._min_cycle_duration is None:
            return True
        if self._last_vent_toggle is None:
            return True
        elapsed = dt_util.utcnow() - self._last_vent_toggle
        return elapsed >= self._min_cycle_duration

    async def _async_control_vent(self) -> None:
        """Evaluate vent state and toggle the switch if needed."""
        desired = self._should_vent_be_open()

        if desired == self._vent_open:
            return  # already in the right state

        if not self._is_min_cycle_respected(desired):
            _LOGGER.debug(
                "%s: min_cycle_duration not yet elapsed, skipping vent toggle",
                self.name,
            )
            return

        await self._async_set_vent(desired)

    async def _async_set_vent(self, open: bool) -> None:  # noqa: A002
        """Open or close the vent switch."""
        service = "turn_on" if open else "turn_off"
        try:
            await self.hass.services.async_call(
                "switch",
                service,
                {"entity_id": self._vent_entity_id},
                blocking=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "%s: failed to %s vent %s", self.name, service, self._vent_entity_id
            )
            return

        self._vent_open = open
        self._last_vent_toggle = dt_util.utcnow()
        self.async_write_ha_state()
        self._coordinator.async_zone_changed()
