#!/usr/bin/env python3
"""
Smart Ticker Management Script
Intelligently adds tickers to EODHD monitoring with automatic capacity management.
"""

import os
import sys
import json
import re
import argparse
import requests
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

# US stock ticker pattern: 1-6 alphanumeric characters
TICKER_PATTERN = re.compile(r'^[A-Z0-9]{1,6}$')


class TickerManager:
    """Manages ticker operations via REST API."""
    
    MAX_TICKERS = 50
    
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.headers = {'X-API-Key': api_key}
    
    def get_current_tickers(self) -> List[Dict]:
        """Fetch currently tracked tickers."""
        response = requests.get(f"{self.api_url}/tickers", headers=self.headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('tickers', [])
    
    def remove_tickers(self, tickers: List[str]) -> Dict:
        """Remove multiple tickers."""
        response = requests.delete(
            f"{self.api_url}/tickers",
            headers=self.headers,
            json={'tickers': tickers},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return {'removed': data.get('removed', [])}
    
    def add_tickers(self, tickers: List[str]) -> Dict:
        """Add multiple tickers."""
        response = requests.post(
            f"{self.api_url}/tickers",
            headers=self.headers,
            json={'tickers': tickers},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return {'added': data.get('added', [])}
    
    def execute(self, input_tickers: List[str], force: bool, dry_run: bool) -> Dict:
        """Execute ticker management with smart capacity handling."""
        # Deduplicate input (preserve order)
        unique_input = list(dict.fromkeys(input_tickers))
        duplicates = [t for t in input_tickers if input_tickers.count(t) > 1]
        duplicates = list(dict.fromkeys(duplicates))
        
        # Limit to 50
        limited_input = unique_input[:self.MAX_TICKERS]
        
        # Get current state
        current = self.get_current_tickers()
        current_symbols = {t['symbol'] for t in current}
        
        # Determine operations
        already_tracked = [t for t in limited_input if t in current_symbols]
        to_add = [t for t in limited_input if t not in current_symbols]
        
        available_slots = self.MAX_TICKERS - len(current)
        need_to_remove = max(0, len(to_add) - available_slots)
        
        # Sort by last_candle_request_at (NULL first, then oldest)
        # ISO datetime strings sort correctly lexicographically
        def sort_key(ticker):
            last_req = ticker.get('last_candle_request_at')
            return (last_req is not None, last_req or '')
        
        sorted_current = sorted(current, key=sort_key)
        to_remove = sorted_current[:need_to_remove]
        
        result = {
            'status': 'success' if not need_to_remove or force or dry_run else 'error',
            'dry_run': dry_run,
            'summary': {
                'requested': len(input_tickers),
                'unique': len(unique_input),
                'limited_to': len(limited_input),
                'already_tracked': len(already_tracked),
                'to_add': len(to_add),
                'to_remove': need_to_remove,
                'added': 0,
                'removed': 0
            },
            'details': {
                'already_tracked': already_tracked,
                'to_add': to_add,
                'to_remove': [
                    {
                        'ticker': t['symbol'],
                        'last_request': t.get('last_candle_request_at'),
                        'reason': 'never_requested' if not t.get('last_candle_request_at') else 'oldest'
                    }
                    for t in to_remove
                ],
                'duplicates_removed': duplicates
            }
        }
        
        # Check if removal needed without force
        if need_to_remove > 0 and not force and not dry_run:
            result['error'] = f'Need to remove {need_to_remove} tickers. Use --force to proceed.'
            return result
        
        # Execute operations
        if not dry_run:
            if to_remove:
                remove_result = self.remove_tickers([t['symbol'] for t in to_remove])
                result['summary']['removed'] = len(remove_result.get('removed', []))
            
            if to_add:
                add_result = self.add_tickers(to_add)
                result['summary']['added'] = len(add_result.get('added', []))
        else:
            result['summary']['added'] = len(to_add)
            result['summary']['removed'] = need_to_remove
        
        return result


def format_human_readable(result: Dict) -> str:
    """Format result as human-readable text."""
    lines = []
    
    if result['dry_run']:
        lines.append("[DRY RUN MODE - No changes will be made]\n")
    
    lines.append("Smart Ticker Management")
    lines.append("=" * 50)
    lines.append("")
    
    s = result['summary']
    lines.append("Input Analysis:")
    lines.append(f"  - Requested: {s['requested']} tickers")
    lines.append(f"  - Unique: {s['unique']} tickers ({s['requested'] - s['unique']} duplicates removed)")
    lines.append(f"  - Limited to: {s['limited_to']} tickers")
    lines.append("")
    
    lines.append("Operations:")
    lines.append(f"  [OK] Already tracked: {s['already_tracked']} tickers")
    lines.append(f"  [+] To add: {s['to_add']} tickers")
    lines.append(f"  [-] To remove: {s['to_remove']} tickers")
    lines.append("")
    
    if result['status'] == 'error':
        lines.append(f"ERROR: {result.get('error', 'Unknown error')}")
        if result['details']['to_remove']:
            lines.append("\nTickers to be removed (oldest first):")
            for i, t in enumerate(result['details']['to_remove'][:10], 1):
                last_req = t['last_request'] or 'never'
                lines.append(f"  {i}. {t['ticker']} (last request: {last_req})")
            if len(result['details']['to_remove']) > 10:
                lines.append(f"  ... and {len(result['details']['to_remove']) - 10} more")
    else:
        if result['dry_run']:
            lines.append("Would perform:")
            lines.append(f"  - Remove: {s['to_remove']} tickers")
            lines.append(f"  - Add: {s['to_add']} tickers")
        else:
            lines.append("Completed:")
            lines.append(f"  - Removed: {s['removed']} tickers")
            lines.append(f"  - Added: {s['added']} tickers")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Smart ticker management with automatic capacity handling',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python manage_tickers.py AAPL MSFT GOOGL
  python manage_tickers.py --dry-run AAPL MSFT ... (55 tickers)
  python manage_tickers.py --force AAPL MSFT ... (55 tickers)
  python manage_tickers.py --json AAPL MSFT GOOGL
        """
    )
    
    parser.add_argument('tickers', nargs='+', help='Ticker symbols to add')
    parser.add_argument('--force', action='store_true', help='Allow removal of old tickers')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without executing')
    parser.add_argument('--json', action='store_true', help='Output JSON format')
    parser.add_argument('--api-url', help='API endpoint URL (default: from .env or localhost:8765)')
    parser.add_argument('--api-key', help='API key (default: from .env)')
    
    args = parser.parse_args()
    
    # Load .env file
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
    
    # Get configuration
    api_url = args.api_url or os.getenv('API_URL', 'http://localhost:8765')
    api_key = args.api_key or os.getenv('API_KEY', '')
    
    if not api_key:
        print("ERROR: API_KEY not found. Set in .env or use --api-key", file=sys.stderr)
        sys.exit(3)
    
    # Normalize and validate tickers
    tickers = []
    invalid = []
    for t in args.tickers:
        t = t.upper().strip()
        if not t:
            continue
        # Validate US stock ticker format: 1-6 alphanumeric
        if TICKER_PATTERN.match(t):
            tickers.append(t)
        else:
            invalid.append(t)
    
    if invalid and not args.json:
        print(f"Warning: Skipping invalid tickers: {', '.join(invalid)}", file=sys.stderr)
    
    try:
        manager = TickerManager(api_url, api_key)
        result = manager.execute(tickers, args.force, args.dry_run)
        
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(format_human_readable(result))
        
        sys.exit(0 if result['status'] == 'success' else 1)
        
    except requests.exceptions.ConnectionError:
        error = {'error': 'Cannot connect to API', 'api_url': api_url}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: Cannot connect to API at {api_url}", file=sys.stderr)
        sys.exit(2)
    
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            error = {'error': 'Authentication failed', 'detail': 'Invalid API key'}
            if args.json:
                print(json.dumps(error, indent=2))
            else:
                print("ERROR: Authentication failed. Check API_KEY.", file=sys.stderr)
            sys.exit(3)
        else:
            error = {'error': 'API request failed', 'detail': str(e)}
            if args.json:
                print(json.dumps(error, indent=2))
            else:
                print(f"ERROR: API request failed: {e}", file=sys.stderr)
            sys.exit(4)
    
    except Exception as e:
        error = {'error': 'Unexpected error', 'detail': str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
