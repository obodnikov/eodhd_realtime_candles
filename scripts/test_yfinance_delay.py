#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to measure actual delay in yfinance data.

This script fetches the last ~20 1-minute candles and compares
the latest candle timestamp with current time to measure delay.

Usage:
    python scripts/test_yfinance_delay.py AAPL
    python scripts/test_yfinance_delay.py TSLA --count 30
"""

import argparse
import sys
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo


def test_data_delay(ticker: str, count: int = 20):
    """
    Test the actual delay in yfinance data by comparing latest candle time with current time.
    
    Args:
        ticker: Stock symbol (e.g., 'AAPL')
        count: Number of recent candles to display
    """
    print(f"\n{'='*80}")
    print(f"Testing yfinance Data Delay for {ticker}")
    print(f"{'='*80}\n")
    
    # Get current time
    ny = ZoneInfo("America/New_York")
    now = datetime.now(ny)
    
    print(f"Current Time (ET): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Current Time (UTC): {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    
    # Fetch 1-minute data
    print(f"Fetching 1-minute data with prepost=True...")
    try:
        df = yf.download(
            tickers=ticker,
            period="1d",
            interval="1m",
            auto_adjust=False,
            prepost=True,
            progress=False,
        )
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return None
    
    if df is None or df.empty:
        print(f"❌ No data returned for {ticker}")
        return None
    
    # Handle MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    print(f"✓ Fetched {len(df)} total 1-minute bars\n")
    
    # Convert to NY timezone
    df.index = df.index.tz_convert(ny) if df.index.tz else df.index.tz_localize(ny)
    
    # Get the last N candles
    df_recent = df.tail(count)
    
    # Find the latest candle
    latest_candle_time = df_recent.index[-1]
    latest_candle_close = df_recent["Close"].iloc[-1]
    
    # Calculate delay
    delay = now - latest_candle_time
    delay_seconds = delay.total_seconds()
    delay_minutes = delay_seconds / 60
    
    print(f"{'='*80}")
    print(f"DELAY ANALYSIS")
    print(f"{'='*80}")
    print(f"Latest Candle Time:  {latest_candle_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Latest Candle Close: ${latest_candle_close:.2f}")
    print(f"Current Time:        {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"\n⏱️  DATA DELAY: {int(delay_minutes)} minutes {int(delay_seconds % 60)} seconds")
    print(f"   ({delay_seconds:.1f} seconds total)")
    print(f"{'='*80}\n")
    
    # Determine market status
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    premarket_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    premarket_end = now.replace(hour=9, minute=29, second=59, microsecond=0)
    
    if premarket_start <= now <= premarket_end:
        market_status = "PREMARKET"
    elif market_open <= now <= market_close:
        market_status = "REGULAR HOURS"
    else:
        market_status = "AFTER HOURS / CLOSED"
    
    print(f"Market Status: {market_status}\n")
    
    # Display recent candles
    print(f"Last {len(df_recent)} Candles (most recent at bottom):")
    print(f"{'-'*80}")
    
    # Format the dataframe for display
    display_df = df_recent[["Open", "High", "Low", "Close", "Volume"]].copy()
    display_df.index = display_df.index.strftime('%H:%M:%S')
    
    # Add time ago column
    time_ago = []
    for idx in df_recent.index:
        diff = now - idx
        mins = int(diff.total_seconds() / 60)
        secs = int(diff.total_seconds() % 60)
        time_ago.append(f"{mins}m {secs}s ago")
    
    display_df["Time Ago"] = time_ago
    
    # Reorder columns
    display_df = display_df[["Time Ago", "Open", "High", "Low", "Close", "Volume"]]
    
    print(display_df.to_string())
    print(f"{'-'*80}\n")
    
    # Interpretation
    print("INTERPRETATION:")
    if delay_minutes < 1:
        print("✅ Data is nearly real-time (< 1 minute delay)")
    elif delay_minutes < 5:
        print("✅ Data has minimal delay (< 5 minutes)")
    elif delay_minutes < 15:
        print("⚠️  Data has moderate delay (< 15 minutes)")
    elif delay_minutes < 20:
        print("⚠️  Data has ~15 minute delay (typical for free feeds)")
    else:
        print("❌ Data has significant delay (> 20 minutes)")
        print("   This could indicate:")
        print("   - Market is closed")
        print("   - Data feed issue")
        print("   - Exchange-specific delay")
    
    return {
        "ticker": ticker,
        "current_time": now,
        "latest_candle_time": latest_candle_time,
        "delay_seconds": delay_seconds,
        "delay_minutes": delay_minutes,
        "market_status": market_status,
        "latest_price": latest_candle_close
    }


def main():
    parser = argparse.ArgumentParser(
        description="Test yfinance data delay by comparing latest candle time with current time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_yfinance_delay.py AAPL
  python scripts/test_yfinance_delay.py TSLA --count 30
  python scripts/test_yfinance_delay.py SPY -c 15

Notes:
  - Measures actual delay between latest candle and current time
  - Shows last N candles with "time ago" information
  - Works during premarket, regular hours, and after hours
        """
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., AAPL, TSLA, SPY)")
    parser.add_argument("-c", "--count", type=int, default=20, help="Number of recent candles to display (default: 20)")
    
    args = parser.parse_args()
    
    ticker = args.ticker.upper()
    result = test_data_delay(ticker, args.count)
    
    if result:
        print(f"\n✅ Test completed successfully")
        return 0
    else:
        print(f"\n❌ Test failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
