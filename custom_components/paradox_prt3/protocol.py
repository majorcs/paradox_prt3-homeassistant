"""Pure codec for the Paradox PRT3 ASCII protocol (no Home Assistant imports).

Every frame is terminated by a carriage return. Queries are echoed back with
the requested data appended (``RA001DOOOOOO``); a failed command is answered
with ``<echo>&fail``. Panel events arrive unsolicited (``G001N009A001``).
"""

from __future__ import annotations

from dataclasses import dataclass
import re

CR = b"\r"

# The panel stores labels in a Central European DOS code page.
DEFAULT_CODEC = "cp852"

MAX_AREAS = 8
MAX_ZONES = 192
MAX_PGMS = 30

ARM_REGULAR = "A"
ARM_FORCE = "F"
ARM_STAY = "S"
ARM_INSTANT = "I"

LABEL_KINDS = {"ZL": "zone", "AL": "area", "UL": "user"}


class ProtocolError(ValueError):
    """Raised when a command cannot be built from the given arguments."""


@dataclass(frozen=True)
class AreaStatus:
    """Reply to ``RA###``."""

    area: int
    arm: str  # D, A, F, S or I
    zone_in_memory: bool
    trouble: bool
    not_ready: bool
    in_programming: bool
    in_alarm: bool
    strobe: bool


@dataclass(frozen=True)
class ZoneStatus:
    """Reply to ``RZ###``."""

    zone: int
    state: str  # C closed, O open, T tampered, F fire loop trouble
    in_alarm: bool
    fire_alarm: bool
    supervision_lost: bool
    low_battery: bool


@dataclass(frozen=True)
class Label:
    """Reply to ``ZL###``, ``AL###`` or ``UL###``."""

    kind: str  # zone, area or user
    index: int
    text: str


@dataclass(frozen=True)
class SystemEvent:
    """Unsolicited ``G<group>N<number>A<area>`` panel event."""

    group: int
    number: int
    area: int


@dataclass(frozen=True)
class PgmEvent:
    """Unsolicited virtual PGM activation/deactivation."""

    pgm: int
    on: bool


@dataclass(frozen=True)
class CommStatus:
    """Link state between the PRT3 and the panel."""

    ok: bool


@dataclass(frozen=True)
class CommandResult:
    """``<echo>&OK`` or ``<echo>&fail``."""

    prefix: str
    ok: bool


@dataclass(frozen=True)
class BufferFull:
    """A lone ``!``: the PRT3 could not accept the command."""


@dataclass(frozen=True)
class Unknown:
    """A frame that matched no known format."""

    raw: str


Message = (
    AreaStatus
    | ZoneStatus
    | Label
    | SystemEvent
    | PgmEvent
    | CommStatus
    | CommandResult
    | BufferFull
    | Unknown
)

_AREA_RE = re.compile(r"^RA(\d{3})([DAFSI])([MO])([TO])([NO])([PO])([AO])([SO])$")
_ZONE_RE = re.compile(r"^RZ(\d{3})([COTF])([AO])([FO])([SO])([LO])$")
_LABEL_RE = re.compile(r"^(ZL|AL|UL)(\d{3})(.*)$")
_EVENT_RE = re.compile(r"^G(\d{3})N(\d{3})A(\d{3})$")
_PGM_RE = re.compile(r"^PGM(\d{2})(ON|OFF)$")
_RESULT_RE = re.compile(r"^(.{5})&(ok|fail)$", re.IGNORECASE)


def parse_line(raw: bytes, codec: str = DEFAULT_CODEC) -> Message | None:
    """Parse one received frame; ``None`` for an empty line."""
    line = raw.strip(b"\r\n\x00")
    if not line:
        return None
    if line == b"!":
        return BufferFull()
    text = line.decode(codec, errors="replace")

    if text.upper().startswith("COMM&"):
        return CommStatus(text[5:].strip().lower() == "ok")
    if match := _RESULT_RE.match(text.rstrip()):
        return CommandResult(match[1], match[2].lower() == "ok")
    if match := _AREA_RE.match(text):
        flags = [char != "O" for char in match.groups()[2:]]
        return AreaStatus(int(match[1]), match[2], *flags)
    if match := _ZONE_RE.match(text):
        flags = [char != "O" for char in match.groups()[2:]]
        return ZoneStatus(int(match[1]), match[2], *flags)
    if match := _LABEL_RE.match(text):
        return Label(LABEL_KINDS[match[1]], int(match[2]), match[3].strip())
    if match := _EVENT_RE.match(text):
        return SystemEvent(int(match[1]), int(match[2]), int(match[3]))
    if match := _PGM_RE.match(text):
        return PgmEvent(int(match[1]), match[2] == "ON")
    return Unknown(text)


def reply_prefix(message: Message) -> str | None:
    """Return the command echo a reply belongs to, or ``None`` if unsolicited."""
    if isinstance(message, AreaStatus):
        return f"RA{message.area:03d}"
    if isinstance(message, ZoneStatus):
        return f"RZ{message.zone:03d}"
    if isinstance(message, Label):
        code = {v: k for k, v in LABEL_KINDS.items()}[message.kind]
        return f"{code}{message.index:03d}"
    if isinstance(message, CommandResult):
        return message.prefix
    return None


def _check(value: int, maximum: int, what: str) -> int:
    if not isinstance(value, int) or not 1 <= value <= maximum:
        raise ProtocolError(f"{what} must be between 1 and {maximum}")
    return value


def query_area_status(area: int) -> str:
    """Build ``RA###``."""
    return f"RA{_check(area, MAX_AREAS, 'area'):03d}"


def query_zone_status(zone: int) -> str:
    """Build ``RZ###``."""
    return f"RZ{_check(zone, MAX_ZONES, 'zone'):03d}"


def query_zone_label(zone: int) -> str:
    """Build ``ZL###``."""
    return f"ZL{_check(zone, MAX_ZONES, 'zone'):03d}"


def query_area_label(area: int) -> str:
    """Build ``AL###``."""
    return f"AL{_check(area, MAX_AREAS, 'area'):03d}"


def validate_code(code: str | None) -> str:
    """Return a user code that is safe to place in a command frame."""
    if not code or not code.isascii() or not code.isdigit() or len(code) > 6:
        raise ProtocolError("user code must be 1 to 6 digits")
    return code


def arm_area(area: int, mode: str, code: str | None) -> str:
    """Build ``AA###<mode><code>``."""
    if mode not in (ARM_REGULAR, ARM_FORCE, ARM_STAY, ARM_INSTANT):
        raise ProtocolError(f"unknown arm mode {mode!r}")
    return f"AA{_check(area, MAX_AREAS, 'area'):03d}{mode}{validate_code(code)}"


def disarm_area(area: int, code: str | None) -> str:
    """Build ``AD###<code>``."""
    return f"AD{_check(area, MAX_AREAS, 'area'):03d}{validate_code(code)}"


EVENT_GROUPS = {
    0: "Zone OK",
    1: "Zone open",
    2: "Zone tampered",
    3: "Zone fire loop trouble",
    4: "Non-reportable event",
    5: "User code entered on keypad",
    6: "User/card access on door",
    7: "Bypass programming access",
    8: "TX delay zone alarm",
    9: "Arming with master",
    10: "Arming with user code",
    11: "Arming with keyswitch",
    12: "Special arming",
    13: "Disarm with master",
    14: "Disarm with user code",
    15: "Disarm with keyswitch",
    16: "Disarm after alarm with master",
    17: "Disarm after alarm with user code",
    18: "Disarm after alarm with keyswitch",
    19: "Alarm cancelled with master",
    20: "Alarm cancelled with user code",
    21: "Alarm cancelled with keyswitch",
    22: "Special disarm",
    23: "Zone bypassed",
    24: "Zone in alarm",
    25: "Fire alarm",
    26: "Zone alarm restore",
    27: "Fire alarm restore",
    28: "Early to disarm by user",
    29: "Late to disarm by user",
    30: "Special alarm",
    31: "Duress alarm by user",
    32: "Zone shutdown",
    33: "Zone tamper",
    34: "Zone tamper restore",
    35: "Special tamper",
    36: "Trouble event",
    37: "Trouble restore",
    38: "Module trouble",
    39: "Module trouble restore",
    40: "Fail to communicate on telephone number",
    41: "Low battery on zone",
    42: "Zone supervision trouble",
    43: "Low battery on zone restored",
    44: "Zone supervision trouble restored",
}


# Groups whose event number is a zone, a user code, a keyswitch or a door.
_ZONE_GROUPS = {0, 1, 2, 3, 8, 23, 24, 25, 26, 27, 32, 33, 34, 41, 42, 43, 44}
_USER_GROUPS = {5, 9, 10, 13, 14, 16, 17, 19, 20, 28, 29, 31}
_KEYSWITCH_GROUPS = {11, 15, 18, 21}

_NON_REPORTABLE = {
    0: "TLM trouble",
    1: "Smoke detector reset",
    2: "Arm with no entry delay",
    3: "Arm in stay mode",
    4: "Arm in away mode",
    5: "Full arm when in stay mode",
    6: "Voice module access",
    7: "Remote control access",
    8: "PC fail to communicate",
    9: "Midnight",
    10: "NEware user login",
    11: "NEware user logout",
    12: "User initiated callup",
    13: "Force answer",
    14: "Force hangup",
}
_SPECIAL_ARMING = {
    0: "Auto arming",
    1: "Arming by WinLoad",
    2: "Late to close",
    3: "No movement arming",
    4: "Partial arming",
    5: "One-touch arming",
    8: "Voice module arming",
}
_SPECIAL_DISARM = {
    0: "Auto arm cancelled",
    1: "One-touch stay/instant disarm",
    2: "Disarming with WinLoad",
    3: "Disarming with WinLoad after alarm",
    4: "WinLoad cancelled alarm",
    8: "Voice module disarming",
}
_SPECIAL_ALARM = {
    0: "Emergency panic",
    1: "Medical panic",
    2: "Fire panic",
    3: "Recent closing",
    4: "Police code",
    5: "Global shutdown",
}
_TROUBLE = {
    0: "TLM trouble",
    1: "AC failure",
    2: "Battery failure",
    3: "Auxiliary current limit",
    4: "Bell current limit",
    5: "Bell absent",
    6: "Clock trouble",
    7: "Global fire loop",
}
_MODULE_TROUBLE = {
    0: "Combus fault",
    1: "Module tamper",
    2: "ROM/RAM error",
    3: "TLM trouble",
    4: "Fail to communicate",
    5: "Printer fault",
    6: "AC failure",
    7: "Battery failure",
    8: "Auxiliary failure",
}
_NAMED_NUMBERS = {
    4: _NON_REPORTABLE,
    12: _SPECIAL_ARMING,
    22: _SPECIAL_DISARM,
    30: _SPECIAL_ALARM,
    36: _TROUBLE,
    37: _TROUBLE,
    38: _MODULE_TROUBLE,
    39: _MODULE_TROUBLE,
    35: {0: "Keypad lockout"},
    7: {0: "One-touch bypass programming"},
}


def describe_event(
    event: SystemEvent,
    *,
    zone_label: str | None = None,
    area_label: str | None = None,
) -> str:
    """Return a readable description, e.g. ``Zone open: Kitchen door (zone 4), Ground floor``.

    ``zone_label``/``area_label`` are the names stored in the panel, when known.
    """
    group = EVENT_GROUPS.get(event.group, f"Event group {event.group}")
    number = event.number
    if event.group in _ZONE_GROUPS:
        subject = f"zone {number}" if not zone_label else f"{zone_label} (zone {number})"
    elif event.group in _USER_GROUPS:
        subject = f"user {number}"
    elif event.group in _KEYSWITCH_GROUPS:
        subject = f"keyswitch {number}"
    elif event.group == 6:
        subject = f"door {number}"
    elif event.group == 40:
        subject = f"telephone number {number}"
    elif event.group == 7 and number:
        subject = f"user {number}"
    elif name := _NAMED_NUMBERS.get(event.group, {}).get(number):
        subject = name
    else:
        subject = f"number {number}"
    text = f"{group}: {subject}"
    if event.area:
        text += f", {area_label or f'area {event.area}'}"
    return text
