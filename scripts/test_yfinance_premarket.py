#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to verify yfinance premarket data fetching.

This script demonstrates and validates that yfinance can fetch premarket data
using the prepost=True parameter and proper time filtering.

Usage:
    python scripts/test_yfinance_premarket.py AAPL
    python scripts/test_yfinance_premarket.py TSLA --verbose
"""

import argparse
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


def fetch_and_analyze_premarket(ticker: str, verbose: bool = False):
    """
    Fetch and analyze premarket data for a ticker.
    
    Args:
        ticker: Stock symbol (e.g., 'AAPL')
        verbose: Show detailed data rows
    
    Returns:
        dict: Premarket statistics or None if no data
    """
    print(f"\n{'='*70}")
    print(f"Testing Premarket Data Fetch for {ticker}")
    print(f"{'='*70}\n")
    
    # Step 1: Fetch data with prepost=True
    print("Step 1: Fetching 1-minute data with prepost=True...")
    try:
        df = yf.download(
            tickers=ticker,
            period="1d",
            interval="1m",
            auto_adjust=False,
            prepost=True,  # KEY: Include pre/post market data
            progress=False,
        )
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return None
    
    if df is None or df.empty:
        print(f"❌ No data returned for {ticker}")
        return None
    
    print(f"✓ Fetched {len(df)} total 1-minute bars")
    
    # Step 2: Convert to NY timezone
    print("\nStep 2: Converting to America/New_York timezone...")
    ny = ZoneInfo("America/New_York")
    df.index = df.index.tz_convert(ny) if df.index.tz else df.index.tz_localize(ny)
    print(f"✓ Timezone: {df.index.tz}")
    
    # Step 3: Filter to today only
    print("\nStep 3: Filtering to today's date...")
    today = datetime.now(ny).date()
    df_today = df[df.index.date == today]
    print(f"✓ Today's date: {today}")
    print(f"✓ Bars for today: {len(df_today)}")
    
    if df_today.empty:
        print(f"❌ No data for today ({today})")
        return None
    
    # Show time range
    print(f"✓ Time range: {df_today.index[0].strftime('%H:%M:%S')} to {df_today.index[-1].strftime('%H:%M:%S')} ET")
    
    # Step 4: Filter to premarket hours (4:00 - 9:29 AM)
    print("\nStep 4: Filtering to premarket hours (4:00 - 9:29 AM ET)...")
    df_premarket = df_today.between_time("04:00", "09:29")
    
    if df_premarket.empty:
        print(f"❌ No premarket data available")
        print(f"   This is normal if:")
        print(f"   - Market hasn't opened yet today")
        print(f"   - Running after market hours without premarket activity")
        print(f"   - Ticker had no premarket trading")
        return None
    
    print(f"✓ Premarket bars: {len(df_premarket)}")
    print(f"✓ Premarket time range: {df_premarket.index[0].strftime('%H:%M:%S')} to {df_premarket.index[-1].strftime('%H:%M:%S')} ET")
    
    # Step 5: Calculate premarket statistics
    print("\nStep 5: Calculating premarket OHLCV statistics...")
    
    # Handle MultiIndex columns from yfinance
    if isinstance(df_premarket.columns, pd.MultiIndex):
        # Flatten MultiIndex columns
        df_premarket.columns = df_premarket.columns.get_level_values(0)
    
    pm_high = df_premarket["High"].max()
    pm_low = df_premarket["Low"].min()
    pm_open = df_premarket["Open"].iloc[0]
    pm_close = df_premarket["Close"].iloc[-1]
    pm_volume = df_premarket["Volume"].sum()
    
    stats = {
        "ticker": ticker,
        "date": today,
        "bars_count": len(df_premarket),
        "open": pm_open,
        "high": pm_high,
        "low": pm_low,
        "close": pm_close,
        "volume": pm_volume,
        "first_bar_time": df_premarket.index[0].strftime('%H:%M:%S ET'),
        "last_bar_time": df_premarket.index[-1].strftime('%H:%M:%S ET'),
    }
    
    # Display results
    print(f"\n{'='*70}")
    print(f"PREMARKET RESULTS for {ticker} on {today}")
    print(f"{'='*70}")
    print(f"  Time Range:  {stats['first_bar_time']} - {stats['last_bar_time']}")
    print(f"  Bars:        {stats['bars_count']}")
    print(f"  Open:        ${stats['open']:.2f}")
    print(f"  High:        ${stats['high']:.2f}")
    print(f"  Low:         ${stats['low']:.2f}")
    print(f"  Close:       ${stats['close']:.2f}")
    print(f"  Volume:      {stats['volume']:,.0f}")
    print(f"  Range:       ${stats['high'] - stats['low']:.2f} ({((stats['high'] - stats['low']) / stats['low'] * 100):.2f}%)")
    print(f"{'='*70}\n")
    
    # Verbose output
    if verbose:
        print("\nDetailed Premarket Data (first 10 and last 10 bars):")
        print("-" * 70)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', None)
        
        if len(df_premarket) <= 20:
            print(df_premarket[["Open", "High", "Low", "Close", "Volume"]])
        else:
            print("\nFirst 10 bars:")
            print(df_premarket[["Open", "High", "Low", "Close", "Volume"]].head(10))
            print("\n... ({} bars omitted) ...\n".format(len(df_premarket) - 20))
            print("Last 10 bars:")
            print(df_premarket[["Open", "High", "Low", "Close", "Volume"]].tail(10))
        print("-" * 70)
    
    # Compare with regular trading hours
    print("\nComparison with Regular Trading Hours (9:30 AM - 4:00 PM ET):")
    df_regular = df_today.between_time("09:30", "16:00")
    
    if not df_regular.empty:
        # Handle MultiIndex columns
        if isinstance(df_regular.columns, pd.MultiIndex):
            df_regular.columns = df_regular.columns.get_level_values(0)
        
        reg_high = df_regular["High"].max()
        reg_low = df_regular["Low"].min()
        reg_volume = df_regular["Volume"].sum()
        
        print(f"  Regular Hours Bars:   {len(df_regular)}")
        print(f"  Regular Hours High:   ${reg_high:.2f}")
        print(f"  Regular Hours Low:    ${reg_low:.2f}")
        print(f"  Regular Hours Volume: {reg_volume:,.0f}")
        print(f"\n  Premarket vs Regular Volume: {(pm_volume / reg_volume * 100):.1f}% of regular hours")
    else:
        print("  ⚠ No regular trading hours data available yet")
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Test yfinance premarket data fetching",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_yfinance_premarket.py AAPL
  python scripts/test_yfinance_premarket.py TSLA --verbose
  python scripts/test_yfinance_premarket.py SPY -v

Notes:
  - Premarket hours are 4:00 AM - 9:29 AM Eastern Time
  - Data availability depends on market hours and ticker activity
  - Use --verbose to see detailed bar-by-bar data
        """
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., AAPL, TSLA, SPY)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed data rows")
    
    args = parser.parse_args()
    
    ticker = args.ticker.upper()
    result = fetch_and_analyze_premarket(ticker, args.verbose)
    
    if result:
        print("\n✅ SUCCESS: Premarket data fetching works correctly!")
        print(f"   The prepost=True parameter and time filtering are functioning as expected.")
        return 0
    else:
        print("\n⚠ WARNING: No premarket data available")
        print(f"   This doesn't mean the code is broken - it could be:")
        print(f"   - Market hasn't opened yet")
        print(f"   - No premarket activity for {ticker}")
        print(f"   - Running outside market hours")
        return 1


if __name__ == "__main__":
    sys.exit(main())
