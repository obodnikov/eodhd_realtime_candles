#!/usr/bin/env python3
"""
Cumulative Volume Calculator from Session Start

Calculates the cumulative total of all shares traded from a specified
session start time through the current moment, including the current
incomplete candle.

Uses the project's REST API (GET /candles/{ticker}) to fetch candle data.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:
    raise ImportError(
        "zoneinfo not available. Please install tzdata: pip install tzdata"
    )


class CumulativeVolumeCalculator:
    """Calculate cumulative volume from session start to current time."""
    
    # Session boundaries in minutes from midnight ET
    PREMARKET_START = 240   # 4:00 AM
    MARKET_OPEN = 570       # 9:30 AM
    MARKET_CLOSE = 960      # 4:00 PM
    AFTER_HOURS_END = 1200  # 8:00 PM
    
    @classmethod
    def _minutes_to_hm(cls, minutes: int) -> tuple:
        """Convert minutes from midnight to (hour, minute) tuple."""
        return (minutes // 60, minutes % 60)
    
    @classmethod
    def get_session_start_times(cls) -> Dict[str, tuple]:
        """Derive session start times from constants to avoid duplication."""
        return {
            'premarket': cls._minutes_to_hm(cls.PREMARKET_START),
            'market': cls._minutes_to_hm(cls.MARKET_OPEN),
            'after_hours': cls._minutes_to_hm(cls.MARKET_CLOSE),
        }
    
    def __init__(self, host: str, api_key: Optional[str] = None):
        """Initialize calculator with API host and key.
        
        Args:
            host: Base URL of the REST API (e.g., http://localhost:8765)
            api_key: API key for authentication (from env if not provided)
        """
        self.host = host.rstrip('/')
        self.api_key = api_key or os.getenv('API_KEY')
        if not self.api_key:
            raise ValueError("API_KEY environment variable is required")
        
        self.et_tz = ZoneInfo("America/New_York")
    
    def get_current_session(self, now_et: datetime) -> str:
        """Determine current market session based on ET time."""
        minutes = now_et.hour * 60 + now_et.minute
        weekday = now_et.weekday()
        
        # Weekend check
        if weekday >= 5:
            return "closed"
        
        if minutes < self.PREMARKET_START:
            return "closed"
        elif minutes < self.MARKET_OPEN:
            return "premarket"
        elif minutes < self.MARKET_CLOSE:
            return "market"
        elif minutes < self.AFTER_HOURS_END:
            return "after_hours"
        else:
            return "closed"
    
    def get_session_start_timestamp(self, now_et: datetime, market: str) -> int:
        """Get Unix timestamp for session start time.
        
        If current time is before the requested session start, uses yesterday's
        session start to get the most recent completed session data.
        
        Args:
            now_et: Current time in ET
            market: Session type (premarket, market, after_hours)
            
        Returns:
            Unix timestamp for session start
        """
        from datetime import timedelta
        
        hour, minute = self.get_session_start_times()[market]
        start_et = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If current time is before session start, use yesterday's session
        if now_et < start_et:
            start_et = start_et - timedelta(days=1)
        
        return int(start_et.timestamp())
    
    def fetch_candles(self, ticker: str, count: int = 1000) -> List[Dict]:
        """Fetch candles from REST API.
        
        Args:
            ticker: Stock ticker symbol (e.g., AAPL)
            count: Maximum number of candles to retrieve
            
        Returns:
            List of candle dictionaries
        """
        url = f"{self.host}/candles/{ticker}"
        headers = {"X-API-Key": self.api_key}
        params = {
            "count": count,
            "include_current": "true"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("candles", [])
        except requests.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def calculate_cumulative_volume(self, ticker: str, market: str = 'premarket', count: int = 1000) -> Dict:
        """Calculate cumulative volume from session start to now.
        
        Args:
            ticker: Stock ticker symbol
            market: Session start point (premarket, market, after_hours)
            count: Maximum number of candles to retrieve
            
        Returns:
            Dictionary with cumulative volume and metadata
        """
        try:
            now_utc = datetime.now(timezone.utc)
            now_et = now_utc.astimezone(self.et_tz)
            current_session = self.get_current_session(now_et)
            session_start_ts = self.get_session_start_timestamp(now_et, market)
            
            # Get session start time for display (same logic as timestamp calculation)
            from datetime import timedelta
            hour, minute = self.get_session_start_times()[market]
            session_start_et = now_et.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if now_et < session_start_et:
                session_start_et = session_start_et - timedelta(days=1)
            
            candles = self.fetch_candles(ticker, count)
            
            if not candles:
                return {
                    'ticker': ticker,
                    'error': 'No candles returned. Ticker may not be tracked.',
                    'status': 'error'
                }
            
            # Sort candles by timestamp to ensure correct first/last time tracking
            candles.sort(key=lambda c: c.get('timestamp', 0))
            
            cumulative_volume = 0
            candles_included = 0
            first_candle_time: Optional[datetime] = None
            last_candle_time: Optional[datetime] = None
            
            for candle in candles:
                timestamp = candle.get('timestamp')
                volume = candle.get('volume', 0)
                
                if timestamp is None:
                    continue
                
                # Only include candles from session start onwards
                if timestamp >= session_start_ts:
                    cumulative_volume += int(volume)
                    candles_included += 1
                    
                    # Track first and last candle times
                    candle_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    candle_et = candle_dt.astimezone(self.et_tz)
                    
                    if first_candle_time is None or candle_et < first_candle_time:
                        first_candle_time = candle_et
                    if last_candle_time is None or candle_et > last_candle_time:
                        last_candle_time = candle_et
            
            if candles_included == 0:
                return {
                    'ticker': ticker,
                    'market': market,
                    'cumulative_volume': 0,
                    'candles_included': 0,
                    'start_time': session_start_et.strftime('%Y-%m-%d %H:%M:%S ET'),
                    'last_candle_time': None,
                    'current_session': current_session,
                    'message': f'No candles found from {session_start_et.strftime("%H:%M")} ET. Session may not have started yet.',
                    'status': 'success'
                }
            
            return {
                'ticker': ticker,
                'market': market,
                'cumulative_volume': cumulative_volume,
                'candles_included': candles_included,
                'start_time': first_candle_time.strftime('%Y-%m-%d %H:%M:%S ET'),
                'last_candle_time': last_candle_time.strftime('%Y-%m-%d %H:%M:%S ET'),
                'current_session': current_session,
                'current_time_et': now_et.strftime('%Y-%m-%d %H:%M:%S ET'),
                'status': 'success'
            }
            
        except Exception as e:
            return {
                'ticker': ticker,
                'error': str(e),
                'status': 'error'
            }


def main():
    parser = argparse.ArgumentParser(
        description='Calculate cumulative volume from session start to current time.',
        epilog='Example: python cumulative_volume_from_premarket.py AAPL --host http://localhost:8765 --market premarket'
    )
    parser.add_argument(
        'ticker',
        help='Stock ticker symbol (e.g., AAPL, MSFT)'
    )
    parser.add_argument(
        '--host',
        default='http://localhost:8765',
        help='REST API host URL (default: http://localhost:8765)'
    )
    parser.add_argument(
        '--market',
        choices=['premarket', 'market', 'after_hours'],
        default='premarket',
        help='Session start point: premarket (4:00 AM ET), market (9:30 AM ET), after_hours (4:00 PM ET). Default: premarket'
    )
    parser.add_argument(
        '--count',
        type=int,
        default=1000,
        help='Maximum number of candles to retrieve (default: 1000)'
    )
    
    args = parser.parse_args()
    
    try:
        calculator = CumulativeVolumeCalculator(host=args.host)
        result = calculator.calculate_cumulative_volume(args.ticker, args.market, args.count)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {
            'ticker': args.ticker,
            'error': str(e),
            'status': 'error'
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
