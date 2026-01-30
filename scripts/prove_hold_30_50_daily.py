#!/usr/bin/env python3
"""
Prove Hold calculation for 30/50 Daily EMA.

Downloads AAPL daily data and shows step-by-step:
1. EMA30 and EMA50 calculation
2. Crossover condition (EMA30 > EMA50)
3. Consecutive holds counting
4. Final Hold value and Trend direction
"""

import pandas as pd
import yfinance as yf

TICKER = "AAPL"


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def consecutive_holds(cond: pd.Series) -> pd.Series:
    """Count consecutive True values in a boolean series."""
    out = []
    c = 0
    for v in cond:
        c = (c + 1) if v else 0
        out.append(c)
    return pd.Series(out, index=cond.index)


def trend_label(diff: float) -> str:
    """Label trend direction based on EMA difference."""
    if diff > 0:
        return "UP"
    elif diff < 0:
        return "DOWN"
    return "FLAT"


def main():
    print(f"\n{'='*70}")
    print(f"PROVING 30/50 DAILY EMA HOLD CALCULATION FOR {TICKER}")
    print(f"{'='*70}\n")

    # Step 1: Download daily data
    print("STEP 1: Downloading 6 months of daily data from Yahoo Finance...")
    df = yf.download(TICKER, period="6mo", interval="1d", progress=False)
    
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
    
    print(f"  Downloaded {len(df)} daily candles\n")

    # Step 2: Calculate EMAs
    print("STEP 2: Calculating EMA30 and EMA50...")
    df["EMA30"] = compute_ema(df["Close"], 30)
    df["EMA50"] = compute_ema(df["Close"], 50)
    print("  EMA30 = ewm(Close, span=30, adjust=False).mean()")
    print("  EMA50 = ewm(Close, span=50, adjust=False).mean()\n")

    # Step 3: Calculate crossover condition
    print("STEP 3: Calculating crossover condition (EMA30 > EMA50)...")
    df["Crossover"] = df["EMA30"] > df["EMA50"]
    print("  Crossover = True when EMA30 > EMA50 (bullish)")
    print("  Crossover = False when EMA30 <= EMA50 (bearish)\n")

    # Step 4: Calculate consecutive holds
    print("STEP 4: Calculating consecutive holds...")
    df["Hold"] = consecutive_holds(df["Crossover"])
    print("  Hold counts consecutive True values")
    print("  Resets to 0 when Crossover becomes False\n")

    # Step 5: Calculate trend
    df["Diff"] = df["EMA30"] - df["EMA50"]
    df["Trend"] = df["Diff"].apply(trend_label)

    # Display last 20 rows
    print("STEP 5: Showing last 20 daily candles with calculations...")
    print("-" * 100)
    
    display_df = df[["Close", "EMA30", "EMA50", "Crossover", "Hold", "Trend"]].tail(20).copy()
    display_df["Close"] = display_df["Close"].round(2)
    display_df["EMA30"] = display_df["EMA30"].round(2)
    display_df["EMA50"] = display_df["EMA50"].round(2)
    
    print(display_df.to_string())
    print("-" * 100)

    # Final result
    last = df.iloc[-1]
    print(f"\n{'='*70}")
    print("FINAL RESULT (Last Row):")
    print(f"{'='*70}")
    print(f"  Date:      {df.index[-1].strftime('%Y-%m-%d')}")
    print(f"  Close:     ${last['Close']:.2f}")
    print(f"  EMA30:     ${last['EMA30']:.2f}")
    print(f"  EMA50:     ${last['EMA50']:.2f}")
    print(f"  Diff:      ${last['Diff']:.2f} (EMA30 - EMA50)")
    print(f"  Crossover: {last['Crossover']} (EMA30 > EMA50)")
    print(f"  Hold:      {int(last['Hold'])} consecutive bullish candles")
    print(f"  Trend:     {last['Trend']}")
    print(f"  Stable:    {last['Hold'] >= 3} (Hold >= 3)")
    print(f"{'='*70}\n")

    # Explanation
    if last["Trend"] == "DOWN":
        print("EXPLANATION:")
        print(f"  EMA30 (${last['EMA30']:.2f}) < EMA50 (${last['EMA50']:.2f})")
        print("  → Crossover = False (bearish)")
        print("  → Hold resets to 0")
        print("  → Trend = DOWN")
        print("  → Stable = False (Hold < 3)")


if __name__ == "__main__":
    main()
