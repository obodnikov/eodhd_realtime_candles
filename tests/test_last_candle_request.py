"""
Tests for last_candle_request_at tracking functionality.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

from src.storage import Storage, TrackedTicker


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except:
        pass


@pytest.fixture
def storage(temp_db):
    """Create a Storage instance with temporary database."""
    return Storage(temp_db)


class TestLastCandleRequestTracking:
    """Tests for last_candle_request_at field."""

    def test_new_ticker_has_null_last_request(self, storage):
        """New tickers should have NULL last_candle_request_at."""
        storage.add_ticker('AAPL')
        tickers = storage.get_tickers()
        
        assert len(tickers) == 1
        assert tickers[0].symbol == 'AAPL'
        assert tickers[0].last_candle_request_at is None

    def test_update_ticker_last_request(self, storage):
        """update_ticker_last_request should set timestamp."""
        storage.add_ticker('AAPL')
        
        before = datetime.now(timezone.utc)
        storage.update_ticker_last_request('AAPL')
        after = datetime.now(timezone.utc)
        
        tickers = storage.get_tickers()
        assert tickers[0].last_candle_request_at is not None
        
        # Parse timestamp and verify it's between before/after
        timestamp = datetime.fromisoformat(tickers[0].last_candle_request_at.replace('Z', '+00:00'))
        assert before <= timestamp <= after

    def test_update_ticker_last_request_case_insensitive(self, storage):
        """update_ticker_last_request should handle case insensitivity."""
        storage.add_ticker('AAPL')
        
        storage.update_ticker_last_request('aapl')
        
        tickers = storage.get_tickers()
        assert tickers[0].last_candle_request_at is not None

    def test_update_ticker_last_request_multiple_times(self, storage):
        """Multiple updates should overwrite previous timestamp."""
        storage.add_ticker('AAPL')
        
        storage.update_ticker_last_request('AAPL')
        tickers = storage.get_tickers()
        first_timestamp = tickers[0].last_candle_request_at
        
        # Small delay to ensure different timestamp
        import time
        time.sleep(0.01)
        
        storage.update_ticker_last_request('AAPL')
        tickers = storage.get_tickers()
        second_timestamp = tickers[0].last_candle_request_at
        
        assert second_timestamp != first_timestamp
        assert second_timestamp > first_timestamp

    def test_update_nonexistent_ticker(self, storage):
        """Updating non-existent ticker should not raise error."""
        # Should not raise exception
        storage.update_ticker_last_request('NONEXISTENT')
        
        # Verify no tickers were created
        tickers = storage.get_tickers()
        assert len(tickers) == 0

    def test_get_tickers_includes_last_request_field(self, storage):
        """get_tickers should include last_candle_request_at in response."""
        storage.add_ticker('AAPL')
        storage.add_ticker('MSFT')
        
        storage.update_ticker_last_request('AAPL')
        
        tickers = storage.get_tickers()
        assert len(tickers) == 2
        
        # Find AAPL
        aapl = next(t for t in tickers if t.symbol == 'AAPL')
        msft = next(t for t in tickers if t.symbol == 'MSFT')
        
        assert aapl.last_candle_request_at is not None
        assert msft.last_candle_request_at is None

    def test_tracked_ticker_to_dict_includes_field(self, storage):
        """TrackedTicker.to_dict() should include last_candle_request_at."""
        storage.add_ticker('AAPL')
        storage.update_ticker_last_request('AAPL')
        
        tickers = storage.get_tickers()
        ticker_dict = tickers[0].to_dict()
        
        assert 'last_candle_request_at' in ticker_dict
        assert ticker_dict['last_candle_request_at'] is not None


class TestMigration:
    """Tests for database migration logic."""

    def test_migration_on_existing_database(self, temp_db):
        """Migration should add column to existing database without the field."""
        # Create old schema without last_candle_request_at
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE tickers (
                symbol TEXT PRIMARY KEY,
                added_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                last_tick_at TEXT,
                last_price REAL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            INSERT INTO tickers (symbol, added_at, status)
            VALUES ('AAPL', '2025-01-01T00:00:00Z', 'active')
        ''')
        conn.commit()
        conn.close()
        
        # Initialize Storage (should trigger migration)
        storage = Storage(temp_db)
        
        # Verify column exists and old data is preserved
        tickers = storage.get_tickers()
        assert len(tickers) == 1
        assert tickers[0].symbol == 'AAPL'
        assert tickers[0].last_candle_request_at is None

    def test_migration_idempotent(self, temp_db):
        """Running migration multiple times should not cause errors."""
        # First initialization
        storage1 = Storage(temp_db)
        storage1.add_ticker('AAPL')
        
        # Second initialization (should not fail)
        storage2 = Storage(temp_db)
        tickers = storage2.get_tickers()
        
        assert len(tickers) == 1
        assert tickers[0].symbol == 'AAPL'

    def test_column_check_uses_pragma(self, temp_db):
        """Migration should use PRAGMA table_info to check column existence."""
        # Create database with column already present
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE tickers (
                symbol TEXT PRIMARY KEY,
                added_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                last_tick_at TEXT,
                last_price REAL,
                last_candle_request_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        
        # Initialize Storage (should not attempt ALTER TABLE)
        storage = Storage(temp_db)
        
        # Verify it works correctly
        storage.add_ticker('AAPL')
        tickers = storage.get_tickers()
        assert len(tickers) == 1
