#!/usr/bin/env python3
"""
Migration Validation Script
===========================

Validates data integrity after SQLite to PostgreSQL migration.

Usage:
    python scripts/validate_migration.py <sqlite_path> <postgres_dsn>
    
Example:
    python scripts/validate_migration.py ./data/candles.db "host=localhost port=5432 dbname=eodhd_candles user=eodhd_user password=secret"

Requirements:
    pip install psycopg2-binary
"""

import sqlite3
import sys
import random
from decimal import Decimal

try:
    import psycopg2
    from psycopg2 import extras
except ImportError:
    print("Error: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(1)


def validate_migration(sqlite_path: str, postgres_dsn: str) -> bool:
    """Validate data integrity after migration."""
    
    print(f"Validating migration from {sqlite_path} to PostgreSQL...")
    
    # Connect to both databases
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    
    pg_conn = psycopg2.connect(postgres_dsn)
    pg_cursor = pg_conn.cursor(cursor_factory=extras.RealDictCursor)
    
    errors = []
    
    try:
        # Validate ticker count
        print("\n[1/6] Validating tickers...")
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
        pg_cursor.execute("SELECT COUNT(*) as cnt FROM tickers")
        pg_count = pg_cursor.fetchone()['cnt']
        
        if sqlite_count != pg_count:
            errors.append(f"Ticker count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
            print(f"   ❌ Count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        else:
            print(f"   ✅ Tickers: {sqlite_count} records match")
        
        # Validate candle count
        print("\n[2/6] Validating candles...")
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        pg_cursor.execute("SELECT COUNT(*) as cnt FROM candles")
        pg_count = pg_cursor.fetchone()['cnt']
        
        if sqlite_count != pg_count:
            errors.append(f"Candle count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
            print(f"   ❌ Count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        else:
            print(f"   ✅ Candles: {sqlite_count} records match")
        
        # Validate sample data integrity
        print("\n[3/6] Validating data integrity (100 random samples)...")
        cursor = sqlite_conn.execute("""
            SELECT ticker, timestamp, interval_minutes, open, high, low, close, volume
            FROM candles
            ORDER BY RANDOM()
            LIMIT 100
        """)
        
        sample_candles = cursor.fetchall()
        mismatches = 0
        mismatch_details = []
        
        for candle in sample_candles:
            pg_cursor.execute("""
                SELECT open, high, low, close, volume
                FROM candles
                WHERE ticker = %s AND timestamp = %s AND interval_minutes = %s
            """, (candle['ticker'], candle['timestamp'], candle['interval_minutes']))
            
            pg_candle = pg_cursor.fetchone()
            
            if not pg_candle:
                mismatches += 1
                mismatch_details.append(f"Missing: {candle['ticker']}@{candle['timestamp']}")
                continue
            
            # Compare values using Decimal for proper precision comparison
            # PostgreSQL returns Decimal, SQLite returns float - convert both to Decimal
            sqlite_open = Decimal(str(candle['open']))
            sqlite_high = Decimal(str(candle['high']))
            sqlite_low = Decimal(str(candle['low']))
            sqlite_close = Decimal(str(candle['close']))
            
            pg_open = Decimal(str(pg_candle['open']))
            pg_high = Decimal(str(pg_candle['high']))
            pg_low = Decimal(str(pg_candle['low']))
            pg_close = Decimal(str(pg_candle['close']))
            
            # Use Decimal tolerance for comparison (0.0001 = 4 decimal places)
            tolerance = Decimal('0.0001')
            has_mismatch = False
            details = []
            
            if abs(pg_open - sqlite_open) > tolerance:
                has_mismatch = True
                details.append(f"open: {sqlite_open} vs {pg_open}")
            if abs(pg_high - sqlite_high) > tolerance:
                has_mismatch = True
                details.append(f"high: {sqlite_high} vs {pg_high}")
            if abs(pg_low - sqlite_low) > tolerance:
                has_mismatch = True
                details.append(f"low: {sqlite_low} vs {pg_low}")
            if abs(pg_close - sqlite_close) > tolerance:
                has_mismatch = True
                details.append(f"close: {sqlite_close} vs {pg_close}")
            if pg_candle['volume'] != candle['volume']:
                has_mismatch = True
                details.append(f"volume: {candle['volume']} vs {pg_candle['volume']}")
            
            if has_mismatch:
                mismatches += 1
                mismatch_details.append(f"{candle['ticker']}@{candle['timestamp']}: {', '.join(details)}")
        
        if mismatches > 0:
            errors.append(f"Data integrity issues: {mismatches}/100 sample candles have mismatches")
            print(f"   ❌ {mismatches}/100 samples have mismatches")
            # Show first 5 mismatch details for debugging
            for detail in mismatch_details[:5]:
                print(f"      - {detail}")
            if len(mismatch_details) > 5:
                print(f"      ... and {len(mismatch_details) - 5} more")
        else:
            print(f"   ✅ Data integrity: 100 sample candles verified")
        
        # Validate config
        print("\n[4/6] Validating config...")
        sqlite_count = sqlite_conn.execute("SELECT COUNT(*) FROM config").fetchone()[0]
        pg_cursor.execute("SELECT COUNT(*) as cnt FROM config")
        pg_count = pg_cursor.fetchone()['cnt']
        
        if sqlite_count != pg_count:
            errors.append(f"Config count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
            print(f"   ❌ Count mismatch: SQLite={sqlite_count}, PostgreSQL={pg_count}")
        else:
            print(f"   ✅ Config: {sqlite_count} records match")
        
        # Validate websocket_status
        print("\n[5/6] Validating websocket_status...")
        sqlite_ws = sqlite_conn.execute("SELECT * FROM websocket_status WHERE id = 1").fetchone()
        pg_cursor.execute("SELECT * FROM websocket_status WHERE id = 1")
        pg_ws = pg_cursor.fetchone()
        
        if sqlite_ws and not pg_ws:
            errors.append("websocket_status: exists in SQLite but missing in PostgreSQL")
            print("   ❌ Missing in PostgreSQL")
        elif not sqlite_ws and pg_ws:
            print("   ⚠️  No data in SQLite (PostgreSQL has data - may be from runtime)")
        elif sqlite_ws and pg_ws:
            # Validate key fields
            ws_mismatches = []
            if bool(sqlite_ws['connected']) != pg_ws['connected']:
                ws_mismatches.append(f"connected: {sqlite_ws['connected']} vs {pg_ws['connected']}")
            if sqlite_ws['subscribed_count'] != pg_ws['subscribed_count']:
                ws_mismatches.append(f"subscribed_count: {sqlite_ws['subscribed_count']} vs {pg_ws['subscribed_count']}")
            if sqlite_ws['tick_count'] != pg_ws['tick_count']:
                ws_mismatches.append(f"tick_count: {sqlite_ws['tick_count']} vs {pg_ws['tick_count']}")
            
            if ws_mismatches:
                errors.append(f"websocket_status field mismatches: {', '.join(ws_mismatches)}")
                print(f"   ❌ Field mismatches: {', '.join(ws_mismatches)}")
            else:
                print("   ✅ websocket_status: data matches")
        else:
            print("   ✅ websocket_status: no data in either database")
        
        # Validate active_candles_status
        print("\n[6/6] Validating active_candles_status...")
        sqlite_acs = sqlite_conn.execute("SELECT * FROM active_candles_status WHERE id = 1").fetchone()
        pg_cursor.execute("SELECT * FROM active_candles_status WHERE id = 1")
        pg_acs = pg_cursor.fetchone()
        
        if sqlite_acs and not pg_acs:
            errors.append("active_candles_status: exists in SQLite but missing in PostgreSQL")
            print("   ❌ Missing in PostgreSQL")
        elif not sqlite_acs and pg_acs:
            print("   ⚠️  No data in SQLite (PostgreSQL has data - may be from runtime)")
        elif sqlite_acs and pg_acs:
            # Validate data field (JSON content)
            if sqlite_acs['data'] != pg_acs['data']:
                errors.append("active_candles_status: data content mismatch")
                print("   ❌ Data content mismatch")
            else:
                print("   ✅ active_candles_status: data matches")
        else:
            print("   ✅ active_candles_status: no data in either database")
        
        # Summary
        if errors:
            print("\n" + "=" * 60)
            print("❌ VALIDATION FAILED")
            print("=" * 60)
            for error in errors:
                print(f"  - {error}")
            return False
        else:
            print("\n" + "=" * 60)
            print("✅ VALIDATION PASSED")
            print("=" * 60)
            print("All checks successful! Migration is complete.")
            return True
            
    finally:
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()


def main():
    if len(sys.argv) != 3:
        print("Usage: python validate_migration.py <sqlite_path> <postgres_dsn>")
        print()
        print("Example:")
        print('  python validate_migration.py ./data/candles.db "host=localhost port=5432 dbname=eodhd_candles user=eodhd_user password=secret"')
        sys.exit(1)
    
    sqlite_path = sys.argv[1]
    postgres_dsn = sys.argv[2]
    
    success = validate_migration(sqlite_path, postgres_dsn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
