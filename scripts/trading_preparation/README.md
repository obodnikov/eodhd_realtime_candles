# Trading Preparation Script

Multi-timeframe EMA trend analysis tool for trading preparation decisions.

## Overview

This script analyzes stock price data using EMA (Exponential Moving Average) crossovers from multiple timeframes to help traders identify trend direction, stability, and optimal entry points.

## Features

- Multi-timeframe EMA crossover analysis (30/50, 10/30, 3/10, 1/3)
- Market state detection (DOWN, BASE, TREND_START, TREND, PULLBACK)
- Intraday score (0-10) based on EMA stability
- Running cumulative volume (VolumeDay) with configurable session start
- Market session detection (PRE, RTH, EXT, CLOSED)
- Multiple output formats (console, JSON, CSV)

## Data Sources

| EMA Crossover | Data Source | Interval | Purpose |
|---------------|-------------|----------|---------|
| 30/50 | Yahoo Finance | Daily | Long-term trend direction |
| 10/30 | Yahoo Finance | Hourly | Medium-term trend |
| 3/10 | EODHD API | 15-minute | Short-term momentum |
| 1/3 | EODHD API | 1-minute | Real-time micro-trend |

## State Detection

The script classifies market state based on EMA alignment. The `State` field in output represents the current trend condition:

| State | Condition | Interpretation |
|-------|-----------|----------------|
| DOWN | 10/30 DOWN, 3/10 DOWN | Bearish alignment - all timeframes pointing down, avoid longs |
| BASE | 10/30 DOWN, 3/10 UP | Building base - higher TF still down but short-term showing strength, potential reversal forming |
| TREND_START | 10/30 UP, not stable | Early trend - hourly EMA crossed up but not yet stable, higher risk entry |
| TREND | 10/30 UP stable, 3/10 UP, 1/3 UP | Confirmed uptrend - all timeframes aligned bullish with stability, optimal entry zone |
| PULLBACK | 10/30 UP stable, lower TFs DOWN | Retracement within uptrend - hourly trend intact but 15m/1m showing weakness, potential dip-buy |
| UNKNOWN | None of the above | Undefined state - mixed signals that don't fit clear patterns |

The state is calculated per row, so you can observe transitions throughout the trading session.

## Intraday Score (0-10)

Score is calculated from stable EMA crossovers:

| Crossover | Weight | Condition |
|-----------|--------|-----------|
| 10/30 (Hourly) | +4 | Stable for N candles |
| 3/10 (15m) | +3 | Stable for N candles |
| 1/3 (1m) | +3 | Stable for N candles |

Higher score = stronger trend alignment.

## Installation

### Prerequisites

- Python 3.10+
- Access to EODHD Real-Time Candle Aggregator API

### Dependencies

```bash
pip install pandas yfinance requests python-dotenv
```

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
N8N_EODHD_API_KEY=your_api_key_here
```

Or pass via command line with `--api-key`.

## Usage

### Basic Usage

```bash
python trading_preparation.py --ticker AAPL
```

### With Custom Parameters

```bash
python trading_preparation.py --ticker TSLA \
    --hold-30-50 3 \
    --hold-10-30 3 \
    --hold-3-10 2 \
    --hold-1-3 1 \
    --tail 50
```

### With Custom Session Start (--market)

```bash
# Default: premarket (4:00 AM ET)
python trading_preparation.py --ticker AAPL

# From market open (9:30 AM ET)
python trading_preparation.py --ticker AAPL --market market

# From after hours (4:00 PM ET)
python trading_preparation.py --ticker AAPL --market after_hours
```

### Save Output to File

```bash
# JSON output
python trading_preparation.py --ticker NVDA --out result.json

# CSV output
python trading_preparation.py --ticker NVDA --out result.csv
```

### Debug Mode

```bash
python trading_preparation.py --ticker AAPL --debug
```

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--ticker` | Required | Stock ticker symbol |
| `--base-url` | `https://n8n.sqowe.com` | EODHD API base URL |
| `--api-key` | From env | API key for authentication |
| `--hold-30-50` | 3 | Candles for 30/50 EMA stability |
| `--hold-10-30` | 3 | Candles for 10/30 EMA stability |
| `--hold-3-10` | 2 | Candles for 3/10 EMA stability |
| `--hold-1-3` | 1 | Candles for 1/3 EMA stability |
| `--tail` | 25 | Number of 1m rows to display |
| `--market` | `premarket` | Session start for VolumeDay: `premarket` (4:00 AM), `market` (9:30 AM), `after_hours` (4:00 PM) |
| `--out` | None | Output file path (.json or .csv) |
| `--debug` | False | Enable debug logging |

## Example Output

### Console Output

```
================================================================================
MULTI-TIMEFRAME EMA ANALYSIS: AAPL
================================================================================
30/50 (Daily):      UP | Hold: 5 | Stable: True
10/30 (Hourly):     UP | Hold: 4 | Stable: True
3/10  (15m):        UP | Hold: 3 | Stable: True
Cumulative Vol: 12,345,678 | Avg 3M: 45,000,000
================================================================================

NY_Time           LastPrice  Open    High    Low     Close   Volume   VolumeDay  Session  State  Score  TrendSummary
2026-01-29 10:15  185.50     185.20  185.60  185.10  185.50  50000    12345678   RTH      TREND  10/10  30/50:UP[OK] | 10/30:UP[OK] | 3/10:UP[OK] | 1/3:UP[OK]
2026-01-29 10:16  185.55     185.50  185.60  185.45  185.55  25000    12370678   RTH      TREND  10/10  30/50:UP[OK] | 10/30:UP[OK] | 3/10:UP[OK] | 1/3:UP[OK]
```

Note: `VolumeDay` is a running cumulative sum - each row shows total volume accumulated from session start up to that candle.

### JSON Output

```json
{
  "ticker": "AAPL",
  "timestamp": "2026-01-29T10:15:00-05:00",
  "signals": {
    "daily_30_50": {
      "ema30_daily": 182.5,
      "ema50_daily": 180.2,
      "trend_30_50": "UP",
      "stable_30_50": true,
      "hold_30_50": 5
    },
    "hourly_10_30": { ... },
    "m15_3_10": { ... },
    "m1_1_3": { ... }
  },
  "state": "TREND",
  "score": "10/10",
  "last_price": 185.50,
  "cumulative_volume": 12345678,
  "avg_3m_volume": 45000000,
  "avg_20d_volume": 42000000
}
```

## Trading Workflow

1. Run script before market open to assess overnight trend
2. Monitor state transitions during premarket (PRE session)
3. Look for TREND state with high score (8-10) for entries
4. Use PULLBACK state for potential add-on entries in uptrend
5. Avoid entries when state is DOWN or BASE

## Error Handling

The script will exit with clear error messages if:
- API key is missing
- Ticker symbol is invalid
- Critical data (daily/hourly OHLCV) cannot be fetched
- 1-minute candle data is unavailable

## Testing

```bash
python -m pytest tests/test_trading_preparation.py -v
```

## Related Scripts

- `STOP_RES_GPT_VOL.py` - Daily pivot point and S/R level calculator
- `combined_trand_rapid_GPT_32_SCORE_EODH.py` - Legacy version (deprecated)

## License

Internal use only.
