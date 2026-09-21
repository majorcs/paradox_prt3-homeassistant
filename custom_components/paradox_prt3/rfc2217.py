"""Minimal Telnet/RFC2217 transport (serial port over TCP).

pyserial's ``rfc2217://`` handler cannot be used with asyncio serial
transports (it rejects ``write_timeout``), so this module speaks just enough
Telnet and RFC2217 for a ser2net ``telnet``/``remctl`` port: binary mode,
suppress-go-ahead and the COM-PORT option used to set the line parameters.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
BINARY, SGA, COM_PORT = 0, 3, 44

# COM-PORT-OPTION client-to-server commands (RFC2217).
SET_BAUDRATE, SET_DATASIZE, SET_PARITY, SET_STOPSIZE = 1, 2, 3, 4
PARITY_NONE = 1
STOP_ONE = 1

CONNECT_TIMEOUT = 5.0
_SUPPORTED = {BINARY, SGA}

_DATA, _GOT_IAC, _GOT_OPTION, _IN_SB, _IN_SB_IAC = range(5)


class TelnetSession:
    """Sans-IO Telnet parser/negotiator for a raw serial-over-TCP link."""

    def __init__(self, baudrate: int) -> None:
        self._baudrate = baudrate
        self._state = _DATA
        self._verb = 0
        self._sent_will = {BINARY, SGA, COM_PORT}
        self._sent_do = {BINARY, SGA}
        self._com_port_configured = False
        self.outbox = b"".join(
            bytes((IAC, verb, option))
            for verb, options in ((WILL, self._sent_will), (DO, self._sent_do))
            for option in sorted(options)
        )

    @staticmethod
    def escape(data: bytes) -> bytes:
        """Double every IAC byte in outgoing data."""
        return data.replace(b"\xff", b"\xff\xff")

    def feed(self, chunk: bytes) -> bytes:
        """Consume received bytes; return payload, queue replies in ``outbox``."""
        payload = bytearray()
        for byte in chunk:
            if self._state == _DATA:
                if byte == IAC:
                    self._state = _GOT_IAC
                else:
                    payload.append(byte)
            elif self._state == _GOT_IAC:
                if byte == IAC:
                    payload.append(IAC)
                    self._state = _DATA
                elif byte in (DO, DONT, WILL, WONT):
                    self._verb = byte
                    self._state = _GOT_OPTION
                elif byte == SB:
                    self._state = _IN_SB
                else:  # NOP, GA and friends carry no data
                    self._state = _DATA
            elif self._state == _GOT_OPTION:
                self._negotiate(self._verb, byte)
                self._state = _DATA
            elif self._state == _IN_SB:
                if byte == IAC:
                    self._state = _IN_SB_IAC
            else:  # _IN_SB_IAC
                # IAC SE ends the subnegotiation, IAC IAC is an escaped data byte.
                self._state = _DATA if byte == SE else _IN_SB
        return bytes(payload)

    def _reply(self, verb: int, option: int) -> None:
        self.outbox += bytes((IAC, verb, option))

    def _negotiate(self, verb: int, option: int) -> None:
        if verb == DO:
            if option in self._sent_will:
                if option == COM_PORT:
                    self._configure_com_port()
            elif option in _SUPPORTED:
                self._sent_will.add(option)
                self._reply(WILL, option)
            else:
                self._reply(WONT, option)
        elif verb == WILL:
            if option in self._sent_do:
                return
            if option in _SUPPORTED:
                self._sent_do.add(option)
                self._reply(DO, option)
            else:
                self._reply(DONT, option)
        # DONT/WONT need no answer.

    def _subnegotiate(self, *body: int, value: bytes = b"") -> None:
        data = bytes((COM_PORT, *body)) + value
        self.outbox += bytes((IAC, SB)) + self.escape(data) + bytes((IAC, SE))

    def _configure_com_port(self) -> None:
        if self._com_port_configured:
            return
        self._com_port_configured = True
        self._subnegotiate(SET_BAUDRATE, value=struct.pack(">I", self._baudrate))
        self._subnegotiate(SET_DATASIZE, value=bytes((8,)))
        self._subnegotiate(SET_PARITY, value=bytes((PARITY_NONE,)))
        self._subnegotiate(SET_STOPSIZE, value=bytes((STOP_ONE,)))


class RFC2217Writer:
    """StreamWriter look-alike that escapes IAC and stops the pump on close."""

    def __init__(
        self, writer: asyncio.StreamWriter, pump: asyncio.Task[None]
    ) -> None:
        self._writer = writer
        self._pump = pump

    def write(self, data: bytes) -> None:
        """Send payload bytes to the serial port."""
        self._writer.write(TelnetSession.escape(data))

    def close(self) -> None:
        """Close the TCP connection."""
        self._pump.cancel()
        self._writer.close()


async def async_open_rfc2217(
    url: str, baudrate: int
) -> tuple[asyncio.StreamReader, RFC2217Writer]:
    """Open ``rfc2217://host:port`` and return a payload-only reader and a writer."""
    parts = urlsplit(url)
    if not parts.hostname or not parts.port:
        raise OSError(f"{url!r} needs a host and a port")
    raw_reader, raw_writer = await asyncio.wait_for(
        asyncio.open_connection(parts.hostname, parts.port), CONNECT_TIMEOUT
    )
    session = TelnetSession(baudrate)
    raw_writer.write(session.outbox)
    session.outbox = b""

    reader = asyncio.StreamReader()
    pump = asyncio.create_task(_pump(raw_reader, raw_writer, session, reader))
    return reader, RFC2217Writer(raw_writer, pump)


async def _pump(
    raw_reader: asyncio.StreamReader,
    raw_writer: asyncio.StreamWriter,
    session: TelnetSession,
    reader: asyncio.StreamReader,
) -> None:
    try:
        while chunk := await raw_reader.read(1024):
            payload = session.feed(chunk)
            if session.outbox:
                raw_writer.write(session.outbox)
                session.outbox = b""
            if payload:
                reader.feed_data(payload)
    except OSError as err:
        _LOGGER.debug("RFC2217 pump ended: %r", err)
    finally:
        reader.feed_eof()
