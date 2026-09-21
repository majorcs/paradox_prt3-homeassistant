"""Last panel event sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PRT3ConfigEntry
from .coordinator import PRT3Coordinator
from .entity import PRT3Entity
from .protocol import describe_event


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PRT3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the last-event sensor."""
    async_add_entities([PRT3LastEventSensor(entry.runtime_data)])


class PRT3LastEventSensor(PRT3Entity, SensorEntity):
    """The most recent system event pushed by the panel."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last event"

    def __init__(self, coordinator: PRT3Coordinator) -> None:
        super().__init__(coordinator, "last_event")

    @property
    def native_value(self) -> str | None:
        """Raw event code, e.g. G001N009A001."""
        event = self.coordinator.data.last_event
        if event is None:
            return None
        return f"G{event.group:03d}N{event.number:03d}A{event.area:03d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Decoded event."""
        event = self.coordinator.data.last_event
        if event is None:
            return {}
        return {
            "group": event.group,
            "number": event.number,
            "area": event.area,
            "description": describe_event(event),
        }
