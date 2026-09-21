"""Zone, area-flag, virtual PGM and link binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PRT3ConfigEntry
from .const import CONF_AREAS, CONF_ZONES
from .coordinator import AreaState, PRT3Coordinator, ZoneState
from .discovery import classify_zone
from .entity import PRT3Entity
from .protocol import MAX_PGMS

_ZONE_CLASSES = {
    "motion": BinarySensorDeviceClass.MOTION,
    "door": BinarySensorDeviceClass.DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "tamper": BinarySensorDeviceClass.TAMPER,
    "smoke": BinarySensorDeviceClass.SMOKE,
}


@dataclass(frozen=True)
class _Flag:
    key: str
    name: str
    value: Callable[[ZoneState], bool] | Callable[[AreaState], bool]
    device_class: BinarySensorDeviceClass | None = None
    enabled_default: bool = False


_ZONE_FLAGS = (
    _Flag("tamper", "tamper", lambda z: z.tamper, BinarySensorDeviceClass.TAMPER),
    _Flag("fire_loop", "fire loop trouble", lambda z: z.fire_loop_trouble, BinarySensorDeviceClass.PROBLEM),
    _Flag("alarm", "alarm", lambda z: z.in_alarm, BinarySensorDeviceClass.SAFETY),
    _Flag("fire_alarm", "fire alarm", lambda z: z.fire_alarm, BinarySensorDeviceClass.SMOKE),
    _Flag("supervision", "supervision lost", lambda z: z.supervision_lost, BinarySensorDeviceClass.PROBLEM),
    _Flag("low_battery", "low battery", lambda z: z.low_battery, BinarySensorDeviceClass.BATTERY),
)

_AREA_FLAGS = (
    _Flag("not_ready", "not ready", lambda a: a.not_ready, None, True),
    _Flag("trouble", "trouble", lambda a: a.trouble, BinarySensorDeviceClass.PROBLEM, True),
    _Flag("memory", "zone in memory", lambda a: a.zone_in_memory),
    _Flag("programming", "in programming", lambda a: a.in_programming),
    _Flag("strobe", "strobe", lambda a: a.strobe),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PRT3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for the enabled zones and areas."""
    coordinator = entry.runtime_data
    zone_labels = entry.data[CONF_ZONES]
    area_labels = entry.data[CONF_AREAS]

    entities: list[PRT3Entity] = [PRT3LinkSensor(coordinator)]
    for zone in coordinator.enabled_zones:
        label = zone_labels.get(str(zone)) or f"Zone {zone}"
        entities.append(PRT3ZoneSensor(coordinator, zone, label))
        entities.extend(PRT3ZoneFlag(coordinator, zone, label, flag) for flag in _ZONE_FLAGS)
    for area in coordinator.enabled_areas:
        label = area_labels.get(str(area)) or f"Area {area}"
        entities.extend(PRT3AreaFlag(coordinator, area, label, flag) for flag in _AREA_FLAGS)
    entities.extend(PRT3PgmSensor(coordinator, pgm) for pgm in range(1, MAX_PGMS + 1))
    async_add_entities(entities)


class PRT3LinkSensor(PRT3Entity, BinarySensorEntity):
    """Whether the PRT3 can talk to the Digiplex panel."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Panel link"

    def __init__(self, coordinator: PRT3Coordinator) -> None:
        super().__init__(coordinator, "panel_link")

    @property
    def is_on(self) -> bool:
        """True while the panel answers."""
        return self.coordinator.data.panel_link_ok


class PRT3ZoneSensor(PRT3Entity, BinarySensorEntity):
    """Open/closed state of a zone."""

    def __init__(self, coordinator: PRT3Coordinator, zone: int, label: str) -> None:
        super().__init__(coordinator, f"zone_{zone}")
        self._zone = zone
        self._attr_name = label
        self._attr_device_class = _ZONE_CLASSES.get(classify_zone(label) or "")

    @property
    def is_on(self) -> bool | None:
        """True while the zone is open."""
        zone = self.coordinator.data.zones.get(self._zone)
        return None if zone is None else zone.open


class PRT3ZoneFlag(PRT3Entity, BinarySensorEntity):
    """A diagnostic flag of a zone (tamper, alarm, low battery, ...)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: PRT3Coordinator, zone: int, label: str, flag: _Flag
    ) -> None:
        super().__init__(coordinator, f"zone_{zone}_{flag.key}")
        self._zone = zone
        self._flag = flag
        self._attr_name = f"{label} {flag.name}"
        self._attr_device_class = flag.device_class

    @property
    def is_on(self) -> bool | None:
        """Value of the flag."""
        zone = self.coordinator.data.zones.get(self._zone)
        return None if zone is None else self._flag.value(zone)


class PRT3AreaFlag(PRT3Entity, BinarySensorEntity):
    """A status flag of an area."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: PRT3Coordinator, area: int, label: str, flag: _Flag
    ) -> None:
        super().__init__(coordinator, f"area_{area}_{flag.key}")
        self._area = area
        self._flag = flag
        self._attr_name = f"{label} {flag.name}"
        self._attr_device_class = flag.device_class
        self._attr_entity_registry_enabled_default = flag.enabled_default

    @property
    def is_on(self) -> bool | None:
        """Value of the flag."""
        area = self.coordinator.data.areas.get(self._area)
        return None if area is None else self._flag.value(area)


class PRT3PgmSensor(PRT3Entity, BinarySensorEntity):
    """State of a virtual PGM, driven by the panel's activation events."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PRT3Coordinator, pgm: int) -> None:
        super().__init__(coordinator, f"pgm_{pgm}")
        self._pgm = pgm
        self._attr_name = f"Virtual PGM {pgm}"

    @property
    def is_on(self) -> bool | None:
        """True after an activation event, None until the first event."""
        return self.coordinator.data.pgms.get(self._pgm)
