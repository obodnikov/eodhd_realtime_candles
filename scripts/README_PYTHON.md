# Python Scripts for EODHD Real-Time Candles

Collection of Python utility scripts for managing and analyzing EODHD data.

---

## Scripts Overview

1. **manage_tickers.py** - Smart ticker management with automatic capacity handling
2. **premarket_volume.py** - Premarket volume calculator using EODHD API

---

# 1. Smart Ticker Management Script

**File**: `manage_tickers.py`

Intelligently adds tickers to EODHD monitoring with automatic capacity management. Handles the 50-ticker limit by removing least-recently-used tickers when needed.

## Features

- **Automatic deduplication**: Removes duplicate tickers from input
- **50-ticker limit enforcement**: Automatically limits to first 50 unique tickers
- **Smart capacity management**: Removes oldest/never-requested tickers when space needed
- **Dry-run mode**: Preview changes before executing
- **Force flag**: Requires confirmation for ticker removal
- **Flexible output**: Human-readable or JSON format
- **Environment-based config**: Reads API key and URL from .env

## Prerequisites

- Python 3.6+
- `requests` library
- `python-dotenv` library
- Running EODHD Real-Time Candles API
- Valid API_KEY configured

## Installation

Dependencies are already in `requirements.txt`:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
# Add a few tickers (simple case)
python scripts/manage_tickers.py AAPL MSFT GOOGL

# Add many tickers (will limit to 50 unique)
python scripts/manage_tickers.py AAPL MSFT GOOGL ... (60 tickers)
```

### With Flags

```bash
# Preview changes without executing
python scripts/manage_tickers.py --dry-run AAPL MSFT ... (55 tickers)

# Force removal of old tickers when capacity reached
python scripts/manage_tickers.py --force AAPL MSFT ... (55 tickers)

# JSON output for automation
python scripts/manage_tickers.py --json AAPL MSFT GOOGL

# Custom API endpoint
python scripts/manage_tickers.py --api-url http://localhost:8765 AAPL MSFT
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `tickers` | List of ticker symbols (required) |
| `--force` | Allow removal of old tickers to make space |
| `--dry-run` | Preview changes without executing |
| `--json` | Output JSON format (default: human-readable) |
| `--api-url` | API endpoint URL (default: from .env or localhost:8765) |
| `--api-key` | API key (default: from .env) |

## Output Examples

### Human-Readable Output (Default)

```
Smart Ticker Management
==================================================

Input Analysis:
  - Requested: 55 tickers
  - Unique: 48 tickers (7 duplicates removed)
  - Limited to: 48 tickers

Operations:
  ✓ Already tracked: 3 tickers
  + To add: 45 tickers
  - To remove: 40 tickers

ERROR: Need to remove 40 tickers. Use --force to proceed.

Tickers to be removed (oldest first):
  1. OLD1 (last request: never)
  2. OLD2 (last request: 2025-01-01T10:00:00Z)
  ...
```

### JSON Output (--json flag)

```json
{
  "status": "success",
  "dry_run": false,
  "summary": {
    "requested": 55,
    "unique": 48,
    "limited_to": 48,
    "already_tracked": 3,
    "to_add": 45,
    "to_remove": 40,
    "added": 45,
    "removed": 40
  },
  "details": {
    "already_tracked": ["AAPL", "MSFT", "GOOGL"],
    "to_add": ["TICK1", "TICK2", ...],
    "to_remove": [
      {"ticker": "OLD1", "last_request": null, "reason": "never_requested"},
      {"ticker": "OLD2", "last_request": "2025-01-01T10:00:00Z", "reason": "oldest"}
    ],
    "duplicates_removed": ["AAPL", "MSFT"]
  }
}
```

## Behavior Details

### Duplicate Handling

1. Input tickers are deduplicated (preserves first occurrence)
2. Deduplication happens BEFORE limiting to 50
3. Duplicates are reported in output

**Example**:
```bash
# Input: AAPL MSFT AAPL GOOGL MSFT (5 tickers, 3 unique)
# Result: [AAPL, MSFT, GOOGL] (3 unique tickers added)
```

### Capacity Management

When system has 50 tickers and you add more:

1. Script identifies tickers already tracked (skips these)
2. Calculates how many new tickers to add
3. If insufficient space, identifies tickers to remove:
   - Priority 1: Tickers never requested (`last_candle_request_at` is NULL)
   - Priority 2: Tickers with oldest `last_candle_request_at` timestamp
4. Requires `--force` flag to proceed with removal
5. Removes old tickers, then adds new ones

### Exit Codes

| Code | Meaning |
|------|----------|
| 0 | Success |
| 1 | Operation failed (e.g., need --force flag) |
| 2 | Cannot connect to API |
| 3 | Authentication failed |
| 4 | API request failed or unexpected error |

## Configuration

### Environment Variables

Create or update `.env` file in project root:

```bash
API_KEY=your_api_key_here
API_URL=http://localhost:8765  # Optional, defaults to localhost:8765
```

### Override via Command Line

```bash
python scripts/manage_tickers.py --api-key YOUR_KEY --api-url http://server:8765 AAPL MSFT
```

## Use Cases

### 1. Bulk Ticker Addition

```bash
# Add 100 tickers from a list (script limits to 50)
python scripts/manage_tickers.py AAPL MSFT GOOGL ... (100 tickers)
```

### 2. Automated Ticker Rotation

```bash
# Rotate tickers daily (removes old, adds new)
python scripts/manage_tickers.py --force --json $(cat new_tickers.txt) > result.json
```

### 3. Preview Before Execution

```bash
# Check what would happen
python scripts/manage_tickers.py --dry-run AAPL MSFT ... (60 tickers)

# If satisfied, execute
python scripts/manage_tickers.py --force AAPL MSFT ... (60 tickers)
```

### 4. Integration with Other Tools

```bash
# Use with n8n or other automation
curl -X POST https://webhook.site/... \
  -H "Content-Type: application/json" \
  -d "$(python scripts/manage_tickers.py --json AAPL MSFT GOOGL)"
```

## Error Handling

### Common Errors

**"API_KEY not found"**
- Set API_KEY in .env file or use --api-key flag

**"Cannot connect to API"**
- Verify API is running (check `docker ps` or service status)
- Check API_URL is correct

**"Authentication failed"**
- Verify API_KEY matches the one configured in API service

**"Need to remove X tickers. Use --force to proceed."**
- System is at capacity, use --force flag to allow removal
- Use --dry-run first to preview what will be removed

## Testing

```bash
# Test with dry-run (no changes made)
python scripts/manage_tickers.py --dry-run AAPL MSFT GOOGL

# Test JSON output
python scripts/manage_tickers.py --json --dry-run AAPL MSFT | jq '.status'

# Test duplicate handling
python scripts/manage_tickers.py --dry-run AAPL MSFT AAPL GOOGL MSFT
```

---

# 2. Premarket Volume Calculator

**File**: `premarket_volume.py`

A Python script that calculates the average premarket trading volume for a given stock ticker using the EODHD Intraday Historical Data API.

## Features

- **1-minute interval support**: Uses EODHD's 1-minute data (120-day history)
- **Premarket focus**: Only counts volume during 4:00 AM - 9:30 AM ET
- **Environment-based API key**: Secure API key management via environment variables
- **Command-line interface**: Simple CLI for quick calculations
- **JSON output**: Structured response format
- **Error handling**: Comprehensive error handling for API issues

## Prerequisites

- Python 3.6+
- `requests` library
- EODHD API key

## Installation

1. **Install dependencies**:
```bash
pip install requests
```

2. **Set environment variable**:
```bash
export EODHD_API_KEY="your_api_key_here"
```

Or add to your `.bashrc`/`.zshrc`:
```bash
echo 'export EODHD_API_KEY="your_api_key_here"' >> ~/.bashrc
source ~/.bashrc
```

## Usage

### Command Line

```bash
python premarket_volume.py <TICKER> [INTERVAL]
```

**Parameters**:
- `TICKER` (required): Stock symbol with exchange (e.g., "AAPL.US", "MSFT.US")
- `INTERVAL` (optional): Data interval, defaults to "1m"
  - Supported: "1m", "5m", "1h"

### Examples

```bash
# Basic usage with 1-minute intervals
python premarket_volume.py AAPL.US

# With specific interval
python premarket_volume.py AAPL.US 5m

# Different stock
python premarket_volume.py MSFT.US 1m
```

### Python Module Usage

```python
from premarket_volume import PremarketVolumeCalculator

# Initialize calculator
calculator = PremarketVolumeCalculator()

# Calculate premarket volume
result = calculator.calculate_premarket_volume("AAPL.US", "1m")
print(result)
```

## Output Format

### Success Response
```json
{
  "ticker": "AAPL.US",
  "average_premarket_volume": 12345678,
  "trading_days_included": 85,
  "date_range": "2025-09-15 to 2026-01-08",
  "interval": "1m",
  "status": "success"
}
```

### Error Response
```json
{
  "ticker": "INVALID.US",
  "error": "No data returned or invalid ticker",
  "status": "error"
}
```

## EODHD API Details

### Data Availability
- **1-minute data**: Last 120 days
- **5-minute data**: Last 600 days  
- **1-hour data**: Last 7200 days

### Ticker Format
- US stocks: `SYMBOL.US` (e.g., `AAPL.US`, `MSFT.US`)
- Other exchanges: `SYMBOL.EXCHANGE` (e.g., `AAPL.MX`)

### API Consumption
- 5 API calls per request
- Check your plan limits at [EODHD pricing](https://eodhd.com/pricing)

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EODHD_API_KEY` | Yes | Your EODHD API key |

### Premarket Hours
The script uses **4:00 AM - 9:30 AM ET** as premarket hours. To modify:

```python
def is_premarket_time(self, dt: datetime) -> bool:
    et_hour = dt.hour - 5  # EST offset
    et_time_minutes = et_hour * 60 + dt.minute
    
    # Modify these values:
    # Current: 4:00 AM (240 min) to 9:30 AM (570 min) ET
    return 240 <= et_time_minutes < 570
```

## Error Handling

### Common Errors

**"EODHD_API_KEY environment variable is required"**
- Set the environment variable with your API key

**"No data returned or invalid ticker"**
- Check ticker format (must include exchange: `.US`, `.MX`, etc.)
- Verify ticker exists and is actively traded

**"API request failed"**
- Check internet connection
- Verify API key is valid
- Check if you've exceeded API limits

**"No premarket data found"**
- Stock may not have premarket trading
- Try different interval or date range

## Class Structure

### PremarketVolumeCalculator

**Methods**:
- `__init__()`: Initialize with API key from environment
- `get_timestamps(days_back=120)`: Generate Unix timestamps for date range
- `fetch_intraday_data(ticker, interval)`: Fetch data from EODHD API
- `is_premarket_time(dt)`: Check if datetime is in premarket hours
- `calculate_premarket_volume(ticker, interval)`: Main calculation method

## Customization Examples

### Different Time Range
```python
# Get last 60 days instead of 120
from_unix, to_unix = calculator.get_timestamps(days_back=60)
```

### Custom Time Zone Handling
```python
import pytz

def is_premarket_time_dst(self, dt: datetime) -> bool:
    """Handle DST properly"""
    et_tz = pytz.timezone('US/Eastern')
    et_dt = dt.astimezone(et_tz)
    hour_minute = et_dt.hour * 60 + et_dt.minute
    return 240 <= hour_minute < 570  # 4:00 AM - 9:30 AM ET
```

### Batch Processing
```python
tickers = ["AAPL.US", "MSFT.US", "GOOGL.US"]
results = []

for ticker in tickers:
    result = calculator.calculate_premarket_volume(ticker)
    results.append(result)
    
print(json.dumps(results, indent=2))
```

## Testing

### Test with Demo API Key
EODHD provides a demo API key for testing:

```bash
export EODHD_API_KEY="demo"
python premarket_volume.py AAPL.US
```

### Validate Output
```bash
# Should return success status
python premarket_volume.py AAPL.US | jq '.status'

# Should return "success" for valid ticker
# Should return "error" for invalid ticker
```

## Performance Notes

- **1-minute intervals**: More precise but limited to 120 days
- **5-minute intervals**: Good balance of precision and history (600 days)
- **API limits**: Consider caching results for repeated queries
- **Timezone**: Script uses approximate EST conversion (doesn't handle DST)

## License

This script is provided as-is for educational and commercial use.
