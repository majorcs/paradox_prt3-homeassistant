"""Tests for the async PRT3 client."""

import asyncio

import pytest

from custom_components.paradox_prt3.client import (
    PRT3Client,
    PRT3CommandError,
    PRT3ConnectionError,
    PRT3TimeoutError,
)
from custom_components.paradox_prt3.protocol import AreaStatus, CommandResult, SystemEvent

from .conftest import FakePanel


def make_client(panel: FakePanel, **kwargs) -> PRT3Client:
    async def opener():
        return panel.reader, panel.writer

    return PRT3Client("test", 57600, opener=opener, **kwargs)


async def test_request_returns_reply(panel: FakePanel) -> None:
    client = make_client(panel)
    await client.async_connect()
    reply = await client.async_request("RA001")
    assert isinstance(reply, AreaStatus)
    assert reply.area == 1
    await client.async_disconnect()
    assert panel.writer.closed
    assert not client.connected


async def test_fail_raises_command_error(panel: FakePanel) -> None:
    client = make_client(panel)
    await client.async_connect()
    with pytest.raises(PRT3CommandError):
        await client.async_request("RA009")
    await client.async_disconnect()


async def test_timeout(panel: FakePanel) -> None:
    client = make_client(panel, timeout=0.05)
    await client.async_connect()
    panel.silent = True
    with pytest.raises(PRT3TimeoutError):
        await client.async_request("RA001")
    await client.async_disconnect()


async def test_buffer_full_is_retried(panel: FakePanel, monkeypatch) -> None:
    client = make_client(panel)
    await client.async_connect()
    panel.buffer_full = 2
    assert isinstance(await client.async_request("RA001"), AreaStatus)
    assert panel.commands == ["RA001"] * 3
    await client.async_disconnect()


async def test_buffer_full_gives_up(panel: FakePanel) -> None:
    client = make_client(panel)
    await client.async_connect()
    panel.buffer_full = 10
    with pytest.raises(PRT3CommandError):
        await client.async_request("RA001")
    await client.async_disconnect()


async def test_not_connected_raises(panel: FakePanel) -> None:
    client = make_client(panel)
    with pytest.raises(PRT3ConnectionError):
        await client.async_request("RA001")


async def test_ok_result_is_returned(panel: FakePanel) -> None:
    client = make_client(panel)
    await client.async_connect()
    reply = await client.async_request("AA001A4711")
    assert reply == CommandResult("AA001", True)
    await client.async_disconnect()


async def test_unsolicited_messages_reach_listeners(panel: FakePanel) -> None:
    client = make_client(panel)
    seen = []
    unsubscribe = client.add_message_listener(seen.append)
    await client.async_connect()
    panel.push("G001N009A001")
    panel.push("")  # blank line is ignored
    panel.push("!")  # stray buffer-full frame is ignored
    await asyncio.sleep(0.05)
    assert seen == [SystemEvent(1, 9, 1)]
    unsubscribe()
    panel.push("G000N009A001")
    await asyncio.sleep(0.05)
    assert len(seen) == 1
    await client.async_disconnect()


async def test_event_during_request_is_not_swallowed(panel: FakePanel) -> None:
    client = make_client(panel)
    seen = []
    client.add_message_listener(seen.append)
    await client.async_connect()
    panel.push("G001N002A001")
    assert isinstance(await client.async_request("RA001"), AreaStatus)
    assert seen == [SystemEvent(1, 2, 1)]
    await client.async_disconnect()


async def test_reconnects_after_connection_loss(panel: FakePanel, monkeypatch) -> None:
    import custom_components.paradox_prt3.client as module

    monkeypatch.setattr(module.asyncio, "sleep", _fast_sleep(module.asyncio.sleep))
    readers = [panel.reader]
    attempts = 0

    async def opener():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise PRT3ConnectionError("still down")
        if attempts >= 3:
            readers.append(asyncio.StreamReader())
        return readers[-1], panel.writer

    client = PRT3Client("test", 57600, opener=opener)
    states = []
    client.add_connection_listener(states.append)
    await client.async_connect()
    panel.reader.feed_eof()
    for _ in range(100):
        await asyncio.sleep(0)
        if attempts >= 3 and client.connected:
            break
    assert states == [True, False, True]
    assert attempts == 3
    await client.async_disconnect()


def _fast_sleep(real_sleep):
    async def sleep(_delay):
        await real_sleep(0)

    return sleep


async def test_default_opener_wraps_serial_errors() -> None:
    client = PRT3Client("/dev/does-not-exist", 57600)
    with pytest.raises(PRT3ConnectionError):
        await client.async_connect()


async def test_query_is_retried_after_lost_frame(panel: FakePanel) -> None:
    client = make_client(panel, timeout=0.05)
    await client.async_connect()
    panel.silent = True
    task = asyncio.create_task(client.async_request("RA001"))
    await asyncio.sleep(0.02)
    panel.silent = False
    assert isinstance(await task, AreaStatus)
    assert panel.commands == ["RA001", "RA001"]
    await client.async_disconnect()


async def test_arm_command_is_not_retried(panel: FakePanel) -> None:
    client = make_client(panel, timeout=0.05)
    await client.async_connect()
    panel.silent = True
    with pytest.raises(PRT3TimeoutError):
        await client.async_request("AA001A4711")
    assert panel.commands == ["AA001A4711"]
    await client.async_disconnect()
