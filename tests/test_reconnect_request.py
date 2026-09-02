"""
Tests for operator-triggered reconnect requests in multi-worker mode.

API workers hold a dummy WebSocketManager and cannot reconnect the live feed.
POST /reconnect therefore records a request in the database and the WebSocket
worker carries it out. These tests cover both halves of that handover.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.storage import Storage
from src.websocket_worker import reconnect_request_task


class TestReconnectRequestStorage:
    """The database side of the handover."""

    @pytest.fixture
    def storage(self):
        return Storage(':memory:')

    def test_no_request_initially(self, storage):
        assert storage.get_websocket_reconnect_request() is None

    def test_request_is_recorded_and_readable(self, storage):
        requested_at = storage.request_websocket_reconnect()

        assert requested_at
        assert storage.get_websocket_reconnect_request() == requested_at

    def test_later_request_replaces_earlier(self, storage):
        """One row per key — the newest request wins, the table does not grow."""
        first = storage.request_websocket_reconnect()
        second = storage.request_websocket_reconnect()

        assert second >= first
        assert storage.get_websocket_reconnect_request() == second


class TestReconnectRequestTask:
    """The worker side of the handover."""

    @pytest.fixture
    def storage(self, tmp_path):
        # A file-backed database, not ':memory:' — the task reads through
        # asyncio.to_thread, and each thread opens its own connection, so an
        # in-memory database would be empty in the worker thread.
        return Storage(str(tmp_path / 'reconnect_test.db'))

    @pytest.mark.asyncio
    async def test_new_request_restarts_the_feed(self, storage):
        ws_manager = AsyncMock()

        # Start the task first, so the request that follows is genuinely new.
        task = asyncio.create_task(
            reconnect_request_task(storage, ws_manager, poll_interval=0)
        )
        await asyncio.sleep(0.05)
        assert ws_manager.start.await_count == 0, "Reconnected without a request"

        storage.request_websocket_reconnect()
        await asyncio.sleep(0.05)

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert ws_manager.stop.await_count >= 1
        assert ws_manager.start.await_count >= 1

    @pytest.mark.asyncio
    async def test_preexisting_request_is_not_replayed(self, storage):
        """
        A request recorded before the worker started must not trigger a
        reconnect on every restart.
        """
        storage.request_websocket_reconnect()

        async def run_briefly():
            task = asyncio.create_task(
                reconnect_request_task(storage, ws_manager, poll_interval=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        ws_manager = AsyncMock()
        # The task reads the stored value at startup and treats it as handled.
        await run_briefly()

        assert ws_manager.stop.await_count == 0
        assert ws_manager.start.await_count == 0

    @pytest.mark.asyncio
    async def test_storage_error_does_not_kill_the_task(self, storage):
        """A database hiccup must be logged and survived, not end the task."""
        ws_manager = AsyncMock()
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("database temporarily unavailable")
            return None

        with patch.object(storage, 'get_websocket_reconnect_request', flaky):
            task = asyncio.create_task(
                reconnect_request_task(storage, ws_manager, poll_interval=0)
            )
            await asyncio.sleep(0.05)
            still_running = not task.done()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert still_running, "Task died on a transient storage error"
        assert len(calls) > 2, "Task stopped polling after the error"
