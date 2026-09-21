"""Push-driven coordinator for a PRT3 panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import PRT3Client, PRT3Error
from .const import CONF_ENABLED_AREAS, CONF_ENABLED_ZONES, DOMAIN, POLL_INTERVAL_SECONDS
from .protocol import (
    AreaStatus,
    CommStatus,
    Message,
    PgmEvent,
    SystemEvent,
    ZoneStatus,
    query_area_status,
    query_zone_status,
)

_LOGGER = logging.getLogger(__name__)

# Event groups after which the arm/alarm state of an area may have changed.
_AREA_STATE_GROUPS = {*range(9, 23), 24, 25, 26, 27, 30, 31}
# Group 4 numbers: arm with no entry delay, stay, away, full arm when in stay.
_AREA_STATE_GROUP4_NUMBERS = {2, 3, 4, 5}


@dataclass
class ZoneState:
    """State of one zone."""

    open: bool = False
    tamper: bool = False
    fire_loop_trouble: bool = False
    in_alarm: bool = False
    fire_alarm: bool = False
    supervision_lost: bool = False
    low_battery: bool = False


@dataclass
class AreaState:
    """State of one area."""

    arm: str = "D"
    zone_in_memory: bool = False
    trouble: bool = False
    not_ready: bool = False
    in_programming: bool = False
    in_alarm: bool = False
    strobe: bool = False


@dataclass
class PanelData:
    """Everything the entities read."""

    zones: dict[int, ZoneState] = field(default_factory=dict)
    areas: dict[int, AreaState] = field(default_factory=dict)
    pgms: dict[int, bool] = field(default_factory=dict)
    panel_link_ok: bool = True
    connected: bool = False
    last_event: SystemEvent | None = None
    last_event_time: datetime | None = None


class PRT3Coordinator(DataUpdateCoordinator[PanelData]):
    """Polls once, then applies the panel's unsolicited events."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, client: PRT3Client
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self.client = client
        self._state = PanelData(connected=client.connected)
        self.enabled_areas: list[int] = sorted(
            int(area) for area in config_entry.options.get(CONF_ENABLED_AREAS, [])
        )
        self.enabled_zones: list[int] = sorted(
            int(zone) for zone in config_entry.options.get(CONF_ENABLED_ZONES, [])
        )
        self._unsubscribers = [
            client.add_message_listener(self._handle_message),
            client.add_connection_listener(self._handle_connection),
        ]

    def async_shutdown_listeners(self) -> None:
        """Detach from the client."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []

    async def _async_update_data(self) -> PanelData:
        try:
            for area in self.enabled_areas:
                await self._refresh_area(area)
            for zone in self.enabled_zones:
                await self._refresh_zone(zone)
        except PRT3Error as err:
            raise UpdateFailed(f"Error talking to the PRT3: {err}") from err
        self._state.connected = self.client.connected
        return self._state

    async def _refresh_area(self, area: int) -> None:
        reply = await self.client.async_request(query_area_status(area))
        if isinstance(reply, AreaStatus):
            self._apply_area(reply)

    async def _refresh_zone(self, zone: int) -> None:
        reply = await self.client.async_request(query_zone_status(zone))
        if isinstance(reply, ZoneStatus):
            self._apply_zone(reply)

    async def async_refresh_areas(self, area: int) -> None:
        """Re-read one area (0 = all enabled areas) and publish the result."""
        targets = self.enabled_areas if area == 0 else [area]
        try:
            for target in targets:
                if target in self.enabled_areas:
                    await self._refresh_area(target)
        except PRT3Error as err:
            _LOGGER.debug("Area refresh failed: %s", err)
            return
        self.async_set_updated_data(self._state)

    def _apply_area(self, status: AreaStatus) -> None:
        self._state.areas[status.area] = AreaState(
            arm=status.arm,
            zone_in_memory=status.zone_in_memory,
            trouble=status.trouble,
            not_ready=status.not_ready,
            in_programming=status.in_programming,
            in_alarm=status.in_alarm,
            strobe=status.strobe,
        )

    def _apply_zone(self, status: ZoneStatus) -> None:
        self._state.zones[status.zone] = ZoneState(
            open=status.state == "O",
            tamper=status.state == "T",
            fire_loop_trouble=status.state == "F",
            in_alarm=status.in_alarm,
            fire_alarm=status.fire_alarm,
            supervision_lost=status.supervision_lost,
            low_battery=status.low_battery,
        )

    def _handle_connection(self, connected: bool) -> None:
        self._state.connected = connected
        if self.data is not None:
            self.async_set_updated_data(self._state)
        if connected:
            self.hass.async_create_task(self.async_request_refresh())

    def _handle_message(self, message: Message) -> None:
        if isinstance(message, SystemEvent):
            self._handle_event(message)
        elif isinstance(message, PgmEvent):
            self._state.pgms[message.pgm] = message.on
        elif isinstance(message, CommStatus):
            self._state.panel_link_ok = message.ok
        else:
            return
        self.async_set_updated_data(self._state)

    def _handle_event(self, event: SystemEvent) -> None:
        self._state.last_event = event
        self._state.last_event_time = dt_util.utcnow()
        zone = self._state.zones.get(event.number)
        if zone is not None:
            self._apply_zone_event(zone, event.group)
        if event.group in _AREA_STATE_GROUPS or (
            event.group == 4 and event.number in _AREA_STATE_GROUP4_NUMBERS
        ):
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh_areas(event.area), "paradox_prt3_area_refresh"
            )

    @staticmethod
    def _apply_zone_event(zone: ZoneState, group: int) -> None:
        if group == 0:
            zone.open = zone.tamper = zone.fire_loop_trouble = False
        elif group == 1:
            zone.open = True
        elif group == 2:
            zone.tamper = True
        elif group == 3:
            zone.fire_loop_trouble = True
        elif group == 24:
            zone.in_alarm = True
        elif group == 25:
            zone.fire_alarm = True
        elif group == 26:
            zone.in_alarm = False
        elif group == 27:
            zone.fire_alarm = False
        elif group == 34:
            zone.tamper = False
        elif group == 41:
            zone.low_battery = True
        elif group == 43:
            zone.low_battery = False
        elif group == 42:
            zone.supervision_lost = True
        elif group == 44:
            zone.supervision_lost = False
