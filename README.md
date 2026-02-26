<p align="center">
  <img src="./logo.png" alt="Ducted HVAC Logo" width="300">
</p>

# Ducted HVAC

A Home Assistant custom integration for zoned ducted HVAC systems. Manages N zone climate entities tied to a single central motor unit, with automatic vent control, motor synchronisation, and a built-in UI wizard for setup.

## Features

- Zone-level climate entities with independent mode and temperature control
- Vent switch control with hysteresis dead-band to avoid unnecessary toggling
- Central motor synchronisation: mode and temperature derived from all active zones (cool takes priority over heat)
- Motor turns off automatically when all vents are closed
- Coordinator status sensor showing open/closed zones, active mode, and motor target temperature
- Multi-step UI wizard for initial setup
- Options flow to edit global settings and manage zones after setup
- State restoration across HA restarts

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu and select "Custom repositories"
3. Add this repository URL and select "Integration" as the category
4. Search for "Ducted HVAC" and install
5. Restart Home Assistant

### Manual

1. Download the [latest release](../../releases/latest) and extract it
2. Copy the `custom_components/ducted_hvac` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

## Setup

After installation, go to **Settings → Devices & Services → Add Integration** and search for **Ducted HVAC**.

The setup wizard guides you through:

1. **Global settings** — central motor entity, supported modes, temperature range and step, tolerance dead-band, minimum cycle duration
2. **Zone configuration** — for each zone: a display name, vent switch entity, and temperature sensor entity
3. Repeat zone step as many times as needed, then finish

## Configuration Options

### Global

| Option               | Type   | Default                                      | Description                                               |
| -------------------- | ------ | -------------------------------------------- | --------------------------------------------------------- |
| `motor`              | string | **required**                                 | Entity ID of the central motor climate entity             |
| `modes`              | list   | `["off", "heat", "cool", "fan_only", "dry"]` | HVAC modes to expose on zone thermostats                  |
| `min_temp`           | float  | `16.0`                                       | Minimum setpoint temperature                              |
| `max_temp`           | float  | `30.0`                                       | Maximum setpoint temperature                              |
| `temp_step`          | float  | `0.5`                                        | Temperature adjustment step                               |
| `tolerance`          | float  | `0.3`                                        | Dead-band around target temperature (°)                   |
| `min_cycle_duration` | int    | `0`                                          | Minimum seconds between vent state changes (0 = disabled) |

### Per Zone

| Option   | Type   | Description                         |
| -------- | ------ | ----------------------------------- |
| `name`   | string | Display name of the zone            |
| `vent`   | string | Entity ID of the vent switch        |
| `sensor` | string | Entity ID of the temperature sensor |

## Entities Created

For each configured system, the integration creates:

- One **climate entity** per zone (e.g. `climate.ac_living_room`) — controllable thermostat with mode and temperature
- One **sensor entity** for the coordinator (e.g. `sensor.ducted_hvac_status`) — shows `open/total` zones and exposes `active_mode`, `motor_target_temperature`, `open_zones`, `closed_zones` as attributes

All entities are grouped under a single device in the HA device registry.

## How It Works

### Vent Control

Each zone evaluates its vent state independently whenever the temperature sensor updates or the mode/setpoint changes:

- `off` → vent always closed
- `fan_only` / `dry` → vent always open
- `heat` → open when `current < target − tolerance`; close when `current ≥ target + tolerance`; hold within dead-band
- `cool` → open when `current > target + tolerance`; close when `current ≤ target − tolerance`; hold within dead-band

### Motor Synchronisation

After any vent state change the coordinator decides the motor state:

- If **all vents are closed**: motor is turned off
- If **any vent is open**: the winning mode is chosen by priority (`cool > heat > dry > fan_only`)
  - `cool` → motor setpoint = minimum target temperature across cooling zones
  - `heat` → motor setpoint = maximum target temperature across heating zones
  - `dry` / `fan_only` → no temperature setpoint sent

## License

MIT License — see [LICENSE](LICENSE) for details.


