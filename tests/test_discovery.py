"""Tests for panel discovery and label heuristics."""

import pytest

from custom_components.paradox_prt3.client import PRT3Client, PRT3CommandError
from custom_components.paradox_prt3.discovery import (
    async_discover,
    classify_zone,
    is_default_label,
)

from .conftest import FakePanel


@pytest.fixture
async def client(panel: FakePanel):
    async def opener():
        return panel.reader, panel.writer

    client = PRT3Client("test", 57600, opener=opener)
    await client.async_connect()
    yield client
    await client.async_disconnect()


async def test_discover_evo192(client: PRT3Client, panel: FakePanel) -> None:
    progress = []
    result = await async_discover(client, lambda done, total: progress.append((done, total)))
    assert len(result.areas) == 8
    assert len(result.zones) == 192
    assert result.areas[2] == "Alsó szint"
    assert result.zones[2] == "Haloszoba M"
    assert progress[-1] == (200, 200)


@pytest.mark.parametrize(("zones", "expected"), [(96, 96), (48, 48)])
async def test_discover_smaller_panels(
    client: PRT3Client, panel: FakePanel, zones: int, expected: int
) -> None:
    panel.zone_labels = {n: f"Zone {n}" for n in range(1, zones + 1)}
    panel.area_labels = {n: f"Area {n}" for n in range(1, 5)}
    result = await async_discover(client)
    assert len(result.zones) == expected
    assert len(result.areas) == 4


async def test_discover_fails_without_panel(client: PRT3Client, panel: FakePanel) -> None:
    panel.area_labels = {}
    with pytest.raises(PRT3CommandError):
        await async_discover(client)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("", True),
        ("Zone 22", True),
        ("Area 4", True),
        ("zone22", True),
        ("Bejarati ajto", False),
        ("Zone 22 door", False),
    ],
)
def test_is_default_label(label: str, expected: bool) -> None:
    assert is_default_label(label) is expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Haloszoba M", "motion"),
        ("Dolgozo M", "motion"),
        ("Nappali mozgás", "motion"),
        ("Gyerek1 ajto", "door"),
        ("Nappali T ajto", "door"),
        ("Front door", "door"),
        ("Halo ablak1", "window"),
        ("KP TAMPER", "tamper"),
        ("SZIRENA TAMPER", "tamper"),
        ("Konyha füst", "smoke"),
        ("Zone 22", None),
        ("", None),
    ],
)
def test_classify_zone(label: str, expected: str | None) -> None:
    assert classify_zone(label) == expected
