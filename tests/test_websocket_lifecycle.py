"""
Tests for WebSocketManager.stop() and start(): one connection loop, always.

POST /reconnect reaches the worker as stop() followed straight away by start().
stop() used to do no more than clear a flag and close the current socket. While
the loop was waiting to reconnect there was no socket to close, so it woke up,
found the flag set again by start(), and carried on beside the new loop.

Production ran that way: the second loop subscribed and got "422 Symbols limit
reached" (the 50-symbol allowance belongs to the token, and the first loop held
it), stayed silent and reconnected every 15 minutes -- and because the two share
one set of flags, each of its reconnects made the healthy loop ignore its ticks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.websocket_manager import WebSocketManager


def running_loops():
    """Tasks currently running WebSocketManager._connection_loop."""
    return [
        task for task in asyncio.all_tasks()
        if getattr(task.get_coro(), '__name__', '') == '_connection_loop'
    ]


class FakeConnect:
    """Stands in for websockets.connect: authorizes, then waits for messages."""

    opened = 0
    closed = 0

    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def reset(cls):
        cls.opened = 0
        cls.closed = 0

    async def __aenter__(self):
        type(self).opened += 1
        socket = MagicMock()
        socket.send = AsyncMock()
        socket.close = AsyncMock()

        async def auth_messages():
            yield '{"status_code": 200, "message": "Authorized"}'

        socket.__aiter__ = lambda _: auth_messages()

        async def block_forever():
            await asyncio.Event().wait()

        socket.recv = block_forever
        return socket

    async def __aexit__(self, *args):
        type(self).closed += 1


class RefusedConnect:
    """Stands in for websockets.connect when the provider cannot be reached."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        raise OSError("connection refused")

    async def __aexit__(self, *args):
        pass


class HangingConnect:
    """Stands in for websockets.connect during a handshake that never finishes."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        await asyncio.Event().wait()

    async def __aexit__(self, *args):
        pass


class TestOneConnectionLoop:

    @pytest.fixture(autouse=True)
    def reset_fake(self):
        FakeConnect.reset()

    @pytest.mark.asyncio
    async def test_restart_during_the_reconnect_wait_leaves_one_loop(self):
        """The production case: the request arrives while no socket is open."""
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', RefusedConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            assert manager._ws is None, "the loop should be waiting to reconnect"

            await manager.stop()
            await manager.start()
            await asyncio.sleep(0.05)

            assert len(running_loops()) == 1

            await manager.stop()

    @pytest.mark.asyncio
    async def test_restart_with_an_open_connection_leaves_one_loop(self):
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', FakeConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            assert FakeConnect.opened == 1

            await manager.stop()
            await manager.start()
            await asyncio.sleep(0.05)

            assert len(running_loops()) == 1
            assert FakeConnect.opened == 2
            assert FakeConnect.closed == 1, "the old connection must be gone"

            await manager.stop()

    @pytest.mark.asyncio
    async def test_repeated_restarts_do_not_add_up(self):
        """Pressing the dashboard button again must not make things worse."""
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', RefusedConnect):
            await manager.start()
            for _ in range(5):
                await asyncio.sleep(0.01)
                await manager.stop()
                await manager.start()
            await asyncio.sleep(0.01)

            assert len(running_loops()) == 1

            await manager.stop()

    @pytest.mark.asyncio
    async def test_starting_twice_does_not_add_a_loop(self):
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', RefusedConnect):
            await manager.start()
            await manager.start()
            await asyncio.sleep(0.01)

            assert len(running_loops()) == 1

            await manager.stop()


class TestStop:

    @pytest.fixture(autouse=True)
    def reset_fake(self):
        FakeConnect.reset()

    @pytest.mark.asyncio
    async def test_no_loop_survives_stop(self):
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', RefusedConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            await manager.stop()

        assert running_loops() == []

    @pytest.mark.asyncio
    async def test_stop_ends_a_handshake_in_progress(self):
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', HangingConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            await asyncio.wait_for(manager.stop(), timeout=1)

        assert running_loops() == []

    @pytest.mark.asyncio
    async def test_stop_clears_the_connection_state(self):
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', FakeConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            assert manager.connected

            await manager.stop()

        assert not manager.connected
        assert manager._ws is None
        assert not manager._authorized

    @pytest.mark.asyncio
    async def test_a_failing_close_still_stops_the_loop_and_allows_a_restart(self):
        """Otherwise POST /reconnect would leave the service with no feed at all."""
        manager = WebSocketManager(api_key='test', reconnect_delay=30)

        with patch('src.websocket_manager.websockets.connect', FakeConnect):
            await manager.start()
            await asyncio.sleep(0.05)
            manager._ws.close = AsyncMock(side_effect=RuntimeError("socket is broken"))

            await manager.stop()
            assert running_loops() == []

            await manager.start()
            await asyncio.sleep(0.05)
            assert len(running_loops()) == 1

            await manager.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_harmless(self):
        manager = WebSocketManager(api_key='test')

        await manager.stop()

        assert running_loops() == []
