# Python Scripts for EODHD Real-Time Candles

Collection of Python utility scripts for managing and analyzing EODHD data.

---

## Scripts Overview

1. **manage_tickers.py** - Smart ticker management with automatic capacity handling
2. **premarket_volume.py** - Premarket volume calculator using EODHD API
3. **premarket_pivots.py** - Premarket pivot point calculator with support/resistance levels
4. **cumulative_volume_from_premarket.py** - Cumulative volume from 4:00 AM ET to current time
4. **cumulative_volume_from_premarket.py** - Cumulative volume from 4:00 AM ET to current time

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

- **1-minute interval only**: Uses EODHD's 1-minute data (up to 90 days of history)
- **Premarket focus**: Only counts volume during 4:00 AM - 9:30 AM ET
- **Maximum data retrieval**: Fetches up to 90 days of premarket data
- **Environment-based API key**: Secure API key management via environment variables
- **Command-line interface**: Simple CLI for quick calculations
- **JSON output**: Structured response format
- **Error handling**: Comprehensive error handling for API issues

## Important Note

**EODHD API Limitation**: Only 1-minute interval data includes premarket hours (4:00-9:30 AM ET). Other intervals (5m, 1h) start at market open (9:30 AM ET) and do not contain premarket data.

## Prerequisites

- Python 3.9+ (for zoneinfo support)
- `requests` library
- `tzdata` library (for Windows timezone support)
- EODHD API key

## Installation

1. **Install dependencies**:
```bash
pip install requests tzdata
```

Or install from requirements.txt:
```bash
pip install -r requirements.txt
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
python premarket_volume.py <TICKER>
```

**Parameters**:
- `TICKER` (required): Stock symbol with exchange (e.g., "AAPL.US", "MSFT.US")

**Note**: The script automatically uses 1-minute intervals and retrieves up to 90 days of data.

### Examples

```bash
# Calculate premarket volume for Apple
python premarket_volume.py AAPL.US

# Calculate for Microsoft
python premarket_volume.py MSFT.US

# Calculate for Tesla
python premarket_volume.py TSLA.US
```

### Python Module Usage

```python
from premarket_volume import PremarketVolumeCalculator

# Initialize calculator
calculator = PremarketVolumeCalculator()

# Calculate premarket volume (automatically uses 1m interval, 90 days)
result = calculator.calculate_premarket_volume("AAPL.US")
print(result)
```

## Output Format

### Success Response
```json
{
  "ticker": "AAPL.US",
  "average_premarket_volume": 590837,
  "trading_days_included": 18,
  "date_range": "2025-12-22 to 2026-01-16",
  "average_interval_volume": 1837,
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
- **1-minute data**: Up to 120 days of historical data
- **Premarket data**: Only available in 1-minute intervals
- **Script retrieves**: Up to 90 days to maximize premarket data points

### Premarket Data Limitation
**Important**: EODHD API only provides premarket hours (4:00-9:30 AM ET) in 1-minute interval data. Other intervals (5m, 1h) start at market open (9:30 AM ET) and do not include premarket trading.

### Ticker Format
- US stocks: `SYMBOL.US` (e.g., `AAPL.US`, `MSFT.US`)
- Other exchanges: `SYMBOL.EXCHANGE` (e.g., `AAPL.MX`)

### API Consumption
- Single API call per request
- Check your plan limits at [EODHD pricing](https://eodhd.com/pricing)

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EODHD_API_KEY` | Yes | Your EODHD API key |

### Premarket Hours
The script uses **4:00 AM - 9:30 AM ET** as premarket hours with proper DST handling via `zoneinfo.ZoneInfo('America/New_York')`. This automatically adjusts for:
- **EST (Eastern Standard Time)**: UTC-5 (November - March)
- **EDT (Eastern Daylight Time)**: UTC-4 (March - November)

No manual adjustment needed for DST transitions.

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
- Verify ticker is correct and actively traded
- Note: Only 1-minute data includes premarket hours

## Class Structure

### PremarketVolumeCalculator

**Methods**:
- `__init__()`: Initialize with API key from environment (sets 1m interval, 90 days)
- `get_timestamps()`: Generate Unix timestamps for 90-day range
- `fetch_intraday_data(ticker)`: Fetch 1m data from EODHD API
- `is_premarket_time(dt)`: Check if datetime is in premarket hours (4:00-9:30 AM ET)
- `calculate_premarket_volume(ticker)`: Main calculation method

## Customization Examples

### Different Time Range
```python
# Modify days_back in __init__ method
class PremarketVolumeCalculator:
    def __init__(self):
        # ...
        self.days_back = 60  # Change from 90 to 60 days
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

### Run Unit Tests
```bash
# Run all tests
python -m pytest tests/test_premarket_volume.py -v

# Or using unittest
python tests/test_premarket_volume.py
```

### Test Coverage
The test suite covers:
- Premarket time detection (EST and EDT)
- DST transition handling
- Volume calculation logic
- API error handling
- Edge cases (missing data, invalid tickers)

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

- **1-minute intervals**: Only interval with premarket data (4:00-9:30 AM ET)
- **90-day window**: Maximizes premarket data points while staying within API limits
- **API limits**: Consider caching results for repeated queries
- **Timezone**: Script uses `zoneinfo.ZoneInfo('America/New_York')` for accurate DST handling

## License

This script is provided as-is for educational and commercial use.


---

# 3. Premarket Pivot Points Calculator

**File**: `premarket_pivots.py`

A comprehensive trading analysis script that calculates pivot points (support and resistance levels) using both historical and premarket data. Combines data from Yahoo Finance and your EODHD Real-Time Candles API for complete market analysis.

## Features

- **Dual data sources**: Yahoo Finance (historical/premarket) + EODHD API (real-time)
- **Pivot point methods**: Classic and Fibonacci pivot calculations
- **Premarket analysis**: Optional premarket OHLC data integration
- **Real-time prices**: Current prices from EODHD WebSocket feed
- **Smart fallbacks**: Graceful handling of missing data
- **Warning system**: Clear messages for tickers not tracked in EODHD
- **Formatted output**: Human-readable tables with tabulate or pandas

## Prerequisites

- Python 3.9+
- `yfinance` library
- `pandas` library
- `requests` library
- `tabulate` library (optional, for better formatting)
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
# Calculate pivot points for tickers (using previous day data)
python scripts/premarket_pivots.py --tickers AAPL MSFT TSLA

# Use premarket data for pivot calculations
python scripts/premarket_pivots.py --premarket --tickers AAPL MSFT

# Use Fibonacci pivots instead of classic
python scripts/premarket_pivots.py --premarket --method fib --tickers AAPL

# Use New York timezone for date calculations
python scripts/premarket_pivots.py --premarket --ny-time --tickers AAPL
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `--tickers` | List of ticker symbols (required) |
| `--premarket` | Use premarket data for pivot calculations (optional) |
| `--method` | Pivot method: `classic` or `fib` (default: classic) |
| `--ny-time` | Use NY timezone for date calculations (optional) |

## Output Format

### Standard Output

```
ticker interval current_price  candle_count  prev_high  prev_low  prev_close  avg_3m_volume  pm_high  pm_low  pm_close  pm_volume      P     R1     S1     R2     S2    R3     S3
AAPL   5m       252.30         342           251.88     251.01    251.62      15234567.0     253.88   251.01  252.30    0.0        252.26 253.51 251.01 254.76 249.76 256.01 248.51
TSLA   5m       425.90         289           424.87     421.72    424.00      8765432.0      437.50   421.72  426.00    0.0        428.41 435.10 421.72 441.79 415.03 448.48 408.34
```

### Column Descriptions

**Ticker Info:**
- `ticker`: Stock symbol
- `interval`: Candle interval from EODHD (e.g., "5m")
- `current_price`: Real-time price from EODHD API
- `candle_count`: Number of candles received from EODHD

**Previous Day Data (from Yahoo Finance):**
- `prev_high`: Previous session high
- `prev_low`: Previous session low
- `prev_close`: Previous session close
- `avg_3m_volume`: Average volume over 60 days (~3 months)

**Premarket Data (if --premarket flag used):**
- `pm_high`: Premarket high (4:00-9:29 AM ET)
- `pm_low`: Premarket low
- `pm_close`: Premarket close (last price before 9:30 AM)
- `pm_volume`: Premarket volume (may be 0 in free feeds)

**Pivot Levels:**
- `P`: Central pivot point
- `R1`, `R2`, `R3`: Resistance levels (where price may struggle to break above)
- `S1`, `S2`, `S3`: Support levels (where price may find buying interest)

## Pivot Point Formulas

### Classic Pivots (default)

```
P = (High + Low + Close) / 3
R1 = 2*P - Low
S1 = 2*P - High
R2 = P + (High - Low)
S2 = P - (High - Low)
R3 = High + 2*(P - Low)
S3 = Low - 2*(High - P)
```

### Fibonacci Pivots (--method fib)

```
P = (High + Low + Close) / 3
R1 = P + 0.382 * (High - Low)
S1 = P - 0.382 * (High - Low)
R2 = P + 0.618 * (High - Low)
S2 = P - 0.618 * (High - Low)
R3 = P + (High - Low)
S3 = P - (High - Low)
```

## Data Sources

### Yahoo Finance (yfinance)
- **Historical data**: 30 days of daily OHLC
- **Premarket data**: 1-minute bars from 4:00-9:29 AM ET
- **Average volume**: Calculated from 60-day history
- **Delay**: < 3 seconds (near real-time for NASDAQ stocks)

### EODHD API (via NGINX proxy)
- **Real-time prices**: From WebSocket feed (< 50ms latency)
- **Candle counts**: Number of candles received
- **Interval**: Global candle interval setting
- **Endpoint**: `https://n8n.sqowe.com/eodhd/tickers`

## Error Handling

### Missing Tickers Warning

If a ticker is not tracked in EODHD, you'll see:

```
================================================================================
[WARNING] The following tickers are NOT tracked in EODHD system:
  • SNDK

To add these tickers, use:
  python scripts/manage_tickers.py SNDK

Or with force flag to auto-remove old tickers if capacity reached:
  python scripts/manage_tickers.py --force SNDK
================================================================================
```

### Smart Fallbacks

The script handles missing data gracefully:

1. **interval = None**: Uses global config from `/config` endpoint
2. **current_price = None**: Tries latest candle → premarket close → previous close
3. **candle_count = 0**: Shows warning with instructions to add ticker

### API Unavailable

If EODHD API is unavailable:
```
[WARN] n8n unavailable: Connection refused
```

Script continues with Yahoo Finance data only (no real-time prices).

## Use Cases

### 1. Pre-Market Trading Preparation

```bash
# Get premarket pivot levels before market open
python scripts/premarket_pivots.py --premarket --ny-time --tickers AAPL TSLA NVDA
```

**Use for:**
- Identifying key support/resistance levels
- Planning entry/exit points
- Setting stop-loss orders

### 2. Day Trading Setup

```bash
# Get current levels with real-time prices
python scripts/premarket_pivots.py --tickers AAPL MSFT GOOGL
```

**Use for:**
- Monitoring price relative to pivot levels
- Identifying breakout/breakdown opportunities
- Confirming trend direction

### 3. Multi-Ticker Analysis

```bash
# Analyze multiple tickers at once
python scripts/premarket_pivots.py --premarket --tickers AAPL MSFT GOOGL TSLA NVDA AMD AMZN META
```

**Use for:**
- Comparing relative strength across stocks
- Finding best trading opportunities
- Portfolio-wide analysis

### 4. Fibonacci Pivot Strategy

```bash
# Use Fibonacci retracement levels
python scripts/premarket_pivots.py --premarket --method fib --tickers SPY QQQ
```

**Use for:**
- Fibonacci-based trading strategies
- More precise support/resistance levels
- Advanced technical analysis

## Configuration

### Environment Variables

The script uses the EODHD API through NGINX proxy. Configuration is in the script:

```python
N8N_EODHD_API_KEY = "your_api_key_here"
N8N_EODHD_TICKERS_URL = "https://n8n.sqowe.com/eodhd/tickers"
```

### NGINX Proxy

The script accesses your EODHD API via NGINX reverse proxy:
- `https://n8n.sqowe.com/eodhd/*` → `http://172.28.0.200:8765/*`

## Examples

### Example 1: Basic Pivot Analysis

```bash
python scripts/premarket_pivots.py --tickers AAPL
```

**Output:**
```
ticker interval current_price  candle_count  prev_high  prev_low  prev_close  ...  P      R1     S1
AAPL   5m       252.30         342           251.88     251.01    251.62      ...  251.50 252.00 251.00
```

### Example 2: Premarket with Multiple Tickers

```bash
python scripts/premarket_pivots.py --premarket --tickers AAPL TSLA NVDA
```

**Output includes premarket columns:**
```
ticker  ...  pm_high  pm_low  pm_close  pm_volume  P      R1     S1
AAPL    ...  253.88   251.01  252.30    0.0        252.40 253.79 250.01
TSLA    ...  437.50   421.72  426.00    0.0        428.41 435.10 421.72
NVDA    ...  145.20   142.50  144.80    0.0        144.17 145.84 142.50
```

### Example 3: Fibonacci Pivots

```bash
python scripts/premarket_pivots.py --premarket --method fib --tickers SPY
```

**Output uses Fibonacci ratios:**
```
ticker  ...  P      R1     S1     R2     S2
SPY     ...  450.50 451.62 449.38 452.29 448.71
```

## Trading Interpretation

### Bullish Signals
- Price above pivot point (P)
- Breaking above R1 with volume
- Holding above S1 on pullbacks

### Bearish Signals
- Price below pivot point (P)
- Breaking below S1 with volume
- Rejecting at R1 resistance

### Key Levels
- **P (Pivot)**: Trend indicator - above = bullish, below = bearish
- **R1/S1**: First targets for breakouts/breakdowns
- **R2/S2**: Strong resistance/support zones
- **R3/S3**: Extreme levels, rarely reached

## Performance Notes

- **Yahoo Finance**: < 3 seconds delay for premarket data
- **EODHD API**: < 50ms latency for real-time prices
- **Script execution**: ~2-5 seconds for 5-10 tickers
- **API calls**: 1 config call + 1 ticker call + N candle calls (only if needed)

## Troubleshooting

### "No tickers provided"
- Add `--tickers` flag with at least one ticker

### "n8n unavailable"
- Check EODHD API is running: `docker ps`
- Verify NGINX proxy is configured
- Check API_KEY is correct

### "No data returned or invalid ticker"
- Verify ticker format (no exchange suffix needed)
- Check ticker exists and is actively traded
- Try with a known ticker like AAPL

### Missing premarket data
- Premarket data only available during/after premarket hours (4:00-9:30 AM ET)
- Some tickers may not have premarket trading
- Yahoo Finance free feed may have limitations

## Related Scripts

- **manage_tickers.py**: Add/remove tickers from EODHD system
- **premarket_volume.py**: Calculate premarket volume statistics
- **test_yfinance_premarket.py**: Test premarket data fetching
- **test_yfinance_delay.py**: Measure data delay

## License

This script is provided as-is for educational and commercial use.


---

# 4. Cumulative Volume from Premarket Start

**File**: `cumulative_volume_from_premarket.py`

Calculates the cumulative total of all shares traded from 4:00 AM ET through the current moment, including the current incomplete candle. Uses the project's REST API (`GET /candles/{ticker}`) to fetch candle data.

## Features

- **Real-time cumulative volume**: Sum of all volume from 4:00 AM ET to now
- **Includes current candle**: Counts volume from incomplete/in-progress candles
- **Session awareness**: Reports current market session (premarket/market/after_hours/closed)
- **Proper timezone handling**: Uses `zoneinfo` for accurate ET/DST calculations
- **REST API integration**: Uses project's candle API instead of direct EODHD calls

## Prerequisites

- Python 3.9+ (for zoneinfo support)
- `requests` library
- `tzdata` library (for Windows timezone support)
- Running EODHD Real-Time Candles API
- Valid API_KEY configured

## Installation

Dependencies are already in `requirements.txt`:
```bash
pip install -r requirements.txt
```

Set environment variable:
```bash
export API_KEY="your_api_key_here"
```

## Usage

### Command Line

```bash
python scripts/cumulative_volume_from_premarket.py <TICKER> [--host <API_URL>] [--market <SESSION>]
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `ticker` | Stock ticker symbol (required, e.g., AAPL, MSFT) |
| `--host` | REST API host URL (default: http://localhost:8765) |
| `--market` | Session start point: `premarket` (4:00 AM ET), `market` (9:30 AM ET), `after_hours` (4:00 PM ET). Default: `premarket` |
| `--count` | Maximum number of candles to retrieve (default: 1000) |

### Examples

```bash
# Get cumulative volume from premarket (4:00 AM ET) - default
python scripts/cumulative_volume_from_premarket.py AAPL

# Get cumulative volume from market open (9:30 AM ET)
python scripts/cumulative_volume_from_premarket.py AAPL --market market

# Get cumulative volume from after-hours start (4:00 PM ET)
python scripts/cumulative_volume_from_premarket.py AAPL --market after_hours

# Get cumulative volume with custom host
python scripts/cumulative_volume_from_premarket.py AAPL --host http://localhost:8765 --market market

# Get cumulative volume via NGINX proxy
python scripts/cumulative_volume_from_premarket.py TSLA --host https://n8n.sqowe.com/eodhd
```

### Python Module Usage

```python
from cumulative_volume_from_premarket import CumulativeVolumeCalculator

calculator = CumulativeVolumeCalculator(host="http://localhost:8765")
result = calculator.calculate_cumulative_volume("AAPL")
print(result)
```

## Output Format

### Success Response
```json
{
  "ticker": "AAPL",
  "market": "premarket",
  "cumulative_volume": 12345678,
  "candles_included": 45,
  "start_time": "2026-01-27 04:00:00 ET",
  "last_candle_time": "2026-01-27 10:15:00 ET",
  "current_session": "market",
  "current_time_et": "2026-01-27 10:16:32 ET",
  "status": "success"
}
```

### No Data Response (before session starts)
```json
{
  "ticker": "AAPL",
  "market": "market",
  "cumulative_volume": 0,
  "candles_included": 0,
  "start_time": "2026-01-27 09:30:00 ET",
  "last_candle_time": null,
  "current_session": "premarket",
  "message": "No candles found from 09:30 ET. Session may not have started yet.",
  "status": "success"
}
```

### Error Response
```json
{
  "ticker": "INVALID",
  "error": "No candles returned. Ticker may not be tracked.",
  "status": "error"
}
```

## Session Start Times

| `--market` Value | Start Time (ET) | Description |
|------------------|-----------------|-------------|
| `premarket` | 4:00 AM | Pre-market trading start (default) |
| `market` | 9:30 AM | Regular market open |
| `after_hours` | 4:00 PM | After-hours trading start |

## Session Types

| Session | Time (ET) | Description |
|---------|-----------|-------------|
| `closed` | Before 4:00 AM | No trading |
| `premarket` | 4:00 AM - 9:30 AM | Pre-market trading |
| `market` | 9:30 AM - 4:00 PM | Regular market hours |
| `after_hours` | 4:00 PM - 8:00 PM | After-hours trading |
| `closed` | After 8:00 PM | No trading |

## Timezone Handling

The script uses `zoneinfo.ZoneInfo('America/New_York')` for accurate timezone handling:

- **EST (Eastern Standard Time)**: UTC-5 (November - March)
- **EDT (Eastern Daylight Time)**: UTC-4 (March - November)

DST transitions are handled automatically - no manual adjustment needed.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | Yes | API key for REST API authentication |

### API Endpoint Used

The script calls `GET /candles/{ticker}` with:
- `count=1000` - Maximum candles to retrieve
- `include_current=true` - Include incomplete candle

## Use Cases

### 1. Real-Time Volume Monitoring

```bash
# Check current cumulative volume from premarket
python scripts/cumulative_volume_from_premarket.py AAPL --host http://localhost:8765

# Check volume since market open only
python scripts/cumulative_volume_from_premarket.py AAPL --market market
```

### 2. Volume Analysis for Day Trading

```bash
# Compare volume across multiple tickers (PowerShell)
foreach ($ticker in @("AAPL", "MSFT", "TSLA")) {
  python scripts/cumulative_volume_from_premarket.py $ticker
}

# Bash version
for ticker in AAPL MSFT TSLA; do
  python scripts/cumulative_volume_from_premarket.py $ticker
done
```

### 3. Integration with Trading Systems

```python
import json
from cumulative_volume_from_premarket import CumulativeVolumeCalculator

calculator = CumulativeVolumeCalculator(host="http://localhost:8765")

# Get volume from premarket
result = calculator.calculate_cumulative_volume("AAPL", market="premarket")

# Get volume from market open only
result = calculator.calculate_cumulative_volume("AAPL", market="market")

if result['status'] == 'success':
    volume = result['cumulative_volume']
    # Use volume in trading logic
```

## Error Handling

### Common Errors

**"API_KEY environment variable is required"**
- Set the environment variable with your API key

**"No candles returned. Ticker may not be tracked."**
- Verify ticker is added to the system via `GET /tickers`
- Use `manage_tickers.py` to add the ticker

**"API request failed"**
- Check API is running: `curl http://localhost:8765/health`
- Verify API_KEY is correct
- Check network connectivity to host

## Class Structure

### CumulativeVolumeCalculator

**Constructor:**
- `__init__(host, api_key=None)`: Initialize with API host and optional key

**Constants:**
- `PREMARKET_START = 240` (4:00 AM in minutes)
- `MARKET_OPEN = 570` (9:30 AM)
- `MARKET_CLOSE = 960` (4:00 PM)
- `AFTER_HOURS_END = 1200` (8:00 PM)

**Methods:**
- `get_current_session(now_et)`: Determine current market session
- `get_session_start_timestamp(now_et, market)`: Get Unix timestamp for session start
- `fetch_candles(ticker)`: Fetch candles from REST API
- `calculate_cumulative_volume(ticker, market)`: Main calculation method

## Performance Notes

- **Single API call**: Fetches up to 1000 candles per request
- **Efficient filtering**: Only processes candles from 4:00 AM ET onwards
- **Includes current candle**: Real-time volume including incomplete candle

## Related Scripts

- **premarket_volume.py**: Calculate average premarket volume (historical, uses EODHD directly)
- **premarket_pivots.py**: Calculate pivot points with premarket data
- **manage_tickers.py**: Add/remove tickers from EODHD system

## License

This script is provided as-is for educational and commercial use.
