#!/usr/bin/env python3
"""
Premarket Volume Calculator using EODHD API
Calculates average premarket trading volume for a stock ticker.
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9 or systems without tzdata
    raise ImportError(
        "zoneinfo not available. Please install tzdata: pip install tzdata"
    )


class PremarketVolumeCalculator:
    def __init__(self):
        self.api_key = os.getenv('EODHD_API_KEY')
        if not self.api_key:
            raise ValueError("EODHD_API_KEY environment variable is required")
        
        self.base_url = "https://eodhd.com/api/intraday"
        # Only 1m interval provides premarket data (4:00-9:30 AM ET)
        self.interval = "1m"
        self.days_back = 90  # Maximum historical data for 1m interval
    
    def get_timestamps(self) -> tuple:
        """Generate Unix timestamps for the date range (90 days)."""
        now = datetime.now()
        from_date = now - timedelta(days=self.days_back)
        return int(from_date.timestamp()), int(now.timestamp())
    
    def fetch_intraday_data(self, ticker: str) -> List[Dict]:
        """Fetch intraday data from EODHD API."""
        from_unix, to_unix = self.get_timestamps()
        
        params = {
            'api_token': self.api_key,
            'fmt': 'json',
            'interval': self.interval,
            'from': from_unix,
            'to': to_unix
        }
        
        url = f"{self.base_url}/{ticker}"
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def is_premarket_time(self, dt: datetime) -> bool:
        """Check if datetime falls within premarket hours (4:00-9:30 AM ET).
        
        Uses America/New_York timezone to properly handle DST transitions.
        """
        if dt.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")
        
        # Convert to ET (handles both EST and EDT automatically)
        et_tz = ZoneInfo("America/New_York")
        et = dt.astimezone(et_tz)
        
        minutes = et.hour * 60 + et.minute
        
        # 4:00 AM = 240 minutes, 9:30 AM = 570 minutes
        return 240 <= minutes < 570
    
    def calculate_premarket_volume(self, ticker: str) -> Dict:
        """Calculate average premarket volume for the ticker using 1m interval."""
        try:
            data = self.fetch_intraday_data(ticker)
            
            if not isinstance(data, list) or len(data) == 0:
                return {
                    'ticker': ticker,
                    'error': 'No data returned or invalid ticker',
                    'status': 'error'
                }
            
            daily_volumes = {}

            total_items = 0
            
            for item in data:
                if not item.get('datetime') or not item.get('volume'):
                    continue
                
                # Parse datetime - API returns UTC time without timezone indicator
                dt = datetime.fromisoformat(item['datetime'])
                # Always set to UTC since API gmtoffset is 0
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                
                if self.is_premarket_time(dt):
                    date_key = dt.date().isoformat()
                    volume = int(item['volume'])
                    
                    if date_key not in daily_volumes:
                        daily_volumes[date_key] = 0
                    daily_volumes[date_key] += volume
                    total_items += 1
            
            if not daily_volumes:
                return {
                    'ticker': ticker,
                    'error': 'No premarket data found. Note: EODHD API only provides premarket data (4:00-9:30 AM ET) for 1-minute intervals. Other intervals (5m, 1h) start at market open (9:30 AM ET).',
                    'status': 'error'
                }
            
            volumes = list(daily_volumes.values())
            trading_days = len(volumes)
            average_volume = round(sum(volumes) / trading_days)
            average_volume_interval = round(sum(volumes) / total_items)
            
            dates = sorted(daily_volumes.keys())
            date_range = f"{dates[0]} to {dates[-1]}"
            
            return {
                'ticker': ticker,
                'average_premarket_volume': average_volume,
                'trading_days_included': trading_days,
                'date_range': date_range,
                'average_interval_volume': average_volume_interval,
                'status': 'success'
            }
            
        except Exception as e:
            return {
                'ticker': ticker,
                'error': str(e),
                'status': 'error'
            }


def main():
    if len(sys.argv) < 2:
        print("Usage: python premarket_volume.py <TICKER>")
        print("Example: python premarket_volume.py AAPL.US")
        print("\nNote: Only 1-minute interval data includes premarket hours (4:00-9:30 AM ET).")
        print("      Retrieves up to 90 days of historical data.")
        sys.exit(1)
    
    ticker = sys.argv[1]
    
    try:
        calculator = PremarketVolumeCalculator()
        result = calculator.calculate_premarket_volume(ticker)
        print(json.dumps(result, indent=2))
    except Exception as e:
        error_result = {
            'ticker': ticker,
            'error': str(e),
            'status': 'error'
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
