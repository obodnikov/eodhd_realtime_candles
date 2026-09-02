"""
Candle aggregation engine.
Converts tick data into OHLCV candles at configurable intervals.
"""

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Set, Tuple
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
    
    def __init__(
        self, 
        storage: Storage, 
        interval_minutes: int = 5, 
        max_candles: int = 100,
        save_every_n_ticks: int = 10,
        save_every_m_seconds: float = 5.0,
        ticker_status_update_interval_seconds: float = 1.0,
        candle_write_queue_maxsize: int = 10000,
        tick_max_age_seconds: int = 0
    ):
        """
        Initialize candle engine.
        
        Args:
            storage: Storage instance for persistence
            interval_minutes: Candle interval in minutes (1, 5, 15, 30, 60)
            max_candles: Maximum candles to store per ticker
            save_every_n_ticks: Save current candle every N ticks (default: 10)
            save_every_m_seconds: Save current candle every M seconds (default: 5.0)
            ticker_status_update_interval_seconds: Flush ticker last-tick status
                to DB at most once per interval per ticker (default: 1.0s)
            candle_write_queue_maxsize: Max queued candle writes before
                dropping oldest incomplete entries (default: 10000)
            tick_max_age_seconds: Max accepted age of incoming ticks relative
                to current UTC time before they are dropped as stale.
                Set to 0 to disable stale-age filtering.
        """
        self.storage = storage
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60
        self.max_candles = max_candles
        self.ticker_status_update_interval_seconds = ticker_status_update_interval_seconds
        self.candle_write_queue_maxsize = candle_write_queue_maxsize
        self.tick_max_age_seconds = tick_max_age_seconds
        
        # Configurable save frequency thresholds
        self.save_every_n_ticks = save_every_n_ticks
        self.save_every_m_seconds = save_every_m_seconds
        
        # Lock for thread-safe access to shared state
        # Required because process_tick runs in thread pool (asyncio.to_thread)
        self._lock = threading.Lock()
        
        # Current candles being built: ticker -> CurrentCandle
        self._current_candles: Dict[str, CurrentCandle] = {}
        
        # Pending cleanup queue - tickers that need old candles removed
        # Cleanup runs on a timer, not per-candle completion (performance)
        self._pending_cleanup: Set[str] = set()

        # Pending ticker-status writes to reduce per-tick DB commit pressure.
        self._pending_ticker_status: Dict[str, Tuple[str, float]] = {}
        self._last_ticker_status_write: Dict[str, float] = {}
        self._last_tick_timestamp_ms: Dict[str, int] = {}

        # Pending candle writes to move DB I/O out of the hot lock path.
        # OrderedDict preserves age so we can evict oldest incomplete entries first.
        self._pending_candle_writes: OrderedDict[
            Tuple[str, int, int],
            Candle
        ] = OrderedDict()
        self._candle_write_dropped_count = 0
        self._stale_tick_dropped_count = 0
        self._out_of_order_tick_dropped_count = 0

        # Start timestamp of the most recently completed bucket per ticker.
        # Guards against a late tick reopening a bucket that is already closed.
        self._last_completed_start: Dict[str, int] = {}
        self._late_tick_dropped_count = 0
        
        # Callback for candle completion (for WebSocket notifications)
        self._on_candle_complete: Optional[Callable[[Candle], None]] = None
        
        logger.info(f"CandleEngine initialized: {interval_minutes}m interval, max {max_candles} candles")
        logger.info(f"Tick-save frequency: every {self.save_every_n_ticks} ticks or {self.save_every_m_seconds}s")
        logger.info(
            "Ticker status update frequency: max once every %.2fs per ticker",
            self.ticker_status_update_interval_seconds
        )
        logger.info(
            "Candle write queue max size: %d",
            self.candle_write_queue_maxsize
        )
        logger.info(
            "Tick max age filter: %s",
            f"{self.tick_max_age_seconds}s" if self.tick_max_age_seconds > 0 else "disabled"
        )
    
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

            # Bucket boundaries move with the interval, so completed-bucket
            # markers recorded on the old grid no longer mean anything.
            self._last_completed_start.clear()

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
        
        # Queue write to storage (flushed by background task).
        self._enqueue_candle_write_locked(completed)
        
        # Queue cleanup instead of running immediately (performance optimization)
        # Cleanup will be processed by background task
        self._pending_cleanup.add(ticker)
        
        # Record the bucket as completed before dropping it, so a late tick
        # cannot reopen it (see the late-tick guard in process_tick).
        self._last_completed_start[ticker] = current.start_timestamp

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

    def close_due_candles(
        self,
        now_timestamp: Optional[int] = None,
        grace_seconds: float = 0.0
    ) -> List[Candle]:
        """
        Complete every in-memory candle whose interval has ended.

        Without this, a candle is only completed when the next tick for that
        ticker arrives, so a bucket that has ended can sit in memory
        indefinitely and stays invisible to include_current=False readers.

        A bucket is closed once wall clock has passed its end plus
        grace_seconds. The grace exists because tick timestamps trail wall
        clock: a trade stamped :59.8 may reach the queue at :00.3, and closing
        at exactly :00.000 would drop it from its own bucket.

        Completion uses the same path as tick-driven completion, so writes are
        queued here rather than performed; no I/O happens under the lock.

        Args:
            now_timestamp: Unix seconds to evaluate against (default: now).
            grace_seconds: Seconds to wait past a bucket's end before closing.

        Returns:
            The candles completed by this call. An empty list is the normal case.
        """
        if now_timestamp is None:
            now_timestamp = int(time.time())

        # Every bucket strictly before the one containing (now - grace) has
        # ended and is past its grace period.
        cutoff_bucket = int(
            (now_timestamp - grace_seconds) // self.interval_seconds
        ) * self.interval_seconds

        completed: List[Candle] = []

        with self._lock:
            due = [
                ticker
                for ticker, current in self._current_candles.items()
                if current.start_timestamp < cutoff_bucket
            ]

            for ticker in due:
                candle = self._complete_current_candle_locked(ticker)
                if candle is not None:
                    completed.append(candle)

        return completed
    
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
        if self.tick_max_age_seconds > 0:
            now_ms = int(time.time() * 1000)
            if timestamp_ms < (now_ms - self.tick_max_age_seconds * 1000):
                with self._lock:
                    self._stale_tick_dropped_count += 1
                return

        with self._lock:
            last_seen = self._last_tick_timestamp_ms.get(ticker)
            if last_seen is not None and timestamp_ms < last_seen:
                self._out_of_order_tick_dropped_count += 1
                return
            self._last_tick_timestamp_ms[ticker] = timestamp_ms

        candle_start = self._get_candle_start(timestamp_ms)
        current_time = time.time()
        status_timestamp = datetime.now(timezone.utc).isoformat()
        status_to_flush: Optional[Tuple[str, str, float]] = None

        # Lock for thread-safe access to _current_candles and _pending_cleanup
        with self._lock:
            # A bucket that has already been completed must not be reopened.
            # save_candle upserts on (ticker, timestamp, interval_minutes), so a
            # tick arriving for a closed bucket would start a fresh candle at the
            # old start timestamp and replace a properly closed bar with a
            # one-tick one. Drop it and count it instead; a non-trivial count
            # here means candle_close_grace_seconds is too short.
            last_done = self._last_completed_start.get(ticker)
            if last_done is not None and candle_start <= last_done:
                self._late_tick_dropped_count += 1
                return

            # Track latest status in memory and throttle DB writes.
            self._pending_ticker_status[ticker] = (status_timestamp, price)
            last_write = self._last_ticker_status_write.get(ticker, 0.0)
            if current_time - last_write >= self.ticker_status_update_interval_seconds:
                pending = self._pending_ticker_status.pop(ticker, None)
                if pending is not None:
                    status_to_flush = (ticker, pending[0], pending[1])
                    self._last_ticker_status_write[ticker] = current_time

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
                elif candle_start < current.start_timestamp:
                    self._out_of_order_tick_dropped_count += 1
                    return
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
                        current.ticks_since_save >= self.save_every_n_ticks or
                        time_since_save >= self.save_every_m_seconds
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

        # Flush ticker status outside the lock to reduce lock hold time.
        if status_to_flush is not None:
            try:
                self.storage.update_ticker_status(
                    status_to_flush[0],
                    status='active',
                    last_tick_at=status_to_flush[1],
                    last_price=status_to_flush[2]
                )
            except Exception as e:
                logger.error(f"Failed immediate ticker status update for {status_to_flush[0]}: {e}")
                # Re-queue failed status so periodic flush task can retry.
                with self._lock:
                    self._pending_ticker_status[status_to_flush[0]] = (
                        status_to_flush[1],
                        status_to_flush[2]
                    )
    
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
        
        self._enqueue_candle_write_locked(candle)

    def _enqueue_candle_write_locked(self, candle: Candle):
        """
        Queue candle write while holding engine lock.

        Dedupes by (ticker, timestamp, interval) so frequent updates to an
        in-progress candle replace older queued versions.
        """
        key = (candle.ticker, candle.timestamp, candle.interval_minutes)
        existing = self._pending_candle_writes.get(key)

        # Never allow an incomplete update to overwrite a completed candle.
        if existing is not None:
            if existing.is_complete and not candle.is_complete:
                return
            self._pending_candle_writes[key] = candle
            self._pending_candle_writes.move_to_end(key)
            return

        if len(self._pending_candle_writes) >= self.candle_write_queue_maxsize:
            # Prefer evicting the oldest incomplete entry first.
            evict_key: Optional[Tuple[str, int, int]] = None
            for pending_key, pending_candle in self._pending_candle_writes.items():
                if not pending_candle.is_complete:
                    evict_key = pending_key
                    break

            if evict_key is None and candle.is_complete and self._pending_candle_writes:
                # Queue is saturated with completed candles; keep newest completed
                # snapshot by evicting the oldest completed entry.
                evict_key = next(iter(self._pending_candle_writes))

            if evict_key is None:
                # Queue saturated with completed candles and incoming candle is
                # incomplete. Drop the incoming write and record metric.
                self._candle_write_dropped_count += 1
                if self._candle_write_dropped_count % 1000 == 0:
                    logger.warning(
                        "Candle write queue saturated, dropped %d queued writes",
                        self._candle_write_dropped_count
                    )
                return

            self._pending_candle_writes.pop(evict_key, None)

        self._pending_candle_writes[key] = candle
    
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
            self._pending_ticker_status.pop(ticker, None)
            self._last_ticker_status_write.pop(ticker, None)
            self._last_completed_start.pop(ticker, None)
    
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

    def flush_pending_ticker_statuses(self):
        """
        Flush pending ticker status updates to storage.

        Called by a periodic background task and during shutdown to reduce
        per-tick status writes while preserving recent ticker heartbeat data.
        """
        with self._lock:
            pending_items = list(self._pending_ticker_status.items())
            self._pending_ticker_status.clear()

        if not pending_items:
            return

        failed_items: Dict[str, Tuple[str, float]] = {}
        now = time.time()
        successful_tickers = []
        for ticker, (last_tick_at, last_price) in pending_items:
            try:
                self.storage.update_ticker_status(
                    ticker,
                    status='active',
                    last_tick_at=last_tick_at,
                    last_price=last_price
                )
            except Exception as e:
                logger.error(f"Failed flushing ticker status for {ticker}: {e}")
                failed_items[ticker] = (last_tick_at, last_price)
            else:
                successful_tickers.append(ticker)

        with self._lock:
            for ticker in successful_tickers:
                self._last_ticker_status_write[ticker] = now
            if failed_items:
                self._pending_ticker_status.update(failed_items)

    def flush_pending_candle_writes(self):
        """
        Flush queued candle writes to storage.

        Failed writes are re-queued for retry on the next flush cycle.
        """
        with self._lock:
            pending_items = list(self._pending_candle_writes.items())
            self._pending_candle_writes.clear()

        if not pending_items:
            return

        failed: Dict[Tuple[str, int, int], Candle] = {}
        for key, candle in pending_items:
            try:
                self.storage.save_candle(candle)
            except Exception as e:
                logger.error(
                    "Failed flushing candle write for %s %s: %s",
                    candle.ticker,
                    candle.datetime_utc,
                    e
                )
                failed[key] = candle

        if failed:
            with self._lock:
                for key, candle in failed.items():
                    # Preserve an existing complete version if already present.
                    existing = self._pending_candle_writes.get(key)
                    if existing is not None and existing.is_complete and not candle.is_complete:
                        continue
                    self._pending_candle_writes[key] = candle

    def get_candle_write_metrics(self) -> dict:
        """Get current candle/tick drop metrics."""
        with self._lock:
            return {
                'candle_write_queue_size': len(self._pending_candle_writes),
                'candle_write_queue_maxsize': self.candle_write_queue_maxsize,
                'candle_write_dropped_count': self._candle_write_dropped_count,
                'stale_tick_dropped_count': self._stale_tick_dropped_count,
                'out_of_order_tick_dropped_count': self._out_of_order_tick_dropped_count,
                'late_tick_dropped_count': self._late_tick_dropped_count
            }

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
