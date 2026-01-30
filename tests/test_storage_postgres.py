"""
Tests for PostgreSQL storage adapter.

Uses mocking to test PostgreSQL storage without requiring a real database.
Tests verify the adapter has the same interface as SQLite Storage.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

# Skip all tests if psycopg2 is not available
psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 required for PostgreSQL tests")

from src.storage import Candle, TrackedTicker


class TestPostgreSQLStorageInterface:
    """Test that PostgreSQL storage has same interface as SQLite Storage."""

    @pytest.fixture
    def mock_pool(self):
        """Create mock connection pool."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        return {
            'pool': mock_pool,
            'conn': mock_conn,
            'cursor': mock_cursor
        }

    @pytest.fixture
    def postgres_storage(self, mock_pool):
        """Create PostgreSQL storage with mocked connection."""
        with patch('src.storage_postgres.psycopg2.pool.ThreadedConnectionPool', return_value=mock_pool['pool']):
            from src.storage_postgres import PostgreSQLStorage
            
            storage = PostgreSQLStorage(
                connection_string="host=localhost dbname=test user=test password=test",
                min_connections=1,
                max_connections=5
            )
            
            # Attach mocks for test access
            storage._test_mock = mock_pool
            
            return storage

    def test_has_all_required_methods(self, postgres_storage):
        """Test that all required methods exist."""
        required_methods = [
            'add_ticker',
            'remove_ticker',
            'get_tickers',
            'get_ticker_symbols',
            'update_ticker_status',
            'ticker_exists',
            'get_ticker',
            'get_ticker_count',
            'get_ticker_intervals',
            'get_candles_for_aggregation',
            'update_ticker_last_request',
            'delete_all_tickers',
            'cleanup_orphaned_candles',
            'save_candle',
            'get_candles',
            'get_current_candle',
            'clear_candles',
            'cleanup_old_candles',
            'get_stats',
            'update_websocket_status',
            'get_websocket_status',
            'update_active_candles',
            'get_active_candles',
            'close'
        ]
        
        for method in required_methods:
            assert hasattr(postgres_storage, method), f"Missing method: {method}"
            assert callable(getattr(postgres_storage, method)), f"Not callable: {method}"

    def test_add_ticker_calls_execute(self, postgres_storage):
        """Test that add_ticker calls cursor.execute."""
        postgres_storage._test_mock['cursor'].rowcount = 1
        
        postgres_storage.add_ticker('AAPL')
        
        # Verify execute was called
        assert postgres_storage._test_mock['cursor'].execute.called
        # Verify commit was called
        assert postgres_storage._test_mock['conn'].commit.called

    def test_ticker_exists_returns_bool(self, postgres_storage):
        """Test that ticker_exists returns boolean."""
        postgres_storage._test_mock['cursor'].fetchone.return_value = (1,)
        
        result = postgres_storage.ticker_exists('AAPL')
        
        assert isinstance(result, bool)
        assert result is True

    def test_ticker_exists_false(self, postgres_storage):
        """Test that ticker_exists returns False when not found."""
        postgres_storage._test_mock['cursor'].fetchone.return_value = None
        
        result = postgres_storage.ticker_exists('AAPL')
        
        assert result is False

    def test_get_ticker_count_returns_int(self, postgres_storage):
        """Test that get_ticker_count returns integer."""
        postgres_storage._test_mock['cursor'].fetchone.return_value = (5,)
        
        result = postgres_storage.get_ticker_count()
        
        assert isinstance(result, int)
        assert result == 5

    def test_get_ticker_symbols_returns_list(self, postgres_storage):
        """Test that get_ticker_symbols returns list."""
        postgres_storage._test_mock['cursor'].fetchall.return_value = [
            ('AAPL',), ('GOOGL',), ('MSFT',)
        ]
        
        result = postgres_storage.get_ticker_symbols()
        
        assert isinstance(result, list)
        assert result == ['AAPL', 'GOOGL', 'MSFT']

    def test_close_closes_pool(self, postgres_storage):
        """Test that close() closes the connection pool."""
        postgres_storage.close()
        
        postgres_storage._test_mock['pool'].closeall.assert_called_once()


class TestPostgreSQLStorageConstants:
    """Test storage constants and configuration."""

    def test_stats_cache_ttl(self):
        """Test that STATS_CACHE_TTL is defined."""
        from src.storage_postgres import PostgreSQLStorage
        
        assert hasattr(PostgreSQLStorage, 'STATS_CACHE_TTL')
        assert PostgreSQLStorage.STATS_CACHE_TTL > 0

    def test_max_retries(self):
        """Test that MAX_RETRIES is defined."""
        from src.storage_postgres import PostgreSQLStorage
        
        assert hasattr(PostgreSQLStorage, 'MAX_RETRIES')
        assert PostgreSQLStorage.MAX_RETRIES > 0

    def test_retry_base_delay(self):
        """Test that RETRY_BASE_DELAY is defined."""
        from src.storage_postgres import PostgreSQLStorage
        
        assert hasattr(PostgreSQLStorage, 'RETRY_BASE_DELAY')
        assert PostgreSQLStorage.RETRY_BASE_DELAY > 0


class TestPostgreSQLStorageDataTypes:
    """Test that data types match SQLite Storage."""

    def test_candle_dataclass_compatible(self):
        """Test that Candle dataclass works with PostgreSQL storage."""
        candle = Candle(
            ticker='AAPL',
            timestamp=1234567890,
            datetime_utc='2026-01-30T10:00:00Z',
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.5,
            volume=1000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )
        
        assert candle.ticker == 'AAPL'
        assert candle.is_complete is True

    def test_tracked_ticker_dataclass_compatible(self):
        """Test that TrackedTicker dataclass works with PostgreSQL storage."""
        ticker = TrackedTicker(
            symbol='AAPL',
            added_at='2026-01-30T10:00:00Z',
            status='active',
            last_tick_at='2026-01-30T10:05:00Z',
            last_price=150.5,
            candle_count=10,
            last_candle_request_at='2026-01-30T10:04:00Z'
        )
        
        assert ticker.symbol == 'AAPL'
        assert ticker.status == 'active'


class TestPostgreSQLStorageImport:
    """Test module import and availability check."""

    def test_psycopg2_available_flag(self):
        """Test that PSYCOPG2_AVAILABLE flag is set correctly."""
        from src.storage_postgres import PSYCOPG2_AVAILABLE
        
        # Since we're running these tests, psycopg2 must be available
        assert PSYCOPG2_AVAILABLE is True

    def test_can_import_postgresql_storage(self):
        """Test that PostgreSQLStorage can be imported."""
        from src.storage_postgres import PostgreSQLStorage
        
        assert PostgreSQLStorage is not None


class TestPostgreSQLStorageWebSocketStatus:
    """Test WebSocket status methods match SQLite interface."""

    @pytest.fixture
    def mock_pool(self):
        """Create mock connection pool."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        return {
            'pool': mock_pool,
            'conn': mock_conn,
            'cursor': mock_cursor
        }

    @pytest.fixture
    def postgres_storage(self, mock_pool):
        """Create PostgreSQL storage with mocked connection."""
        with patch('src.storage_postgres.psycopg2.pool.ThreadedConnectionPool', return_value=mock_pool['pool']):
            from src.storage_postgres import PostgreSQLStorage
            
            storage = PostgreSQLStorage(
                connection_string="host=localhost dbname=test",
                min_connections=1,
                max_connections=5
            )
            storage._test_mock = mock_pool
            return storage

    def test_update_websocket_status_accepts_dict(self, postgres_storage):
        """Test that update_websocket_status accepts status dict."""
        status = {
            'connected': True,
            'subscribed_tickers': ['AAPL', 'GOOGL'],
            'subscribed_count': 2,
            'pending_subscribe': [],
            'connection_count': 1,
            'tick_count': 1000,
            'last_message': '2026-01-30T10:00:00Z'
        }
        
        # Should not raise
        postgres_storage.update_websocket_status(status)
        
        assert postgres_storage._test_mock['cursor'].execute.called

    def test_get_websocket_status_returns_none_when_empty(self, postgres_storage):
        """Test that get_websocket_status returns None when no status."""
        postgres_storage._test_mock['cursor'].fetchone.return_value = None
        
        result = postgres_storage.get_websocket_status()
        
        assert result is None


class TestPostgreSQLStorageActiveCandles:
    """Test active candles methods match SQLite interface."""

    @pytest.fixture
    def mock_pool(self):
        """Create mock connection pool."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        mock_pool.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        return {
            'pool': mock_pool,
            'conn': mock_conn,
            'cursor': mock_cursor
        }

    @pytest.fixture
    def postgres_storage(self, mock_pool):
        """Create PostgreSQL storage with mocked connection."""
        with patch('src.storage_postgres.psycopg2.pool.ThreadedConnectionPool', return_value=mock_pool['pool']):
            from src.storage_postgres import PostgreSQLStorage
            
            storage = PostgreSQLStorage(
                connection_string="host=localhost dbname=test",
                min_connections=1,
                max_connections=5
            )
            storage._test_mock = mock_pool
            return storage

    def test_update_active_candles_accepts_list(self, postgres_storage):
        """Test that update_active_candles accepts list of candles."""
        candles = [
            {'ticker': 'AAPL', 'ticks': 50, 'current_price': 150.0}
        ]
        
        # Should not raise
        postgres_storage.update_active_candles(candles)
        
        assert postgres_storage._test_mock['cursor'].execute.called

    def test_get_active_candles_returns_none_when_empty(self, postgres_storage):
        """Test that get_active_candles returns None when no data."""
        postgres_storage._test_mock['cursor'].fetchone.return_value = None
        
        result = postgres_storage.get_active_candles()
        
        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
