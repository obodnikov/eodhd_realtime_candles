"""
SQLite storage for candles and ticker management.
Provides persistence across service restarts.
"""

import sqlite3
import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
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

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_time: float = 0.0
        self._ensure_directory()
        self._init_db()
        
    def _ensure_directory(self):
        """Ensure the database directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        Applies SQLite tuning for better concurrency:
        - WAL journal mode for better read/write concurrency
        - NORMAL synchronous for fewer fsyncs (good trade-off for this use case)
        - busy_timeout to wait on locks instead of failing immediately
        """
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=5.0,  # seconds to wait when database is locked
            )
            conn.row_factory = sqlite3.Row

            try:
                # Enable WAL mode for better read/write concurrency
                conn.execute("PRAGMA journal_mode=WAL;")
                # Reduce fsyncs - acceptable trade-off for this use case
                conn.execute("PRAGMA synchronous=NORMAL;")
                # Wait up to 5 seconds if database is locked
                conn.execute("PRAGMA busy_timeout=5000;")
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
        conn = self._get_connection()
        cursor = conn.cursor()
        
        symbol = symbol.upper()
        
        # Remove candles first
        cursor.execute('DELETE FROM candles WHERE ticker = ?', (symbol,))
        
        # Remove ticker
        cursor.execute('DELETE FROM tickers WHERE symbol = ?', (symbol,))
        removed = cursor.rowcount > 0
        
        conn.commit()
        
        if removed:
            logger.info(f"Removed ticker and candles: {symbol}")
        
        return removed
    
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
        """Update last candle request timestamp for a ticker."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tickers 
            SET last_candle_request_at = ?
            WHERE symbol = ?
        ''', (datetime.now(timezone.utc).isoformat(), symbol.upper()))
        conn.commit()

    def delete_all_tickers(self) -> int:
        """
        Remove all tickers from tracking and delete their candle data.
        Returns count of removed tickers.
        """
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

    def cleanup_orphaned_candles(self) -> int:
        """
        Remove candles for tickers that are no longer tracked.

        This operation is atomic - it uses a single SQL statement with a subquery
        to ensure consistency even under concurrent ticker additions/removals.
        The NOT IN subquery is evaluated atomically within the DELETE statement,
        preventing race conditions.

        Returns count of deleted candle records.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
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

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to cleanup orphaned candles: {e}")
            raise

    # =========================================================================
    # Candle Management
    # =========================================================================
    
    def save_candle(self, candle: Candle):
        """Save or update a candle."""
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
        """Remove old candles keeping only the most recent max_candles."""
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
