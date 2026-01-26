"""
Unit tests for candle aggregation module.
"""

import pytest
from src.candle_aggregator import CandleAggregator, AggregatedCandle
from src.storage import Candle


class TestValidateRequest:
    """Tests for CandleAggregator.validate_request()"""

    def test_valid_same_interval(self):
        """Requesting same interval as stored should be valid."""
        is_valid, base, error = CandleAggregator.validate_request([5], 5)
        assert is_valid is True
        assert base == 5
        assert error is None

    def test_valid_multiple_of_base(self):
        """Requesting multiple of base interval should be valid."""
        is_valid, base, error = CandleAggregator.validate_request([5], 15)
        assert is_valid is True
        assert base == 5
        assert error is None

    def test_valid_uses_largest_interval(self):
        """When multiple intervals exist, should use largest as base."""
        is_valid, base, error = CandleAggregator.validate_request([1, 5], 10)
        assert is_valid is True
        assert base == 5  # Uses largest (5), not smallest (1)
        assert error is None

    def test_invalid_smaller_than_base(self):
        """Requesting smaller interval than stored should fail."""
        is_valid, base, error = CandleAggregator.validate_request([5], 3)
        assert is_valid is False
        assert base is None
        assert "smaller than largest stored interval" in error

    def test_invalid_not_divisible(self):
        """Requesting non-divisible interval should fail."""
        is_valid, base, error = CandleAggregator.validate_request([5], 12)
        assert is_valid is False
        assert base is None
        assert "not divisible" in error
        assert "Valid options" in error

    def test_invalid_empty_intervals(self):
        """Empty stored intervals should fail."""
        is_valid, base, error = CandleAggregator.validate_request([], 5)
        assert is_valid is False
        assert base is None
        assert "No candles found" in error

    def test_invalid_with_mixed_intervals(self):
        """With mixed intervals [1, 5], requesting 3 should fail (3 < 5)."""
        is_valid, base, error = CandleAggregator.validate_request([1, 5], 3)
        assert is_valid is False
        assert base is None
        assert "smaller than largest stored interval" in error

    def test_valid_large_aggregation(self):
        """Large aggregation factor should work."""
        is_valid, base, error = CandleAggregator.validate_request([5], 60)
        assert is_valid is True
        assert base == 5
        assert error is None


class TestAggregate:
    """Tests for CandleAggregator.aggregate()"""

    def _make_candle(self, ticker: str, timestamp: int, o: float, h: float, 
                     l: float, c: float, v: int, ticks: int, interval: int) -> Candle:
        """Helper to create test candles."""
        return Candle(
            ticker=ticker,
            timestamp=timestamp,
            datetime_utc=f"2025-01-01 00:00:00 UTC",
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            tick_count=ticks,
            is_complete=True,
            interval_minutes=interval
        )

    def test_aggregate_empty_list(self):
        """Empty candle list should return empty result."""
        result = CandleAggregator.aggregate([], 15, 5, "AAPL")
        assert result == []

    def test_aggregate_single_candle(self):
        """Single candle aggregation."""
        candles = [
            self._make_candle("AAPL", 1000, 100.0, 105.0, 99.0, 102.0, 1000, 50, 5)
        ]
        result = CandleAggregator.aggregate(candles, 15, 5, "AAPL")
        
        assert len(result) == 1
        assert result[0].open == 100.0
        assert result[0].high == 105.0
        assert result[0].low == 99.0
        assert result[0].close == 102.0
        assert result[0].volume == 1000
        assert result[0].tick_count == 50
        assert result[0].expected_candles == 3
        assert result[0].actual_candles == 1
        assert result[0].has_gaps is True

    def test_aggregate_full_period(self):
        """Three 5-minute candles into one 15-minute candle."""
        # 15-minute period starting at timestamp 0
        candles = [
            self._make_candle("AAPL", 0, 100.0, 102.0, 99.0, 101.0, 1000, 50, 5),
            self._make_candle("AAPL", 300, 101.0, 103.0, 100.0, 102.0, 1500, 60, 5),
            self._make_candle("AAPL", 600, 102.0, 104.0, 101.0, 103.0, 2000, 70, 5),
        ]
        result = CandleAggregator.aggregate(candles, 15, 5, "AAPL")
        
        assert len(result) == 1
        agg = result[0]
        assert agg.open == 100.0      # First candle's open
        assert agg.high == 104.0      # Max of all highs
        assert agg.low == 99.0        # Min of all lows
        assert agg.close == 103.0     # Last candle's close
        assert agg.volume == 4500     # Sum of volumes
        assert agg.tick_count == 180  # Sum of tick counts
        assert agg.expected_candles == 3
        assert agg.actual_candles == 3
        assert agg.has_gaps is False

    def test_aggregate_with_gap(self):
        """Two candles with one missing in the middle."""
        # 15-minute period, but middle candle is missing
        candles = [
            self._make_candle("AAPL", 0, 100.0, 102.0, 99.0, 101.0, 1000, 50, 5),
            # Missing candle at timestamp 300
            self._make_candle("AAPL", 600, 102.0, 104.0, 101.0, 103.0, 2000, 70, 5),
        ]
        result = CandleAggregator.aggregate(candles, 15, 5, "AAPL")
        
        assert len(result) == 1
        agg = result[0]
        assert agg.open == 100.0      # First candle's open
        assert agg.high == 104.0      # Max of available highs
        assert agg.low == 99.0        # Min of available lows
        assert agg.close == 103.0     # Last candle's close
        assert agg.volume == 3000     # Sum of available volumes
        assert agg.expected_candles == 3
        assert agg.actual_candles == 2
        assert agg.has_gaps is True

    def test_aggregate_multiple_periods(self):
        """Multiple 15-minute periods from 5-minute candles."""
        candles = [
            # First 15-minute period (0-900)
            self._make_candle("AAPL", 0, 100.0, 102.0, 99.0, 101.0, 1000, 50, 5),
            self._make_candle("AAPL", 300, 101.0, 103.0, 100.0, 102.0, 1500, 60, 5),
            self._make_candle("AAPL", 600, 102.0, 104.0, 101.0, 103.0, 2000, 70, 5),
            # Second 15-minute period (900-1800)
            self._make_candle("AAPL", 900, 103.0, 106.0, 102.0, 105.0, 2500, 80, 5),
            self._make_candle("AAPL", 1200, 105.0, 107.0, 104.0, 106.0, 3000, 90, 5),
        ]
        result = CandleAggregator.aggregate(candles, 15, 5, "AAPL")
        
        assert len(result) == 2
        
        # First period - complete
        assert result[0].timestamp == 0
        assert result[0].open == 100.0
        assert result[0].close == 103.0
        assert result[0].has_gaps is False
        
        # Second period - incomplete (2 of 3 candles)
        assert result[1].timestamp == 900
        assert result[1].open == 103.0
        assert result[1].close == 106.0
        assert result[1].expected_candles == 3
        assert result[1].actual_candles == 2
        assert result[1].has_gaps is True

    def test_aggregate_preserves_ticker(self):
        """Aggregated candles should have correct ticker."""
        candles = [
            self._make_candle("TSLA", 0, 200.0, 210.0, 195.0, 205.0, 5000, 100, 5)
        ]
        result = CandleAggregator.aggregate(candles, 15, 5, "TSLA")
        
        assert result[0].ticker == "TSLA"

    def test_aggregate_interval_minutes(self):
        """Aggregated candles should have target interval."""
        candles = [
            self._make_candle("AAPL", 0, 100.0, 102.0, 99.0, 101.0, 1000, 50, 5)
        ]
        result = CandleAggregator.aggregate(candles, 30, 5, "AAPL")
        
        assert result[0].interval_minutes == 30


class TestAggregatedCandleToDict:
    """Tests for AggregatedCandle.to_dict()"""

    def test_to_dict_contains_all_fields(self):
        """to_dict should include all fields including gap tracking."""
        candle = AggregatedCandle(
            ticker="AAPL",
            timestamp=1000,
            datetime_utc="2025-01-01 00:00:00 UTC",
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=5000,
            tick_count=200,
            interval_minutes=15,
            expected_candles=3,
            actual_candles=2,
            has_gaps=True
        )
        
        d = candle.to_dict()
        
        assert d['ticker'] == "AAPL"
        assert d['timestamp'] == 1000
        assert d['open'] == 100.0
        assert d['high'] == 105.0
        assert d['low'] == 99.0
        assert d['close'] == 102.0
        assert d['volume'] == 5000
        assert d['tick_count'] == 200
        assert d['interval_minutes'] == 15
        assert d['expected_candles'] == 3
        assert d['actual_candles'] == 2
        assert d['has_gaps'] is True
