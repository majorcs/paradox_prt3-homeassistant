"""Shared fixtures: an in-memory PRT3 speaking the ASCII protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.paradox_prt3 import client as client_module
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

pytest_plugins = "pytest_homeassistant_custom_component"

PORT = "/dev/serial/by-id/usb-PARADOX_PRT3"
CODE = "4711"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture(autouse=True)
def fast_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the inter-command pacing and shorten timeouts."""
    monkeypatch.setattr(client_module, "COMMAND_DELAY", 0.0)
    monkeypatch.setattr(client_module, "DEFAULT_TIMEOUT", 0.5)
    monkeypatch.setattr(client_module, "BUFFER_FULL_BACKOFF", 0.0)


class FakeWriter:
    """Stands in for asyncio.StreamWriter; commands go to the FakePanel."""

    def __init__(self, panel: FakePanel) -> None:
        self._panel = panel
        self.closed = False

    def write(self, data: bytes) -> None:
        for frame in data.split(b"\r"):
            if frame:
                self._panel.handle(frame.decode("ascii"))

    def close(self) -> None:
        self.closed = True


class FakePanel:
    """Minimal EVO192-like panel behind a PRT3."""

    def __init__(self) -> None:
        self.reader = asyncio.StreamReader()
        self.writer = FakeWriter(self)
        self.commands: list[str] = []
        self.silent = False
        self.buffer_full = 0
        self.area_labels = {
            1: "Emelet",
            2: "Alsó szint",
            3: "Gyerek",
            **{n: f"Area {n}" for n in range(4, 9)},
        }
        self.zone_labels = {n: f"Zone {n}" for n in range(1, 193)}
        self.zone_labels.update(
            {
                2: "Haloszoba M",
                4: "Gyerek1 ajto",
                6: "Halo ablak1",
                16: "KP TAMPER",
            }
        )
        self.area_status = {n: "DOOOOOO" for n in range(1, 9)}
        self.zone_status = {n: "COOOO" for n in range(1, 193)}

    def push(self, text: str) -> None:
        """Send an unsolicited frame to the client."""
        self.reader.feed_data(text.encode("cp852") + b"\r")

    def _reply(self, text: bytes | str) -> None:
        data = text if isinstance(text, bytes) else text.encode("cp852")
        self.reader.feed_data(data + b"\r")

    def handle(self, cmd: str) -> None:
        """Answer one command like the real PRT3."""
        self.commands.append(cmd)
        if self.silent:
            return
        if self.buffer_full:
            self.buffer_full -= 1
            self._reply("!")
            return
        kind, number = cmd[:2], int(cmd[2:5]) if cmd[2:5].isdigit() else 0
        if kind in ("AL", "ZL"):
            labels = self.area_labels if kind == "AL" else self.zone_labels
            if number in labels:
                self._reply(f"{cmd[:5]}{labels[number]:<16}")
            else:
                self._reply(f"{cmd[:5]}&fail")
        elif kind == "RA" and number in self.area_status:
            self._reply(f"{cmd[:5]}{self.area_status[number]}")
        elif kind == "RZ" and number in self.zone_status:
            self._reply(f"{cmd[:5]}{self.zone_status[number]}")
        elif kind in ("AA", "AD") and cmd.endswith(CODE) and number in self.area_status:
            self._reply(f"{cmd[:5]}&OK")
        else:
            self._reply(f"{cmd[:5]}&fail")


@pytest.fixture
def panel() -> Generator[FakePanel]:
    """A fake panel wired into every PRT3Client."""
    fake = FakePanel()

    async def open_serial(self: client_module.PRT3Client):
        return fake.reader, fake.writer

    with patch.object(client_module.PRT3Client, "_open_serial", open_serial):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry with a few discovered areas and zones."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Paradox PRT3 (test)",
        unique_id=PORT,
        data={
            CONF_PORT: PORT,
            CONF_BAUDRATE: 57600,
            CONF_CODEC: "cp852",
            CONF_AREAS: {"1": "Emelet", "2": "Alsó szint", "4": "Area 4"},
            CONF_ZONES: {
                "2": "Haloszoba M",
                "4": "Gyerek1 ajto",
                "6": "Halo ablak1",
                "16": "KP TAMPER",
            },
        },
        options={
            CONF_ENABLED_AREAS: ["1", "2"],
            CONF_ENABLED_ZONES: ["2", "4", "6", "16"],
        },
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, panel: FakePanel
) -> AsyncGenerator[MockConfigEntry]:
    """Set the entry up and unload it afterwards."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
