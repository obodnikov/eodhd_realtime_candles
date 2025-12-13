# EODHD Real-Time Candle Aggregator

Real-time OHLCV candle aggregation from EODHD WebSocket data with REST API and web-based admin UI.

## Quick Start

```bash
docker run -d \
  -p 8765:8765 \
  -p 5000:5000 \
  -e EODHD_API_KEY=your_api_key \
  -e API_KEY=your_secret_key \
  -v candle_data:/data \
  --name eodhd-candles \
  obodnikov/eodhd_realtime_candles:latest
```

Access:
- **REST API**: http://localhost:8765
- **Admin UI**: http://localhost:5000

## Features

- ✅ Real-time candle aggregation from EODHD WebSocket
- ✅ Configurable intervals (1, 5, 15, 30, 60 minutes)
- ✅ REST API for candle data access
- ✅ Professional web-based admin interface
- ✅ Interactive Chart.js visualizations
- ✅ SQLite persistence with WAL mode
- ✅ Docker ready with health checks

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EODHD_API_KEY` | **Yes** | - | Your EODHD API key |
| `API_KEY` | Recommended | - | Authentication key for REST API |
| `DEFAULT_TICKERS` | No | `AAPL,MSFT,GOOGL` | Initial tickers to track |
| `CANDLE_INTERVAL_MINUTES` | No | `5` | Candle interval (1, 5, 15, 30, 60) |
| `ADMIN_HOST` | No | `127.0.0.1` | Admin UI host (localhost or 0.0.0.0) |

## Volumes

- `/data` - SQLite database and configuration persistence

## Ports

- `8765` - REST API (aiohttp)
- `5000` - Admin Web UI (Flask)

## Usage Example

### Using docker-compose

```yaml
version: '3.8'
services:
  eodhd-candles:
    image: obodnikov/eodhd_realtime_candles:latest
    ports:
      - "8765:8765"
      - "5000:5000"
    environment:
      - EODHD_API_KEY=your_api_key
      - API_KEY=your_secret_key
      - DEFAULT_TICKERS=AAPL,MSFT,GOOGL,TSLA
    volumes:
      - candle_data:/data

volumes:
  candle_data:
```

### API Examples

```bash
# Get candles for AAPL
curl -H "X-API-Key: your_secret_key" \
  http://localhost:8765/candles/AAPL?count=10

# Add new tickers
curl -X POST \
  -H "X-API-Key: your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["NVDA", "AMD"]}' \
  http://localhost:8765/tickers

# Get system status
curl -H "X-API-Key: your_secret_key" \
  http://localhost:8765/status
```

## Admin Web Interface

Access the professional admin UI at `http://localhost:5000`:

- 📊 Real-time dashboard with system monitoring
- 🎯 Ticker management interface
- 📈 Interactive candle charts with Chart.js
- ⚙️ Live configuration updates

**Security**: By default, admin UI binds to `127.0.0.1` (localhost only). Set `ADMIN_HOST=0.0.0.0` for external access (use with caution).

## Health Check

```bash
curl http://localhost:8765/health
```

## Documentation

- **GitHub Repository**: https://github.com/obodnikov/eodhd_realtime_candles
- **Full Documentation**: See README.md in repository
- **Admin UI Guide**: See docs/ADMIN_UI.md

## Tags

- `latest` - Latest stable release
- `0.4.0` - Current version with admin UI
- `0.3.1` - Previous stable version

## Support

- **Issues**: https://github.com/obodnikov/eodhd_realtime_candles/issues
- **License**: See repository for license information

## Requirements

- EODHD API key with WebSocket access (EOD+Intraday Extended plan)
- Docker 20.10+ or docker-compose 1.29+

---

**Version**: 0.4.0 | **Maintained by**: obodnikov
