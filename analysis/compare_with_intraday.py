#!/usr/bin/env python3
"""
Compare the service's own candles against EODHD's Intraday Historical API.

The WebSocket feed this service consumes is Cboe EDGX -- one exchange, not the
consolidated (SIP) tape. Intraday Historical carries the full tape but only from
the previous day onward, which makes it a usable reference rather than a
replacement. Comparing the two answers three questions per ticker:

  * what share of market volume reaches the service,
  * how many minutes of the session produced no candle here,
  * how many of those minutes actually traded somewhere else.

Run it after any session to confirm the figures in ARCHITECTURE.md section 4.4
still hold, or when a ticker looks wrong.

Usage:
    python analysis/compare_with_intraday.py 2026-09-03
    python analysis/compare_with_intraday.py 2026-09-03 --tickers NVDA,VPG
    python analysis/compare_with_intraday.py 2026-09-03 --session extended
    python analysis/compare_with_intraday.py 2026-09-03 --csv out.csv

Reads EODHD_API_KEY, API_URL and API_KEY from the environment or .env.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    raise ImportError(
        "zoneinfo not available. Please install tzdata: pip install tzdata"
    )

ET = ZoneInfo('America/New_York')
INTRADAY_URL = 'https://eodhd.com/api/intraday/{ticker}.US'

# Session windows in New York local time, so daylight saving is handled.
SESSIONS = {
    'regular': ((9, 30), (16, 0)),
    'extended': ((4, 0), (20, 0)),
}


def load_env(path: str = '.env') -> None:
    """Read KEY=value lines into the environment without overriding it."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.split('#')[0].strip())


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None,
               timeout: int = 60):
    """GET a URL and parse the JSON body. Returns None on any failure."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  запрос не удался: {exc}", file=sys.stderr)
        return None


def session_bounds(day: datetime, session: str) -> Tuple[int, int]:
    """Unix seconds for the session's start and end on this date."""
    (start_h, start_m), (end_h, end_m) = SESSIONS[session]
    start = datetime(day.year, day.month, day.day, start_h, start_m, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, end_h, end_m, tzinfo=ET)
    return int(start.timestamp()), int(end.timestamp())


def minute_grid(start_ts: int, end_ts: int) -> List[int]:
    """Every minute start in [start, end)."""
    return list(range(start_ts, end_ts, 60))


def fetch_ours(api_url: str, api_key: Optional[str], ticker: str,
               start_ts: int, end_ts: int) -> Optional[Dict[int, int]]:
    """Completed 1-minute candles from this service: {minute -> volume}."""
    query = urllib.parse.urlencode({
        'count': 5000,
        'include_current': 'false',
        'from_timestamp': start_ts,
        'to_timestamp': end_ts,
    })
    headers = {'X-API-Key': api_key} if api_key else {}
    payload = fetch_json(f"{api_url}/candles/{ticker}?{query}", headers)
    if payload is None:
        return None
    candles = payload.get('candles', payload if isinstance(payload, list) else [])
    return {int(c['timestamp']): int(c.get('volume') or 0) for c in candles}


def fetch_intraday(eodhd_key: str, ticker: str, start_ts: int,
                   end_ts: int) -> Optional[Dict[int, int]]:
    """Full-tape 1-minute bars from EODHD: {minute -> volume}."""
    query = urllib.parse.urlencode({
        'interval': '1m', 'from': start_ts, 'to': end_ts,
        'api_token': eodhd_key, 'fmt': 'json',
    })
    bars = fetch_json(f"{INTRADAY_URL.format(ticker=ticker)}?{query}")
    if bars is None:
        return None
    out = {}
    for bar in bars:
        stamp = bar.get('timestamp')
        if stamp is None:
            moment = datetime.strptime(bar['datetime'], '%Y-%m-%d %H:%M:%S')
            stamp = int(moment.replace(tzinfo=timezone.utc).timestamp())
        out[int(stamp)] = int(bar.get('volume') or 0)
    return out


def compare(ours: Dict[int, int], theirs: Dict[int, int],
            grid: List[int]) -> dict:
    """One ticker's figures over the session grid."""
    our_volume = sum(ours.get(m, 0) for m in grid)
    their_volume = sum(theirs.get(m, 0) for m in grid)
    our_minutes = sum(1 for m in grid if m in ours)
    their_minutes = sum(1 for m in grid if m in theirs)
    our_empty = [m for m in grid if m not in ours]
    # Minutes we have no candle for, but which traded on another venue.
    traded_elsewhere = sum(1 for m in our_empty if m in theirs)
    return {
        'our_volume': our_volume,
        'their_volume': their_volume,
        'volume_share': our_volume / their_volume * 100 if their_volume else None,
        'our_minutes': our_minutes,
        'their_minutes': their_minutes,
        'minutes_lost': their_minutes - our_minutes,
        'our_empty': len(our_empty),
        'traded_elsewhere': traded_elsewhere,
        'false_empty_share': (traded_elsewhere / len(our_empty) * 100
                              if our_empty else None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare this service's candles against the full tape.")
    parser.add_argument('date', help='trading day, YYYY-MM-DD')
    parser.add_argument('--tickers', help='comma-separated; default: all tracked')
    parser.add_argument('--session', choices=sorted(SESSIONS), default='regular')
    parser.add_argument('--api-url', default=None,
                        help='service base URL (default: $API_URL or localhost)')
    parser.add_argument('--csv', help='also write a row per ticker here')
    parser.add_argument('--delay', type=float, default=0.25,
                        help='seconds between Intraday requests (default: 0.25)')
    args = parser.parse_args()

    load_env()
    eodhd_key = os.environ.get('EODHD_API_KEY')
    if not eodhd_key:
        print("EODHD_API_KEY не задан", file=sys.stderr)
        return 1
    api_url = (args.api_url or os.environ.get('API_URL')
               or 'http://localhost:8765').rstrip('/')
    api_key = os.environ.get('API_KEY') or None

    try:
        day = datetime.strptime(args.date, '%Y-%m-%d')
    except ValueError:
        print(f"дата должна быть в виде YYYY-MM-DD, а не {args.date!r}",
              file=sys.stderr)
        return 1

    # Intraday is fetched as 1-minute bars, so the service must be aggregating
    # at 1 minute too, or the two grids describe different things and every
    # comparison below is meaningless.
    headers = {'X-API-Key': api_key} if api_key else {}
    config = fetch_json(f"{api_url}/config", headers)
    if config:
        # GET /config nests the settings under "config", and each one is
        # {"value": ..., "source": ...} when source info is included.
        settings = config.get('config', config)
        raw = settings.get('candle_interval_minutes')
        interval = raw.get('value') if isinstance(raw, dict) else raw
        if interval is not None and int(interval) != 1:
            print(f"служба агрегирует по {interval} минут, а сравнение идёт "
                  f"с минутными барами.\nЗапустите при "
                  f"CANDLE_INTERVAL_MINUTES=1 или сравнивайте вручную.",
                  file=sys.stderr)
            return 1

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(',') if t.strip()]
    else:
        payload = fetch_json(f"{api_url}/tickers", headers)
        if payload is None:
            print(f"не удалось получить список бумаг с {api_url}", file=sys.stderr)
            return 2
        tickers = sorted(t['symbol'] for t in payload.get('tickers', []))
    if not tickers:
        print("список бумаг пуст", file=sys.stderr)
        return 2

    start_ts, end_ts = session_bounds(day, args.session)
    grid = minute_grid(start_ts, end_ts)
    print(f"{args.date}, сессия {args.session} — {len(grid)} минут, "
          f"{len(tickers)} бумаг\n")

    rows = []
    for index, ticker in enumerate(tickers, 1):
        ours = fetch_ours(api_url, api_key, ticker, start_ts, end_ts)
        theirs = fetch_intraday(eodhd_key, ticker, start_ts, end_ts)
        time.sleep(args.delay)
        if ours is None or theirs is None:
            print(f"{index:3d}/{len(tickers)} {ticker:8} пропущено")
            continue
        result = compare(ours, theirs, grid)
        result['ticker'] = ticker
        rows.append(result)
        share = (f"{result['volume_share']:5.1f}%"
                 if result['volume_share'] is not None else "    —")
        print(f"{index:3d}/{len(tickers)} {ticker:8} объём {share}   "
              f"минут {result['our_minutes']:4d}/{result['their_minutes']:4d}   "
              f"пустых у нас {result['our_empty']:4d}, "
              f"из них торговались {result['traded_elsewhere']:4d}")

    if not rows:
        print("\nсравнить не удалось ни по одной бумаге", file=sys.stderr)
        return 2

    our_total = sum(r['our_volume'] for r in rows)
    their_total = sum(r['their_volume'] for r in rows)
    empty_total = sum(r['our_empty'] for r in rows)
    traded_total = sum(r['traded_elsewhere'] for r in rows)
    shares = sorted(r['volume_share'] for r in rows
                    if r['volume_share'] is not None)

    print(f"\n{'=' * 62}")
    print(f"объём: {our_total:,} из {their_total:,} = "
          f"{our_total / their_total * 100:.1f}%".replace(',', ' ')
          if their_total else "объём: нет данных")
    if shares:
        print(f"доля по бумагам: мин {shares[0]:.1f}%  "
              f"медиана {shares[len(shares) // 2]:.1f}%  макс {shares[-1]:.1f}%")
    if empty_total:
        print(f"минут без свечи у нас: {empty_total}, из них торговались: "
              f"{traded_total} = {traded_total / empty_total * 100:.1f}%")
    silent = [r['ticker'] for r in rows if r['our_volume'] == 0
              and r['their_volume'] > 0]
    if silent:
        print(f"\nНИ ОДНОЙ СВЕЧИ, хотя бумага торговалась: {', '.join(silent)}")
        print("  проверьте subscription_health в GET /status")

    if args.csv:
        fields = ['ticker', 'our_volume', 'their_volume', 'volume_share',
                  'our_minutes', 'their_minutes', 'minutes_lost', 'our_empty',
                  'traded_elsewhere', 'false_empty_share']
        with open(args.csv, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: r.get(k) for k in fields} for r in rows)
        print(f"\nтаблица записана: {args.csv}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
