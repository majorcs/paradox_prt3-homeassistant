"""Async client for the PRT3 ASCII serial protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

from .rfc2217 import async_open_rfc2217
from .protocol import (
    CR,
    DEFAULT_CODEC,
    BufferFull,
    CommandResult,
    Message,
    parse_line,
    reply_prefix,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0
COMMAND_DELAY = 0.1  # the PRT3 drops commands sent faster than this
BUFFER_FULL_RETRIES = 3
BUFFER_FULL_BACKOFF = 1.0
RECONNECT_MAX_DELAY = 60.0
# Read-only queries are safe to repeat if a frame was lost; arm/disarm are not.
IDEMPOTENT_PREFIXES = frozenset({"RA", "RZ", "ZL", "AL", "UL"})
TIMEOUT_RETRIES = 1

Opener = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class PRT3Error(Exception):
    """Base error of the PRT3 client."""


class PRT3ConnectionError(PRT3Error):
    """The serial connection is not available."""


class PRT3TimeoutError(PRT3Error):
    """The PRT3 did not answer in time."""


class PRT3CommandError(PRT3Error):
    """The PRT3 answered ``&fail`` (or its buffer stayed full)."""


class PRT3Client:
    """Serialises commands and fans unsolicited messages out to listeners."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        codec: str = DEFAULT_CODEC,
        *,
        opener: Opener | None = None,
        command_delay: float | None = None,
        timeout: float | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._codec = codec
        self._opener = opener or self._open_serial
        self._command_delay = COMMAND_DELAY if command_delay is None else command_delay
        self._timeout = DEFAULT_TIMEOUT if timeout is None else timeout

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[str, asyncio.Future[Message]] | None = None
        self._last_write = 0.0
        self._closing = False
        self._message_listeners: list[Callable[[Message], None]] = []
        self._connection_listeners: list[Callable[[bool], None]] = []
        self.connected = False

    async def _open_serial(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._port.lower().startswith("rfc2217://"):
            try:
                return await async_open_rfc2217(self._port, self._baudrate)
            except (OSError, TimeoutError) as err:
                raise PRT3ConnectionError(f"Cannot open {self._port}: {err}") from err

        # Imported lazily so the pure protocol code stays importable everywhere.
        import serial  # noqa: PLC0415
        import serial_asyncio_fast  # noqa: PLC0415

        try:
            return await serial_asyncio_fast.open_serial_connection(
                url=self._port, baudrate=self._baudrate
            )
        except (OSError, serial.SerialException) as err:
            raise PRT3ConnectionError(f"Cannot open {self._port}: {err}") from err

    def add_message_listener(self, listener: Callable[[Message], None]) -> Callable[[], None]:
        """Register a callback for unsolicited messages; returns an unsubscriber."""
        self._message_listeners.append(listener)
        return lambda: self._message_listeners.remove(listener)

    def add_connection_listener(self, listener: Callable[[bool], None]) -> Callable[[], None]:
        """Register a callback for connection state changes."""
        self._connection_listeners.append(listener)
        return lambda: self._connection_listeners.remove(listener)

    async def async_connect(self) -> None:
        """Open the connection and keep it alive in the background."""
        self._closing = False
        await self._open()
        self._supervisor = asyncio.create_task(self._supervise())

    async def async_disconnect(self) -> None:
        """Close the connection and stop reconnecting."""
        self._closing = True
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
        self._close_transport()
        self._set_connected(False)

    async def _open(self) -> None:
        self._reader, self._writer = await self._opener()
        self._set_connected(True)

    def _close_transport(self) -> None:
        if self._writer:
            self._writer.close()
        self._reader = self._writer = None

    def _set_connected(self, connected: bool) -> None:
        if self.connected == connected:
            return
        self.connected = connected
        for listener in list(self._connection_listeners):
            listener(connected)

    async def _supervise(self) -> None:
        delay = 1.0
        while not self._closing:
            await self._read_loop()
            self._close_transport()
            self._set_connected(False)
            self._fail_pending(PRT3ConnectionError("Connection lost"))
            while not self._closing:
                await asyncio.sleep(delay)
                try:
                    await self._open()
                except PRT3ConnectionError as err:
                    _LOGGER.debug("Reconnect failed: %s", err)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)
                else:
                    delay = 1.0
                    break

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                raw = await self._reader.readuntil(CR)
                self._dispatch(parse_line(raw, self._codec))
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError) as err:
            _LOGGER.debug("Read loop ended: %r", err)

    def _dispatch(self, message: Message | None) -> None:
        if message is None:
            return
        if self._pending is not None:
            prefix, future = self._pending
            if isinstance(message, BufferFull) or reply_prefix(message) == prefix:
                if not future.done():
                    future.set_result(message)
                return
        if isinstance(message, BufferFull):
            return
        for listener in list(self._message_listeners):
            listener(message)

    def _fail_pending(self, err: Exception) -> None:
        if self._pending and not self._pending[1].done():
            self._pending[1].set_exception(err)

    async def async_request(self, command: str) -> Message:
        """Send a command and return the reply carrying its echo."""
        async with self._lock:
            timeouts_left = TIMEOUT_RETRIES if command[:2] in IDEMPOTENT_PREFIXES else 0
            for attempt in range(BUFFER_FULL_RETRIES):
                try:
                    reply = await self._send_once(command)
                except PRT3TimeoutError:
                    if not timeouts_left:
                        raise
                    timeouts_left -= 1
                    _LOGGER.debug("No answer to %s, retrying", command[:5])
                    continue
                if isinstance(reply, BufferFull):
                    _LOGGER.debug("PRT3 buffer full for %s (attempt %d)", command, attempt + 1)
                    await asyncio.sleep(BUFFER_FULL_BACKOFF)
                    continue
                if isinstance(reply, CommandResult) and not reply.ok:
                    raise PRT3CommandError(f"{command[:5]} rejected by the PRT3")
                return reply
            raise PRT3CommandError(f"{command[:5]}: PRT3 buffer stayed full")

    async def _send_once(self, command: str) -> Message:
        writer = self._writer
        if not self.connected or writer is None:
            raise PRT3ConnectionError("Not connected")

        loop = asyncio.get_running_loop()
        wait = self._last_write + self._command_delay - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)

        future: asyncio.Future[Message] = loop.create_future()
        self._pending = (command[:5], future)
        try:
            writer.write(command.encode("ascii") + CR)
            self._last_write = loop.time()
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as err:
            raise PRT3TimeoutError(f"No answer to {command[:5]}") from err
        finally:
            self._pending = None
