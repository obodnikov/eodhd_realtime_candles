"""
Candle aggregation engine.
Converts tick data into OHLCV candles at configurable intervals.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Callable, Set
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
    ticks_since_save: int = 0  # Track ticks since last DB save
    last_save_time: float = 0.0  # Track time of last DB save


class CandleEngine:
    """
    Aggregates tick data into OHLCV candles.
    
    Supports dynamic interval changes and notifies on candle completion.
    
    Performance Optimization - Reduced Save Frequency:
    ===================================================
    Current candle state is saved to DB periodically (not on every tick) to reduce
    write pressure and prevent "database is locked" errors under high load.
    
    Trade-off Analysis:
    - RISK: Up to N ticks or M seconds of current candle data may be lost on crash
    - ACCEPTABLE because:
      1. Current candles are ephemeral/in-progress (not official historical data)
      2. Completed candles are ALWAYS saved immediately (no historical data loss)
      3. Service recovers quickly on restart with fresh WebSocket connection
      4. Alternative (save every tick) causes complete system failure due to DB locking
    
    With 50 tickers receiving frequent ticks:
    - Before: ~500-1000 DB writes/second → "database is locked" errors
    - After: ~50-100 DB writes/second → stable operation
    
    This is a conscious design decision prioritizing system stability over
    ephemeral current candle persistence.
    """
    
    # Save current candle state every N ticks or M seconds (whichever comes first)
    # These thresholds balance performance (reduce DB writes) vs. data loss risk
    SAVE_EVERY_N_TICKS = 10
    SAVE_EVERY_M_SECONDS = 5.0
    
    def __init__(self, storage: Storage, interval_minutes: int = 5, 
                 max_candles: int = 100):
        self.storage = storage
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60
        self.max_candles = max_candles
        
        # Lock for thread-safe access to shared state
        # Required because process_tick runs in thread pool (asyncio.to_thread)
        self._lock = threading.Lock()
        
        # Current candles being built: ticker -> CurrentCandle
        self._current_candles: Dict[str, CurrentCandle] = {}
        
        # Pending cleanup queue - tickers that need old candles removed
        # Cleanup runs on a timer, not per-candle completion (performance)
        self._pending_cleanup: Set[str] = set()
        
        # Callback for candle completion (for WebSocket notifications)
        self._on_candle_complete: Optional[Callable[[Candle], None]] = None
        
        logger.info(f"CandleEngine initialized: {interval_minutes}m interval, max {max_candles} candles")
        logger.info(f"Tick-save frequency: every {self.SAVE_EVERY_N_TICKS} ticks or {self.SAVE_EVERY_M_SECONDS}s")
    
    def set_interval(self, interval_minutes: int):
        """
        Update the candle interval.
        Completes any current candles before switching.
        """
        if interval_minutes not in [1, 5, 15, 30, 60]:
            raise ValueError(f"Invalid interval: {interval_minutes}. Must be 1, 5, 15, 30, or 60")
        
        # Complete all current candles (acquires lock internally)
        with self._lock:
            for ticker in list(self._current_candles.keys()):
                self._complete_current_candle_locked(ticker, force=True)
        
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
    
    def _complete_current_candle_locked(self, ticker: str, force: bool = False) -> Optional[Candle]:
        """
        Complete the current candle and save to storage.
        
        MUST be called while holding self._lock.
        """
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
        
        # Queue cleanup instead of running immediately (performance optimization)
        # Cleanup will be processed by background task
        self._pending_cleanup.add(ticker)
        
        # Remove from current candles
        del self._current_candles[ticker]
        
        logger.info(
            f"Completed candle: {ticker} {completed.datetime_utc} "
            f"O:{completed.open:.2f} H:{completed.high:.2f} "
            f"L:{completed.low:.2f} C:{completed.close:.2f} V:{completed.volume}"
        )
        
        # Notify callback (outside lock would be better, but callback should be fast)
        if self._on_candle_complete:
            try:
                self._on_candle_complete(completed)
            except Exception as e:
                logger.error(f"Error in candle completion callback: {e}")
        
        return completed
    
    def _complete_current_candle(self, ticker: str, force: bool = False) -> Optional[Candle]:
        """Complete the current candle and save to storage. Acquires lock."""
        with self._lock:
            return self._complete_current_candle_locked(ticker, force)
    
    def process_tick(self, ticker: str, price: float, volume: int, timestamp_ms: int):
        """
        Process an incoming tick and update candles.
        
        Thread-safe: uses lock to protect shared state since this method
        may be called concurrently from thread pool (asyncio.to_thread).
        
        Performance optimization: Only saves current candle state to DB
        every N ticks or M seconds (whichever comes first) to reduce write pressure.
        Always saves on candle completion.
        
        Args:
            ticker: Stock symbol
            price: Trade price
            volume: Trade volume
            timestamp_ms: Unix timestamp in milliseconds
        """
        ticker = ticker.upper()
        candle_start = self._get_candle_start(timestamp_ms)
        current_time = time.time()
        
        # Update ticker status in storage (thread-safe via thread-local connections)
        self.storage.update_ticker_status(
            ticker, 
            status='active',
            last_tick_at=datetime.now(timezone.utc).isoformat(),
            last_price=price
        )
        
        # Lock for thread-safe access to _current_candles and _pending_cleanup
        with self._lock:
            # Check if we have a current candle for this ticker
            if ticker in self._current_candles:
                current = self._current_candles[ticker]
                
                # Check if this tick belongs to a new candle
                if candle_start > current.start_timestamp:
                    # Complete the current candle (always saves to DB)
                    self._complete_current_candle_locked(ticker)
                    
                    # Start new candle
                    self._current_candles[ticker] = CurrentCandle(
                        ticker=ticker,
                        start_timestamp=candle_start,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=volume,
                        tick_count=1,
                        ticks_since_save=1,
                        last_save_time=current_time
                    )
                    # Save first tick of new candle
                    self._save_current_candle_state_locked(ticker)
                else:
                    # Update current candle
                    current.high = max(current.high, price)
                    current.low = min(current.low, price)
                    current.close = price
                    current.volume += volume
                    current.tick_count += 1
                    current.ticks_since_save += 1
                    
                    # Save to DB only if threshold reached
                    time_since_save = current_time - current.last_save_time
                    should_save = (
                        current.ticks_since_save >= self.SAVE_EVERY_N_TICKS or
                        time_since_save >= self.SAVE_EVERY_M_SECONDS
                    )
                    
                    if should_save:
                        self._save_current_candle_state_locked(ticker)
                        current.ticks_since_save = 0
                        current.last_save_time = current_time
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
                    tick_count=1,
                    ticks_since_save=1,
                    last_save_time=current_time
                )
                self._save_current_candle_state_locked(ticker)
                logger.info(f"Started tracking {ticker} at price {price:.2f}")
    
    def _save_current_candle_state_locked(self, ticker: str):
        """
        Save current candle state to storage for persistence.
        
        MUST be called while holding self._lock.
        """
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
        
        with self._lock:
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
        
        # Try to get from storage (outside lock - DB has its own thread-safety)
        candle = self.storage.get_current_candle(ticker)
        if candle:
            return candle.to_dict()
        
        return None
    
    def remove_ticker(self, ticker: str):
        """Stop tracking a ticker and remove its current candle."""
        ticker = ticker.upper()
        
        with self._lock:
            if ticker in self._current_candles:
                del self._current_candles[ticker]
                logger.info(f"Stopped tracking {ticker}")
    
    def get_active_tickers(self) -> list:
        """Get list of tickers with active (in-progress) candles."""
        with self._lock:
            return list(self._current_candles.keys())

    def get_active_tickers_summary(self) -> list:
        """Get lightweight summary of active candles for dashboard."""
        summaries = []
        current_time = datetime.now(timezone.utc).timestamp()

        with self._lock:
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
        with self._lock:
            for ticker in list(self._current_candles.keys()):
                self._complete_current_candle_locked(ticker, force=True)

    def get_pending_cleanup(self) -> Set[str]:
        """Get set of tickers pending cleanup."""
        with self._lock:
            return self._pending_cleanup.copy()

    def clear_pending_cleanup(self):
        """Clear the pending cleanup queue after processing."""
        with self._lock:
            self._pending_cleanup.clear()

    def remove_from_pending_cleanup(self, ticker: str):
        """
        Remove a single ticker from pending cleanup.
        
        Used by cleanup task to remove tickers one-by-one after successful
        cleanup, preventing data loss if task is cancelled mid-processing.
        """
        with self._lock:
            self._pending_cleanup.discard(ticker)
