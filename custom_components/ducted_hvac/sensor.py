"""Sensor platform for ducted_hvac — coordinator status sensor."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, MotorCoordinator, build_device_info

_LOGGER = logging.getLogger(__name__)

ATTR_ACTIVE_MODE = "active_mode"
ATTR_MOTOR_TARGET_TEMP = "motor_target_temperature"
ATTR_OPEN_ZONES = "open_zones"
ATTR_CLOSED_ZONES = "closed_zones"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coordinator status sensor."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MotorCoordinator = domain_data["coordinator"]
    device_info = build_device_info(entry)

    async_add_entities(
        [
            MotorCoordinatorSensor(
                entry_id=entry.entry_id,
                coordinator=coordinator,
                device_info=device_info,
            )
        ]
    )


class MotorCoordinatorSensor(SensorEntity):
    """Reports overall system state from the MotorCoordinator.

    Shows how many zones are open, which mode the motor is in,
    and the current motor target temperature.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:air-conditioner"

    def __init__(
        self,
        entry_id: str,
        coordinator: MotorCoordinator,
        device_info: DeviceInfo,
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry_id}_coordinator_sensor"
        self._attr_device_info = device_info
        # Register for push notifications after each motor sync
        coordinator.register_sensor(self)

    @property
    def native_value(self) -> str:
        """Return 'open/total' zone count, e.g. '2/3'."""
        zones = self._coordinator.zones
        open_count = sum(1 for z in zones if z.vent_is_open)
        return f"{open_count}/{len(zones)}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zones = self._coordinator.zones
        return {
            ATTR_ACTIVE_MODE: self._coordinator.last_active_mode,
            ATTR_MOTOR_TARGET_TEMP: self._coordinator.last_motor_temp,
            ATTR_OPEN_ZONES: [z.name for z in zones if z.vent_is_open],
            ATTR_CLOSED_ZONES: [z.name for z in zones if not z.vent_is_open],
        }

    @callback
    def async_update_sensor(self) -> None:
        """Called by MotorCoordinator after each sync to push a state update."""
        self.async_write_ha_state()
