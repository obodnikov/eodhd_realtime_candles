"""
Candle aggregation engine.
Converts tick data into OHLCV candles at configurable intervals.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Callable
from dataclasses import dataclass

from .storage import Storage, Candle

logger = logging.getLogger(__name__)


@dataclass
class CurrentCandle:
    """In-progress candle being built from ticks."""
    ticker: str
    start_timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int


class CandleEngine:
    """
    Aggregates tick data into OHLCV candles.
    
    Supports dynamic interval changes and notifies on candle completion.
    """
    
    def __init__(self, storage: Storage, interval_minutes: int = 5, 
                 max_candles: int = 100):
        self.storage = storage
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60
        self.max_candles = max_candles
        
        # Current candles being built: ticker -> CurrentCandle
        self._current_candles: Dict[str, CurrentCandle] = {}
        
        # Callback for candle completion (for WebSocket notifications)
        self._on_candle_complete: Optional[Callable[[Candle], None]] = None
        
        logger.info(f"CandleEngine initialized: {interval_minutes}m interval, max {max_candles} candles")
    
    def set_interval(self, interval_minutes: int):
        """
        Update the candle interval.
        Completes any current candles before switching.
        """
        if interval_minutes not in [1, 5, 15, 30, 60]:
            raise ValueError(f"Invalid interval: {interval_minutes}. Must be 1, 5, 15, 30, or 60")
        
        # Complete all current candles
        for ticker in list(self._current_candles.keys()):
            self._complete_current_candle(ticker, force=True)
        
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60
        logger.info(f"Candle interval changed to {interval_minutes} minutes")
    
    def set_max_candles(self, max_candles: int):
        """Update maximum candles to store per ticker."""
        self.max_candles = max_candles
        logger.info(f"Max candles changed to {max_candles}")
    
    def set_on_candle_complete(self, callback: Callable[[Candle], None]):
        """Set callback for candle completion events."""
        self._on_candle_complete = callback
    
    def _get_candle_start(self, timestamp_ms: int) -> int:
        """Calculate the start timestamp of the candle containing this tick."""
        timestamp_sec = timestamp_ms // 1000
        return (timestamp_sec // self.interval_seconds) * self.interval_seconds
    
    def _format_datetime(self, timestamp: int) -> str:
        """Format Unix timestamp as readable datetime."""
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    def _complete_current_candle(self, ticker: str, force: bool = False) -> Optional[Candle]:
        """Complete the current candle and save to storage."""
        if ticker not in self._current_candles:
            return None
        
        current = self._current_candles[ticker]
        
        completed = Candle(
            ticker=ticker,
            timestamp=current.start_timestamp,
            datetime_utc=self._format_datetime(current.start_timestamp),
            open=current.open,
            high=current.high,
            low=current.low,
            close=current.close,
            volume=current.volume,
            tick_count=current.tick_count,
            is_complete=True,
            interval_minutes=self.interval_minutes
        )
        
        # Save to storage
        self.storage.save_candle(completed)
        
        # Cleanup old candles
        self.storage.cleanup_old_candles(ticker, self.max_candles)
        
        # Remove from current candles
        del self._current_candles[ticker]
        
        logger.info(
            f"Completed candle: {ticker} {completed.datetime_utc} "
            f"O:{completed.open:.2f} H:{completed.high:.2f} "
            f"L:{completed.low:.2f} C:{completed.close:.2f} V:{completed.volume}"
        )
        
        # Notify callback
        if self._on_candle_complete:
            try:
                self._on_candle_complete(completed)
            except Exception as e:
                logger.error(f"Error in candle completion callback: {e}")
        
        return completed
    
    def process_tick(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """
        Process an incoming tick and update candles.
        
        Args:
            ticker: Stock symbol
            price: Trade price
            volume: Trade volume
            timestamp_ms: Unix timestamp in milliseconds
        """
        ticker = ticker.upper()
        candle_start = self._get_candle_start(timestamp_ms)
        
        # Update ticker status in storage
        self.storage.update_ticker_status(
            ticker, 
            status='active',
            last_tick_at=datetime.now(timezone.utc).isoformat(),
            last_price=price
        )
        
        # Check if we have a current candle for this ticker
        if ticker in self._current_candles:
            current = self._current_candles[ticker]
            
            # Check if this tick belongs to a new candle
            if candle_start > current.start_timestamp:
                # Complete the current candle
                self._complete_current_candle(ticker)
                
                # Start new candle
                self._current_candles[ticker] = CurrentCandle(
                    ticker=ticker,
                    start_timestamp=candle_start,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                    tick_count=1
                )
            else:
                # Update current candle
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price
                current.volume += volume
                current.tick_count += 1
            
            # Save current candle state to storage (for persistence)
            self._save_current_candle_state(ticker)
        else:
            # First tick for this ticker - start new candle
            self._current_candles[ticker] = CurrentCandle(
                ticker=ticker,
                start_timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                tick_count=1
            )
            self._save_current_candle_state(ticker)
            logger.info(f"Started tracking {ticker} at price {price:.2f}")
    
    def _save_current_candle_state(self, ticker: str):
        """Save current candle state to storage for persistence."""
        if ticker not in self._current_candles:
            return
        
        current = self._current_candles[ticker]
        
        candle = Candle(
            ticker=ticker,
            timestamp=current.start_timestamp,
            datetime_utc=self._format_datetime(current.start_timestamp),
            open=current.open,
            high=current.high,
            low=current.low,
            close=current.close,
            volume=current.volume,
            tick_count=current.tick_count,
            is_complete=False,
            interval_minutes=self.interval_minutes
        )
        
        self.storage.save_candle(candle)
    
    def get_current_candle(self, ticker: str) -> Optional[dict]:
        """Get the current in-progress candle for a ticker."""
        ticker = ticker.upper()
        
        if ticker in self._current_candles:
            current = self._current_candles[ticker]
            return {
                'ticker': ticker,
                'timestamp': current.start_timestamp,
                'datetime_utc': self._format_datetime(current.start_timestamp),
                'open': current.open,
                'high': current.high,
                'low': current.low,
                'close': current.close,
                'volume': current.volume,
                'tick_count': current.tick_count,
                'is_complete': False,
                'interval_minutes': self.interval_minutes
            }
        
        # Try to get from storage
        candle = self.storage.get_current_candle(ticker)
        if candle:
            return candle.to_dict()
        
        return None
    
    def remove_ticker(self, ticker: str):
        """Stop tracking a ticker and remove its current candle."""
        ticker = ticker.upper()
        
        if ticker in self._current_candles:
            del self._current_candles[ticker]
            logger.info(f"Stopped tracking {ticker}")
    
    def get_active_tickers(self) -> list:
        """Get list of tickers with active (in-progress) candles."""
        return list(self._current_candles.keys())

    def get_active_tickers_summary(self) -> list:
        """Get lightweight summary of active candles for dashboard."""
        summaries = []
        current_time = datetime.now(timezone.utc).timestamp()

        for ticker, candle in self._current_candles.items():
            # Calculate how long ago the candle started
            started_seconds_ago = int(current_time - candle.start_timestamp)

            # Format time ago with appropriate units
            if started_seconds_ago < 60:
                started_ago = f"{started_seconds_ago}s ago"
            elif started_seconds_ago < 3600:  # Less than 1 hour
                started_minutes_ago = started_seconds_ago // 60
                started_ago = f"{started_minutes_ago}m ago"
            elif started_seconds_ago < 86400:  # Less than 1 day
                started_hours_ago = started_seconds_ago // 3600
                started_ago = f"{started_hours_ago}h ago"
            else:  # 1 day or more
                started_days_ago = started_seconds_ago // 86400
                started_ago = f"{started_days_ago}d ago"

            summaries.append({
                'ticker': ticker,
                'ticks': candle.tick_count,
                'current_price': round(candle.close, 2),
                'low': round(candle.low, 2),
                'high': round(candle.high, 2),
                'started': candle.start_timestamp,
                'started_ago': started_ago
            })

        return summaries
    
    def complete_all_candles(self):
        """Force complete all current candles (for shutdown)."""
        for ticker in list(self._current_candles.keys()):
            self._complete_current_candle(ticker, force=True)
