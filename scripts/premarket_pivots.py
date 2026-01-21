#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

try:
    from tabulate import tabulate
    HAVE_TABULATE = True
except Exception:
    HAVE_TABULATE = False


# ==========================
# Logging setup
# ==========================
logger = logging.getLogger(__name__)


# ==========================
# N8N / EODHD settings
# ==========================
N8N_EODHD_API_KEY = "A5OxYhgQB5vHrpDlF4yW8a8i73Z"
N8N_EODHD_TICKERS_URL = "https://n8n.sqowe.com/eodhd/tickers"


class N8nEodhdTickerBuffer:
    """
    Endpoints:
      - DELETE {url}?confirm=true  : clear buffer
      - POST   {url}              : add tickers
      - GET    {url}              : read tickers with last_price, candle_count, interval
    """

    def __init__(self, api_key: str, base_url: str = N8N_EODHD_TICKERS_URL, timeout_sec: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def clear(self) -> None:
        """Clear all tickers from buffer."""
        try:
            r = self.session.delete(self.base_url, params={"confirm": "true"}, timeout=self.timeout_sec)
            r.raise_for_status()
            logger.info("Successfully cleared ticker buffer")
        except requests.Timeout:
            logger.error(f"Timeout while clearing buffer (>{self.timeout_sec}s)")
            raise
        except requests.ConnectionError as e:
            logger.error(f"Connection error while clearing buffer: {e}")
            raise
        except requests.HTTPError as e:
            logger.error(f"HTTP error while clearing buffer: {e.response.status_code}")
            raise

    def add(self, tickers: List[str]) -> None:
        """Add tickers to buffer."""
        tickers = [t.strip().upper() for t in tickers if t and t.strip()]
        try:
            r = self.session.post(
                self.base_url,
                headers={"Content-Type": "application/json"},
                json={"tickers": tickers},
                timeout=self.timeout_sec,
            )
            r.raise_for_status()
            logger.info(f"Successfully added {len(tickers)} tickers")
        except requests.Timeout:
            logger.error(f"Timeout while adding tickers (>{self.timeout_sec}s)")
            raise
        except requests.ConnectionError as e:
            logger.error(f"Connection error while adding tickers: {e}")
            raise
        except requests.HTTPError as e:
            logger.error(f"HTTP error while adding tickers: {e.response.status_code}")
            raise

    def fetch_snapshot(self) -> List[dict]:
        """Fetch current ticker snapshot."""
        try:
            r = self.session.get(self.base_url, timeout=self.timeout_sec)
            r.raise_for_status()
            data = r.json()

            # Expected format:
            # {
            #   "count": 1,
            #   "tickers": [ { "symbol": "...", "last_price": ..., "candle_count": ..., "interval": "1m" } ],
            #   ...
            # }
            tickers = data.get("tickers", []) if isinstance(data, dict) else data
            if not isinstance(tickers, list):
                raise ValueError(f"Unexpected response format: tickers is {type(tickers)}")
            return tickers
        except requests.Timeout:
            logger.warning(f"Timeout while fetching snapshot (>{self.timeout_sec}s)")
            return []
        except requests.ConnectionError as e:
            logger.warning(f"Connection error while fetching snapshot: {e}")
            return []
        except requests.HTTPError as e:
            logger.warning(f"HTTP error while fetching snapshot: {e.response.status_code}")
            return []
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid response format: {e}")
            return []

    def get_last_data(
        self,
        tickers: List[str],
        retries: int = 30,
        sleep_sec: float = 0.5,
    ) -> Tuple[
        Dict[str, Optional[float]],
        Dict[str, Optional[int]],
        Dict[str, Optional[str]],
    ]:
        """
        Returns:
          last_prices   : {TICKER: float | None}
          candle_counts : {TICKER: int | None}
          intervals     : {TICKER: str | None}
        """
        wanted = {t.upper() for t in tickers}
        last_prices = {t: None for t in wanted}
        candle_counts = {t: None for t in wanted}
        intervals = {t: None for t in wanted}

        for attempt in range(retries):
            data = self.fetch_snapshot()

            for item in data:
                sym = str(item.get("symbol", "")).upper()
                if sym not in wanted:
                    continue

                lp = item.get("last_price")
                cc = item.get("candle_count")
                itv = item.get("interval")

                try:
                    last_prices[sym] = None if lp is None else float(lp)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid last_price for {sym}: {lp} ({e})")
                    last_prices[sym] = None

                try:
                    candle_counts[sym] = None if cc is None else int(cc)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid candle_count for {sym}: {cc} ({e})")
                    candle_counts[sym] = None

                intervals[sym] = itv if isinstance(itv, str) else None

            if all(last_prices[t] is not None for t in wanted):
                logger.info(f"Data received after {attempt + 1} attempts")
                break

            time.sleep(sleep_sec)
        
        # Log summary
        received = sum(1 for v in last_prices.values() if v is not None)
        logger.info(f"Received data for {received}/{len(wanted)} tickers")

        return last_prices, candle_counts, intervals

    def get_config(self) -> Optional[dict]:
        """Fetch configuration including candle_interval_minutes."""
        try:
            config_url = self.base_url.replace("/tickers", "/config")
            r = self.session.get(config_url, timeout=self.timeout_sec)
            r.raise_for_status()
            logger.debug("Successfully fetched config")
            return r.json()
        except requests.Timeout:
            logger.warning(f"Timeout while fetching config (>{self.timeout_sec}s)")
            return None
        except requests.ConnectionError as e:
            logger.warning(f"Connection error while fetching config: {e}")
            return None
        except requests.HTTPError as e:
            logger.warning(f"HTTP error while fetching config: {e.response.status_code}")
            return None
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid config response format: {e}")
            return None

    def get_latest_candle_price(self, ticker: str) -> Optional[float]:
        """Fetch the most recent candle and return its close price."""
        try:
            candle_url = self.base_url.replace("/tickers", f"/candles/{ticker}")
            r = self.session.get(
                candle_url,
                params={"count": 1, "include_current": "true"},
                timeout=self.timeout_sec
            )
            r.raise_for_status()
            data = r.json()
            candles = data.get("candles", [])
            if candles and len(candles) > 0:
                close_price = float(candles[0].get("close"))
                logger.debug(f"Got latest price for {ticker}: {close_price}")
                return close_price
            logger.debug(f"No candles found for {ticker}")
            return None
        except requests.Timeout:
            logger.warning(f"Timeout while fetching candle for {ticker} (>{self.timeout_sec}s)")
            return None
        except requests.ConnectionError as e:
            logger.warning(f"Connection error while fetching candle for {ticker}: {e}")
            return None
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"No candle data found for {ticker} (404)")
            else:
                logger.warning(f"HTTP error while fetching candle for {ticker}: {e.response.status_code}")
            return None
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Invalid candle response for {ticker}: {e}")
            return None


# ==========================
# Pivot helpers
# ==========================
def classic_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    P = (high + low + close) / 3.0
    return {
        "P": P,
        "R1": 2 * P - low,
        "S1": 2 * P - high,
        "R2": P + (high - low),
        "S2": P - (high - low),
        "R3": high + 2 * (P - low),
        "S3": low - 2 * (high - P),
    }


def fib_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    P = (high + low + close) / 3.0
    r = high - low
    return {
        "P": P,
        "R1": P + 0.382 * r,
        "S1": P - 0.382 * r,
        "R2": P + 0.618 * r,
        "S2": P - 0.618 * r,
        "R3": P + r,
        "S3": P - r,
    }


def last_full_session_ohlc(df: pd.DataFrame, use_ny_time: bool) -> Optional[Tuple[float, float, float]]:
    """
    Get OHLC from the last full trading session.
    
    Handles:
    - Market holidays
    - Data gaps
    - Weekends
    - Invalid data
    """
    if df is None or df.empty:
        logger.warning("Empty dataframe provided")
        return None

    df = df[["High", "Low", "Close"]].dropna()
    
    if df.empty:
        logger.warning("No valid OHLC data after filtering")
        return None

    today = (
        datetime.now(ZoneInfo("America/New_York")).date()
        if use_ny_time
        else date.today()
    )

    # Filter to dates before today
    df = df[df.index.date < today]
    if df.empty:
        logger.warning("No historical data before today")
        return None

    # Try to get the last valid trading day
    # Iterate backwards to find valid data
    for i in range(min(5, len(df))):  # Check up to 5 days back
        try:
            last = df.iloc[-(i+1)]
            
            # Validate data
            high = float(last["High"])
            low = float(last["Low"])
            close = float(last["Close"])
            
            # Sanity checks
            if high <= 0 or low <= 0 or close <= 0:
                logger.warning(f"Invalid prices at index -{i+1}: H={high}, L={low}, C={close}")
                continue
            
            if low > high:
                logger.warning(f"Low > High at index -{i+1}: L={low}, H={high}")
                continue
            
            if close < low or close > high:
                logger.warning(f"Close outside range at index -{i+1}: C={close}, L={low}, H={high}")
                continue
            
            # Valid data found
            if i > 0:
                logger.info(f"Using data from {i+1} days ago (skipped {i} invalid days)")
            
            return high, low, close
            
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Error processing row at index -{i+1}: {e}")
            continue
    
    logger.error("Could not find valid OHLC data in last 5 days")
    return None


def premarket_ohlc_from_yahoo(ticker: str) -> Optional[Tuple[float, float, float, float]]:
    try:
        df = yf.download(
            tickers=ticker,
            period="1d",
            interval="1m",
            auto_adjust=False,
            prepost=True,
            progress=False,
        )
    except Exception:
        return None

    if df is None or df.empty:
        return None

    ny = ZoneInfo("America/New_York")
    df.index = df.index.tz_convert(ny) if df.index.tz else df.index.tz_localize(ny)

    today = datetime.now(ny).date()
    df = df[df.index.date == today]
    df = df.between_time("04:00", "09:29")
    if df.empty:
        return None

    vol = pd.to_numeric(df["Volume"].values.ravel(), errors="coerce").sum()
    return float(df["High"].max()), float(df["Low"].min()), float(df["Close"].iloc[-1]), float(vol)


def compute_for_ticker(df_panel: pd.DataFrame, t: str, method: str, premarket: bool, use_ny_time: bool):
    if isinstance(df_panel.columns, pd.MultiIndex):
        if t not in df_panel.columns.levels[1]:
            return None
        df = df_panel.xs(t, axis=1, level=1)
    else:
        df = df_panel

    prev = last_full_session_ohlc(df, use_ny_time)
    if not prev:
        return None

    prev_high, prev_low, prev_close = prev
    avg_vol = float(pd.to_numeric(df["Volume"], errors="coerce").tail(60).mean())

    row = {
        "ticker": t,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "prev_close": prev_close,
        "avg_3m_volume": avg_vol,
    }

    if premarket:
        pm = premarket_ohlc_from_yahoo(t)
        if pm:
            ph, pl, pc, pv = pm
            row.update({"pm_high": ph, "pm_low": pl, "pm_close": pc, "pm_volume": pv})
            levels = classic_pivots(ph, pl, pc) if method == "classic" else fib_pivots(ph, pl, pc)
        else:
            levels = classic_pivots(prev_high, prev_low, prev_close)
    else:
        levels = classic_pivots(prev_high, prev_low, prev_close)

    row.update(levels)
    return row


def setup_logging(verbose: bool = False):
    """Configure logging for the script."""
    log_level = logging.DEBUG if verbose else logging.INFO
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    
    # File handler (optional)
    try:
        file_handler = logging.FileHandler('premarket_pivots.log')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        has_file_handler = True
    except (IOError, PermissionError):
        has_file_handler = False
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    if has_file_handler:
        root_logger.addHandler(file_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('yfinance').setLevel(logging.WARNING)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="*", default=[])
    p.add_argument("--premarket", action="store_true")
    p.add_argument("--ny-time", action="store_true")
    p.add_argument("--method", choices=["classic", "fib"], default="classic")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = p.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    
    tickers = sorted(set(t.upper() for t in args.tickers if t))
    if not tickers:
        logger.error("No tickers provided")
        print("No tickers provided")
        return

    logger.info(f"Starting analysis for {len(tickers)} tickers: {', '.join(tickers)}")
    logger.info(f"Method: {args.method}, Premarket: {args.premarket}, NY Time: {args.ny_time}")

    # ---- n8n ----
    last_prices = {}
    candle_counts = {}
    intervals = {}
    global_interval = None
    buf = None

    try:
        logger.info("Connecting to EODHD API...")
        buf = N8nEodhdTickerBuffer(N8N_EODHD_API_KEY)
        buf.clear()
        buf.add(tickers)
        time.sleep(2)
        last_prices, candle_counts, intervals = buf.get_last_data(tickers)
        
        # Get global interval from config
        config_data = buf.get_config()
        if config_data:
            config_dict = config_data.get("config", {})
            interval_value = config_dict.get("candle_interval_minutes", {})
            if isinstance(interval_value, dict):
                interval_mins = interval_value.get("value")
            else:
                interval_mins = interval_value
            if interval_mins:
                global_interval = f"{interval_mins}m"
                logger.info(f"Global interval: {global_interval}")
    except Exception as e:
        logger.warning(f"EODHD API unavailable: {e}", exc_info=args.verbose)
        print(f"[WARN] n8n unavailable: {e}")

    # ---- market data ----
    logger.info("Fetching historical data from Yahoo Finance...")
    df_panel = yf.download(
        tickers=tickers,
        period="30d",
        interval="1d",
        group_by="column",
        auto_adjust=False,
        progress=False,
    )

    rows = []
    for t in tickers:
        logger.debug(f"Computing data for {t}")
        r = compute_for_ticker(df_panel, t, args.method, args.premarket, args.ny_time)
        if r:
            rows.append(r)
        else:
            logger.warning(f"No data computed for {t}")

    if not rows:
        logger.error("No data computed for any ticker")
        print("No data available")
        return

    df = pd.DataFrame(rows)
    logger.info(f"Computed data for {len(rows)} tickers")

    df["current_price"] = df["ticker"].map(last_prices)
    df["candle_count"] = df["ticker"].map(candle_counts)
    df["interval"] = df["ticker"].map(intervals)
    
    # ---- Fill missing data ----
    logger.debug("Filling missing data with fallbacks...")
    for idx, row in df.iterrows():
        ticker = row["ticker"]
        
        # Fix interval: use global config if None
        if pd.isna(row["interval"]) or row["interval"] is None:
            df.at[idx, "interval"] = global_interval
            if global_interval:
                logger.debug(f"{ticker}: Using global interval {global_interval}")
        
        # Fix current_price: try latest candle close, then pm_close, then prev_close
        if pd.isna(row["current_price"]) or row["current_price"] is None:
            # Try to get from latest candle
            if buf:
                latest_price = buf.get_latest_candle_price(ticker)
                if latest_price:
                    df.at[idx, "current_price"] = latest_price
                    logger.debug(f"{ticker}: Using latest candle price {latest_price}")
                    continue
            
            # Fallback to premarket close if available
            if "pm_close" in row and not pd.isna(row["pm_close"]):
                df.at[idx, "current_price"] = row["pm_close"]
                logger.debug(f"{ticker}: Using premarket close {row['pm_close']}")
            # Final fallback to previous close
            elif "prev_close" in row and not pd.isna(row["prev_close"]):
                df.at[idx, "current_price"] = row["prev_close"]
                logger.debug(f"{ticker}: Using previous close {row['prev_close']}")
    
    # ---- Check for missing tickers and show warning ----
    missing_tickers = [t for t in tickers if candle_counts.get(t, 0) == 0]
    if missing_tickers:
        logger.warning(f"{len(missing_tickers)} tickers not tracked in EODHD: {', '.join(missing_tickers)}")
        print(f"\n{'='*80}")
        print(f"[WARNING] The following tickers are NOT tracked in EODHD system:")
        for t in missing_tickers:
            print(f"  • {t}")
        print(f"\nTo add these tickers, use:")
        print(f"  python scripts/manage_tickers.py {' '.join(missing_tickers)}")
        print(f"\nOr with force flag to auto-remove old tickers if capacity reached:")
        print(f"  python scripts/manage_tickers.py --force {' '.join(missing_tickers)}")
        print(f"{'='*80}\n")

    cols = [
        "ticker",
        "interval",
        "current_price",
        "candle_count",
        "prev_high",
        "prev_low",
        "prev_close",
        "avg_3m_volume",
    ]
    cols += [c for c in ["pm_high", "pm_low", "pm_close", "pm_volume"] if c in df.columns]
    cols += ["P", "R1", "S1", "R2", "S2", "R3", "S3"]
    cols = [c for c in cols if c in df.columns]

    if HAVE_TABULATE:
        print(tabulate(df[cols], headers="keys", tablefmt="github", floatfmt=".4f", showindex=False))
    else:
        print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
