# Premarket Volume Calculator - Python Script (EODHD API)

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
