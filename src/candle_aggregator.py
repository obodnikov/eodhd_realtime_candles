"""
Candle aggregation module.
Aggregates smaller interval candles into larger intervals.
"""

import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass, asdict

from .storage import Candle

logger = logging.getLogger(__name__)


@dataclass
class AggregatedCandle:
    """Aggregated OHLCV candle with gap tracking."""
    ticker: str
    timestamp: int
    datetime_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    interval_minutes: int
    expected_candles: int
    actual_candles: int
    has_gaps: bool

    def to_dict(self) -> dict:
        return asdict(self)


class CandleAggregator:
    """
    Aggregates candles from base interval to target interval.
    
    Rules:
    - Target interval must be >= largest stored interval
    - Target interval must be divisible by largest stored interval
    - Gaps are tracked but aggregation proceeds with available data
    """

    @staticmethod
    def validate_request(
        stored_intervals: List[int],
        requested_minutes: int
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Validate if aggregation request is possible.
        
        Args:
            stored_intervals: List of interval_minutes values in DB for ticker
            requested_minutes: Requested aggregation interval
            
        Returns:
            Tuple of (is_valid, base_interval, error_message)
            - If valid: (True, base_interval_to_use, None)
            - If invalid: (False, None, error_description)
        """
        if not stored_intervals:
            return False, None, "No candles found for this ticker"

        largest_interval = max(stored_intervals)

        # Check minimum
        if requested_minutes < largest_interval:
            return (
                False,
                None,
                f"Requested interval ({requested_minutes}m) is smaller than "
                f"largest stored interval ({largest_interval}m). "
                f"Minimum allowed: {largest_interval}m"
            )

        # Check divisibility
        if requested_minutes % largest_interval != 0:
            # Calculate valid options for helpful error message
            valid_options = [
                largest_interval * i
                for i in range(1, 13)  # Show up to 12x multiplier
                if largest_interval * i <= 120  # Cap at 2 hours
            ]
            return (
                False,
                None,
                f"Requested interval ({requested_minutes}m) is not divisible by "
                f"largest stored interval ({largest_interval}m). "
                f"Valid options: {valid_options}"
            )

        return True, largest_interval, None

    @staticmethod
    def aggregate(
        candles: List[Candle],
        target_minutes: int,
        base_interval: int,
        ticker: str
    ) -> List[AggregatedCandle]:
        """
        Aggregate candles into larger intervals.
        
        Args:
            candles: List of base candles (must be sorted by timestamp ASC)
            target_minutes: Target interval in minutes
            base_interval: Base interval of source candles
            ticker: Ticker symbol
            
        Returns:
            List of aggregated candles
        """
        if not candles:
            return []

        target_seconds = target_minutes * 60
        base_seconds = base_interval * 60
        expected_candles_per_period = target_minutes // base_interval

        # Group candles by target period
        periods: dict = {}  # period_start_ts -> list of candles

        for candle in candles:
            # Calculate which target period this candle belongs to
            period_start = (candle.timestamp // target_seconds) * target_seconds

            if period_start not in periods:
                periods[period_start] = []
            periods[period_start].append(candle)

        # Aggregate each period
        result: List[AggregatedCandle] = []

        for period_start in sorted(periods.keys()):
            period_candles = periods[period_start]

            # Sort by timestamp to ensure correct open/close
            period_candles.sort(key=lambda c: c.timestamp)

            aggregated = AggregatedCandle(
                ticker=ticker,
                timestamp=period_start,
                datetime_utc=CandleAggregator._format_datetime(period_start),
                open=period_candles[0].open,
                high=max(c.high for c in period_candles),
                low=min(c.low for c in period_candles),
                close=period_candles[-1].close,
                volume=sum(c.volume for c in period_candles),
                tick_count=sum(c.tick_count for c in period_candles),
                interval_minutes=target_minutes,
                expected_candles=expected_candles_per_period,
                actual_candles=len(period_candles),
                has_gaps=len(period_candles) < expected_candles_per_period
            )

            result.append(aggregated)

        return result

    @staticmethod
    def _format_datetime(timestamp: int) -> str:
        """Format Unix timestamp as readable datetime."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
