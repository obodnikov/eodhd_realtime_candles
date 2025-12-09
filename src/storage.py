"""
SQLite storage for candles and ticker management.
Provides persistence across service restarts.
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path
import threading

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
    
    def to_dict(self) -> dict:
        return asdict(self)


class Storage:
    """SQLite-based storage for candles and tickers."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._ensure_directory()
        self._init_db()
        
    def _ensure_directory(self):
        """Ensure the database directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            self._local.connection.row_factory = sqlite3.Row
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
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
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
                candle_count=row['candle_count']
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
    
    def get_ticker_count(self) -> int:
        """Get count of tracked tickers."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM tickers')
        return cursor.fetchone()[0]
    
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
        """Get database statistics."""
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
        
        return {
            'ticker_count': ticker_count,
            'total_candles': total_candles,
            'complete_candles': complete_candles,
            'incomplete_candles': total_candles - complete_candles,
            'candles_per_ticker': candles_per_ticker
        }
