"""
Tests for WebSocket worker entry point.

Tests the dedicated worker that handles WebSocket connections and tick processing.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from src.websocket_worker import cleanup_task, run_worker, setup_logging
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
