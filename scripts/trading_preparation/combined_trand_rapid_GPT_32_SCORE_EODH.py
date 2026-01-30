#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Intraday EMA trend + state + intraday score + VolumeDay + pivots.

CHANGE #1
---------
Integrate cumulative volume logic (include current incomplete candle) and
PRINT cumulative_volume into VolumeDay.

CHANGE #2 (UPDATED per your latest clarification)
-------------------------------------------------
NY_now in the TABLE must show NOT the script run time,
but the candle close time (from candles payload) in NY time,
ONLY when IsComplete == True.

Implementation:
- We already build DF index from candle timestamp -> dt_ny.
- NY_now per row = index formatted "%Y-%m-%d %H:%M" if IsComplete is True, else <NA>.
"""

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import numpy as np
import requests
import yfinance as yf
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

# --- Market session constants (NY) ---
PREMARKET_START = time(4, 0)
RTH_START = time(9, 30)
RTH_END = time(16, 0)


# ---------------------------------------------------------
# N8N EODHD API CLIENTS (tickers + candles)
# ---------------------------------------------------------
class N8NEODHDTickersClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self, ticker: str) -> Dict[str, Any]:
        url = f"{self.base_url}/eodhd/tickers/{ticker}"
        headers = {"X-API-Key": self.api_key}
        resp = requests.get(url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


class N8NEODHDCandlesClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def fetch(
        self,
        ticker: str,
        interval: str = "1m",
        count: int = 500,
        from_unix_utc: Optional[int] = None,
        to_unix_utc: Optional[int] = None,
        include_current: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch intraday candles from the n8n aggregator.

        Expected response shape:
          { "candles": [ {timestamp, datetime_utc, open, high, low, close, volume, is_complete, ...}, ... ] }

        Query params passed if supported by workflow:
          - interval: like '1m'
          - count: number of candles
          - from: unix seconds (UTC)
          - to: unix seconds (UTC)
          - include_current: "true"/"false" (for incomplete current candle)
        """
        url = f"{self.base_url}/eodhd/candles/{ticker}"
        headers = {"X-API-Key": self.api_key}

        params: Dict[str, Any] = {"interval": interval, "count": int(count)}
        if from_unix_utc is not None:
            params["from"] = int(from_unix_utc)
        if to_unix_utc is not None:
            params["to"] = int(to_unix_utc)
        if include_current:
            params["include_current"] = "true"

        resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


def now_ny() -> datetime:
    return datetime.now(tz=NY_TZ)


def market_session(dt_ny: datetime) -> str:
    t = dt_ny.timetz().replace(tzinfo=None)
    if PREMARKET_START <= t < RTH_START:
        return "PRE"
    if RTH_START <= t < RTH_END:
        return "RTH"
    if t >= RTH_END:
        return "EXT"
    return "CLOSED"


def add_market_session_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    idx_ny = out.index.tz_convert(NY_TZ)
    out["Session"] = [market_session(ts.to_pydatetime()) for ts in idx_ny]
    out["IsPremarket"] = out["Session"].eq("PRE")
    out["IsRTH"] = out["Session"].eq("RTH")
    out["IsExtended"] = out["Session"].eq("EXT")
    return out


# ---------------------------------------------------------
# PIVOTS
# ---------------------------------------------------------
def classic_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    p = (high + low + close) / 3.0
    r1 = (2.0 * p) - low
    s1 = (2.0 * p) - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    r3 = high + 2.0 * (p - low)
    s3 = low - 2.0 * (high - p)
    return {"P": p, "R1": r1, "S1": s1, "R2": r2, "S2": s2, "R3": r3, "S3": s3}


def fib_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    p = (high + low + close) / 3.0
    rng = high - low
    r1 = p + 0.382 * rng
    s1 = p - 0.382 * rng
    r2 = p + 0.618 * rng
    s2 = p - 0.618 * rng
    r3 = p + 1.0 * rng
    s3 = p - 1.0 * rng
    return {"P": p, "R1": r1, "S1": s1, "R2": r2, "S2": s2, "R3": r3, "S3": s3}


def format_pivots_line(levels: Dict[str, float]) -> str:
    return (
        f"P : {levels['P']:.6f} "
        f"R1 : {levels['R1']:.6f} S1 : {levels['S1']:.6f} "
        f"R2 : {levels['R2']:.6f} S2 : {levels['S2']:.6f} "
        f"R3 : {levels['R3']:.6f} S3 : {levels['S3']:.6f}"
    )


# ---------------------------------------------------------
# RULES
# ---------------------------------------------------------
@dataclass
class TrendRules:
    hold_10_30: int = 3
    hold_3_10: int = 2
    hold_1_3: int = 1
    hold_30_50: int = 3
    w_10_30: int = 4
    w_3_10: int = 3
    w_1_3: int = 3


# ---------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------
def quantize_now(minutes: int) -> datetime:
    if minutes <= 0:
        return now_ny()
    dt = now_ny()
    floored_minute = (dt.minute // minutes) * minutes
    return dt.replace(minute=floored_minute, second=0, microsecond=0)


def last_reset_0400_ny(now_dt: Optional[datetime] = None) -> datetime:
    now_dt = now_dt or now_ny()
    return now_dt.replace(hour=4, minute=0, second=0, microsecond=0)


def latest_0400_start_ts_unix_utc(now_dt: Optional[datetime] = None) -> int:
    """
    Session start for cumulative volume:
    - 04:00 NY today, unless now < 04:00 NY -> 04:00 NY yesterday
    """
    now_dt = now_dt or now_ny()
    start = now_dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if now_dt < start:
        start = start - timedelta(days=1)
    return int(start.astimezone(UTC_TZ).timestamp())


def interval_to_seconds(interval: str) -> int:
    s = interval.strip().lower()
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    return 60


# ---------------------------------------------------------
# YF HELPERS
# ---------------------------------------------------------
def fetch_yf_volume_stats(ticker: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return None, None

    avg3m = None
    avg20 = None
    try:
        fi = getattr(t, "fast_info", None)
        if fi:
            for k in ("threeMonthAverageVolume", "three_month_average_volume", "avg_volume_3m"):
                if k in fi and fi[k]:
                    avg3m = float(fi[k])
                    break
            for k in ("twentyDayAverageVolume", "twenty_day_average_volume", "avg_volume_20d"):
                if k in fi and fi[k]:
                    avg20 = float(fi[k])
                    break
            if avg20 is None:
                for k in ("tenDayAverageVolume", "ten_day_average_volume", "avg_volume_10d"):
                    if k in fi and fi[k]:
                        avg20 = float(fi[k])
                        break
    except Exception:
        pass

    if avg3m is None or avg20 is None:
        try:
            info = getattr(t, "info", None) or {}
            if avg3m is None:
                for k in ("averageVolume", "averageDailyVolume3Month", "averageVolume3months"):
                    if k in info and info[k]:
                        avg3m = float(info[k])
                        break
            if avg20 is None:
                for k in ("averageVolume20days", "averageVolume10days"):
                    if k in info and info[k]:
                        avg20 = float(info[k])
                        break
        except Exception:
            pass

    return avg3m, avg20


def fetch_yf_daily_ohlcv(ticker: str, daily_count: int = 60) -> pd.DataFrame:
    period = "6mo" if daily_count <= 60 else ("1y" if daily_count <= 130 else ("2y" if daily_count <= 260 else "5y"))
    try:
        df = yf.download(
            tickers=ticker,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(0, axis=1)

    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].dropna(how="any")

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(NY_TZ)
    else:
        idx = idx.tz_convert(NY_TZ)
    df.index = idx
    return df.tail(max(daily_count, 3))


def prev_complete_daily_candle(daily_df: pd.DataFrame) -> Optional[pd.Series]:
    if daily_df is None or daily_df.empty:
        return None

    df = daily_df.sort_index()
    last_dt = df.index[-1].tz_convert(NY_TZ)
    if last_dt.date() == now_ny().date() and len(df) >= 2:
        return df.iloc[-2]
    return df.iloc[-1]


# ---------------------------------------------------------
# EMA + trend logic
# ---------------------------------------------------------
def compute_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df["Close"].ewm(span=period, adjust=False).mean()


def add_emas_and_percents(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema1"] = compute_ema(df, 1)
    df["ema3"] = compute_ema(df, 3)
    df["ema10"] = compute_ema(df, 10)
    df["ema30"] = compute_ema(df, 30)
    df["ema50"] = compute_ema(df, 50)

    df["Procent_1_3"] = (df["ema1"] / df["ema3"] - 1.0) * 100.0
    df["Procent_3_10"] = (df["ema3"] / df["ema10"] - 1.0) * 100.0
    df["Procent_10_30"] = (df["ema10"] / df["ema30"] - 1.0) * 100.0
    df["Procent_30_50"] = (df["ema30"] / df["ema50"] - 1.0) * 100.0
    return df


def consecutive_holds(cond: pd.Series) -> pd.Series:
    out = []
    c = 0
    for v in cond.fillna(False).tolist():
        c = (c + 1) if bool(v) else 0
        out.append(c)
    return pd.Series(out, index=cond.index)


def _trend_label(diff: pd.Series, eps: float = 1e-12) -> pd.Series:
    return diff.apply(lambda x: "UP" if x > eps else ("DOWN" if x < -eps else "FLAT"))


def classify_trend_states(df: pd.DataFrame, rules: TrendRules) -> pd.DataFrame:
    df = df.copy()
    c30_50 = df["ema30"] > df["ema50"]
    c10_30 = df["ema10"] > df["ema30"]
    c3_10 = df["ema3"] > df["ema10"]
    c1_3 = df["ema1"] > df["ema3"]

    df["hold_30_50"] = consecutive_holds(c30_50)
    df["hold_10_30"] = consecutive_holds(c10_30)
    df["hold_3_10"] = consecutive_holds(c3_10)
    df["hold_1_3"] = consecutive_holds(c1_3)

    df["stable_30_50"] = df["hold_30_50"] >= rules.hold_30_50
    df["stable_10_30"] = df["hold_10_30"] >= rules.hold_10_30
    df["stable_3_10"] = df["hold_3_10"] >= rules.hold_3_10
    df["stable_1_3"] = df["hold_1_3"] >= rules.hold_1_3

    df["trend_30_50"] = _trend_label(df["ema30"] - df["ema50"])
    df["trend_10_30"] = _trend_label(df["ema10"] - df["ema30"])
    df["trend_3_10"] = _trend_label(df["ema3"] - df["ema10"])
    df["trend_1_3"] = _trend_label(df["ema1"] - df["ema3"])

    def _summary(row: pd.Series) -> str:
        t0 = f"30/50:{row['trend_30_50']}{'✓' if row['stable_30_50'] else ''}"
        t1 = f"10/30:{row['trend_10_30']}{'✓' if row['stable_10_30'] else ''}"
        t2 = f"3/10:{row['trend_3_10']}{'✓' if row['stable_3_10'] else ''}"
        t3 = f"1/3:{row['trend_1_3']}{'✓' if row['stable_1_3'] else ''}"
        return f"{t0} | {t1} | {t2} | {t3}"

    df["trend_summary"] = df.apply(_summary, axis=1)
    return df


def intraday_score_from_lastrow(lastrow: pd.Series, rules: TrendRules) -> int:
    score = 0
    if bool(lastrow.get("stable_10_30", False)):
        score += rules.w_10_30
    if bool(lastrow.get("stable_3_10", False)):
        score += rules.w_3_10
    if bool(lastrow.get("stable_1_3", False)):
        score += rules.w_1_3
    return max(0, min(10, int(score)))


def detect_state(row: pd.Series) -> str:
    ema1 = float(row["ema1"])
    ema3 = float(row["ema3"])
    ema10 = float(row["ema10"])
    ema30 = float(row["ema30"])
    stable_10_30 = bool(row.get("stable_10_30", False))

    if ema10 <= ema30 and ema3 <= ema10:
        return "DOWN"
    if ema10 <= ema30 and ema3 > ema10:
        return "BASE"
    if ema10 > ema30 and not stable_10_30:
        return "TREND_START"
    if ema10 > ema30 and stable_10_30 and ema3 >= ema10 and ema1 >= ema3:
        return "TREND"
    if ema10 > ema30 and stable_10_30 and (ema3 < ema10 or ema1 < ema3):
        return "PULLBACK"
    return "UNKNOWN"


# ---------------------------------------------------------
# CUMULATIVE VOLUME (integrated)
# ---------------------------------------------------------
def compute_cumulative_volume_from_payload(payload: Dict[str, Any], session_start_ts_unix: int) -> int:
    candles = payload.get("candles", []) or []
    if not isinstance(candles, list):
        return 0

    cum = 0
    for c in candles:
        try:
            ts = c.get("timestamp")
            if ts is None:
                continue
            ts_i = int(ts)
            if ts_i < int(session_start_ts_unix):
                continue
            vol = c.get("volume", 0) or 0
            cum += int(vol)
        except Exception:
            continue
    return int(cum)


def add_volume_day_from_cumulative(df: pd.DataFrame, cumulative_volume: Optional[int]) -> pd.DataFrame:
    """
    Writes *single* cumulative_volume value into VolumeDay.

    Rule:
    - For rows with NY time < 04:00 => VolumeDay = <NA>
    - For rows >= 04:00 => VolumeDay = cumulative_volume
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    idx_ny = out.index.tz_convert(NY_TZ)
    before_0400 = idx_ny.time < pd.Timestamp("04:00").time()

    out["VolumeDay"] = pd.Series([pd.NA] * len(out), index=out.index, dtype="Int64")
    if cumulative_volume is not None:
        out.loc[~before_0400, "VolumeDay"] = int(cumulative_volume)
    return out


def payload_to_df(payload: Dict[str, Any], drop_incomplete: bool = False) -> pd.DataFrame:
    candles = payload.get("candles", [])
    rows = []
    for c in candles:
        is_complete = c.get("is_complete")
        if drop_incomplete and (is_complete is False):
            continue

        ts = c.get("timestamp")
        if ts is None:
            continue

        dt_utc = datetime.fromtimestamp(int(ts), tz=UTC_TZ)
        dt_ny = dt_utc.astimezone(NY_TZ)

        volume_raw = c.get("volume")

        rows.append(
            {
                "Date": dt_ny,  # candle close time in NY (derived from timestamp)
                "Open": c.get("open"),
                "High": c.get("high"),
                "Low": c.get("low"),
                "Close": c.get("close"),
                "IsComplete": bool(is_complete) if is_complete is not None else None,
                "VolumeCandle": volume_raw,
                "VolumeNotional": volume_raw,
                "VolumeShares": volume_raw,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("Date").sort_index()

    for col in ["Open", "High", "Low", "Close", "VolumeNotional", "VolumeShares", "VolumeCandle"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Close"])
    df.index = df.index.tz_convert(NY_TZ)
    return df


# ---------------------------------------------------------
# last_price extractor (robust) - last_price ONLY
# ---------------------------------------------------------
def extract_last_price(payload: Any) -> Optional[float]:
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


def add_last_price_today_column(df: pd.DataFrame, last_price: Optional[float]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if "Session" not in out.columns:
        out = add_market_session_columns(out)

    out["LastPriceToday"] = None
    if last_price is None:
        return out
    if market_session(now_ny()) == "CLOSED":
        return out
    out.loc[out["Session"].ne("CLOSED"), "LastPriceToday"] = float(last_price)
    return out


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Intraday EMA trends + score + pivots (daily from YF).")
    p.add_argument("--ticker", required=True)
    p.add_argument("--base-url", default="https://n8n.sqowe.com")
    p.add_argument("--api-key", default=None)
    p.add_argument("--interval", default="5m")
    p.add_argument("--count", type=int, default=400)
    p.add_argument("--daily-count", type=int, default=60)
    p.add_argument("--pivots", choices=["classic", "fib", "both", "none"], default="classic")
    p.add_argument("--hold-30-50", type=int, default=3)
    p.add_argument("--hold-10-30", type=int, default=3)
    p.add_argument("--hold-3-10", type=int, default=2)
    p.add_argument("--hold-1-3", type=int, default=1)
    p.add_argument("--keep-incomplete", action="store_true")
    p.add_argument("--quantize-score", type=int, default=0)
    p.add_argument("--tail", type=int, default=25)
    p.add_argument("--no-trend-summary", action="store_true")
    p.add_argument("--out", default=None)
    return p


def main() -> None:
    args = build_argparser().parse_args()
    api_key = args.api_key or os.getenv("N8N_EODHD_API_KEY")
    if not api_key:
        raise SystemExit("Missing API key. Provide --api-key or set env N8N_EODHD_API_KEY")

    rules = TrendRules(
        hold_10_30=args.hold_10_30,
        hold_3_10=args.hold_3_10,
        hold_1_3=args.hold_1_3,
        hold_30_50=args.hold_30_50,
    )

    avg3m, avg20 = fetch_yf_volume_stats(args.ticker)

    tickers_client = N8NEODHDTickersClient(base_url=args.base_url, api_key=api_key)
    last_price = extract_last_price(tickers_client.fetch(args.ticker))

    candles_client = N8NEODHDCandlesClient(base_url=args.base_url, api_key=api_key)

    ny_now = now_ny()
    from_unix = None
    to_unix = None

    interval_sec = interval_to_seconds(args.interval)
    effective_count = int(args.count)

    if ny_now.time() >= PREMARKET_START:
        start_ts_ny = last_reset_0400_ny(ny_now)
        from_unix = int(start_ts_ny.astimezone(UTC_TZ).timestamp())
        to_unix = int(ny_now.astimezone(UTC_TZ).timestamp())

        elapsed_sec = max(0, int((ny_now - start_ts_ny).total_seconds()))
        needed = int(elapsed_sec // interval_sec) + 25
        if needed > effective_count:
            effective_count = needed

    # 1) Intraday DF
    payload_i = candles_client.fetch(
        ticker=args.ticker,
        interval=args.interval,
        count=effective_count,
        from_unix_utc=from_unix,
        to_unix_utc=to_unix,
        include_current=bool(args.keep_incomplete),
    )
    df_i = payload_to_df(payload_i, drop_incomplete=(not args.keep_incomplete))

    if df_i.empty:
        print(f"{args.ticker} | NO INTRADAY DATA")
        return

    # 2) Cumulative volume (always include current candle)
    session_start_ts_unix = latest_0400_start_ts_unix_utc(ny_now)
    payload_cum = candles_client.fetch(
        ticker=args.ticker,
        interval="1m",
        count=1000,
        include_current=True,
    )
    cumulative_volume = compute_cumulative_volume_from_payload(payload_cum, session_start_ts_unix)

    # Build dataframe
    df_i = add_market_session_columns(df_i)
    df_i = add_volume_day_from_cumulative(df_i, cumulative_volume)
    df_i = add_last_price_today_column(df_i, last_price)
    df_i = add_emas_and_percents(df_i)
    df_i = classify_trend_states(df_i, rules)

    df_out = df_i

    if args.quantize_score and args.quantize_score > 0:
        bucket_end = quantize_now(args.quantize_score)
        df_out = df_i[df_i.index <= bucket_end]
        if df_out.empty:
            df_out = df_i.tail(1)

    # TABLE OUTPUT
    df_out = df_out.copy()

    # NY_now must be candle close time (NY) ONLY for complete candles
    # If IsComplete is missing/None -> treat as <NA> for NY_now
    is_complete = df_out.get("IsComplete")
    if is_complete is None:
        df_out["NY_now"] = pd.NA
    else:
        idx_str = df_out.index.tz_convert(NY_TZ).strftime("%Y-%m-%d %H:%M")
        df_out["NY_now"] = pd.Series(idx_str, index=df_out.index).where(is_complete.astype(bool), pd.NA)

    def _row_score(r: pd.Series) -> str:
        try:
            return f"{intraday_score_from_lastrow(r, rules)}/10"
        except Exception:
            return ""

    df_out["INTRADAY SCORE:"] = df_out.apply(_row_score, axis=1)

    cols = [
        "NY_now", "LastPriceToday",
        "Open", "High", "Low", "Close",
        "VolumeNotional", "VolumeCandle", "VolumeShares", "VolumeDay",
        "Session", "IsComplete",
        "stable_30_50", "stable_10_30", "stable_3_10", "stable_1_3",
        "INTRADAY SCORE:",
        "trend_summary",
    ]
    cols = [c for c in cols if c in df_out.columns]
    print(df_out[cols].tail(max(1, args.tail)).to_string(index=False))

    if args.out:
        out = args.out.lower()
        if out.endswith(".json"):
            last = df_out.iloc[-1]
            obj = {
                "ticker": args.ticker,
                "ny_now": now_ny().isoformat(),
                "interval": args.interval,
                "count_requested": args.count,
                "count_effective": effective_count,
                "from_unix_utc": from_unix,
                "to_unix_utc": to_unix,
                "avg_3m_volume": avg3m,
                "avg_day_volume_20": avg20,
                "last_price": last_price,
                "cumulative_volume_from_0400": int(cumulative_volume),
                "last": {
                    k: (
                        float(last[k])
                        if pd.notna(last[k]) and isinstance(last[k], (int, float, np.integer, np.floating))
                        else (None if pd.isna(last[k]) else str(last[k]))
                    )
                    for k in df_out.columns
                },
            }
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        else:
            df_out.to_csv(args.out, index=True)


if __name__ == "__main__":
    main()

