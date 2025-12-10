# EODHD Real-Time Candle Aggregator v1.0

> **Converts EODHD WebSocket tick data into configurable OHLCV candles with full REST API management**

## Overview

This microservice solves the problem that EODHD's Intraday Historical API only provides data **2-3 hours after market close**. By connecting to the real-time WebSocket feed (included in your EOD+Intraday Extended plan), it aggregates ticks into OHLCV candles that you can query during market hours.

### Features

- ✅ **Real-time candles** from WebSocket tick data (<50ms latency)
- ✅ **Configurable interval** (1, 5, 15, 30, 60 minutes)
- ✅ **Ticker management** (add/remove/list via API)
- ✅ **SQLite persistence** (survives restarts)
- ✅ **API key authentication**
- ✅ **Dynamic configuration** (change settings without restart)
- ✅ **Pre-market & after-hours** support
- ✅ **Docker ready** with health checks

---

## Quick Start

### 1. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit with your values
nano .env
```

**Required settings in `.env`:**
```bash
EODHD_API_KEY=your_actual_api_key
API_KEY=your_secret_api_key_for_auth
DEFAULT_TICKERS=AAPL,MSFT,GOOGL,TSLA,NVDA
```

### 2. Start the Service

```bash
# Using Docker Compose (recommended)
docker-compose up -d

# View logs
docker-compose logs -f
```

### 3. Test the API

```bash
# Health check (no auth required)
curl http://localhost:8765/health

# Get status (requires API key)
curl -H "X-API-Key: your_secret_api_key" http://localhost:8765/status

# Get candles for AAPL
curl -H "X-API-Key: your_secret_api_key" http://localhost:8765/candles/AAPL?count=10
```

---

## API Reference

### Authentication

All endpoints except `/health` require authentication via one of:
- Header: `X-API-Key: your_api_key`
- Header: `Authorization: Bearer your_api_key`
- Query param: `?api_key=your_api_key`

### Endpoints

#### Health & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Basic health check (no auth) |
| `GET` | `/status` | Detailed system status |
| `POST` | `/reconnect` | Force WebSocket reconnection |

#### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/config` | Get current configuration |
| `PATCH` | `/config` | Update configuration |
| `POST` | `/config/reset` | Reset to .env defaults |

**Configurable fields via PATCH /config:**
```json
{
  "candle_interval_minutes": 5,
  "max_candles_stored": 100,
  "max_tickers": 50,
  "ws_reconnect_delay": 5,
  "ws_ping_interval": 30
}
```

#### Ticker Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/tickers` | List all tracked tickers |
| `POST` | `/tickers` | Add ticker(s) |
| `DELETE` | `/tickers/{ticker}` | Remove single ticker |
| `DELETE` | `/tickers` (with body) | Remove multiple tickers |
| `DELETE` | `/tickers?confirm=true` (no body) | Remove ALL tickers (requires config) |

**Examples:**
```bash
# List tickers
curl -H "X-API-Key: xxx" http://localhost:8765/tickers

# Add tickers
curl -X POST -H "X-API-Key: xxx" -H "Content-Type: application/json" \
  -d '{"tickers": ["MCD", "KO", "PEP"]}' \
  http://localhost:8765/tickers

# Remove single ticker
curl -X DELETE -H "X-API-Key: xxx" http://localhost:8765/tickers/MCD

# Remove specific tickers
curl -X DELETE -H "X-API-Key: xxx" -H "Content-Type: application/json" \
  -d '{"tickers": ["MCD", "KO"]}' \
  http://localhost:8765/tickers

# Remove ALL tickers (requires ALLOW_DELETE_ALL_TICKERS=true)
curl -X DELETE -H "X-API-Key: xxx" \
  http://localhost:8765/tickers?confirm=true
```

**Important Notes:**
- Removing all tickers is **disabled by default** for safety
- Set `ALLOW_DELETE_ALL_TICKERS=true` in `.env` to enable
- Requires `?confirm=true` query parameter to prevent accidental deletion
- Candle data is **preserved** when tickers are removed
- Re-adding a ticker will restore access to its preserved candles

#### Candle Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/candles/{ticker}` | Get candles for ticker |
| `GET` | `/candles/{ticker}/latest` | Get current incomplete candle |
| `POST` | `/candles/multi` | Get candles for multiple tickers |
| `DELETE` | `/candles/{ticker}` | Clear ticker's candle history |
| `DELETE` | `/candles` | Clear all candle history |

**Query Parameters for `/candles/{ticker}`:**
- `count` - Number of candles (default: 10)
- `include_current` - Include incomplete candle (default: true)
- `from_timestamp` - Filter by start time (Unix timestamp)
- `to_timestamp` - Filter by end time (Unix timestamp)

---

## Response Formats

### Candle Object
```json
{
  "ticker": "AAPL",
  "timestamp": 1733752500,
  "datetime_utc": "2025-12-09 14:15:00 UTC",
  "open": 245.50,
  "high": 246.20,
  "low": 245.30,
  "close": 245.90,
  "volume": 125000,
  "tick_count": 342,
  "is_complete": true,
  "interval_minutes": 5
}
```

### Ticker Object
```json
{
  "symbol": "AAPL",
  "added_at": "2025-12-09T10:00:00Z",
  "status": "active",
  "last_tick_at": "2025-12-09T14:29:55Z",
  "last_price": 245.67,
  "candle_count": 15
}
```

---

## n8n Integration

### HTTP Request Node - Single Ticker
```
Method: GET
URL: http://localhost:8765/candles/{{ $json.ticker }}
Headers:
  X-API-Key: your_api_key
Query Parameters:
  count: 10
  include_current: true
```

### HTTP Request Node - Multiple Tickers
```
Method: POST
URL: http://localhost:8765/candles/multi
Headers:
  X-API-Key: your_api_key
  Content-Type: application/json
Body:
{
  "tickers": {{ $json.tickers }},
  "count": 10
}
```

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `EODHD_API_KEY` | (required) | Your EODHD API key |
| `API_KEY` | (empty) | API authentication key |
| `HTTP_PORT` | `8765` | HTTP server port |
| `DEFAULT_TICKERS` | `AAPL,MSFT,GOOGL` | Initial tickers |
| `ALLOW_DELETE_ALL_TICKERS` | `false` | Enable DELETE /tickers without body |
| `CANDLE_INTERVAL_MINUTES` | `5` | 1, 5, 15, 30, or 60 |
| `MAX_CANDLES_STORED` | `100` | Per ticker |
| `MAX_TICKERS` | `50` | EODHD WebSocket limit |
| `DATABASE_PATH` | `/data/candles.db` | SQLite path |
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |

---

## File Structure

```
eodhd_realtime_candles/
├── src/
│   ├── __init__.py
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration
│   ├── storage.py           # SQLite database
│   ├── candle_engine.py     # Aggregation logic
│   ├── websocket_manager.py # EODHD WebSocket
│   └── api/
│       ├── __init__.py
│       ├── routes.py        # REST endpoints
│       └── middleware.py    # Auth middleware
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
└── ROADMAP.md
```

---

## Troubleshooting

### No data received
1. Verify EODHD API key has WebSocket access
2. Check logs: `docker-compose logs -f`
3. Market must be open for data

### Reset database
```bash
docker-compose down
docker volume rm eodhd_candle_data
docker-compose up -d
```
