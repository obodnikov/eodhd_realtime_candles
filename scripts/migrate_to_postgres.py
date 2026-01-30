#!/usr/bin/env python3
"""
SQLite to PostgreSQL Migration Script
=====================================

Migrates data from SQLite database to PostgreSQL.

Usage:
    python scripts/migrate_to_postgres.py <sqlite_path> <postgres_dsn> [--batch-size N]
    
Example:
    python scripts/migrate_to_postgres.py ./data/candles.db "host=localhost port=5432 dbname=eodhd_candles user=eodhd_user password=secret"
    python scripts/migrate_to_postgres.py ./data/candles.db "..." --batch-size 5000

Requirements:
    pip install psycopg2-binary
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    import psycopg2
    from psycopg2 import extras
except ImportError:
    print("Error: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(1)


def validate_boolean(value, field_name: str, context: str) -> bool:
    """Validate and convert boolean values from SQLite.
    
    SQLite stores booleans as integers (0/1). This function validates
    the value is in expected format and converts it safely.
    """
    if value is None:
        raise ValueError(f"{context}: {field_name} is NULL, expected boolean (0/1)")
    
    if isinstance(value, bool):
        return value
    
    if isinstance(value, int):
        if value not in (0, 1):
            raise ValueError(f"{context}: {field_name}={value} is not a valid boolean (expected 0 or 1)")
        return bool(value)
    
    raise ValueError(f"{context}: {field_name} has unexpected type {type(value).__name__}, expected int (0/1)")


def validate_decimal(value, field_name: str, context: str, precision: int = 4, strict: bool = False) -> Decimal:
    """Validate and convert decimal values from SQLite.
    
    SQLite stores decimals as floats which may lose precision.
    This function converts to Decimal and logs precision warnings.
    In strict mode, raises an error if precision loss is detected.
    """
    global _precision_warnings
    
    if value is None:
        raise ValueError(f"{context}: {field_name} is NULL, expected numeric value")
    
    try:
        # Convert to Decimal for precision
        decimal_value = Decimal(str(value))
        
        # Check if precision was potentially lost (more than expected decimal places)
        str_value = str(value)
        if '.' in str_value:
            decimal_places = len(str_value.split('.')[1])
            if decimal_places > precision:
                _precision_warnings += 1
                msg = f"{context} {field_name}={value} has {decimal_places} decimal places (storing as {precision})"
                if strict:
                    raise ValueError(f"Precision loss detected (strict mode): {msg}")
                print(f"   ⚠️  Precision warning: {msg}")
        
        return decimal_value
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{context}: {field_name}={value} cannot be converted to Decimal: {e}")


def validate_integer(value, field_name: str, context: str) -> int:
    """Validate integer values from SQLite."""
    if value is None:
        raise ValueError(f"{context}: {field_name} is NULL, expected integer")
    
    if not isinstance(value, int):
        raise ValueError(f"{context}: {field_name}={value} has type {type(value).__name__}, expected int")
    
    return value


def convert_timestamp(value, field_name: str, context: str) -> str:
    """Convert SQLite timestamp to PostgreSQL TIMESTAMPTZ compatible format.
    
    SQLite stores timestamps as TEXT in ISO format (e.g., '2026-01-30T12:34:56.123456Z')
    or as INTEGER (Unix epoch). This function normalizes to ISO format for PostgreSQL.
    """
    if value is None:
        return None
    
    # If it's already a string in ISO format, validate and return
    if isinstance(value, str):
        # Handle ISO format with 'T' separator and 'Z' suffix
        # e.g., '2026-01-30T12:34:56.123456Z' or '2026-01-30T12:34:56Z'
        try:
            # Try parsing to validate format
            if value.endswith('Z'):
                # Replace Z with +00:00 for PostgreSQL compatibility
                parsed = value[:-1] + '+00:00'
            elif '+' in value or value.count('-') > 2:
                # Already has timezone info
                parsed = value
            else:
                # No timezone, assume UTC
                parsed = value + '+00:00'
            
            # Validate by parsing (will raise if invalid)
            datetime.fromisoformat(parsed.replace('Z', '+00:00'))
            return parsed
        except ValueError as e:
            raise ValueError(f"{context}: {field_name}='{value}' is not a valid ISO timestamp: {e}")
    
    # If it's an integer, treat as Unix epoch
    if isinstance(value, int):
        try:
            dt = datetime.utcfromtimestamp(value)
            return dt.isoformat() + '+00:00'
        except (ValueError, OSError) as e:
            raise ValueError(f"{context}: {field_name}={value} is not a valid Unix timestamp: {e}")
    
    raise ValueError(f"{context}: {field_name} has unexpected type {type(value).__name__}, expected str or int")


# Track precision warnings globally for --strict mode
_precision_warnings = 0


def migrate_data(sqlite_path: str, postgres_dsn: str, batch_size: int = 1000, strict: bool = False):
    """Migrate data from SQLite to PostgreSQL."""
    
    global _precision_warnings
    _precision_warnings = 0
    
    print(f"Starting migration from {sqlite_path} to PostgreSQL...")
    if strict:
        print("   Running in STRICT mode - will abort on precision loss")
    start_time = time.time()
    
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    
    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(postgres_dsn)
    pg_cursor = pg_conn.cursor()
    
    try:
        # Migrate tickers
        print("\n[1/4] Migrating tickers...")
        cursor = sqlite_conn.execute("SELECT * FROM tickers")
        tickers = cursor.fetchall()
        
        for ticker in tickers:
            context = f"ticker {ticker['symbol']}"
            pg_cursor.execute('''
                INSERT INTO tickers 
                (symbol, added_at, status, last_tick_at, last_price, 
                 last_candle_request_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    status = EXCLUDED.status,
                    last_tick_at = EXCLUDED.last_tick_at,
                    last_price = EXCLUDED.last_price,
                    last_candle_request_at = EXCLUDED.last_candle_request_at,
                    updated_at = EXCLUDED.updated_at
            ''', (
                ticker['symbol'],
                convert_timestamp(ticker['added_at'], 'added_at', context),
                ticker['status'],
                convert_timestamp(ticker['last_tick_at'], 'last_tick_at', context),
                ticker['last_price'],
                convert_timestamp(ticker['last_candle_request_at'], 'last_candle_request_at', context),
                convert_timestamp(ticker['updated_at'], 'updated_at', context)
            ))
        
        # No commit here - will commit at end for atomicity
        print(f"   Prepared {len(tickers)} tickers")
        
        # Migrate candles in batches
        print("\n[2/4] Migrating candles...")
        cursor = sqlite_conn.execute("SELECT COUNT(*) FROM candles")
        total_candles = cursor.fetchone()[0]
        
        migrated = 0
        validation_errors = 0
        
        cursor = sqlite_conn.execute("SELECT * FROM candles ORDER BY id")
        
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            
            # Prepare batch data with type validation
            batch_data = []
            for c in batch:
                context = f"candle {c['ticker']}@{c['timestamp']}"
                try:
                    batch_data.append((
                        c['ticker'],
                        validate_integer(c['timestamp'], 'timestamp', context),
                        c['datetime_utc'],
                        validate_decimal(c['open'], 'open', context, strict=strict),
                        validate_decimal(c['high'], 'high', context, strict=strict),
                        validate_decimal(c['low'], 'low', context, strict=strict),
                        validate_decimal(c['close'], 'close', context, strict=strict),
                        validate_integer(c['volume'], 'volume', context),
                        validate_integer(c['tick_count'], 'tick_count', context),
                        validate_boolean(c['is_complete'], 'is_complete', context),
                        validate_integer(c['interval_minutes'], 'interval_minutes', context),
                        convert_timestamp(c['created_at'], 'created_at', context)
                    ))
                except ValueError as e:
                    validation_errors += 1
                    print(f"\n   ❌ Validation error: {e}")
                    if validation_errors >= 10:
                        raise ValueError(f"Too many validation errors ({validation_errors}). Aborting migration.")
            
            # Batch insert with ON CONFLICT
            extras.execute_batch(pg_cursor, '''
                INSERT INTO candles 
                (ticker, timestamp, datetime_utc, open, high, low, close,
                 volume, tick_count, is_complete, interval_minutes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, timestamp, interval_minutes) DO UPDATE SET
                    datetime_utc = EXCLUDED.datetime_utc,
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    tick_count = EXCLUDED.tick_count,
                    is_complete = EXCLUDED.is_complete
            ''', batch_data)
            
            # No commit here - will commit at end for atomicity
            migrated += len(batch)
            
            # Progress indicator
            progress = (migrated / total_candles * 100) if total_candles > 0 else 100
            print(f"   Progress: {migrated}/{total_candles} candles ({progress:.1f}%)", end='\r')
        
        print(f"\n   Prepared {migrated} candles")
        if validation_errors > 0:
            print(f"   ⚠️  {validation_errors} records had validation warnings")
        
        # Migrate config
        print("\n[3/4] Migrating config...")
        cursor = sqlite_conn.execute("SELECT * FROM config")
        configs = cursor.fetchall()
        
        for config in configs:
            context = f"config {config['key']}"
            pg_cursor.execute('''
                INSERT INTO config (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at
            ''', (config['key'], config['value'], convert_timestamp(config['updated_at'], 'updated_at', context)))
        
        # No commit here - will commit at end for atomicity
        print(f"   Prepared {len(configs)} config entries")
        
        # Migrate WebSocket status
        print("\n[4/4] Migrating WebSocket status...")
        cursor = sqlite_conn.execute("SELECT * FROM websocket_status WHERE id = 1")
        ws_status = cursor.fetchone()
        
        if ws_status:
            context = "websocket_status"
            pg_cursor.execute('''
                INSERT INTO websocket_status 
                (id, connected, subscribed_tickers, subscribed_count,
                 pending_subscribe, connection_count, tick_count,
                 last_message, last_update)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)
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
                validate_boolean(ws_status['connected'], 'connected', context),
                ws_status['subscribed_tickers'],
                validate_integer(ws_status['subscribed_count'], 'subscribed_count', context),
                ws_status['pending_subscribe'],
                validate_integer(ws_status['connection_count'], 'connection_count', context),
                validate_integer(ws_status['tick_count'], 'tick_count', context),
                ws_status['last_message'],
                convert_timestamp(ws_status['last_update'], 'last_update', context)
            ))
            
            # No commit here - will commit at end for atomicity
            print("   Prepared WebSocket status")
        else:
            print("   No WebSocket status to migrate")
        
        # Migrate active candles status
        cursor = sqlite_conn.execute("SELECT * FROM active_candles_status WHERE id = 1")
        active_status = cursor.fetchone()
        
        if active_status:
            context = "active_candles_status"
            pg_cursor.execute('''
                INSERT INTO active_candles_status (id, data, updated_at)
                VALUES (1, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
            ''', (active_status['data'], convert_timestamp(active_status['updated_at'], 'updated_at', context)))
            
            # No commit here - will commit at end for atomicity
            print("   Prepared active candles status")
        
        # ATOMIC COMMIT - all or nothing
        print("\n[COMMIT] Committing all changes atomically...")
        pg_conn.commit()
        
        elapsed = time.time() - start_time
        print(f"\n✅ Migration completed successfully in {elapsed:.1f} seconds!")
        if _precision_warnings > 0:
            print(f"   ⚠️  {_precision_warnings} precision warnings (data migrated with truncation)")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
        
    finally:
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Migrate data from SQLite to PostgreSQL',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Example:
  python migrate_to_postgres.py ./data/candles.db "host=localhost port=5432 dbname=eodhd_candles user=eodhd_user password=secret"
  python migrate_to_postgres.py ./data/candles.db "..." --batch-size 5000
        '''
    )
    parser.add_argument('sqlite_path', help='Path to SQLite database file')
    parser.add_argument('postgres_dsn', help='PostgreSQL connection string')
    parser.add_argument('--batch-size', type=int, default=1000,
                        help='Number of candles to migrate per batch (default: 1000)')
    parser.add_argument('--strict', action='store_true',
                        help='Abort migration on any precision loss (default: warn and continue)')
    
    args = parser.parse_args()
    
    if args.batch_size < 100:
        print("Warning: batch_size < 100 may be slow. Using 100 as minimum.")
        args.batch_size = 100
    elif args.batch_size > 10000:
        print("Warning: batch_size > 10000 may cause memory issues. Using 10000 as maximum.")
        args.batch_size = 10000
    
    migrate_data(args.sqlite_path, args.postgres_dsn, args.batch_size, args.strict)


if __name__ == "__main__":
    main()
