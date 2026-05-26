# EODHD Real-Time Candle Aggregator v0.9.4

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
- ✅ **Multi-worker architecture** for high performance and scalability
- ✅ **Admin Web UI** with real-time monitoring and Chart.js visualizations

### Architecture (v0.9.4)

The service uses a **multi-worker architecture** for optimal performance and scalability:

```
┌─────────────────────────────────────────────────────────┐
│              Docker Container (supervisord)             │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ API Worker 1 │  │ API Worker 2 │  │  WebSocket   │ │
│  │  Port 8765   │  │  Port 8766   │  │    Worker    │ │
│  │ (HTTP API)   │  │ (HTTP API)   │  │ (Tick Data)  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │         │
│         └──────────────────┴──────────────────┘         │
│                            ↓                            │
│                  ┌──────────────────┐                   │
│                  │  SQLite (WAL)    │                   │
│                  │  /data/candles.db│                   │
│                  └──────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

**Worker Responsibilities:**

- **API Workers (2 instances)**: Handle HTTP REST requests in parallel
  - Read operations: Get candles, tickers, status
  - Write operations: Add/remove tickers
  - Ports: 8765, 8766 (load balanced internally)

- **WebSocket Worker (1 instance)**: Dedicated tick processing
  - Connects to EODHD WebSocket feed
  - Aggregates ticks into OHLCV candles
  - Writes candles to database
  - Runs background cleanup tasks

- **Admin UI (1 instance)**: Web dashboard on port 5000

**Performance Benefits:**
- 🚀 **50% faster API response time** under load (parallel request handling)
- 💪 **Better CPU utilization** across multiple cores (60-80% vs 30-40%)
- 🔒 **No database locking** (writes isolated to WebSocket worker)
- 📈 **Easy scaling** (add more API workers as needed)

For detailed deployment information, see [docs/MULTI_WORKER_DEPLOYMENT.md](docs/MULTI_WORKER_DEPLOYMENT.md).

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

# View logs (all workers)
docker-compose logs -f

# View specific worker logs
docker-compose logs -f | grep websocket_worker
docker-compose logs -f | grep api_worker
```

**What starts:**
- 2 API workers (ports 8765, 8766)
- 1 WebSocket worker (tick processing)
- 1 Admin UI (port 5000)

All managed by supervisord for automatic restarts and health monitoring.

### 3. Test the API

```bash
# Health check (no auth required)
curl http://localhost:8765/health

# Get status (requires API key)
curl -H "X-API-Key: your_secret_api_key" http://localhost:8765/status

# Get candles for AAPL
curl -H "X-API-Key: your_secret_api_key" http://localhost:8765/candles/AAPL?count=10
```

### 4. Access Admin Web UI

The service includes a Flask-based admin web interface for easy management:

```bash
# Access admin panel (default: localhost only)
http://localhost:5000

# Login with your API_KEY from .env
```

**Features:**
- 📊 **Dashboard**: Real-time system status and monitoring
- 🎯 **Ticker Management**: Add/remove tickers with visual interface
- 📈 **Candle Viewer**: Browse and visualize OHLCV data with Chart.js
- ⚙️ **Configuration**: Update service settings via web UI

**Security Note:** By default, the admin UI is only accessible from `localhost` (`127.0.0.1`). To enable external access, set `ADMIN_HOST=0.0.0.0` in `.env` (not recommended for production without additional security measures).

For detailed admin UI documentation, see [docs/ADMIN_UI.md](docs/ADMIN_UI.md).

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
| `GET` | `/config` | Get current configuration with source information |
| `PATCH` | `/config` | Update configuration |
| `POST` | `/config/reset` | Reset to .env defaults |

**GET /config response format:**
```json
{
  "config": {
    "candle_interval_minutes": {"value": 5, "source": "env"},
    "max_candles_stored": {"value": 100, "source": "env"},
    "max_tickers": {"value": 50, "source": "env"},
    "ws_reconnect_delay": {"value": 5, "source": "env"},
    "ws_ping_interval": {"value": 30, "source": "env"}
  },
  "persistence_enabled": true,
  "has_persisted_overrides": false,
  "timestamp": "2025-12-13T12:00:00.000Z"
}
```

**Source values:**
- `env` - From environment variables (.env file)
- `override` - Runtime overrides (persisted in config.json)

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
| `GET` | `/tickers/{ticker}` | Get single ticker information |
| `POST` | `/tickers` | Add ticker(s) |
| `DELETE` | `/tickers/{ticker}` | Remove single ticker **and its candles** |
| `DELETE` | `/tickers` (with body) | Remove multiple tickers **and their candles** |
| `DELETE` | `/tickers?confirm=true` (no body) | Remove ALL tickers **and all candles** (requires config) |

**Examples:**
```bash
# List tickers
curl -H "X-API-Key: xxx" http://localhost:8765/tickers

# Get single ticker info
curl -H "X-API-Key: xxx" http://localhost:8765/tickers/AAPL

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
- **⚠️ BREAKING CHANGE (v0.4.3)**: When a ticker is removed (single or batch), its candle data is **also deleted**
  - Previously: Single ticker deletion removed candles ✓, but batch deletion (`DELETE /tickers?confirm=true`) preserved candles ✗
  - Now: **All ticker deletion operations consistently remove candles** ✓
  - Migration: Use `POST /candles/cleanup` to clean up any orphaned candles from legacy data

#### Candle Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/candles/all` | Get candles for ALL tracked tickers (requires confirmation) |
| `GET` | `/candles/{ticker}` | Get candles for ticker |
| `GET` | `/candles/{ticker}/latest` | Get current incomplete candle |
| `GET` | `/candles/{ticker}/{minutes}` | Get aggregated candles at custom interval |
| `POST` | `/candles/multi` | Get candles for multiple tickers |
| `DELETE` | `/candles/{ticker}` | Clear ticker's candle history |
| `DELETE` | `/candles` | Clear all candle history |
| `POST` | `/candles/cleanup` | Remove orphaned candles (for tickers no longer tracked) |

**Aggregated Candles Endpoint (`/candles/{ticker}/{minutes}`):**

Aggregates stored candles into larger intervals on-the-fly.

**Rules:**
- Requested interval must be >= largest stored interval for the ticker
- Requested interval must be divisible by largest stored interval
- Only completed candles are used (no incomplete/current candles)
- Gaps in data are tracked with `has_gaps` flag

**Query Parameters:**
- `count` - Number of aggregated candles to return (default: 10)
- `from_timestamp` - Filter by start time (Unix timestamp)
- `to_timestamp` - Filter by end time (Unix timestamp)

**Example Request:**
```bash
# Get 15-minute candles (aggregated from 5-minute base)
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8765/candles/AAPL/15?count=10"
```

**Example Response:**
```json
{
  "ticker": "AAPL",
  "requested_interval": "15m",
  "base_interval": "5m",
  "aggregation_factor": 3,
  "count": 10,
  "candles": [
    {
      "ticker": "AAPL",
      "timestamp": 1733752500,
      "datetime_utc": "2025-12-09 14:15:00 UTC",
      "open": 245.50,
      "high": 246.80,
      "low": 245.10,
      "close": 246.20,
      "volume": 375000,
      "tick_count": 1026,
      "interval_minutes": 15,
      "expected_candles": 3,
      "actual_candles": 3,
      "has_gaps": false
    },
    {
      "ticker": "AAPL",
      "timestamp": 1733751600,
      "datetime_utc": "2025-12-09 14:00:00 UTC",
      "open": 244.80,
      "high": 245.60,
      "low": 244.50,
      "close": 245.50,
      "volume": 280000,
      "tick_count": 812,
      "interval_minutes": 15,
      "expected_candles": 3,
      "actual_candles": 2,
      "has_gaps": true
    }
  ],
  "timestamp": "2025-12-09T14:30:00.000Z"
}
```

**Validation Error Example:**
```bash
# Request 12-minute candles when base is 5 minutes (invalid: 12 % 5 != 0)
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8765/candles/AAPL/12"
```
```json
{
  "error": "Invalid aggregation request",
  "detail": "Requested interval (12m) is not divisible by largest stored interval (5m). Valid options: [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]",
  "ticker": "AAPL",
  "stored_intervals": [5],
  "requested_minutes": 12
}
```

**Cleanup Endpoint Details:**

The `/candles/cleanup` endpoint removes orphaned candle data:
- **Performance**: Uses atomic transaction with database lock for consistency
- **Duration**: Typically <1s for normal datasets; may take several seconds for >100k orphans
- **Recommendation**: Run during low-traffic periods for large cleanups
- **Response includes**: `deleted_count` and `duration_seconds` for monitoring

**Query Parameters for `/candles/all` (ALL required for safety):**
- `confirm=true` - **Required** - Explicit confirmation to retrieve all candles
- `max_tickers=N` - **Required** - Maximum number of tickers allowed (must be >= 1)
- `count` - Number of candles per ticker (default: 10)
- `include_current` - Include incomplete candle (default: true)
- `from_timestamp` - Filter by start time (Unix timestamp)
- `to_timestamp` - Filter by end time (Unix timestamp)

**Example Request:**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8080/candles/all?confirm=true&max_tickers=50&count=10"
```

**Example Response (flat list with ticker field):**
```json
{
  "total_tickers": 3,
  "total_candles": 30,
  "max_tickers_limit": 50,
  "interval": "5m",
  "candles": [
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
    },
    {
      "ticker": "AAPL",
      "timestamp": 1733752200,
      ...
    },
    {
      "ticker": "TSLA",
      ...
    }
  ],
  "timestamp": "2025-12-13T10:30:00.000Z"
}
```

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
| `ADMIN_ENABLED` | `true` | Enable admin web UI |
| `ADMIN_HOST` | `127.0.0.1` | Admin UI host (localhost only by default) |
| `ADMIN_PORT` | `5000` | Admin UI port |

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
│   ├── candle_aggregator.py # On-demand candle aggregation
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

---

## Changelog

### v0.9.4 (2026-01-30)
- **Multi-Worker Architecture**: Implemented separate API and WebSocket processes for better scalability
  - **2 API Workers**: Handle HTTP requests in parallel (ports 8765, 8766)
  - **1 WebSocket Worker**: Dedicated tick processing and candle aggregation
  - **Performance**: 50% faster API response time under load, better CPU utilization across cores
  - **Reliability**: Eliminated database locking errors by isolating writes to WebSocket worker
- **Code Quality**: Fixed cleanup task data loss risk with individual ticker processing
- **Testing**: Added comprehensive test coverage (19 new tests for API server and WebSocket worker)
- **Documentation**: Added complete multi-worker deployment guide
- **Configuration**: Updated supervisord.conf with explicit worker definitions and correct port allocation

### v0.4.3 (2026-01-20)
- **Premarket Volume Script Enhancement**: Updated `scripts/premarket_volume.py`
  - Removed interval parameter (now hardcoded to 1m - only interval with premarket data)
  - Increased data retrieval from 30 to 90 days for maximum premarket data points
  - Improved error messages explaining EODHD API premarket data limitations
  - Updated documentation to clarify that only 1-minute intervals include premarket hours (4:00-9:30 AM ET)
  - Simplified CLI usage: `python premarket_volume.py AAPL.US` (no interval parameter needed)

### v0.4.2 (2025-12-26)
- **Bug Fix**: Fixed `delete_all_tickers()` to consistently delete candle data
  - **⚠️ BREAKING CHANGE**: `DELETE /tickers?confirm=true` now deletes candles (previously preserved them)
  - This brings batch ticker deletion in line with single ticker deletion behavior
  - Migration: Use new `POST /candles/cleanup` endpoint to remove orphaned candles from legacy data
- **New Endpoint**: Added `POST /candles/cleanup` to remove orphaned candles
- **Documentation**: Added comprehensive breaking change notice and migration guide
- **Tooling**: Added `scripts/cleanup_orphaned_candles.sh` for automated cleanup

### v0.4.2 (2025-12-13)
- **Admin UI Improvements**: Enhanced admin dashboard user experience
  - Removed unused `ADMIN_SESSION_SECRET` from configuration (auto-generated internally)
  - Fixed Configuration display to show human-readable format (e.g., "5 minutes" instead of "5 min")
  - Added oldest/newest candle timestamps to Database statistics display
  - Candle data now sorted with newest candles on top for better usability
  - Config form inputs now show current values as placeholders for better UX

### v0.4.1 (2025-12-13)
- **New Endpoint**: Added `GET /candles/all` to retrieve candles for ALL tracked tickers
  - Requires `confirm=true` and `max_tickers=N` parameters for safety
  - Returns flat list with ticker field included in each candle
  - Supports same filters as single ticker endpoint (count, timestamps, include_current)

### v0.4.0 (2025-12-13)
- **Admin Web UI**: Added Flask-based admin panel with sqowe branding
- **Interactive Dashboard**: Real-time system monitoring with Chart.js visualizations
- **Ticker Management UI**: Visual interface for managing tickers
- **Candle Data Viewer**: Browse and visualize OHLCV candles with interactive charts
- **Configuration UI**: Web interface for updating service configuration
- **Multi-process Container**: Supervisord manages both REST API and admin UI
- **Configurable Access**: Admin UI host configurable for localhost or external access

### v0.3.1 (2025-12-11)
- **SQLite performance tuning**: Added WAL mode, `synchronous=NORMAL`, and `busy_timeout=5000` for better read/write concurrency
- **Stats caching**: `get_stats()` now caches results for 5 seconds to reduce database load from `/status` polling
- **Documentation**: Added `docs/sqlite-performance-tuning.md` with implementation details
