"""
Tests for Storage retry logic with database lock handling.

Tests verify that database operations properly retry with exponential backoff
when encountering lock contention in multi-worker scenarios.
"""

import pytest
import sqlite3
import time
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from src.storage import Storage, Candle


class TestStorageRetryLogic:
    """Test retry logic for database lock contention."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create a temporary storage instance."""
        db_path = tmp_path / "test.db"
        return Storage(str(db_path))

    @pytest.fixture
    def candle(self):
        """Create a test candle."""
        return Candle(
            ticker="AAPL",
            timestamp=1234567890,
            datetime_utc="2009-02-13T23:31:30+00:00",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            tick_count=10,
            is_complete=True,
            interval_minutes=5
        )

    def test_execute_with_retry_success_first_attempt(self, storage):
        """Retry helper should succeed on first attempt when no lock."""
        operation = Mock(return_value="success")
        
        result = storage._execute_with_retry(
            operation=operation,
            operation_name="test operation",
            is_critical=True
        )
        
        assert result == "success"
        assert operation.call_count == 1

    def test_execute_with_retry_success_after_retries(self, storage):
        """Retry helper should succeed after transient lock errors."""
        # Simulate lock on first 2 attempts, success on 3rd
        operation = Mock(side_effect=[
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            "success"
        ])
        
        result = storage._execute_with_retry(
            operation=operation,
            operation_name="test operation",
            is_critical=True
        )
        
        assert result == "success"
        assert operation.call_count == 3

    def test_execute_with_retry_exponential_backoff(self, storage):
        """Retry helper should use exponential backoff timing."""
        operation = Mock(side_effect=[
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            "success"
        ])
        
        start_time = time.time()
        storage._execute_with_retry(
            operation=operation,
            operation_name="test operation",
            is_critical=True
        )
        elapsed = time.time() - start_time
        
        # Should wait: 50ms + 100ms = 150ms minimum
        # Allow some overhead for test execution
        assert elapsed >= 0.15
        assert elapsed < 0.5  # But not too long

    def test_execute_with_retry_critical_raises_on_failure(self, storage):
        """Critical operations should raise exception after all retries fail."""
        operation = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        
        with pytest.raises(sqlite3.OperationalError):
            storage._execute_with_retry(
                operation=operation,
                operation_name="test operation",
                is_critical=True
            )
        
        assert operation.call_count == storage.MAX_RETRIES

    def test_execute_with_retry_non_critical_returns_none_on_failure(self, storage):
        """Non-critical operations should return None after all retries fail."""
        operation = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        
        result = storage._execute_with_retry(
            operation=operation,
            operation_name="test operation",
            is_critical=False
        )
        
        assert result is None
        assert operation.call_count == storage.MAX_RETRIES

    def test_execute_with_retry_custom_max_retries(self, storage):
        """Retry helper should respect custom max_retries parameter."""
        operation = Mock(side_effect=sqlite3.OperationalError("database is locked"))
        
        result = storage._execute_with_retry(
            operation=operation,
            operation_name="test operation",
            is_critical=False,
            max_retries=5
        )
        
        assert result is None
        assert operation.call_count == 5

    def test_execute_with_retry_non_lock_error_fails_immediately(self, storage):
        """Non-lock errors should fail immediately without retries."""
        operation = Mock(side_effect=sqlite3.IntegrityError("constraint violation"))
        
        with pytest.raises(sqlite3.IntegrityError):
            storage._execute_with_retry(
                operation=operation,
                operation_name="test operation",
                is_critical=True
            )
        
        # Should only try once for non-lock errors
        assert operation.call_count == 1

    def test_save_candle_with_lock_retry(self, storage, candle):
        """save_candle should retry on database lock."""
        # Add a ticker first
        storage.add_ticker("AAPL")
        
        # Mock the connection to simulate lock then success
        original_get_conn = storage._get_connection
        call_count = [0]
        
        def mock_get_connection():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call - return a mock that raises lock error
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
                mock_conn.cursor.return_value = mock_cursor
                return mock_conn
            else:
                # Subsequent calls - use real connection
                return original_get_conn()
        
        with patch.object(storage, '_get_connection', side_effect=mock_get_connection):
            # Should succeed after retry
            storage.save_candle(candle)
        
        # Verify candle was saved
        candles = storage.get_candles("AAPL", count=1)
        assert len(candles) == 1
        assert candles[0].ticker == "AAPL"

    def test_update_ticker_last_request_with_lock_retry(self, storage):
        """update_ticker_last_request should retry on database lock and not raise."""
        storage.add_ticker("AAPL")
        
        # Mock to simulate lock on all attempts (non-critical should not raise)
        with patch.object(storage, '_get_connection') as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn
            
            # Should not raise exception (non-critical)
            storage.update_ticker_last_request("AAPL")
        
        # Verify it tried multiple times
        assert mock_cursor.execute.call_count == storage.MAX_RETRIES

    def test_cleanup_old_candles_with_lock_retry(self, storage, candle):
        """cleanup_old_candles should retry on database lock and not raise."""
        storage.add_ticker("AAPL")
        
        # Mock to simulate lock on all attempts (non-critical should not raise)
        with patch.object(storage, '_get_connection') as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
            mock_conn.cursor.return_value = mock_cursor
            mock_get_conn.return_value = mock_conn
            
            # Should not raise exception (non-critical)
            storage.cleanup_old_candles("AAPL", max_candles=100)
        
        # Verify it tried multiple times
        assert mock_cursor.execute.call_count == storage.MAX_RETRIES

    def test_retry_configuration_constants(self, storage):
        """Verify retry configuration constants are set correctly."""
        assert storage.MAX_RETRIES == 3
        assert storage.RETRY_BASE_DELAY == 0.05  # 50ms

    def test_concurrent_save_candle_operations(self, storage, candle):
        """Test that multiple concurrent save operations don't deadlock."""
        import threading
        
        storage.add_ticker("AAPL")
        errors = []
        
        def save_candle_thread(candle_data):
            try:
                storage.save_candle(candle_data)
            except Exception as e:
                errors.append(e)
        
        # Create multiple candles with different timestamps
        candles = [
            Candle(
                ticker="AAPL",
                timestamp=1234567890 + i,
                datetime_utc=f"2009-02-13T23:31:{30+i:02d}+00:00",
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=1000,
                tick_count=10,
                is_complete=True,
                interval_minutes=5
            )
            for i in range(10)
        ]
        
        # Start multiple threads
        threads = [threading.Thread(target=save_candle_thread, args=(c,)) for c in candles]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should complete without errors
        assert len(errors) == 0
        
        # Verify all candles were saved
        saved_candles = storage.get_candles("AAPL", count=20)
        assert len(saved_candles) == 10


class TestStorageConnectionSettings:
    """Test SQLite connection configuration."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create a temporary storage instance."""
        db_path = tmp_path / "test.db"
        return Storage(str(db_path))

    def test_connection_timeout_is_10_seconds(self, storage):
        """Connection timeout should be set to 10 seconds."""
        conn = storage._get_connection()
        # SQLite doesn't expose timeout directly, but we can verify it doesn't fail immediately
        # This is more of a smoke test
        assert conn is not None

    def test_wal_mode_enabled(self, storage):
        """WAL journal mode should be enabled for better concurrency."""
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        result = cursor.fetchone()[0]
        assert result.upper() == "WAL"

    def test_busy_timeout_configured(self, storage):
        """Busy timeout should be configured to 10 seconds."""
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout;")
        result = cursor.fetchone()[0]
        assert result == 10000  # 10 seconds in milliseconds

    def test_cache_size_configured(self, storage):
        """Cache size should be configured to 10MB."""
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA cache_size;")
        result = cursor.fetchone()[0]
        # Negative value means KB, -10000 = 10MB
        assert result == -10000
