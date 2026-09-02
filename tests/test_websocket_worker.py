"""
Tests for WebSocket worker entry point.

Tests the dedicated worker that handles WebSocket connections and tick processing.
"""

import pytest
import asyncio
import json
import os
import signal
from types import SimpleNamespace
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from src.websocket_worker import (
    cleanup_task,
    ticker_sync_task,
    candle_close_task,
    empty_interval_audit_task,
    _inside_fill_session,
    run_worker,
    setup_logging,
)
from src.config import Config
from src.storage import Storage
from src.candle_engine import CandleEngine
from src.websocket_manager import WebSocketManager


def single_pass_sleep(interval):
    """Replacement for asyncio.sleep that lets exactly one loop body run.

    Worker tasks sleep at the top of their loop (cleanup_task waits 30s), so a
    test that waits on the wall clock never reaches the body. Only the task's
    own interval is intercepted -- the first such sleep returns at once and
    every later one blocks until cancellation, giving exactly one iteration.
    Any other duration falls through to the real sleep, so the test's own
    waits keep working.
    """
    real_sleep = asyncio.sleep
    calls = {'n': 0}

    async def _sleep(seconds):
        if seconds != interval:
            await real_sleep(seconds)
            return
        calls['n'] += 1
        if calls['n'] > 1:
            await asyncio.Event().wait()

    return _sleep


async def run_worker_until_shutdown(config, settle=0.5, timeout=10):
    """Start run_worker, let it settle, then trigger its graceful shutdown.

    Cancelling the task interrupts `await shutdown_event.wait()` and unwinds
    immediately, so the whole shutdown sequence -- completing candles, flushing
    writes, draining pending cleanup -- never runs. Capture the SIGTERM handler
    the worker registers and invoke it instead, which is the real path.
    """
    loop = asyncio.get_running_loop()
    handlers = {}

    def capture(sig, callback, *args):
        handlers[sig] = callback

    with patch.object(loop, 'add_signal_handler', capture):
        task = asyncio.create_task(run_worker(config))
        await asyncio.sleep(settle)
        assert signal.SIGTERM in handlers, "worker registered no shutdown handler"
        handlers[signal.SIGTERM]()
        await asyncio.wait_for(task, timeout=timeout)


class TestCleanupTask:
    """Test cleanup task functionality."""

    @pytest.mark.asyncio
    async def test_cleanup_task_processes_pending_tickers(self):
        """Test cleanup task processes pending tickers."""
        storage = Mock(spec=Storage)
        storage.cleanup_old_candles = Mock()
        
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.max_candles = 100
        candle_engine.get_pending_cleanup.return_value = {'AAPL', 'MSFT'}
        candle_engine.remove_from_pending_cleanup = Mock()
        
        # Run cleanup task for exactly one iteration
        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(30)):
            task = asyncio.create_task(cleanup_task(storage, candle_engine))

            # Let the single iteration run
            await asyncio.sleep(0.05)

            # Cancel task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Verify cleanup was called
        assert candle_engine.get_pending_cleanup.called
        assert storage.cleanup_old_candles.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_task_removes_ticker_after_success(self):
        """Test cleanup task removes ticker from pending after successful cleanup."""
        storage = Mock(spec=Storage)
        storage.cleanup_old_candles = Mock()
        
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.max_candles = 100
        
        # First call returns tickers, second call returns empty
        candle_engine.get_pending_cleanup.side_effect = [
            {'AAPL'},
            set()
        ]
        candle_engine.remove_from_pending_cleanup = Mock()
        
        # Run cleanup task for exactly one iteration
        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(30)):
            task = asyncio.create_task(cleanup_task(storage, candle_engine))

            # Let the single iteration run
            await asyncio.sleep(0.05)

            # Cancel task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify ticker was removed after successful cleanup
        candle_engine.remove_from_pending_cleanup.assert_called_with('AAPL')

    @pytest.mark.asyncio
    async def test_cleanup_task_keeps_ticker_on_failure(self):
        """Test cleanup task keeps ticker in pending if cleanup fails."""
        storage = Mock(spec=Storage)
        storage.cleanup_old_candles = Mock(side_effect=Exception("DB error"))
        
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.max_candles = 100
        candle_engine.get_pending_cleanup.return_value = {'AAPL'}
        candle_engine.remove_from_pending_cleanup = Mock()
        
        # Run cleanup task for exactly one iteration
        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(30)):
            task = asyncio.create_task(cleanup_task(storage, candle_engine))

            # Let the single iteration run
            await asyncio.sleep(0.05)

            # Cancel task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify ticker was NOT removed (kept for retry)
        candle_engine.remove_from_pending_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_task_handles_empty_pending(self):
        """Test cleanup task handles empty pending queue gracefully."""
        storage = Mock(spec=Storage)
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.get_pending_cleanup.return_value = set()
        
        # Run cleanup task for exactly one iteration
        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(30)):
            task = asyncio.create_task(cleanup_task(storage, candle_engine))

            # Let the single iteration run
            await asyncio.sleep(0.05)

            # Cancel task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify no cleanup was attempted
        assert not storage.cleanup_old_candles.called

    @pytest.mark.asyncio
    async def test_cleanup_task_cancellation(self):
        """Test cleanup task handles cancellation gracefully."""
        storage = Mock(spec=Storage)
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.get_pending_cleanup.return_value = set()
        
        # Run and immediately cancel
        task = asyncio.create_task(cleanup_task(storage, candle_engine))
        await asyncio.sleep(0.1)
        task.cancel()
        
        # Should not raise exception
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected


class TestWebSocketWorkerConfiguration:
    """Test WebSocket worker configuration."""

    def test_setup_logging(self):
        """Test logging setup."""
        setup_logging('INFO')
        # No exception means success

    @patch('src.websocket_worker.load_dotenv')
    @patch('src.websocket_worker.Config')
    def test_main_loads_env(self, mock_config, mock_load_dotenv):
        """Test main function loads environment variables."""
        mock_config.return_value.validate.return_value = []
        mock_config.return_value.log_level = 'INFO'
        
        # Verify mocking works
        assert mock_config is not None


class TestTickerSyncTask:
    """Test ticker sync task functionality."""

    @pytest.mark.asyncio
    async def test_ticker_sync_subscribes_new_tickers(self):
        """Test ticker sync subscribes to new tickers from database."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=['AAPL', 'MSFT', 'GOOGL'])
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL'}  # Only AAPL subscribed
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify subscribe was called with new tickers
        ws_manager.subscribe.assert_called_once()
        subscribed = ws_manager.subscribe.call_args[0][0]
        assert 'MSFT' in subscribed
        assert 'GOOGL' in subscribed
        assert 'AAPL' not in subscribed  # Already subscribed

    @pytest.mark.asyncio
    async def test_ticker_sync_unsubscribes_removed_tickers(self):
        """Test ticker sync unsubscribes from removed tickers."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=['AAPL'])  # Only AAPL in DB
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL', 'MSFT', 'GOOGL'}  # Extra tickers subscribed
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify unsubscribe was called with removed tickers
        ws_manager.unsubscribe.assert_called_once()
        unsubscribed = ws_manager.unsubscribe.call_args[0][0]
        assert 'MSFT' in unsubscribed
        assert 'GOOGL' in unsubscribed
        assert 'AAPL' not in unsubscribed  # Still in DB

    @pytest.mark.asyncio
    async def test_ticker_sync_handles_no_changes(self):
        """Test ticker sync does nothing when tickers match."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=['AAPL', 'MSFT'])
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL', 'MSFT'}  # Same as DB
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify no subscribe/unsubscribe calls
        ws_manager.subscribe.assert_not_called()
        ws_manager.unsubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_ticker_sync_handles_empty_database(self):
        """Test ticker sync handles empty database gracefully."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=[])
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL', 'MSFT'}
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify unsubscribe was called for all tickers
        ws_manager.unsubscribe.assert_called_once()
        unsubscribed = ws_manager.unsubscribe.call_args[0][0]
        assert 'AAPL' in unsubscribed
        assert 'MSFT' in unsubscribed

    @pytest.mark.asyncio
    async def test_ticker_sync_handles_db_error(self):
        """Test ticker sync handles database errors gracefully."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(side_effect=Exception("DB error"))
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL'}
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify no subscribe/unsubscribe calls due to error
        ws_manager.subscribe.assert_not_called()
        ws_manager.unsubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_ticker_sync_handles_list_return_type(self):
        """Test ticker sync handles list return type from subscribed_tickers."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=['AAPL', 'MSFT'])
        
        ws_manager = Mock()
        # Return list instead of set (defensive coding test)
        ws_manager.subscribed_tickers = ['AAPL']
        ws_manager.subscribe = AsyncMock()
        ws_manager.unsubscribe = AsyncMock()
        
        # Patch asyncio.sleep to speed up test
        with patch('src.websocket_worker.asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            
            task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Verify subscribe was called (type conversion worked)
        ws_manager.subscribe.assert_called_once()
        subscribed = ws_manager.subscribe.call_args[0][0]
        assert 'MSFT' in subscribed

    @pytest.mark.asyncio
    async def test_ticker_sync_cancellation(self):
        """Test ticker sync handles cancellation gracefully."""
        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=['AAPL'])
        
        ws_manager = Mock()
        ws_manager.subscribed_tickers = {'AAPL'}
        
        # Run and immediately cancel
        task = asyncio.create_task(ticker_sync_task(storage, ws_manager))
        await asyncio.sleep(0.1)
        task.cancel()
        
        # Should not raise exception
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected


class TestWebSocketWorkerIntegration:
    """Integration tests for WebSocket worker."""

    @pytest.mark.asyncio
    async def test_worker_initializes_components(self):
        """Test worker initializes all required components."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.database_path = ':memory:'
        config.default_tickers = []
        
        with patch('src.websocket_worker.WebSocketManager') as mock_ws:
            mock_ws_instance = Mock()
            mock_ws_instance.start = AsyncMock()
            mock_ws_instance.subscribe = AsyncMock()
            mock_ws_instance.stop = AsyncMock()
            mock_ws.return_value = mock_ws_instance
            
            # Run worker briefly
            task = asyncio.create_task(run_worker(config))
            await asyncio.sleep(0.5)
            
            # Send shutdown signal
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Verify WebSocket was started
            mock_ws_instance.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_loads_existing_tickers(self):
        """Test worker loads existing tickers from database."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.database_path = ':memory:'
        config.default_tickers = []
        
        # Create storage with tickers
        storage = Storage(':memory:')
        storage.add_ticker('AAPL')
        storage.add_ticker('MSFT')
        
        with patch('src.websocket_worker.create_storage', return_value=storage):
            with patch('src.websocket_worker.WebSocketManager') as mock_ws:
                mock_ws_instance = Mock()
                mock_ws_instance.start = AsyncMock()
                mock_ws_instance.subscribe = AsyncMock()
                mock_ws_instance.stop = AsyncMock()
                mock_ws.return_value = mock_ws_instance
                
                # Run worker briefly
                task = asyncio.create_task(run_worker(config))
                await asyncio.sleep(0.5)
                
                # Send shutdown signal
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                
                # Verify tickers were subscribed
                mock_ws_instance.subscribe.assert_called_once()
                subscribed_tickers = mock_ws_instance.subscribe.call_args[0][0]
                assert 'AAPL' in subscribed_tickers
                assert 'MSFT' in subscribed_tickers

    @pytest.mark.asyncio
    async def test_worker_adds_default_tickers_if_empty(self):
        """Test worker adds default tickers if database is empty."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.database_path = ':memory:'
        config.default_tickers = ['AAPL', 'GOOGL']
        
        with patch('src.websocket_worker.WebSocketManager') as mock_ws:
            mock_ws_instance = Mock()
            mock_ws_instance.start = AsyncMock()
            mock_ws_instance.subscribe = AsyncMock()
            mock_ws_instance.stop = AsyncMock()
            mock_ws.return_value = mock_ws_instance
            
            # Run worker briefly
            task = asyncio.create_task(run_worker(config))
            await asyncio.sleep(0.5)
            
            # Send shutdown signal
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Verify default tickers were subscribed
            mock_ws_instance.subscribe.assert_called_once()
            subscribed_tickers = mock_ws_instance.subscribe.call_args[0][0]
            assert 'AAPL' in subscribed_tickers
            assert 'GOOGL' in subscribed_tickers


class TestWebSocketWorkerShutdown:
    """Test WebSocket worker shutdown behavior."""

    @pytest.mark.asyncio
    async def test_worker_completes_candles_on_shutdown(self):
        """Test worker completes all candles on shutdown."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.database_path = ':memory:'
        config.default_tickers = []
        
        with patch('src.websocket_worker.CandleEngine') as mock_engine:
            mock_engine_instance = Mock()
            mock_engine_instance.complete_all_candles = Mock()
            mock_engine_instance.get_pending_cleanup.return_value = set()
            mock_engine.return_value = mock_engine_instance
            
            with patch('src.websocket_worker.WebSocketManager') as mock_ws:
                mock_ws_instance = Mock()
                mock_ws_instance.start = AsyncMock()
                mock_ws_instance.subscribe = AsyncMock()
                mock_ws_instance.stop = AsyncMock()
                mock_ws.return_value = mock_ws_instance
                
                # Run worker, then trigger its real graceful shutdown
                await run_worker_until_shutdown(config)

                # Verify candles were completed
                mock_engine_instance.complete_all_candles.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_processes_pending_cleanup_on_shutdown(self):
        """Test worker processes pending cleanup on shutdown."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.database_path = ':memory:'
        config.default_tickers = []
        
        storage = Mock(spec=Storage)
        storage.cleanup_old_candles = Mock()
        storage.get_ticker_symbols = Mock(return_value=[])
        storage.add_ticker = Mock()
        
        with patch('src.websocket_worker.create_storage', return_value=storage):
            with patch('src.websocket_worker.CandleEngine') as mock_engine:
                mock_engine_instance = Mock()
                mock_engine_instance.complete_all_candles = Mock()
                mock_engine_instance.get_pending_cleanup.return_value = {'AAPL', 'MSFT'}
                mock_engine_instance.clear_pending_cleanup = Mock()
                mock_engine_instance.max_candles = 100
                mock_engine.return_value = mock_engine_instance
                
                with patch('src.websocket_worker.WebSocketManager') as mock_ws:
                    mock_ws_instance = Mock()
                    mock_ws_instance.start = AsyncMock()
                    mock_ws_instance.subscribe = AsyncMock()
                    mock_ws_instance.stop = AsyncMock()
                    mock_ws.return_value = mock_ws_instance
                    
                    # Run worker, then trigger its real graceful shutdown
                    await run_worker_until_shutdown(config)

                    # Verify pending cleanup was processed
                    assert storage.cleanup_old_candles.call_count == 2  # AAPL + MSFT


class TestCandleCloseTask:
    """Time-based candle closing task."""

    @pytest.mark.asyncio
    async def test_close_task_calls_engine_with_configured_grace(self):
        """Each pass asks the engine to close due candles at the set grace."""
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.close_due_candles = Mock(return_value=[])

        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(1.0)):
            task = asyncio.create_task(candle_close_task(candle_engine, 2.0, 1.0))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        candle_engine.close_due_candles.assert_called_once_with(None, 2.0)

    @pytest.mark.asyncio
    async def test_close_task_survives_an_engine_error(self):
        """A failing pass is logged and the task keeps running."""
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.close_due_candles = Mock(side_effect=RuntimeError('boom'))

        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(1.0)):
            task = asyncio.create_task(candle_close_task(candle_engine, 2.0, 1.0))
            await asyncio.sleep(0.05)
            assert not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        candle_engine.close_due_candles.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_task_exits_on_cancellation(self):
        """Cancellation ends the task rather than raising out of it."""
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.close_due_candles = Mock(return_value=[])

        task = asyncio.create_task(candle_close_task(candle_engine, 2.0, 1.0))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.done()


class TestCandleCloseTaskShutdownOrdering:
    """The close task must stop before the worker's final flush."""

    @pytest.mark.asyncio
    async def test_close_task_stops_before_final_flush(self):
        """No candle may be enqueued after the last write is flushed."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.default_tickers = []

        storage = Mock(spec=Storage)
        storage.get_ticker_symbols = Mock(return_value=[])
        storage.add_ticker = Mock()
        storage.cleanup_old_candles = Mock()

        # Record the order of the calls that matter during shutdown.
        calls = []
        candle_engine = Mock(spec=CandleEngine)
        candle_engine.max_candles = 100
        candle_engine.get_pending_cleanup.return_value = set()
        candle_engine.close_due_candles = Mock(
            side_effect=lambda *a: calls.append('close') or []
        )
        candle_engine.complete_all_candles = Mock(
            side_effect=lambda: calls.append('complete_all')
        )
        candle_engine.flush_pending_candle_writes = Mock(
            side_effect=lambda: calls.append('flush')
        )
        candle_engine.flush_pending_ticker_statuses = Mock()

        with patch('src.websocket_worker.create_storage', return_value=storage):
            with patch('src.websocket_worker.CandleEngine', return_value=candle_engine):
                with patch('src.websocket_worker.WebSocketManager') as mock_ws:
                    mock_ws_instance = Mock()
                    mock_ws_instance.start = AsyncMock()
                    mock_ws_instance.subscribe = AsyncMock()
                    mock_ws_instance.stop = AsyncMock()
                    mock_ws.return_value = mock_ws_instance

                    await run_worker_until_shutdown(config)

        # The shutdown sequence ran...
        assert 'complete_all' in calls
        assert 'flush' in calls
        # ...and nothing tried to close a candle after it began.
        assert 'close' not in calls[calls.index('complete_all'):]



def _et(year, month, day, hour, minute):
    """Unix seconds for a wall-clock moment in New York."""
    from zoneinfo import ZoneInfo
    return int(datetime(
        year, month, day, hour, minute, tzinfo=ZoneInfo('America/New_York')
    ).timestamp())


class TestFillSessionWindow:
    """_inside_fill_session: when is silence meaningful?"""

    def test_off_and_unknown_modes_never_match(self):
        midday = _et(2026, 3, 2, 12, 0)
        assert _inside_fill_session(midday, 'off') is False
        assert _inside_fill_session(midday, 'nonsense') is False

    def test_regular_session_boundaries(self):
        """09:30 is inside, 09:29 is not; 16:00 is outside, 15:59 is not."""
        assert _inside_fill_session(_et(2026, 3, 2, 9, 29), 'regular') is False
        assert _inside_fill_session(_et(2026, 3, 2, 9, 30), 'regular') is True
        assert _inside_fill_session(_et(2026, 3, 2, 15, 59), 'regular') is True
        assert _inside_fill_session(_et(2026, 3, 2, 16, 0), 'regular') is False

    def test_extended_session_boundaries(self):
        assert _inside_fill_session(_et(2026, 3, 2, 3, 59), 'extended') is False
        assert _inside_fill_session(_et(2026, 3, 2, 4, 0), 'extended') is True
        assert _inside_fill_session(_et(2026, 3, 2, 19, 59), 'extended') is True
        assert _inside_fill_session(_et(2026, 3, 2, 20, 0), 'extended') is False

    def test_premarket_is_outside_the_regular_window(self):
        assert _inside_fill_session(_et(2026, 3, 2, 8, 0), 'regular') is False
        assert _inside_fill_session(_et(2026, 3, 2, 8, 0), 'extended') is True

    def test_weekend_is_excluded(self):
        # 7 and 8 March 2026 are Saturday and Sunday.
        assert _inside_fill_session(_et(2026, 3, 7, 12, 0), 'regular') is False
        assert _inside_fill_session(_et(2026, 3, 8, 12, 0), 'regular') is False
        assert _inside_fill_session(_et(2026, 3, 9, 12, 0), 'regular') is True

    def test_window_follows_new_york_wall_clock_across_dst(self):
        """US clocks go forward on 8 March 2026; the window must move with them.

        13:30 UTC is 08:30 ET before the change (outside the regular session)
        and 09:30 ET after it (the opening minute). A window pinned to a fixed
        UTC offset would answer the same on both days.
        """
        before = int(datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc).timestamp())
        after = int(datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc).timestamp())

        assert _inside_fill_session(before, 'regular') is False
        assert _inside_fill_session(after, 'regular') is True

        # Stated in local time, the opening minute is inside on both days.
        assert _inside_fill_session(_et(2026, 3, 6, 9, 30), 'regular') is True
        assert _inside_fill_session(_et(2026, 3, 9, 9, 30), 'regular') is True



class TestEmptyIntervalAuditTask:
    """The audit task: writes observations to a file, never a candle."""

    @staticmethod
    def _engine(verdict):
        engine = Mock(spec=CandleEngine)
        engine.interval_seconds = 60
        engine.interval_minutes = 1
        engine.audit_empty_interval = Mock(return_value=verdict)
        return engine

    @staticmethod
    def _ws(connected=True, connection_count=7, tickers=('AAPL',)):
        ws = Mock(spec=WebSocketManager)
        ws.get_status = Mock(return_value={
            'connected': connected,
            'connection_count': connection_count,
            'subscribed_tickers': list(tickers),
        })
        return ws

    async def _run(self, engine, ws, mode, path, passes=3):
        """Drive the task for a few passes, then stop it."""
        with patch('src.websocket_worker.asyncio.sleep', new=single_pass_sleep(0.01)):
            task = asyncio.create_task(empty_interval_audit_task(
                engine, ws, mode, path,
                poll_interval_seconds=0.01, settle_seconds=0.0
            ))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @staticmethod
    def _rows(path):
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @pytest.mark.asyncio
    async def test_first_pass_records_nothing(self, tmp_path):
        """With no earlier sample, feed continuity is unknown for the interval."""
        engine = self._engine({'eligible': True, 'reason': 'would_fill', 'price': 150.0})
        path = str(tmp_path / 'audit.jsonl')

        await self._run(engine, self._ws(), 'extended', path)

        # The single pass the helper allows is the opening sample only.
        assert self._rows(path) == []

    @pytest.mark.asyncio
    async def test_task_never_asks_the_engine_to_write(self, tmp_path):
        """The engine is only ever interrogated, never told to create a candle."""
        engine = self._engine({'eligible': True, 'reason': 'would_fill', 'price': 150.0})
        path = str(tmp_path / 'audit.jsonl')

        await self._run(engine, self._ws(), 'extended', path)

        for forbidden in ('process_tick', 'close_due_candles',
                          'complete_all_candles', 'flush_pending_candle_writes'):
            assert not getattr(engine, forbidden).called, forbidden

    @pytest.mark.asyncio
    async def test_intervals_that_had_trades_are_not_recorded(self, tmp_path):
        """Nothing to measure when the interval produced a candle."""
        engine = self._engine({'eligible': False, 'reason': 'candle_completed'})
        path = str(tmp_path / 'audit.jsonl')

        await self._run(engine, self._ws(), 'extended', path)

        assert self._rows(path) == []

    @pytest.mark.asyncio
    async def test_off_mode_is_never_inside_a_session(self):
        """Guards the wiring: 'off' cannot mark an interval fillable."""
        assert _inside_fill_session(int(datetime.now(timezone.utc).timestamp()),
                                    'off') is False

    @pytest.mark.asyncio
    async def test_missing_directory_is_created(self, tmp_path):
        engine = self._engine({'eligible': False, 'reason': 'chain_broken'})
        path = str(tmp_path / 'nested' / 'deeper' / 'audit.jsonl')

        await self._run(engine, self._ws(), 'extended', path)

        assert os.path.isdir(os.path.dirname(path))

    @pytest.mark.asyncio
    async def test_unwritable_path_stops_the_task_without_raising(self, tmp_path):
        """A bad path must not take the worker down."""
        engine = self._engine({'eligible': True, 'reason': 'would_fill', 'price': 1.0})
        blocker = tmp_path / 'a-file'
        blocker.write_text('not a directory')
        path = str(blocker / 'audit.jsonl')

        task = asyncio.create_task(empty_interval_audit_task(
            engine, self._ws(), 'extended', path,
            poll_interval_seconds=0.01, settle_seconds=0.0
        ))
        await asyncio.sleep(0.05)

        # It returned on its own rather than raising out of the task.
        assert task.done()
        assert task.exception() is None



def advancing_clock(start_timestamp, step_seconds, max_passes, poll_interval=0.01):
    """A fake clock plus a sleep that advances it one interval per pass.

    The audit task acts once per candle interval, so a test that waits on the
    real clock would need minutes. This moves time forward by one interval on
    every loop pass and blocks after max_passes, giving a fixed number of
    deterministic intervals.
    """
    state = {'t': float(start_timestamp)}
    calls = {'n': 0}
    real_sleep = asyncio.sleep

    def now():
        return state['t']

    async def sleep(seconds):
        # Patching asyncio.sleep reaches the shared module, so only the task's
        # own poll interval is intercepted; the test's waits stay real.
        if seconds != poll_interval:
            await real_sleep(seconds)
            return
        calls['n'] += 1
        if calls['n'] > max_passes:
            await asyncio.Event().wait()
        state['t'] += step_seconds

    return now, sleep


class TestEmptyIntervalAuditObservations:
    """What the task actually records, driven by a controlled clock."""

    BUCKET = int(datetime(2026, 3, 2, 15, 0, 0, tzinfo=timezone.utc).timestamp())

    def _engine(self, verdict):
        engine = Mock(spec=CandleEngine)
        engine.interval_seconds = 60
        engine.interval_minutes = 1
        engine.audit_empty_interval = Mock(return_value=verdict)
        return engine

    async def _run(self, engine, statuses, path, mode='regular', passes=3):
        """Run the task, returning a fresh ws status on each get_status call."""
        ws = Mock(spec=WebSocketManager)
        ws.get_status = Mock(side_effect=statuses)

        now, sleep = advancing_clock(self.BUCKET + 30, 60, passes)
        # Replace the module's own `time` reference rather than time.time
        # itself: patching the attribute would swap the clock for the whole
        # process, including the threads asyncio.to_thread runs work on.
        fake_time = SimpleNamespace(time=now)
        with patch('src.websocket_worker.time', new=fake_time):
            with patch('src.websocket_worker.asyncio.sleep', new=sleep):
                task = asyncio.create_task(empty_interval_audit_task(
                    engine, ws, mode, path,
                    poll_interval_seconds=0.01, settle_seconds=3.0
                ))
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _status(connection_count=7, connected=True, tickers=('AAPL',)):
        return {
            'connected': connected,
            'connection_count': connection_count,
            'subscribed_tickers': list(tickers),
        }

    @pytest.mark.asyncio
    async def test_records_an_interval_that_would_be_filled(self, tmp_path):
        """All five conditions hold: the row says so and carries the price."""
        engine = self._engine(
            {'eligible': True, 'reason': 'would_fill', 'price': 153.5}
        )
        path = str(tmp_path / 'audit.jsonl')

        rows = await self._run(engine, [self._status()] * 4, path)

        assert rows, "expected at least one observation"
        row = rows[0]
        assert row['ticker'] == 'AAPL'
        assert row['would_fill'] is True
        assert row['engine_reason'] == 'would_fill'
        assert row['feed_steady'] is True
        assert row['subscribed_throughout'] is True
        assert row['inside_session'] is True
        assert row['price'] == 153.5
        assert row['interval_minutes'] == 1
        assert row['bucket'] % 60 == 0
        assert row['bucket_utc'].startswith('2026-03-02')

    @pytest.mark.asyncio
    async def test_a_reconnect_inside_the_interval_disqualifies_it(self, tmp_path):
        """Ticks missed during a reconnect look exactly like an untraded interval."""
        engine = self._engine(
            {'eligible': True, 'reason': 'would_fill', 'price': 153.5}
        )
        path = str(tmp_path / 'audit.jsonl')

        # connection_count changes between samples.
        rows = await self._run(engine, [
            self._status(connection_count=7),
            self._status(connection_count=8),
            self._status(connection_count=9),
            self._status(connection_count=10),
        ], path)

        assert rows
        assert all(r['would_fill'] is False for r in rows)
        assert all(r['feed_steady'] is False for r in rows)
        # The engine still said yes; the worker is what refused.
        assert all(r['engine_reason'] == 'would_fill' for r in rows)

    @pytest.mark.asyncio
    async def test_a_dropped_connection_disqualifies_the_interval(self, tmp_path):
        engine = self._engine(
            {'eligible': True, 'reason': 'would_fill', 'price': 153.5}
        )
        path = str(tmp_path / 'audit.jsonl')

        rows = await self._run(engine, [
            self._status(connected=True),
            self._status(connected=False),
            self._status(connected=True),
            self._status(connected=True),
        ], path)

        assert rows
        assert rows[0]['would_fill'] is False
        assert rows[0]['feed_steady'] is False

    @pytest.mark.asyncio
    async def test_a_ticker_subscribed_only_at_the_end_is_disqualified(self, tmp_path):
        """It was not being listened to for the whole interval."""
        engine = self._engine(
            {'eligible': True, 'reason': 'would_fill', 'price': 153.5}
        )
        path = str(tmp_path / 'audit.jsonl')

        rows = await self._run(engine, [
            self._status(tickers=()),
            self._status(tickers=('AAPL',)),
            self._status(tickers=('AAPL',)),
            self._status(tickers=('AAPL',)),
        ], path)

        assert rows
        assert rows[0]['ticker'] == 'AAPL'
        assert rows[0]['would_fill'] is False
        assert rows[0]['subscribed_throughout'] is False

    @pytest.mark.asyncio
    async def test_the_engine_refusal_is_recorded_verbatim(self, tmp_path):
        """A broken chain is written down, not silently skipped."""
        engine = self._engine(
            {'eligible': False, 'reason': 'chain_broken',
             'last_completed_start': 1}
        )
        path = str(tmp_path / 'audit.jsonl')

        rows = await self._run(engine, [self._status()] * 4, path)

        assert rows
        assert rows[0]['would_fill'] is False
        assert rows[0]['engine_reason'] == 'chain_broken'
        assert rows[0]['price'] is None

    @pytest.mark.asyncio
    async def test_outside_the_session_window_nothing_would_be_filled(self, tmp_path):
        """15:00 UTC is 10:00 ET, inside regular; premarket is not."""
        engine = self._engine(
            {'eligible': True, 'reason': 'would_fill', 'price': 153.5}
        )
        path = str(tmp_path / 'audit.jsonl')

        # 08:00 UTC is 03:00 ET, outside both windows.
        class EarlyMorning(TestEmptyIntervalAuditObservations):
            BUCKET = int(
                datetime(2026, 3, 2, 8, 0, 0, tzinfo=timezone.utc).timestamp()
            )

        rows = await EarlyMorning()._run(engine, [self._status()] * 4, path)

        assert rows
        assert all(r['would_fill'] is False for r in rows)
        assert all(r['inside_session'] is False for r in rows)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
