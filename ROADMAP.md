# EODHD Candle Aggregator - Roadmap

## Current Version: v1.0

### ✅ Implemented Features

- Real-time WebSocket connection to EODHD
- Configurable candle intervals (1, 5, 15, 30, 60 minutes)
- Ticker management (add/remove/list via REST API)
- SQLite persistence for candles and tickers
- API key authentication
- Dynamic configuration updates
- Automatic reconnection with configurable delay
- Pre-market and after-hours data support
- Docker deployment with health checks
- Comprehensive REST API

---

## Version 1.1 - Enhanced Operations

**Target:** Improved reliability and observability

### 1. Prometheus Metrics Endpoint

**Endpoint:** `GET /metrics`

Expose metrics in Prometheus format for monitoring:

```
# HELP candle_aggregator_ticks_total Total ticks processed
# TYPE candle_aggregator_ticks_total counter
candle_aggregator_ticks_total{ticker="AAPL"} 15234

# HELP candle_aggregator_candles_completed Total candles completed
# TYPE candle_aggregator_candles_completed counter
candle_aggregator_candles_completed{ticker="AAPL",interval="5m"} 48

# HELP candle_aggregator_websocket_connected WebSocket connection status
# TYPE candle_aggregator_websocket_connected gauge
candle_aggregator_websocket_connected 1

# HELP candle_aggregator_websocket_reconnects_total Total reconnection attempts
# TYPE candle_aggregator_websocket_reconnects_total counter
candle_aggregator_websocket_reconnects_total 3
```

**Use case:** Integration with Grafana dashboards for monitoring service health.

---

### 2. Dead Ticker Detection

Automatically detect tickers that stop receiving data:

```json
// GET /tickers response enhancement
{
  "symbol": "HALTED",
  "status": "stale",           // "active", "stale", "no_data"
  "stale_since": "2025-12-09T14:30:00Z",
  "stale_minutes": 15,
  "last_tick_at": "2025-12-09T14:15:00Z"
}
```

**Configuration:**
```bash
STALE_TICKER_THRESHOLD_MINUTES=10   # Mark as stale after 10 min no data
```

**API:**
- `GET /tickers?status=stale` - Filter stale tickers
- `POST /tickers/cleanup` - Remove all stale tickers

**Use case:** Detect halted stocks, delisted symbols, or data issues.

---

### 3. Market Hours Awareness

Intelligent handling based on US market schedule:

```json
// GET /status enhancement
{
  "market": {
    "status": "open",           // "pre_market", "open", "after_hours", "closed"
    "session_start": "09:30:00 EST",
    "session_end": "16:00:00 EST",
    "next_open": null,
    "is_holiday": false
  }
}
```

**Configuration:**
```bash
MARKET_HOURS_ONLY=false        # Only collect during market hours
CLEAR_ON_MARKET_CLOSE=false    # Clear candles at 8pm EST
```

**Features:**
- Auto-detect US market holidays
- Separate pre-market/regular/after-hours in candle metadata
- Optional: pause collection outside trading hours

---

### 4. Batch Operations

Import/export functionality:

```bash
# Export tickers to JSON
GET /tickers/export
Response: {"tickers": ["AAPL", "MSFT", ...], "exported_at": "..."}

# Import tickers from JSON
POST /tickers/import
Body: {"tickers": ["AAPL", "MSFT", "GOOGL"]}

# Export all data (backup)
GET /backup
Response: Binary SQLite dump or JSON

# Restore from backup
POST /restore
Body: Backup file
```

**Use case:** Migrate between instances, backup before updates.

---

### 5. Rate Limiting

Protect the API from abuse:

```bash
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100        # Requests per window
RATE_LIMIT_WINDOW_SECONDS=60   # Window duration
```

**Response headers:**
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1733752500
```

---

## Version 2.0 - Advanced Features

**Target:** Enhanced analysis capabilities

### 1. Multi-Interval Support

Track multiple intervals simultaneously:

```bash
# Configuration
CANDLE_INTERVALS=1,5,15,60     # Track all these intervals
```

**API:**
```bash
# Get 5-minute candles
GET /candles/AAPL?interval=5m

# Get 15-minute candles (derived from 5m or 1m)
GET /candles/AAPL?interval=15m

# Get all intervals
GET /candles/AAPL/all-intervals
```

**Response:**
```json
{
  "ticker": "AAPL",
  "intervals": {
    "1m": [...candles...],
    "5m": [...candles...],
    "15m": [...candles...],
    "60m": [...candles...]
  }
}
```

**Implementation:** Derive larger intervals from smallest tracked interval.

---

### 2. Technical Indicators

Calculate indicators on-the-fly:

**Supported Indicators:**
| Indicator | Parameters | Description |
|-----------|------------|-------------|
| SMA | period (5,10,20,50) | Simple Moving Average |
| EMA | period (5,10,20,50) | Exponential Moving Average |
| RSI | period (14) | Relative Strength Index |
| VWAP | - | Volume Weighted Average Price |
| BB | period, std_dev | Bollinger Bands |

**API:**
```bash
# Get candles with indicators
GET /candles/AAPL?indicators=sma_5,sma_20,rsi_14,vwap
```

**Response:**
```json
{
  "ticker": "AAPL",
  "candles": [
    {
      "timestamp": 1733752500,
      "open": 245.50,
      "high": 246.20,
      "low": 245.30,
      "close": 245.90,
      "volume": 125000,
      "indicators": {
        "sma_5": 245.75,
        "sma_20": 244.80,
        "rsi_14": 62.5,
        "vwap": 245.62
      }
    }
  ]
}
```

---

### 3. WebSocket Output (Push to Clients)

Real-time candle updates via WebSocket:

**Endpoint:** `ws://localhost:8765/ws/candles`

**Subscribe:**
```json
{"action": "subscribe", "tickers": ["AAPL", "MSFT"]}
```

**Events pushed to client:**
```json
// Candle completed
{
  "event": "candle_complete",
  "ticker": "AAPL",
  "candle": {
    "timestamp": 1733752500,
    "open": 245.50,
    "high": 246.20,
    "low": 245.30,
    "close": 245.90,
    "volume": 125000,
    "interval_minutes": 5
  }
}

// Current candle update (every N seconds)
{
  "event": "candle_update",
  "ticker": "AAPL",
  "candle": {...}
}
```

**Use case:** Build real-time dashboards, eliminate polling from n8n.

---

### 4. Alert Webhooks

Configure price/volume alerts with webhook notifications:

**API:**
```bash
# Create alert
POST /alerts
{
  "ticker": "AAPL",
  "condition": "price_above",    // price_above, price_below, volume_above, price_change_pct
  "value": 250.00,
  "webhook_url": "http://n8n:5678/webhook/price-alert",
  "cooldown_minutes": 15,        // Don't repeat for 15 min
  "enabled": true
}

# List alerts
GET /alerts

# Delete alert
DELETE /alerts/{alert_id}
```

**Webhook payload:**
```json
{
  "alert_id": "abc123",
  "ticker": "AAPL",
  "condition": "price_above",
  "threshold": 250.00,
  "triggered_value": 251.25,
  "triggered_at": "2025-12-09T14:30:00Z",
  "candle": {...}
}
```

**Alert types:**
- `price_above` / `price_below` - Absolute price threshold
- `price_change_pct` - Percentage change from open
- `volume_above` - Volume spike detection
- `rsi_oversold` / `rsi_overbought` - RSI thresholds (requires v2 indicators)

---

### 5. Historical Data Backfill

Combine real-time with EODHD Intraday Historical API:

**API:**
```bash
# Backfill missing historical data
POST /candles/backfill
{
  "ticker": "AAPL",
  "from_date": "2025-12-01",
  "to_date": "2025-12-08"
}
```

**Logic:**
1. Fetch historical intraday data from EODHD Intraday API
2. Fill gaps in SQLite database
3. Merge with real-time data seamlessly

**Use case:** Get continuous history when service was down or for backtesting.

---

## Implementation Priority

### Phase 1 (v1.1) - Q1 2026
1. ⬜ Prometheus metrics (high value for operations)
2. ⬜ Dead ticker detection (improves reliability)
3. ⬜ Market hours awareness (reduces unnecessary data)
4. ⬜ Batch operations (operational convenience)
5. ⬜ Rate limiting (security)

### Phase 2 (v2.0) - Q2 2026
1. ⬜ Technical indicators (high value for analysis)
2. ⬜ Multi-interval support (flexibility)
3. ⬜ Alert webhooks (automation)
4. ⬜ WebSocket output (real-time dashboards)
5. ⬜ Historical backfill (data completeness)

---

## Contributing

Suggestions and contributions are welcome! Priority will be given to features that:
1. Improve reliability and observability
2. Reduce operational overhead
3. Enhance n8n integration
4. Add analytical capabilities without external dependencies

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| v1.0 | 2025-12-09 | Initial release with core features |
| v1.1 | TBD | Operations & monitoring enhancements |
| v2.0 | TBD | Advanced analysis features |
