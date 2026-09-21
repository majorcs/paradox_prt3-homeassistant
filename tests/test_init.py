"""Tests for setup, unload, the coordinator and the entities."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.paradox_prt3.client import PRT3ConnectionError
from custom_components.paradox_prt3.const import POLL_INTERVAL_SECONDS

from .conftest import CODE, FakePanel

ZONE_MOTION = "binary_sensor.paradox_prt3_test_haloszoba_m"
ZONE_DOOR = "binary_sensor.paradox_prt3_test_gyerek1_ajto"
AREA_1 = "alarm_control_panel.paradox_prt3_test_emelet"


async def settle(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ZONE_MOTION).state == "off"
    assert hass.states.get(ZONE_MOTION).attributes["device_class"] == "motion"
    assert hass.states.get(ZONE_DOOR).attributes["device_class"] == "door"
    assert hass.states.get(AREA_1).state == "disarmed"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_port_missing(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.paradox_prt3.client.PRT3Client._open_serial",
        side_effect=PRT3ConnectionError("gone"),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_when_panel_silent(
    hass: HomeAssistant, config_entry: MockConfigEntry, panel: FakePanel
) -> None:
    panel.silent = True
    config_entry.add_to_hass(hass)
    with patch("custom_components.paradox_prt3.client.DEFAULT_TIMEOUT", 0.05):
        await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_zone_events_update_state(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    panel.push("G001N002A001")
    await settle(hass)
    assert hass.states.get(ZONE_MOTION).state == "on"
    panel.push("G000N002A001")
    await settle(hass)
    assert hass.states.get(ZONE_MOTION).state == "off"

    sensor = hass.states.get("sensor.paradox_prt3_test_last_event")
    assert sensor.state == "G000N002A001"
    assert "Zone OK" in sensor.attributes["description"]


async def test_zone_flag_events(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    coordinator = setup_integration.runtime_data
    sequence = [
        (2, "tamper", True), (34, "tamper", False),
        (3, "fire_loop_trouble", True), (0, "fire_loop_trouble", False),
        (24, "in_alarm", True), (26, "in_alarm", False),
        (25, "fire_alarm", True), (27, "fire_alarm", False),
        (41, "low_battery", True), (43, "low_battery", False),
        (42, "supervision_lost", True), (44, "supervision_lost", False),
    ]
    for group, attr, expected in sequence:
        panel.push(f"G{group:03d}N002A001")
        await settle(hass)
        assert getattr(coordinator.data.zones[2], attr) is expected, (group, attr)


async def test_pgm_and_comm_events(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    registry = er.async_get(hass)
    pgm_entity = registry.async_get_entity_id(
        "binary_sensor", "paradox_prt3", f"{setup_integration.entry_id}_pgm_3"
    )
    link_entity = "binary_sensor.paradox_prt3_test_panel_link"
    assert hass.states.get(link_entity).state == "on"
    registry.async_update_entity(pgm_entity, disabled_by=None)
    await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(pgm_entity).state == STATE_UNKNOWN
    panel.push("PGM03ON")
    panel.push("COMM&fail")
    await settle(hass)
    assert hass.states.get(pgm_entity).state == "on"
    assert hass.states.get(link_entity).state == "off"
    panel.push("PGM03OFF")
    panel.push("COMM&ok")
    await settle(hass)
    assert hass.states.get(pgm_entity).state == "off"
    assert hass.states.get(link_entity).state == "on"


async def test_arm_event_refreshes_area(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    panel.area_status[1] = "AOOOOOO"
    panel.push("G009N001A001")
    await settle(hass)
    assert hass.states.get(AREA_1).state == "armed_away"

    panel.area_status[1] = "SOOOOOO"
    panel.push("G004N003A000")  # area 0 = every enabled area
    await settle(hass)
    assert hass.states.get(AREA_1).state == "armed_home"


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("DOOOOOO", "disarmed"),
        ("FOOOOOO", "armed_away"),
        ("IOOOOOO", "armed_night"),
        ("SOOOOAO", "armed_home"),
    ],
)
async def test_area_states(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel, status, state
) -> None:
    panel.area_status[1] = status
    panel.push("G009N001A001")
    await settle(hass)
    expected = "triggered" if status[5] == "A" else state
    assert hass.states.get(AREA_1).state == expected


async def test_area_attributes(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    panel.area_status[1] = "DMTNPOS"
    panel.push("G014N001A001")
    await settle(hass)
    attrs = hass.states.get(AREA_1).attributes
    assert attrs["zone_in_memory"] and attrs["trouble"] and attrs["not_ready"]
    assert attrs["in_programming"] and attrs["strobe"]


@pytest.mark.parametrize(
    ("service", "prefix"),
    [
        ("alarm_arm_away", "AA001A"),
        ("alarm_arm_home", "AA001S"),
        ("alarm_arm_night", "AA001I"),
        ("alarm_disarm", "AD001"),
    ],
)
async def test_alarm_services(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel, service, prefix
) -> None:
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {"entity_id": AREA_1, "code": CODE},
        blocking=True,
    )
    assert f"{prefix}{CODE}" in panel.commands


async def test_alarm_rejects_bad_code(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    before = list(panel.commands)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": AREA_1, "code": "12x4"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": AREA_1, "code": "12x4"},
            blocking=True,
        )
    assert panel.commands == before


async def test_alarm_wrong_code_is_reported(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": AREA_1, "code": "9999"},
            blocking=True,
        )


async def test_periodic_poll_corrects_drift(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    panel.zone_status[4] = "OOOOO"
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=POLL_INTERVAL_SECONDS + 1)
    )
    await settle(hass)
    assert hass.states.get(ZONE_DOOR).state == "on"


async def test_entities_unavailable_when_link_drops_and_recover(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    client = setup_integration.runtime_data.client
    with patch.object(client, "_open", side_effect=PRT3ConnectionError("down")):
        panel.reader.feed_eof()
        await settle(hass)
        assert hass.states.get(ZONE_MOTION).state == STATE_UNAVAILABLE
        assert hass.states.get(AREA_1).state == STATE_UNAVAILABLE


async def test_poll_failure_marks_update_failed(
    hass: HomeAssistant, setup_integration: MockConfigEntry, panel: FakePanel
) -> None:
    coordinator = setup_integration.runtime_data
    panel.zone_status.pop(2)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
