# Claude Chat Export
**Generated: 2025-12-13 16:56:42**

---

👤 **USER:**
> Find the main API endpoints related to candles. Look for:
1. Where candle endpoints are defined (routes/controllers)
2. The current version number in the project
3. Documentation files that need updating for API changes
4. Any existing /candles endpoints to understand the pattern

I'll help you find the main API endpoints related to candles. Let me start by exploring the project structure to understand the codebase layout.

Let me look for Python files and the general structure:

Let me check the directory structure:

Now let me search for candle-related endpoints and check the main files:

Now let me search for version information and check documentation files:

Let me check the config file and look at the documentation structure more closely:

Let me check for any package.json or version files:

Perfect! Let me check the API init file and look at other documentation:

Now let me check what other API-related documentation exists:

Perfect! Now let me create a comprehensive summary document. Let me also check if there are any additional route files:

Excellent! Now I have all the information I need. Let me compile a comprehensive report:

## Summary: Main API Endpoints and Project Information

Based on my exploration of the EODHD Real-Time Candles project, here's what I found:

### 1. Candle API Endpoints

All candle-related endpoints are defined in `/Volumes/mike/src/eodhd_realtime_candles/src/api/routes.py` (463 lines). The endpoints are registered in the `APIRoutes` class via the `_setup_routes()` method (lines 30-53):

**Candle Data Endpoints:**
- `GET /candles/{ticker}` - Get candles for a specific ticker with optional query params:
  - `count` (default: 10)
  - `include_current` (default: true)
  - `from_timestamp` (Unix timestamp filter)
  - `to_timestamp` (Unix timestamp filter)

- `GET /candles/{ticker}/latest` - Get the current incomplete candle for a ticker

- `POST /candles/multi` - Get candles for multiple tickers in a single request
  - Body: `{"tickers": [...], "count": 10, "include_current": true}`

- `DELETE /candles/{ticker}` - Clear candle history for a specific ticker

- `DELETE /candles` - Clear all candle history across all tickers

### 2. Current Version Number

**v0.4.0** (as of 2025-12-13)

Version references found in:
- `/Volumes/mike/src/eodhd_realtime_candles/README.md` (line 1): `# EODHD Real-Time Candle Aggregator v0.4.0`
- `/Volumes/mike/src/eodhd_realtime_candles/src/admin/__init__.py`: `__version__ = '0.4.0'`

### 3. Documentation Files for API

Main documentation files:
- `/Volumes/mike/src/eodhd_realtime_candles/README.md` - Primary API reference (9,226 bytes)
- `/Volumes/mike/src/eodhd_realtime_candles/docs/ADMIN_UI.md` - Admin UI documentation (11,499 bytes)
- `/Volumes/mike/src/eodhd_realtime_candles/docs/IMPLEMENTATION_v0.4.0.md` - Implementation details
- `/Volumes/mike/src/eodhd_realtime_candles/docs/NGINX_REVERSE_PROXY.md` - Reverse proxy setup
- `/Volumes/mike/src/eodhd_realtime_candles/docs/sqlite-performance-tuning.md` - DB optimization

**Sections in README.md covering API:**
- Lines 87-214: Full API Reference including:
  - Authentication methods (lines 89-94)
  - Health & Status endpoints (lines 98-104)
  - Configuration endpoints (lines 106-123)
  - Ticker Management endpoints (lines 125-163)
  - Candle Data endpoints (lines 165-180)
  - Response formats (lines 183-212)
  - n8n integration examples (lines 216-241)

### 4. Related Endpoints Pattern

The project follows a REST API pattern with these endpoint categories:

**Health & Status:**
- `GET /health` - Basic health check (no auth required)
- `GET /status` - Detailed system status
- `POST /reconnect` - Force WebSocket reconnection

**Configuration:**
- `GET /config` - Get current configuration
- `PATCH /config` - Update configuration
- `POST /config/reset` - Reset to defaults

**Ticker Management:**
- `GET /tickers` - List all tracked tickers
- `POST /tickers` - Add ticker(s)
- `DELETE /tickers/{ticker}` - Remove single ticker
- `DELETE /tickers` - Remove multiple/all tickers (with query param confirmation)

**Candle Data:**
- (Listed above in section 1)

### 5. Key Project Files

**Route Definition:**
- `/Volumes/mike/src/eodhd_realtime_candles/src/api/routes.py` - Single routes file (463 lines) containing the `APIRoutes` class

**API Module Structure:**
- `/Volumes/mike/src/eodhd_realtime_candles/src/api/__init__.py` - Exports APIRoutes and middleware
- `/Volumes/mike/src/eodhd_realtime_candles/src/api/middleware.py` - Auth and error handling middleware

**Application Entry Point:**
- `/Volumes/mike/src/eodhd_realtime_candles/src/main.py` - Initializes aiohttp application and registers routes

**Configuration:**
- `/Volumes/mike/src/eodhd_realtime_candles/src/config.py` - Config management with runtime persistence
- `/Volumes/mike/src/eodhd_realtime_candles/.env.example` - Configuration template

### 6. Technology Stack

- **Framework:** aiohttp (async HTTP server)
- **Authentication:** API Key based (X-API-Key header, Bearer token, or query param)
- **Database:** SQLite with WAL mode for performance
- **Admin UI:** Flask on port 5000
- **Data Format:** JSON responses with ISO timestamps

All endpoints require authentication except `/health`. The candle endpoints follow a consistent pattern with `{ticker}` path parameter and support pagination via `count` query parameter.

