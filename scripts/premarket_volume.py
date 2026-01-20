#!/usr/bin/env python3
"""
Premarket Volume Calculator using EODHD API
Calculates average premarket trading volume for a stock ticker.
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional


class PremarketVolumeCalculator:
    def __init__(self):
        self.api_key = os.getenv('EODHD_API_KEY')
        if not self.api_key:
            raise ValueError("EODHD_API_KEY environment variable is required")
        
        self.base_url = "https://eodhd.com/api/intraday"
        self.valid_intervals = ["1m", "5m", "1h"]
    
    def get_timestamps(self, days_back: int = 120) -> tuple:
        """Generate Unix timestamps for the date range."""
        now = datetime.now()
        from_date = now - timedelta(days=days_back)
        return int(from_date.timestamp()), int(now.timestamp())
    
    def fetch_intraday_data(self, ticker: str, interval: str = "1m") -> List[Dict]:
        """Fetch intraday data from EODHD API."""
        from_unix, to_unix = self.get_timestamps()
        
        params = {
            'api_token': self.api_key,
            'fmt': 'json',
            'interval': interval,
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
        """Check if datetime falls within premarket hours (4:00-9:30 AM ET)."""
        if dt.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")
        
        et = dt.astimezone(ZoneInfo("America/New_York"))
        minutes = et.hour * 60 + et.minute
        
        return 240 <= minutes < 570
    
    def calculate_premarket_volume(self, ticker: str, interval: str = "1m") -> Dict:
        """Calculate average premarket volume for the ticker."""
        # Validate interval
        if interval not in self.valid_intervals:
            return {
                'ticker': ticker,
                'error': f'Invalid interval. Supported intervals: {", ".join(self.valid_intervals)}',
                'status': 'error'
            }
        
        try:
            data = self.fetch_intraday_data(ticker, interval)
            
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
                
                dt = datetime.fromisoformat(item['datetime'].replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                
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
                    'error': 'No premarket data found',
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
                'interval': interval,
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
        print("Usage: python premarket_volume.py <TICKER> [INTERVAL]")
        print("Example: python premarket_volume.py AAPL.US 1m")
        sys.exit(1)
    
    ticker = sys.argv[1]
    interval = sys.argv[2] if len(sys.argv) > 2 else "1m"
    
    try:
        calculator = PremarketVolumeCalculator()
        result = calculator.calculate_premarket_volume(ticker, interval)
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
