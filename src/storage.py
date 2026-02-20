"""
SQLite storage for candles and ticker management.
Provides persistence across service restarts.
"""

import sqlite3
import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import threading
import os

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """OHLCV candle data structure."""
    ticker: str
    timestamp: int          # Unix timestamp (start of candle)
    datetime_utc: str       # Human-readable datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    tick_count: int
    is_complete: bool
    interval_minutes: int
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrackedTicker:
    """Tracked ticker with metadata."""
    symbol: str
    added_at: str
    status: str             # 'active', 'no_data', 'error'
    last_tick_at: Optional[str]
    last_price: Optional[float]
    candle_count: int
    last_candle_request_at: Optional[str]
    
    def to_dict(self) -> dict:
        return asdict(self)


class Storage:
    """SQLite-based storage for candles and tickers."""
    
    # Cache TTL for get_stats() in seconds
    STATS_CACHE_TTL = 5.0

    def __init__(
        self, 
        db_path: str,
        max_retries: int = 3,
        retry_base_delay_ms: int = 50,
        busy_timeout_ms: int = 10000
    ):
        """
        Initialize SQLite storage.
        
        Args:
            db_path: Path to SQLite database file
            max_retries: Number of retry attempts on database lock (default: 3)
            retry_base_delay_ms: Base delay in milliseconds for exponential backoff (default: 50)
            busy_timeout_ms: SQLite busy_timeout in milliseconds (default: 10000)
        """
        self.db_path = db_path
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay_ms / 1000.0  # Convert to seconds
        self.busy_timeout = busy_timeout_ms / 1000.0  # Convert to seconds
        
        self._local = threading.local()
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_time: float = 0.0
        self._ensure_directory()
        self._init_db()
        
    def _ensure_directory(self):
        """Ensure the database directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    def checkpoint_wal(self) -> dict:
        """
        Run a passive WAL checkpoint to keep the WAL file from growing unbounded.

        Uses PASSIVE mode so it never blocks readers or writers.
        Should be called periodically (e.g., every 30-60 seconds) from the
        websocket worker's cleanup task.

        Returns:
            dict with checkpoint results or error info
        """
        try:
            conn = self._get_connection()
            # PASSIVE: checkpoint as much as possible without blocking
            result = conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchone()
            # result = (busy, log_pages, checkpointed_pages)
            info = {
                'busy': result[0],
                'log_pages': result[1],
                'checkpointed_pages': result[2]
            }
            if result[1] > 0:
                logger.debug(
                    f"WAL checkpoint: {result[2]}/{result[1]} pages checkpointed"
                    f"{' (busy)' if result[0] else ''}"
                )
            return info
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")
            return {'error': str(e)}
    
    def _execute_with_retry(
        self,
        operation: Callable[[], Any],
        operation_name: str,
        is_critical: bool = True,
        max_retries: Optional[int] = None
    ) -> Optional[Any]:
        """
        Execute a database operation with retry logic for lock contention.
        
        Uses exponential backoff to handle transient database locks without
        blocking for extended periods. Non-blocking approach suitable for
        use with asyncio.to_thread().
        
        Args:
            operation: Callable that performs the database operation
            operation_name: Description for logging (e.g., "save candle for AAPL")
            is_critical: If True, raises exception on final failure. If False, logs and returns None.
            max_retries: Override default max_retries
            
        Returns:
            Result of operation, or None if non-critical and failed
            
        Raises:
            Exception: If critical operation fails after all retries
        """
        retries = max_retries if max_retries is not None else self.max_retries
        
        for attempt in range(retries):
            try:
                return operation()
            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and attempt < retries - 1:
                    # Database locked - retry with exponential backoff
                    wait_time = self.retry_base_delay * (2 ** attempt)
                    logger.warning(
                        f"Database locked during {operation_name}, "
                        f"retry {attempt + 1}/{retries} after {wait_time*1000:.0f}ms"
                    )
                    time.sleep(wait_time)
                else:
                    # Final attempt failed or non-lock error
                    if is_critical:
                        logger.error(f"Failed {operation_name} after {attempt + 1} attempts: {e}")
                        raise
                    else:
                        logger.error(f"Failed {operation_name} after {attempt + 1} attempts: {e}")
                        return None
            except Exception as e:
                # Unexpected error
                if is_critical:
                    logger.error(f"Unexpected error during {operation_name}: {e}")
                    raise
                else:
                    logger.error(f"Unexpected error during {operation_name}: {e}")
                    return None
        
        return None
        
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        Applies SQLite tuning for better concurrency:
        - WAL journal mode for better read/write concurrency
        - NORMAL synchronous for fewer fsyncs (good trade-off for this use case)
        - busy_timeout to wait on locks instead of failing immediately
        - Configurable timeout for multi-worker scenarios
        """
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=self.busy_timeout,
            )
            conn.row_factory = sqlite3.Row

            try:
                # Enable WAL mode for better read/write concurrency
                conn.execute("PRAGMA journal_mode=WAL;")
                # Reduce fsyncs - acceptable trade-off for this use case
                conn.execute("PRAGMA synchronous=NORMAL;")
                # Wait if database is locked (configurable via busy_timeout_ms)
                conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout * 1000)};")
                # Cache size for better performance (10MB)
                # Note: In memory-constrained environments, this can be reduced
                conn.execute("PRAGMA cache_size=-10000;")
                # Auto-checkpoint every 1000 pages (~4MB) to prevent WAL bloat
                conn.execute("PRAGMA wal_autocheckpoint=1000;")
            except Exception as e:
                # PRAGMA tuning is best-effort - don't break startup if it fails
                logger.warning(f"Failed to apply SQLite PRAGMA settings: {e}")

            self._local.connection = conn

        return self._local.connection
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Candles table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                datetime_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                tick_count INTEGER NOT NULL,
                is_complete INTEGER NOT NULL,
                interval_minutes INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, timestamp, interval_minutes)
            )
        ''')
        
        # Index for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_candles_ticker_timestamp 
            ON candles(ticker, timestamp DESC)
        ''')
        
        # Tracked tickers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickers (
                symbol TEXT PRIMARY KEY,
                added_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                last_tick_at TEXT,
                last_price REAL,
                last_candle_request_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Migration: Add last_candle_request_at column if it doesn't exist
        cursor.execute("PRAGMA table_info(tickers)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'last_candle_request_at' not in columns:
            cursor.execute("ALTER TABLE tickers ADD COLUMN last_candle_request_at TEXT")
            conn.commit()
            logger.info("Added last_candle_request_at column to tickers table")
        
        # Runtime config table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # WebSocket status table (for multi-worker status sharing)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS websocket_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                connected INTEGER NOT NULL,
                subscribed_tickers TEXT NOT NULL,
                subscribed_count INTEGER NOT NULL,
                pending_subscribe TEXT NOT NULL,
                connection_count INTEGER NOT NULL,
                tick_count INTEGER NOT NULL,
                tick_queue_size INTEGER NOT NULL DEFAULT 0,
                tick_queue_maxsize INTEGER NOT NULL DEFAULT 0,
                tick_enqueued_count INTEGER NOT NULL DEFAULT 0,
                tick_processed_count INTEGER NOT NULL DEFAULT 0,
                tick_dropped_count INTEGER NOT NULL DEFAULT 0,
                candle_write_queue_size INTEGER NOT NULL DEFAULT 0,
                candle_write_queue_maxsize INTEGER NOT NULL DEFAULT 0,
                candle_write_dropped_count INTEGER NOT NULL DEFAULT 0,
                last_message TEXT,
                last_update TEXT NOT NULL
            )
        ''')

        # Migration: Add websocket queue metric columns if they don't exist
        cursor.execute("PRAGMA table_info(websocket_status)")
        ws_columns = [row[1] for row in cursor.fetchall()]
        if 'tick_queue_size' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN tick_queue_size INTEGER NOT NULL DEFAULT 0")
        if 'tick_queue_maxsize' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN tick_queue_maxsize INTEGER NOT NULL DEFAULT 0")
        if 'tick_enqueued_count' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN tick_enqueued_count INTEGER NOT NULL DEFAULT 0")
        if 'tick_processed_count' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN tick_processed_count INTEGER NOT NULL DEFAULT 0")
        if 'tick_dropped_count' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN tick_dropped_count INTEGER NOT NULL DEFAULT 0")
        if 'candle_write_queue_size' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN candle_write_queue_size INTEGER NOT NULL DEFAULT 0")
        if 'candle_write_queue_maxsize' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN candle_write_queue_maxsize INTEGER NOT NULL DEFAULT 0")
        if 'candle_write_dropped_count' not in ws_columns:
            cursor.execute("ALTER TABLE websocket_status ADD COLUMN candle_write_dropped_count INTEGER NOT NULL DEFAULT 0")
        
        # Active candles status table (for multi-worker status sharing)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_candles_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info(f"Database initialized at {self.db_path}")
    
    # =========================================================================
    # Ticker Management
    # =========================================================================
    
    def add_ticker(self, symbol: str) -> bool:
        """Add a ticker to track. Returns True if added, False if already exists."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO tickers (symbol, added_at, status)
                VALUES (?, ?, 'active')
            ''', (symbol.upper(), datetime.now(timezone.utc).isoformat()))
            conn.commit()
            logger.info(f"Added ticker: {symbol}")
            return True
        except sqlite3.IntegrityError:
            logger.debug(f"Ticker already exists: {symbol}")
            return False
    
    def remove_ticker(self, symbol: str) -> bool:
        """Remove a ticker and its candles. Returns True if removed."""
        def _remove():
            conn = self._get_connection()
            cursor = conn.cursor()
            
            symbol_upper = symbol.upper()
            
            # Remove candles first
            cursor.execute('DELETE FROM candles WHERE ticker = ?', (symbol_upper,))
            
            # Remove ticker
            cursor.execute('DELETE FROM tickers WHERE symbol = ?', (symbol_upper,))
            removed = cursor.rowcount > 0
            
            conn.commit()
            
            if removed:
                logger.info(f"Removed ticker and candles: {symbol_upper}")
            
            return removed
        
        result = self._execute_with_retry(
            operation=_remove,
            operation_name=f"remove ticker {symbol}",
            is_critical=True
        )
        
        # Invalidate stats cache
        self._stats_cache = None
        
        return result if result is not None else False
    
    def get_tickers(self) -> List[TrackedTicker]:
        """Get all tracked tickers with their status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                t.symbol,
                t.added_at,
                t.status,
                t.last_tick_at,
                t.last_price,
                t.last_candle_request_at,
                COUNT(c.id) as candle_count
            FROM tickers t
            LEFT JOIN candles c ON t.symbol = c.ticker AND c.is_complete = 1
            GROUP BY t.symbol
            ORDER BY t.symbol
        ''')
        
        tickers = []
        for row in cursor.fetchall():
            tickers.append(TrackedTicker(
                symbol=row['symbol'],
                added_at=row['added_at'],
                status=row['status'],
                last_tick_at=row['last_tick_at'],
                last_price=row['last_price'],
                candle_count=row['candle_count'],
                last_candle_request_at=row['last_candle_request_at']
            ))
        
        return tickers
    
    def get_ticker_symbols(self) -> List[str]:
        """Get list of ticker symbols only."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol FROM tickers ORDER BY symbol')
        return [row['symbol'] for row in cursor.fetchall()]
    
    def update_ticker_status(self, symbol: str, status: str, 
                            last_tick_at: Optional[str] = None,
                            last_price: Optional[float] = None):
        """Update ticker status and last tick info."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if last_tick_at and last_price is not None:
            cursor.execute('''
                UPDATE tickers 
                SET status = ?, last_tick_at = ?, last_price = ?, updated_at = ?
                WHERE symbol = ?
            ''', (status, last_tick_at, last_price, 
                  datetime.now(timezone.utc).isoformat(), symbol.upper()))
        else:
            cursor.execute('''
                UPDATE tickers 
                SET status = ?, updated_at = ?
                WHERE symbol = ?
            ''', (status, datetime.now(timezone.utc).isoformat(), symbol.upper()))
        
        conn.commit()
    
    def ticker_exists(self, symbol: str) -> bool:
        """Check if ticker exists."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM tickers WHERE symbol = ?', (symbol.upper(),))
        return cursor.fetchone() is not None

    def get_ticker(self, symbol: str) -> Optional[TrackedTicker]:
        """Get single ticker with metadata."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                t.symbol,
                t.added_at,
                t.status,
                t.last_tick_at,
                t.last_price,
                t.last_candle_request_at,
                COUNT(c.id) as candle_count
            FROM tickers t
            LEFT JOIN candles c ON t.symbol = c.ticker AND c.is_complete = 1
            WHERE t.symbol = ?
            GROUP BY t.symbol
        ''', (symbol.upper(),))

        row = cursor.fetchone()
        if not row:
            return None

        return TrackedTicker(
            symbol=row['symbol'],
            added_at=row['added_at'],
            status=row['status'],
            last_tick_at=row['last_tick_at'],
            last_price=row['last_price'],
            candle_count=row['candle_count'],
            last_candle_request_at=row['last_candle_request_at']
        )

    def get_ticker_count(self) -> int:
        """Get count of tracked tickers."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM tickers')
        return cursor.fetchone()[0]

    def get_ticker_intervals(self, ticker: str) -> List[int]:
        """
        Get unique interval_minutes values for a ticker's completed candles.
        
        Returns sorted list of intervals (ascending).
        Used to determine valid aggregation options.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT interval_minutes
            FROM candles
            WHERE ticker = ? AND is_complete = 1
            ORDER BY interval_minutes ASC
        ''', (ticker.upper(),))
        return [row[0] for row in cursor.fetchall()]

    def get_candles_for_aggregation(
        self,
        ticker: str,
        base_interval: int,
        count: Optional[int] = None,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None
    ) -> List['Candle']:
        """
        Get completed candles for aggregation.
        
        Only returns completed candles with the specified base interval.
        Results are sorted by timestamp ASC for proper aggregation.
        
        Args:
            ticker: Ticker symbol
            base_interval: Base interval to filter by
            count: Max number of BASE candles to fetch (before aggregation)
            from_timestamp: Optional start timestamp filter
            to_timestamp: Optional end timestamp filter
            
        Returns:
            List of Candle objects sorted by timestamp ASC
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        query = '''
            SELECT * FROM candles
            WHERE ticker = ? AND is_complete = 1 AND interval_minutes = ?
        '''
        params: List[Any] = [ticker.upper(), base_interval]

        if from_timestamp:
            query += ' AND timestamp >= ?'
            params.append(from_timestamp)

        if to_timestamp:
            query += ' AND timestamp <= ?'
            params.append(to_timestamp)

        query += ' ORDER BY timestamp DESC'

        if count:
            query += ' LIMIT ?'
            params.append(count)

        cursor.execute(query, params)

        candles = []
        for row in cursor.fetchall():
            candles.append(Candle(
                ticker=row['ticker'],
                timestamp=row['timestamp'],
                datetime_utc=row['datetime_utc'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                tick_count=row['tick_count'],
                is_complete=bool(row['is_complete']),
                interval_minutes=row['interval_minutes']
            ))

        # Return in chronological order for aggregation
        return list(reversed(candles))
    
    def update_ticker_last_request(self, symbol: str):
        """
        Update last candle request timestamp for a ticker.
        
        This is a non-critical operation - failures are logged but don't raise exceptions.
        Uses retry logic with exponential backoff for database lock contention.
        
        Args:
            symbol: Ticker symbol
        """
        def _update():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE tickers 
                SET last_candle_request_at = ?
                WHERE symbol = ?
            ''', (datetime.now(timezone.utc).isoformat(), symbol.upper()))
            conn.commit()
        
        self._execute_with_retry(
            operation=_update,
            operation_name=f"update last_request for {symbol}",
            is_critical=False  # Non-critical - don't fail API calls
        )

    def delete_all_tickers(self) -> int:
        """
        Remove all tickers from tracking and delete their candle data.
        Returns count of removed tickers.
        """
        def _delete_all():
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get count before deletion
            cursor.execute('SELECT COUNT(*) FROM tickers')
            count = cursor.fetchone()[0]

            # Delete all candles first
            cursor.execute('DELETE FROM candles')

            # Delete all tickers
            cursor.execute('DELETE FROM tickers')

            conn.commit()
            logger.info(f"Deleted all {count} tickers and their candle data")
            return count
        
        result = self._execute_with_retry(
            operation=_delete_all,
            operation_name="delete all tickers",
            is_critical=True
        )
        
        # Invalidate stats cache
        self._stats_cache = None
        
        return result if result is not None else 0

    def cleanup_orphaned_candles(self) -> int:
        """
        Remove candles for tickers that are no longer tracked.

        This operation is atomic - it uses a single SQL statement with a subquery
        to ensure consistency even under concurrent ticker additions/removals.
        The NOT IN subquery is evaluated atomically within the DELETE statement,
        preventing race conditions.

        Returns count of deleted candle records.
        """
        def _cleanup():
            conn = self._get_connection()
            cursor = conn.cursor()

            # Use IMMEDIATE transaction to lock the database for writing
            # This prevents concurrent modifications during cleanup
            cursor.execute('BEGIN IMMEDIATE')

            # Delete candles where ticker doesn't exist in tickers table
            # The subquery is evaluated atomically within this transaction
            cursor.execute('''
                DELETE FROM candles
                WHERE ticker NOT IN (SELECT symbol FROM tickers)
            ''')

            deleted = cursor.rowcount
            conn.commit()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} orphaned candles")
            else:
                logger.debug("No orphaned candles found")

            return deleted
        
        result = self._execute_with_retry(
            operation=_cleanup,
            operation_name="cleanup orphaned candles",
            is_critical=True
        )
        
        # Invalidate stats cache
        self._stats_cache = None
        
        return result if result is not None else 0

    # =========================================================================
    # Candle Management
    # =========================================================================
    
    def save_candle(self, candle: Candle):
        """
        Save or update a candle with retry logic for database lock contention.
        
        Args:
            candle: Candle object to save
        """
        def _save():
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO candles 
                (ticker, timestamp, datetime_utc, open, high, low, close, 
                 volume, tick_count, is_complete, interval_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                candle.ticker, candle.timestamp, candle.datetime_utc,
                candle.open, candle.high, candle.low, candle.close,
                candle.volume, candle.tick_count, 
                1 if candle.is_complete else 0,
                candle.interval_minutes
            ))
            
            conn.commit()
        
        self._execute_with_retry(
            operation=_save,
            operation_name=f"save candle for {candle.ticker}",
            is_critical=True
        )
    
    def get_candles(self, ticker: str, count: int = 10, 
                   include_current: bool = True,
                   interval_minutes: Optional[int] = None,
                   from_timestamp: Optional[int] = None,
                   to_timestamp: Optional[int] = None) -> List[Candle]:
        """Get candles for a ticker with optional filters."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM candles WHERE ticker = ?'
        params: List[Any] = [ticker.upper()]
        
        if not include_current:
            query += ' AND is_complete = 1'
        
        if interval_minutes:
            query += ' AND interval_minutes = ?'
            params.append(interval_minutes)
            
        if from_timestamp:
            query += ' AND timestamp >= ?'
            params.append(from_timestamp)
            
        if to_timestamp:
            query += ' AND timestamp <= ?'
            params.append(to_timestamp)
        
        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(count)
        
        cursor.execute(query, params)
        
        candles = []
        for row in cursor.fetchall():
            candles.append(Candle(
                ticker=row['ticker'],
                timestamp=row['timestamp'],
                datetime_utc=row['datetime_utc'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                tick_count=row['tick_count'],
                is_complete=bool(row['is_complete']),
                interval_minutes=row['interval_minutes']
            ))
        
        # Return in chronological order
        return list(reversed(candles))
    
    def get_current_candle(self, ticker: str) -> Optional[Candle]:
        """Get the current incomplete candle for a ticker."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM candles 
            WHERE ticker = ? AND is_complete = 0
            ORDER BY timestamp DESC LIMIT 1
        ''', (ticker.upper(),))
        
        row = cursor.fetchone()
        if row:
            return Candle(
                ticker=row['ticker'],
                timestamp=row['timestamp'],
                datetime_utc=row['datetime_utc'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                tick_count=row['tick_count'],
                is_complete=bool(row['is_complete']),
                interval_minutes=row['interval_minutes']
            )
        return None
    
    def clear_candles(self, ticker: Optional[str] = None):
        """Clear candle history for a ticker or all tickers."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if ticker:
            cursor.execute('DELETE FROM candles WHERE ticker = ?', (ticker.upper(),))
            logger.info(f"Cleared candles for {ticker}")
        else:
            cursor.execute('DELETE FROM candles')
            logger.info("Cleared all candles")
        
        conn.commit()
    
    def cleanup_old_candles(self, ticker: str, max_candles: int):
        """
        Remove old candles keeping only the most recent max_candles.
        
        This is a non-critical operation - failures are logged but don't raise exceptions.
        
        Args:
            ticker: Ticker symbol
            max_candles: Maximum number of candles to keep
        """
        def _cleanup():
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM candles 
                WHERE ticker = ? AND id NOT IN (
                    SELECT id FROM candles 
                    WHERE ticker = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                )
            ''', (ticker.upper(), ticker.upper(), max_candles))
            
            deleted = cursor.rowcount
            if deleted > 0:
                conn.commit()
                logger.debug(f"Cleaned up {deleted} old candles for {ticker}")
        
        self._execute_with_retry(
            operation=_cleanup,
            operation_name=f"cleanup old candles for {ticker}",
            is_critical=False  # Non-critical - don't fail on cleanup errors
        )
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_stats(self) -> dict:
        """
        Get database statistics with TTL-based caching.

        Stats are cached for STATS_CACHE_TTL seconds to avoid
        expensive full-table scans on every /status request.
        """
        now = time.time()

        # Return cached stats if still valid
        if self._stats_cache and (now - self._stats_cache_time) < self.STATS_CACHE_TTL:
            return self._stats_cache

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM tickers')
        ticker_count = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM candles')
        total_candles = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM candles WHERE is_complete = 1')
        complete_candles = cursor.fetchone()[0]

        cursor.execute('''
            SELECT ticker, COUNT(*) as count
            FROM candles
            GROUP BY ticker
        ''')
        candles_per_ticker = {row['ticker']: row['count'] for row in cursor.fetchall()}

        # Get oldest and newest candle timestamps
        cursor.execute('SELECT MIN(timestamp) FROM candles')
        oldest_timestamp = cursor.fetchone()[0]

        cursor.execute('SELECT MAX(timestamp) FROM candles')
        newest_timestamp = cursor.fetchone()[0]

        result = {
            'ticker_count': ticker_count,
            'total_candles': total_candles,
            'complete_candles': complete_candles,
            'incomplete_candles': total_candles - complete_candles,
            'candles_per_ticker': candles_per_ticker,
            'oldest_candle_timestamp': oldest_timestamp,
            'newest_candle_timestamp': newest_timestamp
        }

        # Update cache
        self._stats_cache = result
        self._stats_cache_time = now

        return result
    
    # =========================================================================
    # WebSocket Status (Multi-Worker Status Sharing)
    # =========================================================================
    
    def update_websocket_status(self, status: Dict[str, Any]):
        """
        Update WebSocket status in database for multi-worker visibility.
        
        Called by WebSocket worker to share status with API workers.
        Uses REPLACE to upsert single row (id=1).
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            REPLACE INTO websocket_status (
                id, connected, subscribed_tickers, subscribed_count,
                pending_subscribe, connection_count, tick_count,
                tick_queue_size, tick_queue_maxsize,
                tick_enqueued_count, tick_processed_count, tick_dropped_count,
                candle_write_queue_size, candle_write_queue_maxsize, candle_write_dropped_count,
                last_message, last_update
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            1 if status.get('connected') else 0,
            json.dumps(status.get('subscribed_tickers', [])),
            status.get('subscribed_count', 0),
            json.dumps(status.get('pending_subscribe', [])),
            status.get('connection_count', 0),
            status.get('tick_count', 0),
            status.get('tick_queue_size', 0),
            status.get('tick_queue_maxsize', 0),
            status.get('tick_enqueued_count', 0),
            status.get('tick_processed_count', 0),
            status.get('tick_dropped_count', 0),
            status.get('candle_write_queue_size', 0),
            status.get('candle_write_queue_maxsize', 0),
            status.get('candle_write_dropped_count', 0),
            status.get('last_message'),
            datetime.now(timezone.utc).isoformat()
        ))
        
        conn.commit()
    
    def get_websocket_status(self, stale_threshold_seconds: int = 30) -> Optional[Dict[str, Any]]:
        """
        Get WebSocket status from database.
        
        Args:
            stale_threshold_seconds: Seconds after which status is considered stale (default: 30)
        
        Returns status dict with 'is_stale' flag if last update > threshold.
        Returns None if no status has been written yet.
        Handles JSON parsing errors and datetime parsing errors gracefully.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM websocket_status WHERE id = 1')
        row = cursor.fetchone()
        
        if not row:
            return None
        
        # Parse JSON fields with error handling
        try:
            subscribed_tickers = json.loads(row['subscribed_tickers'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse subscribed_tickers JSON: {e}")
            subscribed_tickers = []
        
        try:
            pending_subscribe = json.loads(row['pending_subscribe'])
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse pending_subscribe JSON: {e}")
            pending_subscribe = []
        
        # Parse datetime with error handling
        try:
            last_update = datetime.fromisoformat(row['last_update'])
            now = datetime.now(timezone.utc)
            age_seconds = (now - last_update).total_seconds()
            is_stale = age_seconds > stale_threshold_seconds
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse last_update timestamp: {e}")
            # If timestamp is malformed, consider it very stale
            age_seconds = 999999
            is_stale = True
        
        return {
            'connected': bool(row['connected']),
            'subscribed_tickers': subscribed_tickers,
            'subscribed_count': row['subscribed_count'],
            'pending_subscribe': pending_subscribe,
            'connection_count': row['connection_count'],
            'tick_count': row['tick_count'],
            'tick_queue_size': row['tick_queue_size'],
            'tick_queue_maxsize': row['tick_queue_maxsize'],
            'tick_enqueued_count': row['tick_enqueued_count'],
            'tick_processed_count': row['tick_processed_count'],
            'tick_dropped_count': row['tick_dropped_count'],
            'candle_write_queue_size': row['candle_write_queue_size'],
            'candle_write_queue_maxsize': row['candle_write_queue_maxsize'],
            'candle_write_dropped_count': row['candle_write_dropped_count'],
            'last_message': row['last_message'],
            'last_update': row['last_update'],
            'is_stale': is_stale,
            'age_seconds': age_seconds
        }
    
    # =========================================================================
    # Active Candles Status (Multi-Worker Status Sharing)
    # =========================================================================
    
    def update_active_candles(self, candles: List[Dict[str, Any]]):
        """
        Update active candles status in database for multi-worker visibility.
        
        Called by WebSocket worker to share active candles with API workers.
        Uses REPLACE to upsert single row (id=1).
        
        Args:
            candles: List of active candle summaries from CandleEngine.get_active_tickers_summary()
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            REPLACE INTO active_candles_status (id, data, updated_at)
            VALUES (1, ?, ?)
        ''', (
            json.dumps(candles),
            datetime.now(timezone.utc).isoformat()
        ))
        
        conn.commit()
    
    def get_active_candles(self, stale_threshold_seconds: int = 30) -> Optional[List[Dict[str, Any]]]:
        """
        Get active candles status from database.
        
        Args:
            stale_threshold_seconds: Seconds after which status is considered stale (default: 30)
        
        Returns list of active candle summaries, or None if no data or stale.
        Handles JSON parsing errors gracefully.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM active_candles_status WHERE id = 1')
        row = cursor.fetchone()
        
        if not row:
            return None
        
        # Check if data is stale
        try:
            updated_at = datetime.fromisoformat(row['updated_at'])
            now = datetime.now(timezone.utc)
            age_seconds = (now - updated_at).total_seconds()
            
            if age_seconds > stale_threshold_seconds:
                logger.debug(f"Active candles data is stale ({age_seconds:.0f}s old)")
                return None
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse updated_at timestamp: {e}")
            return None
        
        # Parse JSON data with error handling
        try:
            candles = json.loads(row['data'])
            if not isinstance(candles, list):
                logger.error("Active candles data is not a list")
                return None
            return candles
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Failed to parse active candles JSON: {e}")
            return None


def _get_default_config_path() -> str:
    """
    Get default config file path.

    For Docker: /data/config.json
    For local development: ./data/config.json (relative to project root)
    """
    # Check if /data exists and is writable (Docker environment)
    if os.path.exists('/data') and os.access('/data', os.W_OK):
        return '/data/config.json'

    # Use local data directory for development
    # Navigate from src/storage.py to project root
    project_root = Path(__file__).parent.parent
    local_data_dir = project_root / 'data'
    return str(local_data_dir / 'config.json')


class ConfigStorage:
    """
    JSON-based storage for runtime configuration overrides.

    Stores only fields that differ from .env defaults (sparse storage).
    Never stores sensitive information like API keys.
    """

    # Fields that should never be persisted (security sensitive)
    EXCLUDED_FIELDS = {
        'eodhd_api_key',
        'api_key',
        'database_path',
        'http_host',
        'http_port',
        'log_level',
        'default_tickers',
        'allow_delete_all_tickers'
    }

    # Fields that can be persisted
    ALLOWED_FIELDS = {
        'candle_interval_minutes',
        'max_candles_stored',
        'max_tickers',
        'ws_reconnect_delay',
        'ws_ping_interval'
    }

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config storage.

        Args:
            config_path: Path to config JSON file. If None, uses auto-detected default.
        """
        self.config_path = config_path or _get_default_config_path()
        self._ensure_directory()

    def _ensure_directory(self):
        """Ensure the config directory exists."""
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)

    def save_config(self, overrides: Dict[str, Any]) -> bool:
        """
        Save configuration overrides to JSON file.

        Only saves allowed fields (excludes sensitive data).

        Args:
            overrides: Dictionary of config field overrides

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Filter to only allowed fields
            filtered = {
                k: v for k, v in overrides.items()
                if k in self.ALLOWED_FIELDS
            }

            if not filtered:
                logger.debug("No allowed fields to persist")
                return True

            data = {
                'version': '1.0',
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'overrides': filtered
            }

            # Atomic write using temp file
            temp_path = f"{self.config_path}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)

            # Atomic rename
            os.replace(temp_path, self.config_path)

            logger.info(f"Saved config overrides to {self.config_path}: {list(filtered.keys())}")
            return True

        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def load_config(self) -> Optional[Dict[str, Any]]:
        """
        Load configuration overrides from JSON file.

        Returns:
            Dictionary of overrides, or None if file doesn't exist or is invalid
        """
        if not os.path.exists(self.config_path):
            logger.debug(f"Config file not found: {self.config_path}")
            return None

        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)

            # Validate structure
            if not isinstance(data, dict) or 'overrides' not in data:
                logger.warning(f"Invalid config file structure in {self.config_path}")
                return None

            overrides = data['overrides']

            # Filter to only allowed fields (security check)
            filtered = {
                k: v for k, v in overrides.items()
                if k in self.ALLOWED_FIELDS
            }

            if filtered:
                logger.info(f"Loaded config overrides from {self.config_path}: {list(filtered.keys())}")
            else:
                logger.debug("No valid overrides found in config file")

            return filtered

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in config file {self.config_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return None

    def delete_config(self) -> bool:
        """
        Delete the config file (used on reset).

        Returns:
            True if deleted or didn't exist, False on error
        """
        try:
            if os.path.exists(self.config_path):
                os.remove(self.config_path)
                logger.info(f"Deleted config file: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete config file: {e}")
            return False

    def exists(self) -> bool:
        """Check if config file exists."""
        return os.path.exists(self.config_path)
