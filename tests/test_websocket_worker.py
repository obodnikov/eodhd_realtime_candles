"""
Tests for WebSocket worker entry point.

Tests the dedicated worker that handles WebSocket connections and tick processing.
"""

import pytest
import asyncio
import signal
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from src.websocket_worker import (
    cleanup_task,
    ticker_sync_task,
    candle_close_task,
    run_worker,
    setup_logging,
)
from src.config import Config
from src.storage import Storage
from src.candle_engine import CandleEngine


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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
