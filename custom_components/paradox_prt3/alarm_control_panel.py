"""One alarm control panel per PRT3 area."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PRT3ConfigEntry
from .client import PRT3Error
from .const import CONF_AREAS
from .coordinator import PRT3Coordinator
from .entity import PRT3Entity
from .protocol import (
    ARM_INSTANT,
    ARM_REGULAR,
    ARM_STAY,
    ProtocolError,
    arm_area,
    disarm_area,
)

_ARM_STATES = {
    "D": AlarmControlPanelState.DISARMED,
    "A": AlarmControlPanelState.ARMED_AWAY,
    "F": AlarmControlPanelState.ARMED_AWAY,
    "S": AlarmControlPanelState.ARMED_HOME,
    "I": AlarmControlPanelState.ARMED_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PRT3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create an alarm panel for every enabled area."""
    coordinator = entry.runtime_data
    labels = entry.data[CONF_AREAS]
    async_add_entities(
        PRT3AreaAlarm(coordinator, area, labels.get(str(area)) or f"Area {area}")
        for area in coordinator.enabled_areas
    )


class PRT3AreaAlarm(PRT3Entity, AlarmControlPanelEntity):
    """An area that can be armed and disarmed with a user code."""

    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )

    def __init__(self, coordinator: PRT3Coordinator, area: int, label: str) -> None:
        super().__init__(coordinator, f"area_{area}")
        self._area = area
        self._attr_name = label

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Map the PRT3 arm state to the HA alarm state."""
        area = self.coordinator.data.areas.get(self._area)
        if area is None:
            return None
        if area.in_alarm:
            return AlarmControlPanelState.TRIGGERED
        return _ARM_STATES.get(area.arm)

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Expose the remaining area flags."""
        area = self.coordinator.data.areas.get(self._area)
        if area is None:
            return {}
        return {
            "zone_in_memory": area.zone_in_memory,
            "trouble": area.trouble,
            "not_ready": area.not_ready,
            "in_programming": area.in_programming,
            "strobe": area.strobe,
        }

    async def _send(self, command: str) -> None:
        try:
            await self.coordinator.client.async_request(command)
        except PRT3Error as err:
            raise HomeAssistantError(f"The panel refused the command: {err}") from err
        await self.coordinator.async_refresh_areas(self._area)

    async def _arm(self, mode: str, code: str | None) -> None:
        try:
            command = arm_area(self._area, mode, code)
        except ProtocolError as err:
            raise ServiceValidationError(str(err)) from err
        await self._send(command)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the area."""
        try:
            command = disarm_area(self._area, code)
        except ProtocolError as err:
            raise ServiceValidationError(str(err)) from err
        await self._send(command)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the area (regular arm)."""
        await self._arm(ARM_REGULAR, code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the area in stay mode."""
        await self._arm(ARM_STAY, code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm the area in instant mode."""
        await self._arm(ARM_INSTANT, code)
