"""
Tests for CandleEngine functionality.

Tests candle aggregation, interval changes, and active ticker summary generation.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.candle_engine import CandleEngine, CurrentCandle
from src.storage import Storage, Candle


class TestCandleEngineActiveTickers(unittest.TestCase):
    """Test cases for active ticker tracking and summary generation."""

    def setUp(self):
        """Create a temporary database and candle engine for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)
        self.engine = CandleEngine(self.storage, interval_minutes=5, max_candles=100)

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    def _simulate_tick(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """Helper to simulate a tick being processed."""
        self.engine.process_tick(ticker, price, volume, timestamp_ms)

    def test_get_active_tickers_empty(self):
        """Test get_active_tickers() returns empty list when no candles are active."""
        active = self.engine.get_active_tickers()
        self.assertEqual(active, [])
        self.assertIsInstance(active, list)

    def test_get_active_tickers_with_tickers(self):
        """Test get_active_tickers() returns list of ticker symbols."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Process ticks for multiple tickers
        self._simulate_tick('AAPL', 150.0, 100, timestamp)
        self._simulate_tick('MSFT', 300.0, 200, timestamp)
        self._simulate_tick('GOOGL', 140.0, 150, timestamp)

        active = self.engine.get_active_tickers()

        self.assertEqual(len(active), 3)
        self.assertIn('AAPL', active)
        self.assertIn('MSFT', active)
        self.assertIn('GOOGL', active)

    def test_get_active_tickers_summary_empty(self):
        """Test get_active_tickers_summary() returns empty list when no candles are active."""
        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(summary, [])
        self.assertIsInstance(summary, list)

    def test_get_active_tickers_summary_single_ticker(self):
        """Test get_active_tickers_summary() returns correct data for single ticker."""
        # Use a timestamp from 5 minutes ago to ensure it's in the past
        timestamp = int((datetime.now(timezone.utc).timestamp() - 300) * 1000)

        # Process multiple ticks for same ticker to build a candle
        self._simulate_tick('AAPL', 150.0, 100, timestamp)
        self._simulate_tick('AAPL', 155.0, 200, timestamp + 1000)
        self._simulate_tick('AAPL', 148.0, 150, timestamp + 2000)
        self._simulate_tick('AAPL', 152.0, 300, timestamp + 3000)

        summary = self.engine.get_active_tickers_summary()

        # Verify structure
        self.assertEqual(len(summary), 1)
        candle_summary = summary[0]

        # Check all required fields exist
        self.assertIn('ticker', candle_summary)
        self.assertIn('ticks', candle_summary)
        self.assertIn('current_price', candle_summary)
        self.assertIn('low', candle_summary)
        self.assertIn('high', candle_summary)
        self.assertIn('started', candle_summary)
        self.assertIn('started_ago', candle_summary)

        # Verify values
        self.assertEqual(candle_summary['ticker'], 'AAPL')
        self.assertEqual(candle_summary['ticks'], 4)
        self.assertEqual(candle_summary['current_price'], 152.0)  # Last price
        self.assertEqual(candle_summary['low'], 148.0)
        self.assertEqual(candle_summary['high'], 155.0)
        self.assertIsInstance(candle_summary['started'], int)
        self.assertIsInstance(candle_summary['started_ago'], str)

        # Verify started_ago format (should be in minutes since timestamp is 5 minutes ago)
        self.assertTrue(candle_summary['started_ago'].endswith('m ago'))
        minutes = int(candle_summary['started_ago'].split('m')[0])
        self.assertGreaterEqual(minutes, 4)  # At least 4 minutes
        self.assertLessEqual(minutes, 6)  # At most 6 minutes

    def test_get_active_tickers_summary_multiple_tickers(self):
        """Test get_active_tickers_summary() with multiple active tickers."""
        # Use a timestamp from 5 minutes ago to ensure it's in the past
        timestamp = int((datetime.now(timezone.utc).timestamp() - 300) * 1000)

        # AAPL ticks
        self._simulate_tick('AAPL', 150.0, 100, timestamp)
        self._simulate_tick('AAPL', 155.0, 200, timestamp + 1000)

        # MSFT ticks
        self._simulate_tick('MSFT', 300.0, 100, timestamp)
        self._simulate_tick('MSFT', 305.0, 200, timestamp + 1000)
        self._simulate_tick('MSFT', 298.0, 150, timestamp + 2000)

        # GOOGL ticks
        self._simulate_tick('GOOGL', 140.0, 50, timestamp)

        summary = self.engine.get_active_tickers_summary()

        # Should have 3 tickers
        self.assertEqual(len(summary), 3)

        # Extract tickers
        tickers = {s['ticker'] for s in summary}
        self.assertEqual(tickers, {'AAPL', 'MSFT', 'GOOGL'})

        # Verify each ticker has correct data
        aapl_summary = next(s for s in summary if s['ticker'] == 'AAPL')
        self.assertEqual(aapl_summary['ticks'], 2)
        self.assertEqual(aapl_summary['high'], 155.0)
        self.assertEqual(aapl_summary['low'], 150.0)

        msft_summary = next(s for s in summary if s['ticker'] == 'MSFT')
        self.assertEqual(msft_summary['ticks'], 3)
        self.assertEqual(msft_summary['high'], 305.0)
        self.assertEqual(msft_summary['low'], 298.0)

        googl_summary = next(s for s in summary if s['ticker'] == 'GOOGL')
        self.assertEqual(googl_summary['ticks'], 1)

    def test_get_active_tickers_summary_time_format_seconds(self):
        """Test that started_ago shows seconds for very recent candles (less than 60s)."""
        # Get a timestamp at the exact start of the current 5-minute interval
        now = datetime.now(timezone.utc)
        current_timestamp = int(now.timestamp())
        interval_seconds = 5 * 60  # 5 minutes
        interval_start = (current_timestamp // interval_seconds) * interval_seconds

        # Use timestamp from this interval (very recent)
        timestamp_ms = int(interval_start * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp_ms)

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        # Since the candle started at the beginning of the current interval,
        # it should show time in seconds or low minutes depending on how far into the interval we are
        started_ago = summary[0]['started_ago']
        self.assertIsInstance(started_ago, str)
        self.assertTrue(started_ago.endswith(' ago'))

    def test_get_active_tickers_summary_time_format_minutes(self):
        """Test that started_ago shows minutes for older candles."""
        # Create a candle that started 5 minutes ago
        five_minutes_ago = int((datetime.now(timezone.utc).timestamp() - 300) * 1000)

        self._simulate_tick('AAPL', 150.0, 100, five_minutes_ago)

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        # Should show minutes (e.g., "5m ago")
        self.assertTrue(summary[0]['started_ago'].endswith('m ago'))
        # Extract minutes value
        minutes = int(summary[0]['started_ago'].split('m')[0])
        self.assertGreaterEqual(minutes, 4)  # At least 4 minutes (accounting for test execution time)

    def test_get_active_tickers_summary_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Use prices with many decimal places
        self._simulate_tick('AAPL', 150.123456, 100, timestamp)
        self._simulate_tick('AAPL', 155.987654, 200, timestamp + 1000)
        self._simulate_tick('AAPL', 148.555555, 150, timestamp + 2000)

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        candle = summary[0]

        # Verify rounding to 2 decimals
        self.assertEqual(candle['current_price'], 148.56)  # 148.555555 rounded
        self.assertEqual(candle['high'], 155.99)  # 155.987654 rounded
        self.assertEqual(candle['low'], 148.56)  # 148.555555 rounded

    def test_get_active_tickers_summary_after_candle_complete(self):
        """Test that summary doesn't include completed candles."""
        # Start candle in first interval
        timestamp1 = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 150.0, 100, timestamp1)

        # Verify candle is active
        summary1 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary1), 1)

        # Move to next interval (5 minutes later) to complete previous candle
        timestamp2 = int(datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 152.0, 200, timestamp2)

        # Should only show the new active candle, not the completed one
        summary2 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary2), 1)

        # But tick count should be reset for the new candle
        self.assertEqual(summary2[0]['ticks'], 1)

    def test_get_active_tickers_summary_consistency_with_get_active_tickers(self):
        """Test that summary and simple list return consistent ticker sets."""
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        tickers = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA']
        for ticker in tickers:
            self._simulate_tick(ticker, 100.0, 100, timestamp)

        active_list = self.engine.get_active_tickers()
        summary_list = self.engine.get_active_tickers_summary()

        # Both should return same number of tickers
        self.assertEqual(len(active_list), len(summary_list))

        # All tickers in simple list should be in summary
        summary_tickers = {s['ticker'] for s in summary_list}
        self.assertEqual(set(active_list), summary_tickers)

    def test_get_active_tickers_summary_edge_case_zero_ticks(self):
        """Test that summary handles edge case of candle with zero tick_count gracefully."""
        # This is a theoretical edge case - manually inject a candle with 0 ticks
        # to test robustness
        start_time = int(datetime.now(timezone.utc).timestamp())
        self.engine._current_candles['EDGE'] = CurrentCandle(
            ticker='EDGE',
            start_timestamp=start_time,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=0,
            tick_count=0
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['ticker'], 'EDGE')
        self.assertEqual(summary[0]['ticks'], 0)

    def test_get_active_tickers_summary_preserves_original_data(self):
        """Test that getting summary doesn't modify internal candle state."""
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Get summary multiple times
        summary1 = self.engine.get_active_tickers_summary()
        summary2 = self.engine.get_active_tickers_summary()

        # Should return same data
        self.assertEqual(summary1[0]['ticker'], summary2[0]['ticker'])
        self.assertEqual(summary1[0]['ticks'], summary2[0]['ticks'])
        self.assertEqual(summary1[0]['current_price'], summary2[0]['current_price'])

    def test_get_active_tickers_summary_time_format_deterministic_30s(self):
        """Test started_ago calculation for 30 seconds (deterministic without mocking)."""
        # Create a candle that started 30 seconds ago from now
        now = datetime.now(timezone.utc)
        candle_start_time = int((now.timestamp() - 30))

        self.engine._current_candles['AAPL'] = CurrentCandle(
            ticker='AAPL',
            start_timestamp=candle_start_time,
            open=150.0,
            high=155.0,
            low=148.0,
            close=152.0,
            volume=1000,
            tick_count=10
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        # Should be around 30s ago (allow 29-31s for test execution time)
        started_ago = summary[0]['started_ago']
        self.assertTrue(started_ago.endswith('s ago'), f"Expected seconds format, got: {started_ago}")
        seconds = int(started_ago.split('s')[0])
        self.assertGreaterEqual(seconds, 29)
        self.assertLessEqual(seconds, 32)

    def test_get_active_tickers_summary_time_format_deterministic_boundary_59s(self):
        """Test started_ago at seconds/minutes boundary (59-60s)."""
        # Create a candle 59 seconds ago
        now = datetime.now(timezone.utc)
        candle_start_time = int((now.timestamp() - 59))

        self.engine._current_candles['AAPL'] = CurrentCandle(
            ticker='AAPL',
            start_timestamp=candle_start_time,
            open=150.0,
            high=150.0,
            low=150.0,
            close=150.0,
            volume=100,
            tick_count=1
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        started_ago = summary[0]['started_ago']
        # Should show seconds (<60s) or just transitioned to 1m ago
        self.assertTrue(
            started_ago.endswith('s ago') or started_ago == '1m ago',
            f"Expected seconds or 1m ago at boundary, got: {started_ago}"
        )

    def test_get_active_tickers_summary_time_format_deterministic_60s(self):
        """Test started_ago at exactly 60 seconds (should show 1m ago)."""
        # Create a candle 65 seconds ago (to ensure we're past the boundary)
        now = datetime.now(timezone.utc)
        candle_start_time = int((now.timestamp() - 65))

        self.engine._current_candles['AAPL'] = CurrentCandle(
            ticker='AAPL',
            start_timestamp=candle_start_time,
            open=150.0,
            high=150.0,
            low=150.0,
            close=150.0,
            volume=100,
            tick_count=1
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        started_ago = summary[0]['started_ago']
        # Should definitely be in minutes now
        self.assertTrue(started_ago.endswith('m ago'), f"Expected minutes format, got: {started_ago}")
        minutes = int(started_ago.split('m')[0])
        self.assertEqual(minutes, 1)

    def test_get_active_tickers_summary_time_format_deterministic_hours(self):
        """Test started_ago for candles started hours ago."""
        # Create a candle 2 hours (7200 seconds) ago
        now = datetime.now(timezone.utc)
        candle_start_time = int((now.timestamp() - 7200))

        self.engine._current_candles['AAPL'] = CurrentCandle(
            ticker='AAPL',
            start_timestamp=candle_start_time,
            open=150.0,
            high=150.0,
            low=150.0,
            close=150.0,
            volume=100,
            tick_count=1
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        started_ago = summary[0]['started_ago']
        # Should show in hours (2h ago)
        self.assertTrue(started_ago.endswith('h ago'), f"Expected hours format, got: {started_ago}")
        hours = int(started_ago.split('h')[0])
        self.assertEqual(hours, 2)

    def test_get_active_tickers_summary_time_format_deterministic_days(self):
        """Test started_ago for candles started days ago."""
        # Create a candle 3 days (259200 seconds) ago
        now = datetime.now(timezone.utc)
        candle_start_time = int((now.timestamp() - 259200))

        self.engine._current_candles['AAPL'] = CurrentCandle(
            ticker='AAPL',
            start_timestamp=candle_start_time,
            open=150.0,
            high=150.0,
            low=150.0,
            close=150.0,
            volume=100,
            tick_count=1
        )

        summary = self.engine.get_active_tickers_summary()

        self.assertEqual(len(summary), 1)
        started_ago = summary[0]['started_ago']
        # Should show in days (3d ago)
        self.assertTrue(started_ago.endswith('d ago'), f"Expected days format, got: {started_ago}")
        days = int(started_ago.split('d')[0])
        self.assertEqual(days, 3)

    def test_get_active_tickers_summary_all_candles_completed(self):
        """Test that summary returns empty list when all candles are completed mid-test."""
        # Start candles in first interval
        timestamp1 = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 150.0, 100, timestamp1)
        self._simulate_tick('MSFT', 300.0, 200, timestamp1)

        # Verify both candles are active
        summary1 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary1), 2)

        # Move to next interval (5 minutes later) to complete both candles
        timestamp2 = int(datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 152.0, 100, timestamp2)
        self._simulate_tick('MSFT', 302.0, 200, timestamp2)

        # Now we should have 2 new active candles
        summary2 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary2), 2)

        # Both should have tick_count = 1 (new candles)
        for candle in summary2:
            self.assertEqual(candle['ticks'], 1)

    def test_get_active_tickers_summary_mixed_completion(self):
        """Test summary when some candles complete but others remain active."""
        # Start AAPL in interval 1
        timestamp1 = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 150.0, 100, timestamp1)
        self._simulate_tick('AAPL', 151.0, 100, timestamp1 + 1000)

        # Start MSFT also in interval 1
        self._simulate_tick('MSFT', 300.0, 200, timestamp1)

        # Verify both active
        summary1 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary1), 2)

        # Move AAPL to interval 2, but keep MSFT in interval 1
        timestamp2 = int(datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 152.0, 100, timestamp2)

        # Now AAPL should have new candle (tick=1), MSFT should still have old candle
        summary2 = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary2), 2)

        aapl_summary = next(s for s in summary2 if s['ticker'] == 'AAPL')
        msft_summary = next(s for s in summary2 if s['ticker'] == 'MSFT')

        self.assertEqual(aapl_summary['ticks'], 1)  # New candle
        self.assertEqual(msft_summary['ticks'], 1)  # Old candle still active


class TestCandleEngineBasicFunctionality(unittest.TestCase):
    """Test cases for basic candle engine operations."""

    def setUp(self):
        """Create a temporary database and candle engine for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)
        self.engine = CandleEngine(self.storage, interval_minutes=5, max_candles=100)

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    def _simulate_tick(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """Helper to simulate a tick being processed."""
        self.engine.process_tick(ticker, price, volume, timestamp_ms)

    def test_process_tick_creates_candle(self):
        """Test that processing a tick creates a new candle."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        active = self.engine.get_active_tickers()
        self.assertEqual(len(active), 1)
        self.assertIn('AAPL', active)

    def test_process_tick_updates_ohlcv(self):
        """Test that multiple ticks update OHLCV values correctly."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick sets open
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Higher tick updates high
        self._simulate_tick('AAPL', 155.0, 200, timestamp + 1000)

        # Lower tick updates low
        self._simulate_tick('AAPL', 148.0, 150, timestamp + 2000)

        # Last tick sets close
        self._simulate_tick('AAPL', 152.0, 300, timestamp + 3000)

        summary = self.engine.get_active_tickers_summary()
        self.assertEqual(len(summary), 1)

        candle = summary[0]
        self.assertEqual(candle['current_price'], 152.0)  # Close
        self.assertEqual(candle['high'], 155.0)
        self.assertEqual(candle['low'], 148.0)
        self.assertEqual(candle['ticks'], 4)

    def test_interval_change_completes_candles(self):
        """Test that changing interval completes current candles."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Verify candle is active
        self.assertEqual(len(self.engine.get_active_tickers()), 1)

        # Change interval
        self.engine.set_interval(15)

        # Active candles should be cleared
        self.assertEqual(len(self.engine.get_active_tickers()), 0)

    def test_remove_ticker(self):
        """Test that removing a ticker removes its active candle."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp)
        self._simulate_tick('MSFT', 300.0, 200, timestamp)

        self.assertEqual(len(self.engine.get_active_tickers()), 2)

        # Remove one ticker
        self.engine.remove_ticker('AAPL')

        active = self.engine.get_active_tickers()
        self.assertEqual(len(active), 1)
        self.assertNotIn('AAPL', active)
        self.assertIn('MSFT', active)


if __name__ == '__main__':
    unittest.main()


class TestCandleEngineSaveFrequency(unittest.TestCase):
    """Test cases for tick-save frequency optimization."""

    def setUp(self):
        """Create a temporary database and candle engine for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.storage = Storage(self.temp_db.name)
        self.engine = CandleEngine(self.storage, interval_minutes=5, max_candles=100)

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    def _simulate_tick(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """Helper to simulate a tick being processed."""
        self.engine.process_tick(ticker, price, volume, timestamp_ms)

    def _get_db_candle(self, ticker: str):
        """Helper to get current candle from database."""
        return self.storage.get_current_candle(ticker)

    def test_first_tick_saves_to_db(self):
        """Test that the first tick of a new candle is saved to DB."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # First tick should be saved
        db_candle = self._get_db_candle('AAPL')
        self.assertIsNotNone(db_candle)
        self.assertEqual(db_candle.open, 150.0)
        self.assertEqual(db_candle.tick_count, 1)

    def test_ticks_below_threshold_not_saved(self):
        """Test that ticks below SAVE_EVERY_N_TICKS threshold are not saved to DB."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick (always saved)
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Get initial DB state
        db_candle = self._get_db_candle('AAPL')
        initial_tick_count = db_candle.tick_count

        # Process N-1 more ticks (should not trigger save)
        for i in range(1, self.engine.SAVE_EVERY_N_TICKS - 1):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp + i * 1000)

        # DB should still show initial tick count
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, initial_tick_count)

    def test_tick_threshold_triggers_save(self):
        """Test that reaching SAVE_EVERY_N_TICKS threshold triggers DB save."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick (always saved)
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Process exactly N-1 more ticks to reach threshold
        for i in range(1, self.engine.SAVE_EVERY_N_TICKS):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp + i * 1000)

        # DB should now show all ticks
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS)
        self.assertEqual(db_candle.close, 150.0 + self.engine.SAVE_EVERY_N_TICKS - 1)

    def test_time_threshold_triggers_save(self):
        """Test that SAVE_EVERY_M_SECONDS threshold triggers DB save."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick (always saved)
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Wait for time threshold
        sleep(self.engine.SAVE_EVERY_M_SECONDS + 0.5)

        # Process one more tick (should trigger time-based save)
        self._simulate_tick('AAPL', 151.0, 100, timestamp + 1000)

        # DB should show both ticks
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, 2)
        self.assertEqual(db_candle.close, 151.0)

    def test_save_counter_resets_after_save(self):
        """Test that ticks_since_save counter resets after a save."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick (always saved)
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Process N ticks to trigger first save
        for i in range(1, self.engine.SAVE_EVERY_N_TICKS):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp + i * 1000)

        # Verify save happened
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS)

        # Process N-1 more ticks (should not save yet)
        for i in range(self.engine.SAVE_EVERY_N_TICKS - 1):
            self._simulate_tick('AAPL', 160.0 + i, 100, timestamp + (self.engine.SAVE_EVERY_N_TICKS + i) * 1000)

        # DB should still show old tick count
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS)

        # One more tick should trigger next save
        self._simulate_tick('AAPL', 170.0, 100, timestamp + (self.engine.SAVE_EVERY_N_TICKS * 2) * 1000)

        # DB should now show updated tick count
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS * 2)

    def test_candle_completion_always_saves(self):
        """Test that candle completion always saves to DB regardless of thresholds."""
        # Start candle in first interval
        timestamp1 = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 150.0, 100, timestamp1)

        # Process only 2 more ticks (below threshold)
        self._simulate_tick('AAPL', 151.0, 100, timestamp1 + 1000)
        self._simulate_tick('AAPL', 152.0, 100, timestamp1 + 2000)

        # Move to next interval to complete candle
        timestamp2 = int(datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 153.0, 100, timestamp2)

        # Get completed candles from DB
        candles = self.storage.get_candles('AAPL', count=10, include_current=False)

        # Should have 1 completed candle with 3 ticks
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].tick_count, 3)
        self.assertTrue(candles[0].is_complete)

    def test_multiple_tickers_independent_save_counters(self):
        """Test that different tickers have independent save counters."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # AAPL: Process N ticks (should trigger save)
        for i in range(self.engine.SAVE_EVERY_N_TICKS):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp + i * 1000)

        # MSFT: Process only 2 ticks (should not trigger save after first)
        self._simulate_tick('MSFT', 300.0, 100, timestamp)
        self._simulate_tick('MSFT', 301.0, 100, timestamp + 1000)

        # AAPL should be saved with N ticks
        aapl_candle = self._get_db_candle('AAPL')
        self.assertEqual(aapl_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS)

        # MSFT should only show first tick in DB
        msft_candle = self._get_db_candle('MSFT')
        self.assertEqual(msft_candle.tick_count, 1)

    def test_rapid_ticks_respect_threshold(self):
        """Test that rapid ticks (within time threshold) only save at tick threshold."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Process ticks rapidly (no time delay)
        for i in range(self.engine.SAVE_EVERY_N_TICKS + 5):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp + i * 100)  # 100ms apart

        # Should have saved at tick threshold (N) but not the extra 5
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, self.engine.SAVE_EVERY_N_TICKS)

    def test_slow_ticks_respect_time_threshold(self):
        """Test that slow ticks (exceeding time threshold) save based on time."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # First tick
        self._simulate_tick('AAPL', 150.0, 100, timestamp)

        # Wait for time threshold
        sleep(self.engine.SAVE_EVERY_M_SECONDS + 0.5)

        # Second tick (should trigger time-based save)
        self._simulate_tick('AAPL', 151.0, 100, timestamp + 1000)

        # Should show 2 ticks even though we're far below tick threshold
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.tick_count, 2)

    def test_new_candle_after_completion_resets_counters(self):
        """Test that starting a new candle after completion resets save counters."""
        # Start candle in first interval
        timestamp1 = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Process many ticks in first candle
        for i in range(self.engine.SAVE_EVERY_N_TICKS * 2):
            self._simulate_tick('AAPL', 150.0 + i, 100, timestamp1 + i * 1000)

        # Move to next interval (completes candle and starts new one)
        timestamp2 = int(datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self._simulate_tick('AAPL', 200.0, 100, timestamp2)

        # New candle should be saved (first tick always saves)
        db_candle = self._get_db_candle('AAPL')
        self.assertIsNotNone(db_candle)
        self.assertEqual(db_candle.tick_count, 1)
        self.assertEqual(db_candle.open, 200.0)
        self.assertFalse(db_candle.is_complete)

    def test_ohlcv_accuracy_despite_delayed_saves(self):
        """Test that OHLCV values are accurate even when saves are delayed."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Process ticks with varying prices
        prices = [150.0, 155.0, 148.0, 152.0, 160.0, 145.0, 158.0]
        for i, price in enumerate(prices):
            self._simulate_tick('AAPL', price, 100, timestamp + i * 1000)

        # Force save by reaching threshold
        for i in range(self.engine.SAVE_EVERY_N_TICKS - len(prices)):
            self._simulate_tick('AAPL', 150.0, 100, timestamp + (len(prices) + i) * 1000)

        # Verify OHLCV is correct
        db_candle = self._get_db_candle('AAPL')
        self.assertEqual(db_candle.open, 150.0)  # First price
        self.assertEqual(db_candle.high, 160.0)  # Max price
        self.assertEqual(db_candle.low, 145.0)   # Min price
        self.assertEqual(db_candle.close, 150.0) # Last price

    def test_volume_accumulation_despite_delayed_saves(self):
        """Test that volume accumulates correctly even when saves are delayed."""
        timestamp = int(datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Process ticks with varying volumes
        volumes = [100, 200, 150, 300, 250]
        for i, volume in enumerate(volumes):
            self._simulate_tick('AAPL', 150.0, volume, timestamp + i * 1000)

        # Force save
        for i in range(self.engine.SAVE_EVERY_N_TICKS - len(volumes)):
            self._simulate_tick('AAPL', 150.0, 100, timestamp + (len(volumes) + i) * 1000)

        # Verify volume is correct sum
        db_candle = self._get_db_candle('AAPL')
        expected_volume = sum(volumes) + (100 * (self.engine.SAVE_EVERY_N_TICKS - len(volumes)))
        self.assertEqual(db_candle.volume, expected_volume)


class TestCandleEngineTickerStatusFlush(unittest.TestCase):
    """Tests for ticker status throttling and flush failure handling."""

    def setUp(self):
        self.storage = Mock()
        self.engine = CandleEngine(
            self.storage,
            interval_minutes=1,
            max_candles=100,
            save_every_n_ticks=1000,
            save_every_m_seconds=1000.0,
            ticker_status_update_interval_seconds=1.0
        )

    def test_immediate_status_failure_requeues_pending_update(self):
        """Failed immediate status update should be re-queued for retry."""
        self.storage.update_ticker_status.side_effect = RuntimeError("db down")

        self.engine.process_tick("AAPL", 150.0, 10, 1735747200000)

        self.assertTrue(self.storage.update_ticker_status.called)
        self.assertIn("AAPL", self.engine._pending_ticker_status)
        last_tick_at, last_price = self.engine._pending_ticker_status["AAPL"]
        self.assertIsInstance(last_tick_at, str)
        self.assertEqual(last_price, 150.0)

    def test_flush_pending_statuses_success_clears_queue(self):
        """Successful flush should clear pending queue and update write marker."""
        with self.engine._lock:
            self.engine._pending_ticker_status["MSFT"] = (
                datetime.now(timezone.utc).isoformat(),
                320.5
            )

        self.engine.flush_pending_ticker_statuses()

        self.assertNotIn("MSFT", self.engine._pending_ticker_status)
        self.assertIn("MSFT", self.engine._last_ticker_status_write)
        self.storage.update_ticker_status.assert_called_once()

    def test_flush_pending_statuses_failure_keeps_queue(self):
        """Failed flush should keep pending status for next retry cycle."""
        with self.engine._lock:
            self.engine._pending_ticker_status["NVDA"] = (
                datetime.now(timezone.utc).isoformat(),
                900.0
            )
        self.storage.update_ticker_status.side_effect = RuntimeError("temporary db error")

        self.engine.flush_pending_ticker_statuses()

        self.assertIn("NVDA", self.engine._pending_ticker_status)
        self.storage.update_ticker_status.assert_called_once()


class TestCandleEngineCandleWriteFlush(unittest.TestCase):
    """Tests for queued candle writes and flush behavior."""

    def setUp(self):
        self.storage = Mock()
        self.engine = CandleEngine(
            self.storage,
            interval_minutes=1,
            max_candles=100,
            save_every_n_ticks=1000,
            save_every_m_seconds=1000.0,
            ticker_status_update_interval_seconds=1.0,
            candle_write_queue_maxsize=100
        )

    def test_process_tick_enqueues_candle_write_without_immediate_db_write(self):
        """Tick processing should queue candle write; flush performs DB write."""
        self.engine.process_tick("AAPL", 100.0, 10, 1735747200000)

        self.storage.save_candle.assert_not_called()
        metrics = self.engine.get_candle_write_metrics()
        self.assertEqual(metrics["candle_write_queue_size"], 1)

        self.engine.flush_pending_candle_writes()
        self.storage.save_candle.assert_called_once()
        metrics = self.engine.get_candle_write_metrics()
        self.assertEqual(metrics["candle_write_queue_size"], 0)

    def test_flush_failed_candle_write_is_requeued(self):
        """Failed candle write flush should keep item queued for retry."""
        self.engine.process_tick("MSFT", 200.0, 10, 1735747200000)
        self.storage.save_candle.side_effect = RuntimeError("db error")

        self.engine.flush_pending_candle_writes()

        self.assertEqual(self.storage.save_candle.call_count, 1)
        metrics = self.engine.get_candle_write_metrics()
        self.assertEqual(metrics["candle_write_queue_size"], 1)

    def test_complete_candle_overrides_incomplete_for_same_key(self):
        """Completed candle write should replace queued incomplete version."""
        base_ts = 1735747200000
        self.engine.process_tick("NVDA", 300.0, 10, base_ts)
        self.engine.process_tick("NVDA", 301.0, 10, base_ts + 60000)  # next 1m candle

        self.engine.flush_pending_candle_writes()

        writes = [call.args[0] for call in self.storage.save_candle.call_args_list]
        completed_same_timestamp = [
            c for c in writes if c.ticker == "NVDA" and c.timestamp == 1735747200 and c.is_complete
        ]
        self.assertEqual(len(completed_same_timestamp), 1)

    def test_full_completed_queue_evicts_oldest_for_new_completed_candle(self):
        """When queue is full of completed candles, newest completed should replace oldest."""
        self.engine.candle_write_queue_maxsize = 2

        with self.engine._lock:
            self.engine._enqueue_candle_write_locked(Candle("AAPL", 1, "", 1, 1, 1, 1, 1, 1, True, 1))
            self.engine._enqueue_candle_write_locked(Candle("MSFT", 2, "", 1, 1, 1, 1, 1, 1, True, 1))
            self.engine._enqueue_candle_write_locked(Candle("NVDA", 3, "", 1, 1, 1, 1, 1, 1, True, 1))

            queued_keys = list(self.engine._pending_candle_writes.keys())

        self.assertEqual(len(queued_keys), 2)
        self.assertNotIn(("AAPL", 1, 1), queued_keys)
        self.assertIn(("MSFT", 2, 1), queued_keys)
        self.assertIn(("NVDA", 3, 1), queued_keys)

    def test_full_completed_queue_drops_incomplete_candle(self):
        """When queue is full of completed candles, incoming incomplete write is dropped."""
        self.engine.candle_write_queue_maxsize = 2

        with self.engine._lock:
            self.engine._enqueue_candle_write_locked(Candle("AAPL", 1, "", 1, 1, 1, 1, 1, 1, True, 1))
            self.engine._enqueue_candle_write_locked(Candle("MSFT", 2, "", 1, 1, 1, 1, 1, 1, True, 1))
            self.engine._enqueue_candle_write_locked(Candle("NVDA", 3, "", 1, 1, 1, 1, 1, 1, False, 1))

            queued_keys = list(self.engine._pending_candle_writes.keys())

        self.assertEqual(len(queued_keys), 2)
        self.assertIn(("AAPL", 1, 1), queued_keys)
        self.assertIn(("MSFT", 2, 1), queued_keys)
        self.assertNotIn(("NVDA", 3, 1), queued_keys)

        metrics = self.engine.get_candle_write_metrics()
        self.assertEqual(metrics["candle_write_dropped_count"], 1)

    def test_full_mixed_queue_evicts_oldest_incomplete_first(self):
        """When full, eviction should prefer oldest incomplete entry over completed ones."""
        self.engine.candle_write_queue_maxsize = 3

        with self.engine._lock:
            self.engine._enqueue_candle_write_locked(Candle("AAPL", 1, "", 1, 1, 1, 1, 1, 1, False, 1))
            self.engine._enqueue_candle_write_locked(Candle("MSFT", 2, "", 1, 1, 1, 1, 1, 1, True, 1))
            self.engine._enqueue_candle_write_locked(Candle("GOOG", 3, "", 1, 1, 1, 1, 1, 1, False, 1))
            self.engine._enqueue_candle_write_locked(Candle("NVDA", 4, "", 1, 1, 1, 1, 1, 1, True, 1))

            queued_keys = list(self.engine._pending_candle_writes.keys())

        self.assertEqual(len(queued_keys), 3)
        self.assertNotIn(("AAPL", 1, 1), queued_keys)
        self.assertIn(("MSFT", 2, 1), queued_keys)
        self.assertIn(("GOOG", 3, 1), queued_keys)
        self.assertIn(("NVDA", 4, 1), queued_keys)

    def test_multiple_flush_failures_keep_item_until_success(self):
        """Repeated flush failures should keep item queued until a successful write."""
        self.engine.process_tick("TSLA", 250.0, 10, 1735747200000)
        self.storage.save_candle.side_effect = [
            RuntimeError("db error #1"),
            RuntimeError("db error #2"),
            None
        ]

        self.engine.flush_pending_candle_writes()
        self.engine.flush_pending_candle_writes()
        self.engine.flush_pending_candle_writes()

        self.assertEqual(self.storage.save_candle.call_count, 3)
        metrics = self.engine.get_candle_write_metrics()
        self.assertEqual(metrics["candle_write_queue_size"], 0)


if __name__ == '__main__':
    unittest.main()
