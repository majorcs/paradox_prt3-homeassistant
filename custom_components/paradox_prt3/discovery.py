"""Discover areas and zones (with their labels) from a PRT3."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import unicodedata

from .client import PRT3Client, PRT3CommandError
from .protocol import (
    Label,
    query_area_label,
    query_zone_label,
)

_DEFAULT_LABEL_RE = re.compile(r"^(zone|area)\s*(\d+)$", re.IGNORECASE)


@dataclass
class DiscoveryResult:
    """Labels of every area and zone the panel supports."""

    areas: dict[int, str]
    zones: dict[int, str]


def is_default_label(label: str) -> bool:
    """Return True for empty or factory-default (``Zone 22``) labels."""
    return not label or bool(_DEFAULT_LABEL_RE.match(label))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def classify_zone(label: str) -> str | None:
    """Guess the sensor type from the (Hungarian/English) label.

    The protocol does not expose the zone type, so this is a heuristic.
    """
    text = _fold(label)
    words = set(re.findall(r"[a-z0-9]+", text))
    if "tamper" in text:
        return "tamper"
    if "ablak" in text or "window" in text:
        return "window"
    if "ajto" in text or "door" in text:
        return "door"
    if words & {"m", "pir", "motion", "mozgas"} or "mozg" in text:
        return "motion"
    if "fust" in text or "smoke" in text:
        return "smoke"
    return None


async def _label(client: PRT3Client, command: str) -> str | None:
    """Return the label, or ``None`` if the index does not exist."""
    try:
        reply = await client.async_request(command)
    except PRT3CommandError:
        return None
    return reply.text if isinstance(reply, Label) else None


async def async_discover(
    client: PRT3Client, progress: Callable[[int, int], None] | None = None
) -> DiscoveryResult:
    """Probe the panel size and read all area and zone labels.

    Raises PRT3Error if the panel does not answer at all.
    """
    if await _label(client, query_area_label(1)) is None:
        raise PRT3CommandError("The panel did not return an area label")

    area_count = 8 if await _label(client, query_area_label(5)) is not None else 4
    if await _label(client, query_zone_label(97)) is not None:
        zone_count = 192
    elif await _label(client, query_zone_label(49)) is not None:
        zone_count = 96
    else:
        zone_count = 48

    areas: dict[int, str] = {}
    zones: dict[int, str] = {}
    total = area_count + zone_count
    for done, area in enumerate(range(1, area_count + 1), start=1):
        areas[area] = await _label(client, query_area_label(area)) or ""
        if progress:
            progress(done, total)
    for done, zone in enumerate(range(1, zone_count + 1), start=area_count + 1):
        zones[zone] = await _label(client, query_zone_label(zone)) or ""
        if progress:
            progress(done, total)
    return DiscoveryResult(areas, zones)
