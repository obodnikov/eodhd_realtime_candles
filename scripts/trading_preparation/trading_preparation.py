#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trading Preparation Script - Multi-Timeframe EMA Trend Analysis.

Analyzes price data using EMA crossovers from multiple timeframes:
- 30/50 EMA crossover: Yahoo Finance daily candles
- 10/30 EMA crossover: Yahoo Finance hourly candles
- 3/10 EMA crossover: N8N EODHD API 15-minute candles
- 1/3 EMA crossover: N8N EODHD API 1-minute candles

Features:
- Multi-timeframe EMA trend analysis
- State detection (DOWN, BASE, TREND_START, TREND, PULLBACK)
- Intraday score (0-10) based on EMA stability
- Cumulative volume from 4:00 AM NY
- Market session detection (PRE, RTH, EXT, CLOSED)
"""

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from prettytable import PrettyTable
from zoneinfo import ZoneInfo


# =============================================================================
# Custom Exceptions
# =============================================================================

class TradingPreparationError(Exception):
    """Base exception for trading preparation errors."""
    pass


class APIError(TradingPreparationError):
    """Raised when API request fails."""
    pass


class DataError(TradingPreparationError):
    """Raised when data is insufficient or invalid."""
    pass


class ValidationError(TradingPreparationError):
    """Raised when input validation fails."""
    pass

# Load environment variables
env_path = Path(__file__).parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Timezone constants
NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

# Market session constants (NY time)
PREMARKET_START = time(4, 0)
RTH_START = time(9, 30)
RTH_END = time(16, 0)


# =============================================================================
# API Clients
# =============================================================================

class EODHDTickersClient:
    """Client for fetching ticker information from EODHD API."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, ticker: str) -> Dict[str, Any]:
        """Fetch ticker information including last_price."""
        url = f"{self.base_url}/eodhd/tickers/{ticker}"
        headers = {"X-API-Key": self.api_key}
        logger.debug(f"Fetching ticker info: {url}")
        resp = requests.get(url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


class EODHDCandlesClient:
    """Client for fetching candle data from EODHD API."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def fetch_aggregated(
        self,
        ticker: str,
        interval_minutes: int,
        count: int = 500,
        from_unix_utc: Optional[int] = None,
        to_unix_utc: Optional[int] = None,
        include_current: bool = False,
    ) -> Dict[str, Any]:
        """Fetch aggregated candles at specified interval."""
        url = f"{self.base_url}/eodhd/candles/{ticker}/{interval_minutes}"
        headers = {"X-API-Key": self.api_key}
        params: Dict[str, Any] = {"count": int(count)}
        if from_unix_utc is not None:
            params["from_timestamp"] = int(from_unix_utc)
        if to_unix_utc is not None:
            params["to_timestamp"] = int(to_unix_utc)
        if include_current:
            params["include_current"] = "true"
        logger.debug(f"Fetching aggregated candles: {url} params={params}")
        resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_base_candles(
        self,
        ticker: str,
        count: int = 1000,
        include_current: bool = True,
    ) -> Dict[str, Any]:
        """Fetch 1-minute base candles."""
        url = f"{self.base_url}/eodhd/candles/{ticker}"
        headers = {"X-API-Key": self.api_key}
        params: Dict[str, Any] = {
            "count": int(count),
            "include_current": "true" if include_current else "false",
        }
        logger.debug(f"Fetching base candles: {url} params={params}")
        resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Time Helpers
# =============================================================================

def now_ny() -> datetime:
    """Get current time in New York timezone."""
    return datetime.now(tz=NY_TZ)


def market_session(dt_ny: datetime) -> str:
    """Determine market session for a given NY datetime."""
    t = dt_ny.timetz().replace(tzinfo=None)
    if PREMARKET_START <= t < RTH_START:
        return "PRE"
    if RTH_START <= t < RTH_END:
        return "RTH"
    if t >= RTH_END:
        return "EXT"
    return "CLOSED"


def latest_0400_start_ts_unix_utc(now_dt: Optional[datetime] = None) -> int:
    """Get Unix timestamp for session start (4:00 AM NY)."""
    now_dt = now_dt or now_ny()
    start = now_dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if now_dt < start:
        start = start - timedelta(days=1)
    return int(start.astimezone(UTC_TZ).timestamp())


# =============================================================================
# Trend Rules Configuration
# =============================================================================

@dataclass
class TrendRules:
    """Configuration for trend detection thresholds."""
    hold_10_30: int = 3   # Candles for 10/30 EMA stability (hourly)
    hold_3_10: int = 2    # Candles for 3/10 EMA stability (15m)
    hold_1_3: int = 1     # Candles for 1/3 EMA stability (1m)
    hold_30_50: int = 3   # Candles for 30/50 EMA stability (daily)
    w_10_30: int = 4      # Weight for 10/30 in score
    w_3_10: int = 3       # Weight for 3/10 in score
    w_1_3: int = 3        # Weight for 1/3 in score


# =============================================================================
# Yahoo Finance Data Fetchers
# =============================================================================

def fetch_yf_daily_ohlcv(ticker: str, count: int = 60) -> pd.DataFrame:
    """
    Fetch daily OHLCV data from Yahoo Finance for 30/50 EMA.
    
    Args:
        ticker: Stock ticker symbol
        count: Number of daily candles (need ~60 for EMA50)
    
    Returns:
        DataFrame with OHLCV data, empty DataFrame on failure.
    
    Raises:
        Logs warning on network/API errors but returns empty DataFrame.
    """
    period = "6mo" if count <= 60 else "1y"
    logger.debug(f"Fetching daily data: ticker={ticker}, period={period}, interval=1d")
    
    try:
        df = yf.download(
            tickers=ticker,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error fetching daily data for {ticker}: {e}")
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"Yahoo Finance error fetching daily data for {ticker}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning(
            f"No daily data returned from Yahoo Finance for {ticker}. "
            f"Requested period={period}. Ticker may be new/invalid or have no trading history."
        )
        return pd.DataFrame()

    logger.debug(f"Yahoo Finance returned {len(df)} daily rows for {ticker}")
    logger.debug(f"Columns before processing: {list(df.columns)}")

    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
        logger.debug(f"Columns after droplevel: {list(df.columns)}")

    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    logger.debug(f"Columns to keep: {keep}")
    df = df[keep].dropna(how="any")
    logger.debug(f"After dropna: {len(df)} rows")
    
    if len(df) < 50:
        logger.warning(
            f"Insufficient daily data for {ticker}: got {len(df)} rows, need at least 50 for EMA50. "
            f"Ticker may be a recent IPO or have limited trading history."
        )

    if len(df) == 0:
        logger.error(f"All rows dropped after dropna for {ticker}")
        return pd.DataFrame()

    idx = pd.to_datetime(df.index)
    logger.debug(f"Index timezone before conversion: {getattr(idx, 'tz', None)}")
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(NY_TZ)
    else:
        idx = idx.tz_convert(NY_TZ)
    df.index = idx
    logger.debug(f"Index timezone after conversion: {getattr(df.index, 'tz', None)}")
    
    logger.debug(f"Returning {len(df.tail(max(count, 60)))} daily candles for {ticker}")
    return df.tail(max(count, 60))


def fetch_yf_hourly_ohlcv(ticker: str, count: int = 100) -> pd.DataFrame:
    """
    Fetch hourly OHLCV data from Yahoo Finance for 10/30 EMA.
    
    Args:
        ticker: Stock ticker symbol
        count: Number of hourly candles (need ~40 for EMA30)
    
    Returns:
        DataFrame with OHLCV data, empty DataFrame on failure.
    
    Note: Uses 30d period to ensure sufficient data for EMA30 calculation.
    """
    period = "30d"
    logger.debug(f"Fetching hourly data: ticker={ticker}, period={period}, interval=1h")
    
    try:
        df = yf.download(
            tickers=ticker,
            period=period,
            interval="1h",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error fetching hourly data for {ticker}: {e}")
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"Yahoo Finance error fetching hourly data for {ticker}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning(
            f"No hourly data returned from Yahoo Finance for {ticker}. "
            f"Requested period={period}. Ticker may be new/invalid or have no trading history."
        )
        return pd.DataFrame()

    logger.debug(f"Yahoo Finance returned {len(df)} hourly rows for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)

    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].dropna(how="any")

    # Validate minimum data for EMA30
    if len(df) < 30:
        logger.warning(
            f"Insufficient hourly data for {ticker}: got {len(df)} candles, need at least 30 for EMA30. "
            f"Ticker may be a recent IPO or have limited trading history."
        )

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(UTC_TZ).tz_convert(NY_TZ)
    else:
        idx = idx.tz_convert(NY_TZ)
    df.index = idx
    
    logger.debug(f"Returning {len(df.tail(max(count, 40)))} hourly candles for {ticker}")
    return df.tail(max(count, 40))


def fetch_yf_volume_stats(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    """Fetch volume statistics from Yahoo Finance."""
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return None, None

    avg3m: Optional[float] = None
    avg20: Optional[float] = None

    try:
        fi = getattr(t, "fast_info", None)
        if fi:
            for k in ("threeMonthAverageVolume", "three_month_average_volume"):
                if k in fi and fi[k]:
                    avg3m = float(fi[k])
                    break
            for k in ("twentyDayAverageVolume", "twenty_day_average_volume"):
                if k in fi and fi[k]:
                    avg20 = float(fi[k])
                    break
    except Exception:
        pass

    if avg3m is None or avg20 is None:
        try:
            info = getattr(t, "info", None) or {}
            if avg3m is None and "averageVolume" in info:
                avg3m = float(info["averageVolume"])
            if avg20 is None and "averageVolume10days" in info:
                avg20 = float(info["averageVolume10days"])
        except Exception:
            pass

    return avg3m, avg20


# =============================================================================
# EMA Calculation Helpers
# =============================================================================

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def consecutive_holds(cond: pd.Series) -> pd.Series:
    """Count consecutive True values in a boolean series."""
    out: List[int] = []
    c = 0
    # Convert to boolean, treating NA as False (avoid deprecation warning)
    bool_series = cond.copy()
    bool_series = bool_series.where(bool_series.notna(), False)
    bool_values = bool_series.astype(bool).tolist()
    for v in bool_values:
        c = (c + 1) if v else 0
        out.append(c)
    return pd.Series(out, index=cond.index)


def trend_label(diff: pd.Series, eps: float = 1e-12) -> pd.Series:
    """Label trend direction based on EMA difference."""
    return diff.apply(lambda x: "UP" if x > eps else ("DOWN" if x < -eps else "FLAT"))


# =============================================================================
# Multi-Timeframe EMA Crossover Calculations
# =============================================================================

def compute_daily_30_50_crossover(df_daily: pd.DataFrame, hold_threshold: int = 3) -> Dict[str, Any]:
    """
    Calculate 30/50 EMA crossover from daily data.
    
    Returns dict with latest values:
    - ema30_daily, ema50_daily
    - trend_30_50, stable_30_50, hold_30_50
    """
    if df_daily is None or df_daily.empty or len(df_daily) < 50:
        logger.warning("Insufficient daily data for 30/50 EMA")
        return {
            "ema30_daily": None, "ema50_daily": None,
            "trend_30_50": "UNKNOWN", "stable_30_50": False, "hold_30_50": 0
        }

    df = df_daily.copy()
    df["ema30"] = compute_ema(df["Close"], 30)
    df["ema50"] = compute_ema(df["Close"], 50)
    df["crossover"] = df["ema30"] > df["ema50"]
    df["hold"] = consecutive_holds(df["crossover"])
    df["trend"] = trend_label(df["ema30"] - df["ema50"])

    last = df.iloc[-1]
    return {
        "ema30_daily": float(last["ema30"]),
        "ema50_daily": float(last["ema50"]),
        "trend_30_50": str(last["trend"]),
        "stable_30_50": bool(last["hold"] >= hold_threshold),
        "hold_30_50": int(last["hold"]),
    }


def compute_hourly_10_30_crossover(df_hourly: pd.DataFrame, hold_threshold: int = 3) -> Dict[str, Any]:
    """
    Calculate 10/30 EMA crossover from hourly data.
    
    Returns dict with latest values:
    - ema10_hourly, ema30_hourly
    - trend_10_30, stable_10_30, hold_10_30
    """
    if df_hourly is None or df_hourly.empty or len(df_hourly) < 30:
        logger.warning("Insufficient hourly data for 10/30 EMA")
        return {
            "ema10_hourly": None, "ema30_hourly": None,
            "trend_10_30": "UNKNOWN", "stable_10_30": False, "hold_10_30": 0
        }

    df = df_hourly.copy()
    df["ema10"] = compute_ema(df["Close"], 10)
    df["ema30"] = compute_ema(df["Close"], 30)
    df["crossover"] = df["ema10"] > df["ema30"]
    df["hold"] = consecutive_holds(df["crossover"])
    df["trend"] = trend_label(df["ema10"] - df["ema30"])

    last = df.iloc[-1]
    return {
        "ema10_hourly": float(last["ema10"]),
        "ema30_hourly": float(last["ema30"]),
        "trend_10_30": str(last["trend"]),
        "stable_10_30": bool(last["hold"] >= hold_threshold),
        "hold_10_30": int(last["hold"]),
    }


def compute_15m_3_10_crossover(
    candles_client: EODHDCandlesClient,
    ticker: str,
    hold_threshold: int = 2
) -> Dict[str, Any]:
    """
    Calculate 3/10 EMA crossover from 15-minute EODHD data.
    
    Uses aggregated endpoint: /eodhd/candles/{ticker}/15
    """
    try:
        payload = candles_client.fetch_aggregated(
            ticker=ticker,
            interval_minutes=15,
            count=100,  # Need ~15 candles for EMA10
            include_current=True,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error fetching 15m candles: {e}")
        return {
            "ema3_15m": None, "ema10_15m": None,
            "trend_3_10": "UNKNOWN", "stable_3_10": False, "hold_3_10": 0
        }

    candles = payload.get("candles", [])
    if not candles or len(candles) < 10:
        logger.warning("Insufficient 15m data for 3/10 EMA")
        return {
            "ema3_15m": None, "ema10_15m": None,
            "trend_3_10": "UNKNOWN", "stable_3_10": False, "hold_3_10": 0
        }

    # Convert to DataFrame
    rows = []
    skipped_count = 0
    for c in candles:
        ts = c.get("timestamp")
        if not is_valid_timestamp(ts):
            skipped_count += 1
            continue
        dt_utc = datetime.fromtimestamp(int(ts), tz=UTC_TZ)
        dt_ny = dt_utc.astimezone(NY_TZ)
        rows.append({
            "Date": dt_ny,
            "Close": c.get("close"),
        })
    
    if skipped_count > 0:
        logger.warning(f"Skipped {skipped_count} candles with invalid timestamps in 15m data")

    if not rows:
        return {
            "ema3_15m": None, "ema10_15m": None,
            "trend_3_10": "UNKNOWN", "stable_3_10": False, "hold_3_10": 0
        }

    df = pd.DataFrame(rows).set_index("Date").sort_index()
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna()

    if len(df) < 10:
        return {
            "ema3_15m": None, "ema10_15m": None,
            "trend_3_10": "UNKNOWN", "stable_3_10": False, "hold_3_10": 0
        }

    df["ema3"] = compute_ema(df["Close"], 3)
    df["ema10"] = compute_ema(df["Close"], 10)
    df["crossover"] = df["ema3"] > df["ema10"]
    df["hold"] = consecutive_holds(df["crossover"])
    df["trend"] = trend_label(df["ema3"] - df["ema10"])

    last = df.iloc[-1]
    return {
        "ema3_15m": float(last["ema3"]),
        "ema10_15m": float(last["ema10"]),
        "trend_3_10": str(last["trend"]),
        "stable_3_10": bool(last["hold"] >= hold_threshold),
        "hold_3_10": int(last["hold"]),
    }


def compute_1m_1_3_crossover_df(
    candles_client: EODHDCandlesClient,
    ticker: str,
    hold_threshold: int = 1,
    count: int = 500,
) -> pd.DataFrame:
    """
    Calculate 1/3 EMA crossover from 1-minute EODHD data.
    
    Uses base candles endpoint: /eodhd/candles/{ticker}
    Returns full DataFrame with EMA columns for display.
    """
    try:
        payload = candles_client.fetch_base_candles(
            ticker=ticker,
            count=count,
            include_current=True,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error fetching 1m candles: {e}")
        return pd.DataFrame()

    candles = payload.get("candles", [])
    if not candles:
        logger.warning("No 1m candle data received")
        return pd.DataFrame()

    # Convert to DataFrame
    rows = []
    skipped_count = 0
    for c in candles:
        ts = c.get("timestamp")
        if not is_valid_timestamp(ts):
            skipped_count += 1
            continue
        dt_utc = datetime.fromtimestamp(int(ts), tz=UTC_TZ)
        dt_ny = dt_utc.astimezone(NY_TZ)
        rows.append({
            "Date": dt_ny,
            "Open": c.get("open"),
            "High": c.get("high"),
            "Low": c.get("low"),
            "Close": c.get("close"),
            "Volume": c.get("volume"),
            "IsComplete": c.get("is_complete"),
        })

    if skipped_count > 0:
        logger.warning(f"Skipped {skipped_count} candles with invalid timestamps in 1m data")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("Date").sort_index()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Close"])

    if len(df) < 3:
        return df

    # Calculate 1/3 EMA crossover
    df["ema1"] = compute_ema(df["Close"], 1)
    df["ema3"] = compute_ema(df["Close"], 3)
    df["crossover_1_3"] = df["ema1"] > df["ema3"]
    df["hold_1_3"] = consecutive_holds(df["crossover_1_3"])
    df["trend_1_3"] = trend_label(df["ema1"] - df["ema3"])
    df["stable_1_3"] = df["hold_1_3"] >= hold_threshold

    return df


def compute_cumulative_volume(
    candles_client: EODHDCandlesClient,
    ticker: str,
    session_start_ts: int
) -> int:
    """
    Calculate cumulative volume from session start.
    
    Args:
        candles_client: EODHD API client
        ticker: Stock ticker symbol
        session_start_ts: Unix timestamp for session start (4:00 AM NY)
    
    Returns:
        Cumulative volume as integer, 0 on failure.
    
    Note:
        Only includes complete candles (is_complete=True) to ensure accuracy.
    """
    try:
        payload = candles_client.fetch_base_candles(
            ticker=ticker,
            count=1000,
            include_current=True,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error fetching candles for volume: {e}")
        return 0

    candles = payload.get("candles", []) or []
    
    # Sort candles by timestamp and deduplicate
    valid_candles = [c for c in candles if is_valid_timestamp(c.get("timestamp"))]
    sorted_candles = sorted(valid_candles, key=lambda c: int(c["timestamp"]))
    
    # Deduplicate by timestamp (keep first occurrence)
    seen_timestamps = set()
    unique_candles = []
    for c in sorted_candles:
        ts = int(c["timestamp"])
        if ts not in seen_timestamps:
            seen_timestamps.add(ts)
            unique_candles.append(c)
    
    cum = 0
    for c in unique_candles:
        ts = int(c["timestamp"])
        if ts < session_start_ts:
            continue
        # Only include complete candles for accurate volume
        if not c.get("is_complete", False):
            continue
        vol = c.get("volume", 0) or 0
        cum += int(vol)
    return cum


# =============================================================================
# State Detection and Scoring
# =============================================================================

def detect_state(signals: Dict[str, Any]) -> str:
    """
    Detect market state from multi-timeframe EMA signals.
    
    States:
    - DOWN: Bearish alignment (10/30 DOWN)
    - BASE: Building base (10/30 DOWN but 3/10 UP)
    - TREND_START: Early trend (10/30 UP, not stable)
    - TREND: Confirmed uptrend (10/30 UP stable, 3/10 UP, 1/3 UP)
    - PULLBACK: Retracement in uptrend
    """
    trend_10_30 = signals.get("trend_10_30", "UNKNOWN")
    stable_10_30 = signals.get("stable_10_30", False)
    trend_3_10 = signals.get("trend_3_10", "UNKNOWN")
    trend_1_3 = signals.get("trend_1_3", "UNKNOWN")

    if trend_10_30 == "DOWN" and trend_3_10 == "DOWN":
        return "DOWN"
    if trend_10_30 == "DOWN" and trend_3_10 == "UP":
        return "BASE"
    if trend_10_30 == "UP" and not stable_10_30:
        return "TREND_START"
    if trend_10_30 == "UP" and stable_10_30 and trend_3_10 == "UP" and trend_1_3 == "UP":
        return "TREND"
    if trend_10_30 == "UP" and stable_10_30 and (trend_3_10 == "DOWN" or trend_1_3 == "DOWN"):
        return "PULLBACK"
    return "UNKNOWN"


def calculate_intraday_score(signals: Dict[str, Any], rules: TrendRules) -> int:
    """
    Calculate intraday score (0-10) from multi-timeframe signals.
    
    Score based on stable crossovers:
    - stable_10_30: +4 points
    - stable_3_10: +3 points
    - stable_1_3: +3 points
    """
    score = 0
    if signals.get("stable_10_30", False):
        score += rules.w_10_30
    if signals.get("stable_3_10", False):
        score += rules.w_3_10
    if signals.get("stable_1_3", False):
        score += rules.w_1_3
    return max(0, min(10, score))


def build_trend_summary(signals: Dict[str, Any]) -> str:
    """Build trend summary string from signals (ASCII-safe)."""
    def stable_mark(is_stable: bool) -> str:
        return "[OK]" if is_stable else ""
    
    t0 = f"30/50:{signals.get('trend_30_50', '?')}{stable_mark(signals.get('stable_30_50'))}"
    t1 = f"10/30:{signals.get('trend_10_30', '?')}{stable_mark(signals.get('stable_10_30'))}"
    t2 = f"3/10:{signals.get('trend_3_10', '?')}{stable_mark(signals.get('stable_3_10'))}"
    t3 = f"1/3:{signals.get('trend_1_3', '?')}{stable_mark(signals.get('stable_1_3'))}"
    return f"{t0} | {t1} | {t2} | {t3}"


# =============================================================================
# Last Price Extraction
# =============================================================================

def extract_last_price(payload: Any) -> Optional[float]:
    """Extract last_price from API response (handles nested structures)."""
    if payload is None:
        return None
    if isinstance(payload, list):
        for item in payload:
            v = extract_last_price(item)
            if v is not None:
                return v
        return None
    if isinstance(payload, dict):
        if "last_price" in payload and payload["last_price"] is not None:
            try:
                return float(payload["last_price"])
            except Exception:
                return None
        for k in ("data", "result", "item", "ticker", "payload", "items"):
            if k in payload:
                v = extract_last_price(payload[k])
                if v is not None:
                    return v
    return None


def validate_ticker(ticker: str) -> str:
    """
    Validate and normalize ticker symbol.
    
    Args:
        ticker: Stock ticker symbol to validate
    
    Returns:
        Uppercase ticker symbol
    
    Raises:
        ValidationError: If ticker is invalid
    """
    if not ticker:
        raise ValidationError("Ticker symbol cannot be empty")
    
    # Remove whitespace and convert to uppercase
    ticker = ticker.strip().upper()
    
    # Check length (1-10 characters typical for stock symbols)
    if len(ticker) < 1 or len(ticker) > 10:
        raise ValidationError(f"Invalid ticker length: {len(ticker)} (expected 1-10)")
    
    # Check alphanumeric (allow dots for class shares like BRK.A)
    if not re.match(r'^[A-Z0-9.]+$', ticker):
        raise ValidationError(f"Invalid ticker format: {ticker} (only A-Z, 0-9, . allowed)")
    
    return ticker


def is_valid_timestamp(ts: Any, min_ts: int = 0, max_ts: Optional[int] = None) -> bool:
    """
    Validate timestamp is within reasonable range.
    
    Args:
        ts: Timestamp value to validate
        min_ts: Minimum valid timestamp (default: 0 = 1970-01-01)
        max_ts: Maximum valid timestamp (default: now + 1 day)
    
    Returns:
        True if timestamp is valid, False otherwise
    """
    if ts is None:
        return False
    try:
        ts_int = int(ts)
        if max_ts is None:
            max_ts = int(datetime.now(tz=UTC_TZ).timestamp()) + 86400  # now + 1 day
        return min_ts <= ts_int <= max_ts
    except (ValueError, TypeError):
        return False


# =============================================================================
# Argument Parser
# =============================================================================

def build_argparser() -> argparse.ArgumentParser:
    """Build command-line argument parser."""
    p = argparse.ArgumentParser(
        description="Multi-timeframe EMA trend analysis for trading preparation."
    )
    p.add_argument("--ticker", required=True, help="Stock ticker symbol")
    p.add_argument("--base-url", default="https://n8n.sqowe.com",
                   help="API base URL")
    p.add_argument("--api-key", default=None,
                   help="API key (or set N8N_EODHD_API_KEY env var)")
    p.add_argument("--hold-30-50", type=int, default=3,
                   help="Hold threshold for 30/50 EMA (daily)")
    p.add_argument("--hold-10-30", type=int, default=3,
                   help="Hold threshold for 10/30 EMA (hourly)")
    p.add_argument("--hold-3-10", type=int, default=2,
                   help="Hold threshold for 3/10 EMA (15m)")
    p.add_argument("--hold-1-3", type=int, default=1,
                   help="Hold threshold for 1/3 EMA (1m)")
    p.add_argument("--tail", type=int, default=25,
                   help="Number of 1m rows to display")
    p.add_argument("--out", default=None,
                   help="Output file path (.json or .csv)")
    p.add_argument("--debug", action="store_true",
                   help="Enable debug logging")
    return p


# =============================================================================
# Main Function
# =============================================================================

def main() -> None:
    """Main entry point for multi-timeframe trading preparation analysis."""
    args = build_argparser().parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Get API key
    api_key = args.api_key or os.getenv("N8N_EODHD_API_KEY")
    if not api_key:
        raise SystemExit(
            "Missing API key. Provide --api-key or set env N8N_EODHD_API_KEY"
        )

    # Build trend rules
    rules = TrendRules(
        hold_30_50=args.hold_30_50,
        hold_10_30=args.hold_10_30,
        hold_3_10=args.hold_3_10,
        hold_1_3=args.hold_1_3,
    )

    # Validate ticker
    try:
        ticker = validate_ticker(args.ticker)
    except ValidationError as e:
        logger.error(f"Invalid ticker: {e}")
        raise SystemExit(f"Invalid ticker: {e}")
    
    logger.info(f"Analyzing {ticker} with multi-timeframe EMAs")

    # Initialize API client
    candles_client = EODHDCandlesClient(
        base_url=args.base_url,
        api_key=api_key
    )

    # Fetch last price
    tickers_client = EODHDTickersClient(
        base_url=args.base_url,
        api_key=api_key
    )
    try:
        ticker_data = tickers_client.fetch(ticker)
        last_price = extract_last_price(ticker_data)
    except Exception as e:
        logger.warning(f"Failed to fetch ticker info: {e}")
        last_price = None

    # ==========================================================================
    # 1. Daily 30/50 EMA (Yahoo Finance)
    # ==========================================================================
    logger.info("Fetching daily data for 30/50 EMA...")
    df_daily = fetch_yf_daily_ohlcv(ticker, count=60)
    if df_daily.empty:
        logger.error(f"Failed to fetch daily data for {ticker} - cannot calculate 30/50 EMA")
        raise SystemExit(f"Critical data missing: daily OHLCV for {ticker}")
    signals_30_50 = compute_daily_30_50_crossover(df_daily, rules.hold_30_50)
    logger.info(f"30/50 EMA: {signals_30_50['trend_30_50']} (stable={signals_30_50['stable_30_50']})")

    # ==========================================================================
    # 2. Hourly 10/30 EMA (Yahoo Finance)
    # ==========================================================================
    logger.info("Fetching hourly data for 10/30 EMA...")
    df_hourly = fetch_yf_hourly_ohlcv(ticker, count=100)
    if df_hourly.empty:
        logger.error(f"Failed to fetch hourly data for {ticker} - cannot calculate 10/30 EMA")
        raise SystemExit(f"Critical data missing: hourly OHLCV for {ticker}")
    signals_10_30 = compute_hourly_10_30_crossover(df_hourly, rules.hold_10_30)
    logger.info(f"10/30 EMA: {signals_10_30['trend_10_30']} (stable={signals_10_30['stable_10_30']})")

    # ==========================================================================
    # 3. 15-minute 3/10 EMA (EODHD API aggregated)
    # ==========================================================================
    logger.info("Fetching 15m data for 3/10 EMA...")
    signals_3_10 = compute_15m_3_10_crossover(candles_client, ticker, rules.hold_3_10)
    logger.info(f"3/10 EMA: {signals_3_10['trend_3_10']} (stable={signals_3_10['stable_3_10']})")

    # ==========================================================================
    # 4. 1-minute 1/3 EMA (EODHD API base candles)
    # ==========================================================================
    logger.info("Fetching 1m data for 1/3 EMA...")
    df_1m = compute_1m_1_3_crossover_df(candles_client, ticker, rules.hold_1_3, count=500)

    if df_1m.empty:
        logger.error(f"No 1-minute data available for {ticker}")
        raise SystemExit(f"Critical data missing: 1-minute candles for {ticker}")

    # ==========================================================================
    # 5. Cumulative Volume
    # ==========================================================================
    ny_now = now_ny()
    session_start_ts = latest_0400_start_ts_unix_utc(ny_now)
    cumulative_volume = compute_cumulative_volume(candles_client, ticker, session_start_ts)
    logger.info(f"Cumulative volume from 4:00 AM: {cumulative_volume:,}")

    # ==========================================================================
    # 6. Volume Stats (Yahoo Finance)
    # ==========================================================================
    avg3m, avg20 = fetch_yf_volume_stats(ticker)

    # ==========================================================================
    # 7. Merge signals and build output
    # ==========================================================================
    # Add higher timeframe signals to 1m DataFrame (constant for all rows)
    df_1m["trend_30_50"] = signals_30_50["trend_30_50"]
    df_1m["stable_30_50"] = signals_30_50["stable_30_50"]
    df_1m["hold_30_50"] = signals_30_50["hold_30_50"]

    df_1m["trend_10_30"] = signals_10_30["trend_10_30"]
    df_1m["stable_10_30"] = signals_10_30["stable_10_30"]
    df_1m["hold_10_30"] = signals_10_30["hold_10_30"]

    df_1m["trend_3_10"] = signals_3_10["trend_3_10"]
    df_1m["stable_3_10"] = signals_3_10["stable_3_10"]
    df_1m["hold_3_10"] = signals_3_10["hold_3_10"]

    # Add market session
    df_1m["Session"] = [market_session(ts.to_pydatetime()) for ts in df_1m.index.tz_convert(NY_TZ)]

    # Add cumulative volume
    df_1m["VolumeDay"] = cumulative_volume

    # Add last price
    df_1m["LastPrice"] = last_price

    # Calculate intraday score for each row
    def calc_score(row: pd.Series) -> str:
        signals = {
            "stable_10_30": row.get("stable_10_30", False),
            "stable_3_10": row.get("stable_3_10", False),
            "stable_1_3": row.get("stable_1_3", False),
        }
        return f"{calculate_intraday_score(signals, rules)}/10"

    df_1m["Score"] = df_1m.apply(calc_score, axis=1)

    # Build trend summary for each row
    def build_summary(row: pd.Series) -> str:
        signals = {
            "trend_30_50": row.get("trend_30_50"),
            "stable_30_50": row.get("stable_30_50"),
            "trend_10_30": row.get("trend_10_30"),
            "stable_10_30": row.get("stable_10_30"),
            "trend_3_10": row.get("trend_3_10"),
            "stable_3_10": row.get("stable_3_10"),
            "trend_1_3": row.get("trend_1_3"),
            "stable_1_3": row.get("stable_1_3"),
        }
        return build_trend_summary(signals)

    df_1m["TrendSummary"] = df_1m.apply(build_summary, axis=1)

    # Detect state for each row
    def calc_state(row: pd.Series) -> str:
        signals = {
            "trend_10_30": row.get("trend_10_30"),
            "stable_10_30": row.get("stable_10_30"),
            "trend_3_10": row.get("trend_3_10"),
            "trend_1_3": row.get("trend_1_3"),
        }
        return detect_state(signals)

    df_1m["State"] = df_1m.apply(calc_state, axis=1)

    # Format NY time
    df_1m["NY_Time"] = df_1m.index.tz_convert(NY_TZ).strftime("%Y-%m-%d %H:%M")

    # ==========================================================================
    # 8. Output
    # ==========================================================================
    cols = [
        "NY_Time", "Open", "High", "Low", "Close", "Volume",
        "VolumeDay", "Session", "State", "Score",
        "stable_30_50", "stable_10_30", "stable_3_10", "stable_1_3",
        "TrendSummary",
    ]
    cols = [c for c in cols if c in df_1m.columns]

    # Summary header table
    summary_table = PrettyTable()
    summary_table.field_names = ["Timeframe", "Trend", "Hold", "Stable"]
    summary_table.align = "l"
    summary_table.add_row(["30/50 (Daily)", signals_30_50['trend_30_50'], signals_30_50['hold_30_50'], signals_30_50['stable_30_50']])
    summary_table.add_row(["10/30 (Hourly)", signals_10_30['trend_10_30'], signals_10_30['hold_10_30'], signals_10_30['stable_10_30']])
    summary_table.add_row(["3/10 (15m)", signals_3_10['trend_3_10'], signals_3_10['hold_3_10'], signals_3_10['stable_3_10']])

    avg3m_str = f"{avg3m:,.0f}" if avg3m else "N/A"
    
    print(f"\n{'='*60}")
    print(f"  MULTI-TIMEFRAME EMA ANALYSIS: {ticker}")
    print(f"{'='*60}")
    print(summary_table)
    print(f"\nCumulative Vol: {cumulative_volume:,} | Avg 3M: {avg3m_str}")
    print(f"{'='*60}\n")

    # Data table
    data_table = PrettyTable()
    data_table.field_names = cols
    data_table.align = "r"
    data_table.align["NY_Time"] = "l"
    data_table.align["Session"] = "c"
    data_table.align["State"] = "l"
    
    tail_df = df_1m[cols].tail(max(1, args.tail))
    for _, row in tail_df.iterrows():
        formatted_row = []
        for col in cols:
            val = row[col]
            if col in ["Open", "High", "Low", "Close"]:
                formatted_row.append(f"{val:.2f}" if pd.notna(val) else "N/A")
            elif col in ["Volume", "VolumeDay"]:
                formatted_row.append(f"{int(val):,}" if pd.notna(val) else "N/A")
            elif isinstance(val, bool):
                formatted_row.append("Yes" if val else "No")
            else:
                formatted_row.append(str(val) if pd.notna(val) else "N/A")
        data_table.add_row(formatted_row)
    
    print(data_table)

    # Save output if requested
    if args.out:
        out_path = args.out.lower()
        last = df_1m.iloc[-1]

        if out_path.endswith(".json"):
            obj = {
                "ticker": ticker,
                "timestamp": now_ny().isoformat(),
                "signals": {
                    "daily_30_50": signals_30_50,
                    "hourly_10_30": signals_10_30,
                    "m15_3_10": signals_3_10,
                    "m1_1_3": {
                        "trend_1_3": str(last.get("trend_1_3")),
                        "stable_1_3": bool(last.get("stable_1_3")),
                        "hold_1_3": int(last.get("hold_1_3", 0)),
                    },
                },
                "state": str(last.get("State")),
                "score": str(last.get("Score")),
                "last_price": last_price,
                "cumulative_volume": cumulative_volume,
                "avg_3m_volume": avg3m,
                "avg_20d_volume": avg20,
            }
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            logger.info(f"JSON saved to {args.out}")
        else:
            df_1m.to_csv(args.out, index=True)
            logger.info(f"CSV saved to {args.out}")


if __name__ == "__main__":
    main()
