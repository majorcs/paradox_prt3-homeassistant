"""Tests for the config and options flow."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.paradox_prt3 import config_flow
from custom_components.paradox_prt3.client import PRT3ConnectionError
from custom_components.paradox_prt3.const import (
    CONF_AREAS,
    CONF_BAUDRATE,
    CONF_CODEC,
    CONF_ENABLED_AREAS,
    CONF_ENABLED_ZONES,
    CONF_PORT,
    CONF_ZONES,
    DOMAIN,
)

from .conftest import PORT, FakePanel

_REAL_LIST_PORTS = config_flow._list_serial_ports
USER_INPUT = {CONF_PORT: PORT, CONF_BAUDRATE: 57600, CONF_CODEC: "cp852"}


@pytest.fixture(autouse=True)
def no_real_ports():
    with patch.object(config_flow, "_list_serial_ports", return_value=[PORT]):
        yield


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.paradox_prt3.async_setup_entry", return_value=True):
        yield


async def test_user_flow_creates_entry(hass: HomeAssistant, panel: FakePanel) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Paradox PRT3 (usb-PARADOX_PRT3)"
    assert result["data"][CONF_AREAS]["2"] == "Alsó szint"
    assert len(result["data"][CONF_ZONES]) == 192
    # Only labelled areas/zones are enabled by default.
    assert result["options"][CONF_ENABLED_AREAS] == ["1", "2", "3"]
    assert result["options"][CONF_ENABLED_ZONES] == ["2", "4", "6", "16"]


async def test_url_title(hass: HomeAssistant, panel: FakePanel) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_PORT: "socket://10.0.0.5:4000"}
    )
    assert result["title"] == "Paradox PRT3 (10.0.0.5:4000)"


async def test_duplicate_port_aborts(
    hass: HomeAssistant, panel: FakePanel, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("side_effect", "silent", "error"),
    [
        (PRT3ConnectionError("nope"), False, "cannot_connect"),
        (None, True, "no_response"),
        (RuntimeError("boom"), False, "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, panel: FakePanel, side_effect, silent: bool, error: str
) -> None:
    panel.silent = silent
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with (
        patch(
            "custom_components.paradox_prt3.config_flow.PRT3Client.async_connect",
            side_effect=side_effect,
        )
        if side_effect
        else patch("custom_components.paradox_prt3.client.DEFAULT_TIMEOUT", 0.05)
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    panel.silent = False
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENABLED_AREAS: ["1"], CONF_ENABLED_ZONES: ["2"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_ENABLED_AREAS: ["1"], CONF_ENABLED_ZONES: ["2"]}


def test_list_serial_ports_falls_back_to_pyserial() -> None:
    with (
        patch.object(config_flow.Path, "is_dir", return_value=False),
        patch("serial.tools.list_ports.comports") as comports,
    ):
        comports.return_value = [type("P", (), {"device": "/dev/ttyUSB1"})()]
        assert _REAL_LIST_PORTS() == ["/dev/ttyUSB1"]


def test_list_serial_ports_prefers_by_id(tmp_path) -> None:
    (tmp_path / "usb-a").touch()
    with patch.object(config_flow, "Path", return_value=tmp_path):
        assert _REAL_LIST_PORTS() == [str(tmp_path / "usb-a")]
