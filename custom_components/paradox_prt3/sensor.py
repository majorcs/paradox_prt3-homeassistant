"""Last panel event sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PRT3ConfigEntry
from .const import CONF_AREAS, CONF_ZONES
from .coordinator import PRT3Coordinator
from .entity import PRT3Entity
from .protocol import describe_event


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PRT3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the last-event sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [PRT3LastEventSensor(coordinator), PRT3LastEventTimeSensor(coordinator)]
    )


class PRT3LastEventSensor(PRT3Entity, SensorEntity):
    """The most recent system event pushed by the panel, in words."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last event"

    def __init__(self, coordinator: PRT3Coordinator) -> None:
        super().__init__(coordinator, "last_event")
        data = coordinator.config_entry.data
        self._zone_labels: dict[str, str] = data[CONF_ZONES]
        self._area_labels: dict[str, str] = data[CONF_AREAS]

    @property
    def native_value(self) -> str | None:
        """Readable description, e.g. ``Zone open: Kitchen door (zone 4), Ground floor``."""
        event = self.coordinator.data.last_event
        if event is None:
            return None
        return describe_event(
            event,
            zone_label=self._zone_labels.get(str(event.number)),
            area_label=self._area_labels.get(str(event.area)),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Raw event code and its parts."""
        event = self.coordinator.data.last_event
        if event is None:
            return {}
        return {
            "code": f"G{event.group:03d}N{event.number:03d}A{event.area:03d}",
            "group": event.group,
            "number": event.number,
            "area": event.area,
        }


class PRT3LastEventTimeSensor(PRT3Entity, SensorEntity):
    """When the most recent system event was received."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last event time"

    def __init__(self, coordinator: PRT3Coordinator) -> None:
        super().__init__(coordinator, "last_event_time")

    @property
    def native_value(self) -> datetime | None:
        """Time of the last event, None until one arrives."""
        return self.coordinator.data.last_event_time
