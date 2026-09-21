"""Config and options flow for the Paradox PRT3."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import PRT3Client, PRT3ConnectionError, PRT3Error
from .const import (
    BAUDRATES,
    CONF_AREAS,
    CONF_BAUDRATE,
    CONF_CODEC,
    CONF_ENABLED_AREAS,
    CONF_ENABLED_ZONES,
    CONF_PORT,
    CONF_ZONES,
    DEFAULT_BAUDRATE,
    DOMAIN,
)
from .discovery import DiscoveryResult, async_discover, is_default_label
from .protocol import DEFAULT_CODEC

_LOGGER = logging.getLogger(__name__)


def _list_serial_ports() -> list[str]:
    """Return serial ports, preferring stable /dev/serial/by-id names."""
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        return sorted(str(path) for path in by_id.iterdir())
    from serial.tools import list_ports  # noqa: PLC0415

    return sorted(port.device for port in list_ports.comports())


def _title(port: str) -> str:
    name = Path(port).name if "://" not in port else port.split("://", 1)[1]
    return f"Paradox PRT3 ({name})"


def _default_enabled(labels: dict[int, str]) -> list[str]:
    return [str(index) for index, label in labels.items() if not is_default_label(label)]


def _choices(labels: dict[str, str], kind: str) -> list[selector.SelectOptionDict]:
    return [
        selector.SelectOptionDict(value=index, label=f"{index}: {labels[index] or kind + ' ' + index}")
        for index in sorted(labels, key=int)
    ]


class PRT3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a PRT3 by serial port or serial-over-network URL."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the connection details and discover the panel."""
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_PORT].strip()
            await self.async_set_unique_id(port)
            self._abort_if_unique_id_configured()

            client = PRT3Client(port, user_input[CONF_BAUDRATE], user_input[CONF_CODEC])
            discovery: DiscoveryResult | None = None
            try:
                await client.async_connect()
                discovery = await async_discover(client)
            except PRT3ConnectionError:
                errors["base"] = "cannot_connect"
            except PRT3Error:
                errors["base"] = "no_response"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while discovering the PRT3")
                errors["base"] = "unknown"
            finally:
                await client.async_disconnect()

            if discovery is not None:
                return self.async_create_entry(
                    title=_title(port),
                    data={
                        CONF_PORT: port,
                        CONF_BAUDRATE: user_input[CONF_BAUDRATE],
                        CONF_CODEC: user_input[CONF_CODEC],
                        CONF_AREAS: {str(k): v for k, v in discovery.areas.items()},
                        CONF_ZONES: {str(k): v for k, v in discovery.zones.items()},
                    },
                    options={
                        CONF_ENABLED_AREAS: _default_enabled(discovery.areas),
                        CONF_ENABLED_ZONES: _default_enabled(discovery.zones),
                    },
                )

        ports = await self.hass.async_add_executor_job(_list_serial_ports)
        schema = vol.Schema(
            {
                vol.Required(CONF_PORT): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ports,
                        custom_value=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(BAUDRATES),
                vol.Required(CONF_CODEC, default=DEFAULT_CODEC): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return PRT3OptionsFlow()


class PRT3OptionsFlow(OptionsFlow):
    """Choose which discovered areas and zones become entities."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick areas and zones."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLED_AREAS,
                    default=entry.options.get(CONF_ENABLED_AREAS, []),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_choices(entry.data[CONF_AREAS], "Area"), multiple=True
                    )
                ),
                vol.Required(
                    CONF_ENABLED_ZONES,
                    default=entry.options.get(CONF_ENABLED_ZONES, []),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_choices(entry.data[CONF_ZONES], "Zone"), multiple=True
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
