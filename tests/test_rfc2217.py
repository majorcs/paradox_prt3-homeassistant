"""Tests for the Telnet/RFC2217 transport."""

import asyncio
import struct

import pytest

from custom_components.paradox_prt3.client import PRT3Client, PRT3ConnectionError
from custom_components.paradox_prt3.protocol import AreaStatus
from custom_components.paradox_prt3.rfc2217 import (
    BINARY,
    COM_PORT,
    DO,
    DONT,
    IAC,
    SB,
    SE,
    SGA,
    WILL,
    WONT,
    TelnetSession,
    async_open_rfc2217,
)


def test_initial_offer() -> None:
    session = TelnetSession(9600)
    assert bytes((IAC, WILL, BINARY)) in session.outbox
    assert bytes((IAC, WILL, COM_PORT)) in session.outbox
    assert bytes((IAC, DO, SGA)) in session.outbox


def test_payload_passes_and_iac_is_unescaped() -> None:
    session = TelnetSession(9600)
    assert session.feed(b"RA001\xff\xffX\r") == b"RA001\xffX\r"


def test_negotiation_split_across_chunks() -> None:
    session = TelnetSession(9600)
    session.outbox = b""
    assert session.feed(b"ab\xff") == b"ab"
    assert session.feed(bytes((DO, 1))) == b""  # DO ECHO is refused
    assert session.outbox == bytes((IAC, WONT, 1))


def test_unsupported_will_is_refused_and_supported_accepted() -> None:
    session = TelnetSession(9600)
    session.outbox = b""
    session.feed(bytes((IAC, WILL, 1, IAC, WILL, 24)))
    assert session.outbox == bytes((IAC, DONT, 1, IAC, DONT, 24))


def test_acks_of_our_offers_are_not_answered_again() -> None:
    session = TelnetSession(9600)
    session.outbox = b""
    session.feed(bytes((IAC, DO, BINARY, IAC, WILL, BINARY, IAC, DONT, 9, IAC, WONT, 9)))
    assert session.outbox == b""


def test_server_initiated_options_are_accepted_once() -> None:
    session = TelnetSession(9600)
    session.outbox = b""
    session.feed(bytes((IAC, WILL, 5)))  # not supported
    session.feed(bytes((IAC, 241)))  # NOP
    assert session.outbox == bytes((IAC, DONT, 5))


def test_subnegotiation_content_is_skipped() -> None:
    session = TelnetSession(9600)
    frame = bytes((IAC, SB, COM_PORT, 101, 255, 255, 7, IAC, SE))
    assert session.feed(b"A" + frame + b"B") == b"AB"


def test_do_com_port_configures_line_once() -> None:
    session = TelnetSession(19200)
    session.outbox = b""
    session.feed(bytes((IAC, DO, COM_PORT)))
    baud = bytes((IAC, SB, COM_PORT, 1)) + struct.pack(">I", 19200) + bytes((IAC, SE))
    assert baud in session.outbox
    assert bytes((IAC, SB, COM_PORT, 2, 8, IAC, SE)) in session.outbox
    first = session.outbox
    session.feed(bytes((IAC, DO, COM_PORT)))
    assert session.outbox == first


def test_escape() -> None:
    assert TelnetSession.escape(b"a\xffb") == b"a\xff\xffb"


class FakeRfc2217Server:
    """Answers RA001 like a PRT3 behind a ser2net telnet/remctl port."""

    def __init__(self) -> None:
        self.received = b""
        self.baud: int | None = None
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in self._writers:
            writer.close()
        self._server.close()
        await asyncio.wait_for(self._server.wait_closed(), 2)

    async def _handle(self, reader, writer) -> None:
        self._writers.append(writer)
        writer.write(bytes((IAC, DO, COM_PORT, IAC, WILL, COM_PORT)))
        writer.write(bytes((IAC, WILL, 1)))  # something we must refuse
        buffer = b""
        while chunk := await reader.read(256):
            buffer += chunk
            marker = bytes((IAC, SB, COM_PORT, 1))
            if marker in buffer and self.baud is None:
                at = buffer.index(marker) + len(marker)
                self.baud = struct.unpack(">I", buffer[at : at + 4])[0]
            payload = TelnetSession(0).feed(buffer)
            if b"RA001\r" in payload and b"RA001\r" not in self.received:
                self.received += b"RA001\r"
                writer.write(b"RA001DOOOOOO\r")
                writer.write(bytes((IAC, SB, COM_PORT, 107, 0, IAC, SE)))
        writer.close()


async def test_client_talks_through_rfc2217(socket_enabled) -> None:
    server = FakeRfc2217Server()
    await server.start()
    client = PRT3Client(f"rfc2217://127.0.0.1:{server.port}", 9600, command_delay=0)
    await client.async_connect()
    reply = await client.async_request("RA001")
    assert isinstance(reply, AreaStatus)
    assert server.baud == 9600
    await client.async_disconnect()
    await server.stop()


async def test_reader_sees_eof_when_server_closes(socket_enabled) -> None:
    server = FakeRfc2217Server()
    await server.start()
    reader, writer = await async_open_rfc2217(f"rfc2217://127.0.0.1:{server.port}", 9600)
    writer.write(b"\xff")  # escaped on the wire
    await server.stop()
    writer.close()
    assert await asyncio.wait_for(reader.read(), 2) == b""


@pytest.mark.parametrize("url", ["rfc2217://", "rfc2217://host", "rfc2217://:4000"])
async def test_bad_url_is_a_connection_error(url: str) -> None:
    with pytest.raises(PRT3ConnectionError):
        await PRT3Client(url, 9600).async_connect()


async def test_refused_connection_is_a_connection_error(socket_enabled) -> None:
    with pytest.raises(PRT3ConnectionError):
        await PRT3Client("rfc2217://127.0.0.1:1", 9600).async_connect()
