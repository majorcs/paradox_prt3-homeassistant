"""Tests for the pure protocol codec."""

import pytest

from custom_components.paradox_prt3.protocol import (
    ARM_STAY,
    AreaStatus,
    BufferFull,
    CommandResult,
    CommStatus,
    Label,
    PgmEvent,
    ProtocolError,
    SystemEvent,
    Unknown,
    ZoneStatus,
    arm_area,
    describe_event,
    disarm_area,
    parse_line,
    query_area_label,
    query_area_status,
    query_zone_label,
    query_zone_status,
    reply_prefix,
)


def test_parse_area_status_from_real_panel() -> None:
    msg = parse_line(b"RA004DOONOOO\r")
    assert msg == AreaStatus(4, "D", False, False, True, False, False, False)


def test_parse_area_status_alarm() -> None:
    msg = parse_line(b"RA001AMTNPAS\r")
    assert msg == AreaStatus(1, "A", True, True, True, True, True, True)


def test_parse_zone_status() -> None:
    assert parse_line(b"RZ010COOOO\r") == ZoneStatus(10, "C", False, False, False, False)
    assert parse_line(b"RZ011OAFSL\r") == ZoneStatus(11, "O", True, True, True, True)


def test_parse_labels_use_cp852_and_strip_padding() -> None:
    assert parse_line(b"AL002Als\xa2 szint      \r") == Label("area", 2, "Alsó szint")
    assert parse_line(b"UL003Tartal\x82k        \r") == Label("user", 3, "Tartalék")
    assert parse_line(b"ZL097                \r") == Label("zone", 97, "")


def test_parse_label_with_other_codec() -> None:
    assert parse_line(b"ZL001Caf\xe9\r", codec="latin-1") == Label("zone", 1, "Café")


def test_parse_events() -> None:
    assert parse_line(b"G001N009A001\r") == SystemEvent(1, 9, 1)
    assert parse_line(b"PGM03ON\r") == PgmEvent(3, True)
    assert parse_line(b"PGM30OFF\r") == PgmEvent(30, False)
    assert parse_line(b"COMM&fail\r") == CommStatus(False)
    assert parse_line(b"COMM&ok\r") == CommStatus(True)


def test_parse_results_and_buffer_full() -> None:
    assert parse_line(b"AA001&OK\r") == CommandResult("AA001", True)
    assert parse_line(b"RA009&fail\r") == CommandResult("RA009", False)
    assert parse_line(b"!\r") == BufferFull()


def test_parse_empty_and_unknown() -> None:
    assert parse_line(b"\r") is None
    assert parse_line(b"") is None
    assert parse_line(b"garbage\r") == Unknown("garbage")


def test_reply_prefix() -> None:
    assert reply_prefix(parse_line(b"RA001DOOOOOO")) == "RA001"
    assert reply_prefix(parse_line(b"RZ010COOOO")) == "RZ010"
    assert reply_prefix(parse_line(b"ZL002Foo")) == "ZL002"
    assert reply_prefix(parse_line(b"AA001&OK")) == "AA001"
    assert reply_prefix(SystemEvent(1, 2, 3)) is None


def test_builders() -> None:
    assert query_area_status(1) == "RA001"
    assert query_zone_status(192) == "RZ192"
    assert query_zone_label(5) == "ZL005"
    assert query_area_label(8) == "AL008"
    assert arm_area(2, ARM_STAY, "4711") == "AA002S4711"
    assert disarm_area(3, "12") == "AD00312"


@pytest.mark.parametrize("builder", [query_area_status, query_area_label])
@pytest.mark.parametrize("value", [0, 9, -1])
def test_area_range_checked(builder, value: int) -> None:
    with pytest.raises(ProtocolError):
        builder(value)


def test_zone_range_checked() -> None:
    with pytest.raises(ProtocolError):
        query_zone_status(193)


@pytest.mark.parametrize("code", [None, "", "4711567", "12a4", "12\r34", "١٢٣"])
def test_bad_codes_rejected(code) -> None:
    with pytest.raises(ProtocolError):
        disarm_area(1, code)


def test_bad_arm_mode_rejected() -> None:
    with pytest.raises(ProtocolError):
        arm_area(1, "X", "4711")


def test_describe_event() -> None:
    assert "Zone open" in describe_event(SystemEvent(1, 9, 1))
    assert "Event group 99" in describe_event(SystemEvent(99, 1, 1))
