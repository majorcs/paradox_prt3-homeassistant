"""Paradox PRT3 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import PRT3Client, PRT3ConnectionError
from .const import CONF_BAUDRATE, CONF_CODEC, CONF_PORT
from .coordinator import PRT3Coordinator

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

type PRT3ConfigEntry = ConfigEntry[PRT3Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PRT3ConfigEntry) -> bool:
    """Connect to the PRT3 and set up the platforms."""
    client = PRT3Client(
        entry.data[CONF_PORT], entry.data[CONF_BAUDRATE], entry.data[CONF_CODEC]
    )
    try:
        await client.async_connect()
    except PRT3ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = PRT3Coordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        coordinator.async_shutdown_listeners()
        await client.async_disconnect()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: PRT3ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PRT3ConfigEntry) -> bool:
    """Unload the platforms and close the serial connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.async_shutdown_listeners()
        await entry.runtime_data.client.async_disconnect()
    return unloaded
