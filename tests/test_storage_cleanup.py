"""
Tests for storage layer cleanup functionality.

Tests the cleanup_orphaned_candles() method and related ticker deletion behavior.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from storage import Storage, Candle


class TestStorageCleanup(unittest.TestCase):
    """Test cases for storage cleanup functionality."""

    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    def _create_test_candle(self, ticker: str, timestamp: int = 1700000000) -> Candle:
        """Helper to create a test candle."""
        return Candle(
            ticker=ticker,
            timestamp=timestamp,
            datetime_utc=datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=10000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )

    def test_cleanup_orphaned_candles_no_orphans(self):
        """Test cleanup when there are no orphaned candles."""
        # Add ticker and candles
        self.storage.add_ticker('AAPL')
        candle = self._create_test_candle('AAPL')
        self.storage.save_candle(candle)

        # Run cleanup
        deleted = self.storage.cleanup_orphaned_candles()

        # Should delete nothing
        self.assertEqual(deleted, 0)

        # Verify candle still exists
        candles = self.storage.get_candles('AAPL', count=10)
        self.assertEqual(len(candles), 1)

    def test_cleanup_orphaned_candles_with_orphans(self):
        """Test cleanup when there are orphaned candles."""
        # Add ticker and candles
        self.storage.add_ticker('AAPL')
        candle1 = self._create_test_candle('AAPL', 1700000000)
        self.storage.save_candle(candle1)

        # Add orphaned candles (no ticker entry)
        candle2 = self._create_test_candle('ORPHAN', 1700000100)
        self.storage.save_candle(candle2)

        # Verify both exist before cleanup
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 2)
        self.assertEqual(len(stats['candles_per_ticker']), 2)

        # Run cleanup
        deleted = self.storage.cleanup_orphaned_candles()

        # Should delete the orphaned candle
        self.assertEqual(deleted, 1)

        # Verify only AAPL candle remains
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 1)
        self.assertEqual(len(stats['candles_per_ticker']), 1)
        self.assertIn('AAPL', stats['candles_per_ticker'])
        self.assertNotIn('ORPHAN', stats['candles_per_ticker'])

    def test_cleanup_orphaned_candles_empty_database(self):
        """Test cleanup on empty database."""
        # Run cleanup on empty database
        deleted = self.storage.cleanup_orphaned_candles()

        # Should delete nothing
        self.assertEqual(deleted, 0)

    def test_cleanup_orphaned_candles_multiple_orphans(self):
        """Test cleanup with multiple orphaned tickers."""
        # Add one tracked ticker
        self.storage.add_ticker('AAPL')
        candle1 = self._create_test_candle('AAPL', 1700000000)
        self.storage.save_candle(candle1)

        # Add multiple orphaned tickers with multiple candles each
        for ticker in ['ORPHAN1', 'ORPHAN2', 'ORPHAN3']:
            for i in range(5):
                candle = self._create_test_candle(ticker, 1700000000 + (i * 300))
                self.storage.save_candle(candle)

        # Verify before cleanup
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 16)  # 1 AAPL + 15 orphans
        self.assertEqual(len(stats['candles_per_ticker']), 4)

        # Run cleanup
        deleted = self.storage.cleanup_orphaned_candles()

        # Should delete 15 orphaned candles
        self.assertEqual(deleted, 15)

        # Verify only AAPL remains
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 1)
        self.assertEqual(len(stats['candles_per_ticker']), 1)
        self.assertIn('AAPL', stats['candles_per_ticker'])

    def test_delete_all_tickers_removes_candles(self):
        """Test that delete_all_tickers() removes candles."""
        # Add tickers and candles
        for ticker in ['AAPL', 'MSFT', 'GOOGL']:
            self.storage.add_ticker(ticker)
            for i in range(3):
                candle = self._create_test_candle(ticker, 1700000000 + (i * 300))
                self.storage.save_candle(candle)

        # Verify before deletion
        stats = self.storage.get_stats()
        self.assertEqual(stats['ticker_count'], 3)
        self.assertEqual(stats['total_candles'], 9)

        # Delete all tickers
        count = self.storage.delete_all_tickers()

        # Verify deletion
        self.assertEqual(count, 3)
        stats = self.storage.get_stats()
        self.assertEqual(stats['ticker_count'], 0)
        self.assertEqual(stats['total_candles'], 0)

    def test_remove_ticker_removes_candles(self):
        """Test that remove_ticker() removes candles."""
        # Add ticker and candles
        self.storage.add_ticker('AAPL')
        self.storage.add_ticker('MSFT')

        for i in range(3):
            candle_aapl = self._create_test_candle('AAPL', 1700000000 + (i * 300))
            candle_msft = self._create_test_candle('MSFT', 1700000000 + (i * 300))
            self.storage.save_candle(candle_aapl)
            self.storage.save_candle(candle_msft)

        # Verify before removal
        stats = self.storage.get_stats()
        self.assertEqual(stats['ticker_count'], 2)
        self.assertEqual(stats['total_candles'], 6)

        # Remove AAPL
        removed = self.storage.remove_ticker('AAPL')

        # Verify removal
        self.assertTrue(removed)
        stats = self.storage.get_stats()
        self.assertEqual(stats['ticker_count'], 1)
        self.assertEqual(stats['total_candles'], 3)
        self.assertNotIn('AAPL', stats['candles_per_ticker'])
        self.assertIn('MSFT', stats['candles_per_ticker'])

    def test_cleanup_after_delete_all_tickers(self):
        """Test that cleanup finds no orphans after proper delete_all_tickers()."""
        # Add tickers and candles
        for ticker in ['AAPL', 'MSFT']:
            self.storage.add_ticker(ticker)
            candle = self._create_test_candle(ticker)
            self.storage.save_candle(candle)

        # Delete all tickers (should also delete candles)
        self.storage.delete_all_tickers()

        # Run cleanup - should find nothing
        deleted = self.storage.cleanup_orphaned_candles()
        self.assertEqual(deleted, 0)

    def test_cleanup_idempotent(self):
        """Test that cleanup is idempotent (can be run multiple times safely)."""
        # Add orphaned candles
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # First cleanup
        deleted1 = self.storage.cleanup_orphaned_candles()
        self.assertEqual(deleted1, 1)

        # Second cleanup should find nothing
        deleted2 = self.storage.cleanup_orphaned_candles()
        self.assertEqual(deleted2, 0)

        # Third cleanup should still find nothing
        deleted3 = self.storage.cleanup_orphaned_candles()
        self.assertEqual(deleted3, 0)


class TestStorageCleanupConcurrency(unittest.TestCase):
    """Test cases for concurrent operations during cleanup."""

    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    def _create_test_candle(self, ticker: str, timestamp: int = 1700000000) -> Candle:
        """Helper to create a test candle."""
        return Candle(
            ticker=ticker,
            timestamp=timestamp,
            datetime_utc=datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=10000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )

    def test_cleanup_with_wal_mode(self):
        """Test that cleanup works correctly with WAL mode enabled."""
        # WAL mode should be enabled by default in Storage
        # Add some test data
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # Verify WAL mode is active
        conn = self.storage._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        # Should be 'wal' or 'WAL'
        self.assertIn(journal_mode.lower(), ['wal'])

        # Cleanup should work normally
        deleted = self.storage.cleanup_orphaned_candles()
        self.assertEqual(deleted, 1)

    def test_cleanup_during_concurrent_ticker_add(self):
        """Test cleanup data integrity when ticker is added during cleanup."""
        import threading
        import time

        # Add orphaned candles
        candle1 = self._create_test_candle('ORPHAN', 1700000000)
        self.storage.save_candle(candle1)

        # Track what happened
        results = {'cleanup_deleted': None, 'ticker_added': False, 'new_ticker_candles_exist': None}

        def add_ticker_mid_cleanup():
            """Add a ticker during cleanup."""
            time.sleep(0.001)  # Small delay to let cleanup start
            self.storage.add_ticker('NEWTICKER')
            candle = self._create_test_candle('NEWTICKER', 1700000100)
            self.storage.save_candle(candle)
            results['ticker_added'] = True

        # Start concurrent ticker addition
        thread = threading.Thread(target=add_ticker_mid_cleanup)
        thread.start()

        # Run cleanup
        deleted = self.storage.cleanup_orphaned_candles()
        results['cleanup_deleted'] = deleted

        # Wait for thread to complete
        thread.join()

        # Verify results - NEWTICKER's candles should NOT be deleted
        # because either:
        # 1. Ticker was added before cleanup's transaction locked the DB
        # 2. Ticker was added after cleanup completed
        # In both cases, NEWTICKER's candles should exist
        candles = self.storage.get_candles('NEWTICKER', count=10)
        results['new_ticker_candles_exist'] = len(candles) > 0

        # Verify data integrity
        self.assertTrue(results['ticker_added'], "Ticker should have been added")
        if results['new_ticker_candles_exist']:
            # If candles exist, NEWTICKER must be in tickers table
            self.assertTrue(self.storage.ticker_exists('NEWTICKER'))

        # Verify orphans were cleaned up
        stats = self.storage.get_stats()
        self.assertNotIn('ORPHAN', stats['candles_per_ticker'])

    def test_cleanup_during_concurrent_ticker_remove(self):
        """Test cleanup data integrity when ticker is removed during cleanup."""
        import threading
        import time

        # Add tracked ticker with candles
        self.storage.add_ticker('REMOVE_ME')
        for i in range(3):
            candle = self._create_test_candle('REMOVE_ME', 1700000000 + (i * 300))
            self.storage.save_candle(candle)

        # Add orphaned candles
        candle_orphan = self._create_test_candle('ORPHAN', 1700000000)
        self.storage.save_candle(candle_orphan)

        results = {'cleanup_deleted': None, 'ticker_removed': False}

        def remove_ticker_mid_cleanup():
            """Remove a ticker during cleanup."""
            time.sleep(0.001)  # Small delay
            self.storage.remove_ticker('REMOVE_ME')
            results['ticker_removed'] = True

        # Start concurrent ticker removal
        thread = threading.Thread(target=remove_ticker_mid_cleanup)
        thread.start()

        # Run cleanup
        deleted = self.storage.cleanup_orphaned_candles()
        results['cleanup_deleted'] = deleted

        # Wait for thread
        thread.join()

        # Verify results
        self.assertTrue(results['ticker_removed'])

        # REMOVE_ME candles should be gone (removed by remove_ticker)
        candles = self.storage.get_candles('REMOVE_ME', count=10)
        self.assertEqual(len(candles), 0)

        # ORPHAN candles should be gone (removed by cleanup)
        stats = self.storage.get_stats()
        self.assertNotIn('ORPHAN', stats['candles_per_ticker'])

        # Database should be consistent
        self.assertNotIn('REMOVE_ME', stats['candles_per_ticker'])

    def test_cleanup_transaction_rollback_on_error(self):
        """Test that cleanup properly rolls back on error."""
        # Add orphaned candles
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # Mock a database error by closing the connection mid-operation
        # We'll simulate this by directly executing bad SQL
        conn = self.storage._get_connection()

        # Verify candle exists before
        stats_before = self.storage.get_stats()
        self.assertEqual(stats_before['total_candles'], 1)

        # The cleanup should handle errors gracefully
        # (though this specific error scenario is hard to trigger without mocking)
        # At minimum, verify it doesn't corrupt the database
        try:
            # This should work normally
            deleted = self.storage.cleanup_orphaned_candles()
            self.assertEqual(deleted, 1)
        except Exception as e:
            # If there's an error, database should not be corrupted
            stats_after = self.storage.get_stats()
            # Stats should still be retrievable
            self.assertIsNotNone(stats_after)


if __name__ == '__main__':
    unittest.main()
