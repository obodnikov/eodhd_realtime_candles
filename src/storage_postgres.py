"""
PostgreSQL storage for candles and ticker management.
Drop-in replacement for SQLite Storage class with same interface.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Callable
from pathlib import Path
import threading

try:
    import psycopg2
    from psycopg2 import pool, extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

from .storage import Candle, TrackedTicker, ConfigStorage

logger = logging.getLogger(__name__)


class PostgreSQLStorage:
    """PostgreSQL-based storage for candles and tickers."""
    
    # Cache TTL for get_stats() in seconds
    STATS_CACHE_TTL = 5.0
    
    # Retry configuration for database operations
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 0.05  # 50ms base delay

    def __init__(self, connection_string: str, min_connections: int = 2, max_connections: int = 10):
        """
        Initialize PostgreSQL storage.
        
        Args:
            connection_string: PostgreSQL connection string (DSN format)
            min_connections: Minimum pool connections
            max_connections: Maximum pool connections
        """
        if not PSYCOPG2_AVAILABLE:
            raise ImportError("psycopg2 is required for PostgreSQL storage. Install with: pip install psycopg2-binary")
        
        self.connection_string = connection_string
        self.min_connections = min_connections
        self.max_connections = max_connections
        self._pool: Optional[pool.ThreadedConnectionPool] = None
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_time: float = 0.0
        self._init_pool()
        self._init_db()

    def _init_pool(self):
        """Initialize connection pool."""
        try:
            self._pool = pool.ThreadedConnectionPool(
                self.min_connections,
                self.max_connections,
                self.connection_string
            )
            logger.info(f"PostgreSQL connection pool initialized (min={self.min_connections}, max={self.max_connections})")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL connection pool: {e}")
            raise
    
    def _get_connection(self):
        """Get connection from pool."""
        if self._pool is None:
            raise RuntimeError("Connection pool not initialized")
        return self._pool.getconn()
    
    def _put_connection(self, conn):
        """Return connection to pool."""
        if self._pool is not None:
            self._pool.putconn(conn)
    
    def _execute_with_retry(
        self,
        operation: Callable[[], Any],
        operation_name: str,
        is_critical: bool = True,
        max_retries: Optional[int] = None
    ) -> Optional[Any]:
        """
        Execute a database operation with retry logic.
        
        PostgreSQL has much better concurrency than SQLite, so retries
        are mainly for transient connection issues.
        """
        retries = max_retries if max_retries is not None else self.MAX_RETRIES
        
        for attempt in range(retries):
            try:
                return operation()
            except psycopg2.OperationalError as e:
                if attempt < retries - 1:
                    wait_time = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Database error during {operation_name}, "
                        f"retry {attempt + 1}/{retries} after {wait_time*1000:.0f}ms: {e}"
                    )
                    time.sleep(wait_time)
                else:
                    if is_critical:
                        logger.error(f"Failed {operation_name} after {attempt + 1} attempts: {e}")
                        raise
                    else:
                        logger.error(f"Failed {operation_name} after {attempt + 1} attempts: {e}")
                        return None
            except Exception as e:
                if is_critical:
                    logger.error(f"Unexpected error during {operation_name}: {e}")
                    raise
                else:
                    logger.error(f"Unexpected error during {operation_name}: {e}")
                    return None
        
        return None
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            # Read and execute schema from init_postgres.sql
            schema_path = Path(__file__).parent.parent / 'scripts' / 'init_postgres.sql'
            if schema_path.exists():
                with open(schema_path, 'r') as f:
                    schema_sql = f.read()
                cursor.execute(schema_sql)
            else:
                # Inline schema if file not found
                self._create_schema_inline(cursor)
            
            conn.commit()
            logger.info("PostgreSQL database schema initialized")
        finally:
            self._put_connection(conn)

    def _create_schema_inline(self, cursor):
        """Create schema inline if SQL file not found."""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS candles (
                id BIGSERIAL PRIMARY KEY,
                ticker VARCHAR(10) NOT NULL,
                timestamp BIGINT NOT NULL,
                datetime_utc TEXT NOT NULL,
                open DECIMAL(12, 4) NOT NULL,
                high DECIMAL(12, 4) NOT NULL,
                low DECIMAL(12, 4) NOT NULL,
                close DECIMAL(12, 4) NOT NULL,
                volume BIGINT NOT NULL,
                tick_count INTEGER NOT NULL,
                is_complete BOOLEAN NOT NULL,
                interval_minutes INTEGER NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, timestamp, interval_minutes)
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_candles_ticker_timestamp ON candles(ticker, timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_candles_ticker_complete ON candles(ticker, is_complete) WHERE is_complete = true')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles(timestamp DESC)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickers (
                symbol VARCHAR(10) PRIMARY KEY,
                added_at TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'active',
                last_tick_at TEXT,
                last_price DECIMAL(12, 4),
                last_candle_request_at TEXT,
                updated_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS websocket_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                connected BOOLEAN NOT NULL,
                subscribed_tickers TEXT NOT NULL,
                subscribed_count INTEGER NOT NULL,
                pending_subscribe TEXT NOT NULL,
                connection_count INTEGER NOT NULL,
                tick_count BIGINT NOT NULL,
                last_message TEXT,
                last_update TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_candles_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
    
    def close(self):
        """Close connection pool."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL connection pool closed")
    
    # =========================================================================
    # Ticker Management
    # =========================================================================
    
    def add_ticker(self, symbol: str) -> bool:
        """Add a ticker to track. Returns True if added, False if already exists."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO tickers (symbol, added_at, status)
                    VALUES (%s, %s, 'active')
                ''', (symbol.upper(), datetime.now(timezone.utc).isoformat()))
                conn.commit()
                logger.info(f"Added ticker: {symbol}")
                return True
            except psycopg2.IntegrityError:
                conn.rollback()
                logger.debug(f"Ticker already exists: {symbol}")
                return False
        finally:
            self._put_connection(conn)
    
    def remove_ticker(self, symbol: str) -> bool:
        """Remove a ticker and its candles. Returns True if removed."""
        def _remove():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                symbol_upper = symbol.upper()
                
                cursor.execute('DELETE FROM candles WHERE ticker = %s', (symbol_upper,))
                cursor.execute('DELETE FROM tickers WHERE symbol = %s', (symbol_upper,))
                removed = cursor.rowcount > 0
                
                conn.commit()
                
                if removed:
                    logger.info(f"Removed ticker and candles: {symbol_upper}")
                
                return removed
            finally:
                self._put_connection(conn)
        
        result = self._execute_with_retry(
            operation=_remove,
            operation_name=f"remove ticker {symbol}",
            is_critical=True
        )
        
        self._stats_cache = None
        return result if result is not None else False

    def get_tickers(self) -> List[TrackedTicker]:
        """Get all tracked tickers with their status."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
            
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
                LEFT JOIN candles c ON t.symbol = c.ticker AND c.is_complete = true
                GROUP BY t.symbol, t.added_at, t.status, t.last_tick_at, t.last_price, t.last_candle_request_at
                ORDER BY t.symbol
            ''')
            
            tickers = []
            for row in cursor.fetchall():
                tickers.append(TrackedTicker(
                    symbol=row['symbol'],
                    added_at=self._to_isoformat(row['added_at']),
                    status=row['status'],
                    last_tick_at=self._to_isoformat(row['last_tick_at']),
                    last_price=float(row['last_price']) if row['last_price'] else None,
                    candle_count=row['candle_count'],
                    last_candle_request_at=self._to_isoformat(row['last_candle_request_at'])
                ))
            
            return tickers
        finally:
            self._put_connection(conn)
    
    def get_ticker_symbols(self) -> List[str]:
        """Get list of ticker symbols only."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT symbol FROM tickers ORDER BY symbol')
            return [row[0] for row in cursor.fetchall()]
        finally:
            self._put_connection(conn)
    
    def update_ticker_status(self, symbol: str, status: str, 
                            last_tick_at: Optional[str] = None,
                            last_price: Optional[float] = None):
        """Update ticker status and last tick info."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            if last_tick_at and last_price is not None:
                cursor.execute('''
                    UPDATE tickers 
                    SET status = %s, last_tick_at = %s, last_price = %s, updated_at = %s
                    WHERE symbol = %s
                ''', (status, last_tick_at, last_price, 
                      datetime.now(timezone.utc).isoformat(), symbol.upper()))
            else:
                cursor.execute('''
                    UPDATE tickers 
                    SET status = %s, updated_at = %s
                    WHERE symbol = %s
                ''', (status, datetime.now(timezone.utc).isoformat(), symbol.upper()))
            
            conn.commit()
        finally:
            self._put_connection(conn)
    
    def ticker_exists(self, symbol: str) -> bool:
        """Check if ticker exists."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM tickers WHERE symbol = %s', (symbol.upper(),))
            return cursor.fetchone() is not None
        finally:
            self._put_connection(conn)

    def get_ticker(self, symbol: str) -> Optional[TrackedTicker]:
        """Get single ticker with metadata."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

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
                LEFT JOIN candles c ON t.symbol = c.ticker AND c.is_complete = true
                WHERE t.symbol = %s
                GROUP BY t.symbol, t.added_at, t.status, t.last_tick_at, t.last_price, t.last_candle_request_at
            ''', (symbol.upper(),))

            row = cursor.fetchone()
            if not row:
                return None

            return TrackedTicker(
                symbol=row['symbol'],
                added_at=self._to_isoformat(row['added_at']),
                status=row['status'],
                last_tick_at=self._to_isoformat(row['last_tick_at']),
                last_price=float(row['last_price']) if row['last_price'] else None,
                candle_count=row['candle_count'],
                last_candle_request_at=self._to_isoformat(row['last_candle_request_at'])
            )
        finally:
            self._put_connection(conn)

    def get_ticker_count(self) -> int:
        """Get count of tracked tickers."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM tickers')
            return cursor.fetchone()[0]
        finally:
            self._put_connection(conn)

    def get_ticker_intervals(self, ticker: str) -> List[int]:
        """Get unique interval_minutes values for a ticker's completed candles."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT interval_minutes
                FROM candles
                WHERE ticker = %s AND is_complete = true
                ORDER BY interval_minutes ASC
            ''', (ticker.upper(),))
            return [row[0] for row in cursor.fetchall()]
        finally:
            self._put_connection(conn)

    def get_candles_for_aggregation(
        self,
        ticker: str,
        base_interval: int,
        count: Optional[int] = None,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None
    ) -> List[Candle]:
        """Get completed candles for aggregation."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

            query = '''
                SELECT * FROM candles
                WHERE ticker = %s AND is_complete = true AND interval_minutes = %s
            '''
            params: List[Any] = [ticker.upper(), base_interval]

            if from_timestamp:
                query += ' AND timestamp >= %s'
                params.append(from_timestamp)

            if to_timestamp:
                query += ' AND timestamp <= %s'
                params.append(to_timestamp)

            query += ' ORDER BY timestamp DESC'

            if count:
                query += ' LIMIT %s'
                params.append(count)

            cursor.execute(query, params)

            candles = []
            for row in cursor.fetchall():
                candles.append(Candle(
                    ticker=row['ticker'],
                    timestamp=row['timestamp'],
                    datetime_utc=row['datetime_utc'],
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=row['volume'],
                    tick_count=row['tick_count'],
                    is_complete=bool(row['is_complete']),
                    interval_minutes=row['interval_minutes']
                ))

            return list(reversed(candles))
        finally:
            self._put_connection(conn)
    
    def update_ticker_last_request(self, symbol: str):
        """Update last candle request timestamp for a ticker."""
        def _update():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE tickers 
                    SET last_candle_request_at = %s
                    WHERE symbol = %s
                ''', (datetime.now(timezone.utc).isoformat(), symbol.upper()))
                conn.commit()
            finally:
                self._put_connection(conn)
        
        self._execute_with_retry(
            operation=_update,
            operation_name=f"update last_request for {symbol}",
            is_critical=False
        )

    def delete_all_tickers(self) -> int:
        """Remove all tickers from tracking and delete their candle data."""
        def _delete_all():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                cursor.execute('SELECT COUNT(*) FROM tickers')
                count = cursor.fetchone()[0]

                cursor.execute('DELETE FROM candles')
                cursor.execute('DELETE FROM tickers')

                conn.commit()
                logger.info(f"Deleted all {count} tickers and their candle data")
                return count
            finally:
                self._put_connection(conn)
        
        result = self._execute_with_retry(
            operation=_delete_all,
            operation_name="delete all tickers",
            is_critical=True
        )
        
        self._stats_cache = None
        return result if result is not None else 0

    def cleanup_orphaned_candles(self) -> int:
        """Remove candles for tickers that are no longer tracked."""
        def _cleanup():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

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
            finally:
                self._put_connection(conn)
        
        result = self._execute_with_retry(
            operation=_cleanup,
            operation_name="cleanup orphaned candles",
            is_critical=True
        )
        
        self._stats_cache = None
        return result if result is not None else 0

    # =========================================================================
    # Candle Management
    # =========================================================================
    
    def save_candle(self, candle: Candle):
        """Save or update a candle with retry logic."""
        def _save():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # PostgreSQL UPSERT using ON CONFLICT
                cursor.execute('''
                    INSERT INTO candles 
                    (ticker, timestamp, datetime_utc, open, high, low, close, 
                     volume, tick_count, is_complete, interval_minutes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, timestamp, interval_minutes) 
                    DO UPDATE SET
                        datetime_utc = EXCLUDED.datetime_utc,
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        tick_count = EXCLUDED.tick_count,
                        is_complete = EXCLUDED.is_complete
                ''', (
                    candle.ticker, candle.timestamp, candle.datetime_utc,
                    candle.open, candle.high, candle.low, candle.close,
                    candle.volume, candle.tick_count, 
                    candle.is_complete,
                    candle.interval_minutes
                ))
                
                conn.commit()
            finally:
                self._put_connection(conn)
        
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
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
            
            query = 'SELECT * FROM candles WHERE ticker = %s'
            params: List[Any] = [ticker.upper()]
            
            if not include_current:
                query += ' AND is_complete = true'
            
            if interval_minutes:
                query += ' AND interval_minutes = %s'
                params.append(interval_minutes)
                
            if from_timestamp:
                query += ' AND timestamp >= %s'
                params.append(from_timestamp)
                
            if to_timestamp:
                query += ' AND timestamp <= %s'
                params.append(to_timestamp)
            
            query += ' ORDER BY timestamp DESC LIMIT %s'
            params.append(count)
            
            cursor.execute(query, params)
            
            candles = []
            for row in cursor.fetchall():
                candles.append(Candle(
                    ticker=row['ticker'],
                    timestamp=row['timestamp'],
                    datetime_utc=row['datetime_utc'],
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=row['volume'],
                    tick_count=row['tick_count'],
                    is_complete=bool(row['is_complete']),
                    interval_minutes=row['interval_minutes']
                ))
            
            return list(reversed(candles))
        finally:
            self._put_connection(conn)
    
    def get_current_candle(self, ticker: str) -> Optional[Candle]:
        """Get the current incomplete candle for a ticker."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
            
            cursor.execute('''
                SELECT * FROM candles 
                WHERE ticker = %s AND is_complete = false
                ORDER BY timestamp DESC LIMIT 1
            ''', (ticker.upper(),))
            
            row = cursor.fetchone()
            if row:
                return Candle(
                    ticker=row['ticker'],
                    timestamp=row['timestamp'],
                    datetime_utc=row['datetime_utc'],
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=row['volume'],
                    tick_count=row['tick_count'],
                    is_complete=bool(row['is_complete']),
                    interval_minutes=row['interval_minutes']
                )
            return None
        finally:
            self._put_connection(conn)
    
    def clear_candles(self, ticker: Optional[str] = None):
        """Clear candle history for a ticker or all tickers."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            if ticker:
                cursor.execute('DELETE FROM candles WHERE ticker = %s', (ticker.upper(),))
                logger.info(f"Cleared candles for {ticker}")
            else:
                cursor.execute('DELETE FROM candles')
                logger.info("Cleared all candles")
            
            conn.commit()
        finally:
            self._put_connection(conn)
    
    def cleanup_old_candles(self, ticker: str, max_candles: int):
        """Remove old candles keeping only the most recent max_candles."""
        def _cleanup():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # PostgreSQL subquery for deletion
                cursor.execute('''
                    DELETE FROM candles 
                    WHERE ticker = %s AND id NOT IN (
                        SELECT id FROM candles 
                        WHERE ticker = %s 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                    )
                ''', (ticker.upper(), ticker.upper(), max_candles))
                
                deleted = cursor.rowcount
                if deleted > 0:
                    conn.commit()
                    logger.debug(f"Cleaned up {deleted} old candles for {ticker}")
            finally:
                self._put_connection(conn)
        
        self._execute_with_retry(
            operation=_cleanup,
            operation_name=f"cleanup old candles for {ticker}",
            is_critical=False
        )

    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_stats(self) -> dict:
        """Get database statistics with TTL-based caching."""
        now = time.time()

        if self._stats_cache and (now - self._stats_cache_time) < self.STATS_CACHE_TTL:
            return self._stats_cache

        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

            cursor.execute('SELECT COUNT(*) as cnt FROM tickers')
            ticker_count = cursor.fetchone()['cnt']

            cursor.execute('SELECT COUNT(*) as cnt FROM candles')
            total_candles = cursor.fetchone()['cnt']

            cursor.execute('SELECT COUNT(*) as cnt FROM candles WHERE is_complete = true')
            complete_candles = cursor.fetchone()['cnt']

            cursor.execute('''
                SELECT ticker, COUNT(*) as count
                FROM candles
                GROUP BY ticker
            ''')
            candles_per_ticker = {row['ticker']: row['count'] for row in cursor.fetchall()}

            cursor.execute('SELECT MIN(timestamp) as ts FROM candles')
            oldest_timestamp = cursor.fetchone()['ts']

            cursor.execute('SELECT MAX(timestamp) as ts FROM candles')
            newest_timestamp = cursor.fetchone()['ts']

            result = {
                'ticker_count': ticker_count,
                'total_candles': total_candles,
                'complete_candles': complete_candles,
                'incomplete_candles': total_candles - complete_candles,
                'candles_per_ticker': candles_per_ticker,
                'oldest_candle_timestamp': oldest_timestamp,
                'newest_candle_timestamp': newest_timestamp
            }

            self._stats_cache = result
            self._stats_cache_time = now

            return result
        finally:
            self._put_connection(conn)
    
    # =========================================================================
    # WebSocket Status (Multi-Worker Status Sharing)
    # =========================================================================
    
    def update_websocket_status(self, status: Dict[str, Any]):
        """Update WebSocket status in database for multi-worker visibility."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            # PostgreSQL UPSERT
            cursor.execute('''
                INSERT INTO websocket_status (
                    id, connected, subscribed_tickers, subscribed_count,
                    pending_subscribe, connection_count, tick_count,
                    last_message, last_update
                ) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    connected = EXCLUDED.connected,
                    subscribed_tickers = EXCLUDED.subscribed_tickers,
                    subscribed_count = EXCLUDED.subscribed_count,
                    pending_subscribe = EXCLUDED.pending_subscribe,
                    connection_count = EXCLUDED.connection_count,
                    tick_count = EXCLUDED.tick_count,
                    last_message = EXCLUDED.last_message,
                    last_update = EXCLUDED.last_update
            ''', (
                status.get('connected', False),
                json.dumps(status.get('subscribed_tickers', [])),
                status.get('subscribed_count', 0),
                json.dumps(status.get('pending_subscribe', [])),
                status.get('connection_count', 0),
                status.get('tick_count', 0),
                status.get('last_message'),
                datetime.now(timezone.utc).isoformat()
            ))
            
            conn.commit()
        finally:
            self._put_connection(conn)
    
    @staticmethod
    def _to_isoformat(value: Any) -> Optional[str]:
        """Convert a value to ISO format string for JSON serialization.
        
        Handles datetime objects (from TIMESTAMPTZ columns) and strings.
        Returns None if value is None.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _parse_timestamp(value: Any, field_name: str) -> datetime:
        """Parse a timestamp value from PostgreSQL into a timezone-aware datetime.
        
        Handles native datetime objects (from TIMESTAMPTZ columns) and
        ISO-format strings (from migrated data or TEXT columns).
        
        Returns:
            Timezone-aware datetime (UTC if no tzinfo present).
            
        Raises:
            ValueError: If value is None or not a recognized type.
        """
        if value is None:
            raise ValueError(f"{field_name} is None")
        
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value)
        else:
            raise ValueError(f"{field_name} has unexpected type {type(value).__name__}")
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        
        return dt

    def get_websocket_status(self, stale_threshold_seconds: int = 30) -> Optional[Dict[str, Any]]:
        """Get WebSocket status from database."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
            
            cursor.execute('SELECT * FROM websocket_status WHERE id = 1')
            row = cursor.fetchone()
            
            if not row:
                return None
            
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
            
            try:
                last_update = self._parse_timestamp(row['last_update'], 'last_update')
                now = datetime.now(timezone.utc)
                age_seconds = (now - last_update).total_seconds()
                is_stale = age_seconds > stale_threshold_seconds
            except (ValueError, TypeError) as e:
                logger.error(f"Failed to parse last_update timestamp: {e}")
                age_seconds = 999999
                is_stale = True
                last_update = None
            
            return {
                'connected': bool(row['connected']),
                'subscribed_tickers': subscribed_tickers,
                'subscribed_count': row['subscribed_count'],
                'pending_subscribe': pending_subscribe,
                'connection_count': row['connection_count'],
                'tick_count': row['tick_count'],
                'last_message': row['last_message'],
                'last_update': last_update.isoformat() if last_update else None,
                'is_stale': is_stale,
                'age_seconds': age_seconds
            }
        finally:
            self._put_connection(conn)

    # =========================================================================
    # Active Candles Status (Multi-Worker Status Sharing)
    # =========================================================================
    
    def update_active_candles(self, candles: List[Dict[str, Any]]):
        """Update active candles status in database for multi-worker visibility."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO active_candles_status (id, data, updated_at)
                VALUES (1, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
            ''', (
                json.dumps(candles),
                datetime.now(timezone.utc).isoformat()
            ))
            
            conn.commit()
        finally:
            self._put_connection(conn)
    
    def get_active_candles(self, stale_threshold_seconds: int = 30) -> Optional[List[Dict[str, Any]]]:
        """Get active candles status from database."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
            
            cursor.execute('SELECT * FROM active_candles_status WHERE id = 1')
            row = cursor.fetchone()
            
            if not row:
                return None
            
            try:
                updated_at = self._parse_timestamp(row['updated_at'], 'updated_at')
                now = datetime.now(timezone.utc)
                age_seconds = (now - updated_at).total_seconds()
                
                if age_seconds > stale_threshold_seconds:
                    logger.debug(f"Active candles data is stale ({age_seconds:.0f}s old)")
                    return None
            except (ValueError, TypeError) as e:
                logger.error(f"Failed to parse updated_at timestamp: {e}")
                return None
            
            try:
                candles = json.loads(row['data'])
                if not isinstance(candles, list):
                    logger.error("Active candles data is not a list")
                    return None
                return candles
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to parse active candles JSON: {e}")
                return None
        finally:
            self._put_connection(conn)
