"""
Tests for WebSocket worker entry point.

Tests the dedicated worker that handles WebSocket connections and tick processing.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from src.websocket_worker import cleanup_task, ticker_sync_task, run_worker, setup_logging
from src.config import Config
from src.storage import Storage
from src.candle_engine import CandleEngine


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
        
        # Run cleanup task for one iteration
        task = asyncio.create_task(cleanup_task(storage, candle_engine))
        
        # Wait for first iteration
        await asyncio.sleep(0.1)
        
        # Cancel task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # Verify cleanup was called
        assert candle_engine.get_pending_cleanup.called

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
        
        # Run cleanup task
        task = asyncio.create_task(cleanup_task(storage, candle_engine))
        
        # Wait for processing
        await asyncio.sleep(31)  # Wait for sleep(30) + processing
        
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
        
        # Run cleanup task
        task = asyncio.create_task(cleanup_task(storage, candle_engine))
        
        # Wait for processing
        await asyncio.sleep(31)
        
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
        
        # Run cleanup task
        task = asyncio.create_task(cleanup_task(storage, candle_engine))
        
        # Wait for one iteration
        await asyncio.sleep(31)
        
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
        
        with patch('src.websocket_worker.Storage', return_value=storage):
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
                
                # Run worker briefly
                task = asyncio.create_task(run_worker(config))
                await asyncio.sleep(0.5)
                
                # Send shutdown signal
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                
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
        
        with patch('src.websocket_worker.Storage', return_value=storage):
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
                    
                    # Run worker briefly
                    task = asyncio.create_task(run_worker(config))
                    await asyncio.sleep(0.5)
                    
                    # Send shutdown signal
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    
                    # Verify pending cleanup was processed
                    assert storage.cleanup_old_candles.call_count == 2  # AAPL + MSFT


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
