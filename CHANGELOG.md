# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The version number itself lives in [`src/__init__.py`](src/__init__.py) and is
served by `GET /health` and `GET /status`, so a running instance can always be
compared against this file. See *Versioning and changelog* in
[CLAUDE.md](CLAUDE.md) for the working rule.

> **Note on 0.9.5 – 0.9.8.** These four releases shipped before the changelog
> existed. Their entries below were reconstructed from the git history and cite
> the commits they cover; they are not release notes written at the time, and
> they may omit changes that left no trace in a commit message.

## [0.9.9] - 2026-09-02

### Fixed
- **WebSocket status was never shared between workers on SQLite.**
  `Storage.update_websocket_status()` listed 19 columns but supplied only 18
  values, so every call raised `sqlite3.OperationalError: 18 values for 19
  columns`. The only caller catches and logs all exceptions, so the failure was
  invisible: the `websocket_status` row was never written, and API workers
  always fell through to the "WebSocket worker not started yet" branch and
  reported `connected: false` regardless of the real feed state. Present since
  0.9.5. The PostgreSQL backend was unaffected.

### Added
- Service version exposed in `GET /health` and `GET /status` as `version`.
- `src/__init__.py` now defines `__version__` as the single source of truth.
- `CHANGELOG.md` (this file) and a written versioning rule in `CLAUDE.md`.
- `pytest.ini` setting `testpaths = tests`, so the documented `pytest` command
  no longer collects command-line utilities under `scripts/` that happen to be
  named `test_*`.

### Changed
- `src/admin/__init__.py` no longer carries its own stale `__version__`
  (`0.6.0`); it re-exports the package version.
- **Test suite repaired.** The suite had drifted badly against the code:
  48 failures, two modules that could not be imported at all, and one collection
  error. It now passes in full (384 tests). The repairs were to the tests, not
  to the behaviour they check — renamed class constants (`SAVE_EVERY_N_TICKS`,
  `MAX_RETRIES`) and functions (`setup_auth_middleware`), the queued candle-write
  path needing an explicit flush before a database read, `:memory:` databases
  being invisible across threads, and shutdown tests that cancelled the worker
  task instead of triggering its shutdown signal. `tests/test_websocket_worker.py`
  also dropped from 93 seconds to under one, having previously slept through
  real 30-second task intervals.

## [0.9.8] - 2026-08-07

### Fixed
- WebSocket reconnection after an EODHD 5xx response, which could leave the
  client stuck rather than reconnecting (`8805ffe`).
- Idle-feed watchdog relaxed so a quiet but healthy feed is no longer torn down
  (`8805ffe`).
- Phantom feeds: connections that lingered without delivering data are now
  stopped (`8805ffe`).

### Changed
- AI coding rule set aligned with the actual stack (`e4f72c5`).

## [0.9.7] - 2026-06-01

### Added
- Admin log viewer page backed by an in-memory ring buffer (`e838653`).
- Data-timeout watchdog that detects a silent, dead feed (`44ee636`).

### Fixed
- Faster recovery after an EODHD 500, and cross-process log visibility so logs
  from every worker reach the admin panel (`1965e6c`).

## [0.9.6] - 2026-05-26

### Added
- Exponential backoff on reconnect (`b0f54fd`).

### Changed
- EODHD 500 responses are logged as warnings rather than errors (`b0f54fd`).

### Fixed
- Forced reconnect when EODHD returns 5xx during an active stream (`8041c6c`).

## [0.9.5] - 2026-02-20

### Added
- Guards against stale and out-of-order ticks, with drop metrics exposed
  through `/status` (`0448072`).

### Known issues
- This release introduced the SQLite `update_websocket_status` defect fixed in
  0.9.9.

## [0.9.4] - 2026-01-30

- **Multi-Worker Architecture**: Implemented separate API and WebSocket processes for better scalability
  - **2 API Workers**: Handle HTTP requests in parallel (ports 8765, 8766)
  - **1 WebSocket Worker**: Dedicated tick processing and candle aggregation
  - **Performance**: 50% faster API response time under load, better CPU utilization across cores
  - **Reliability**: Eliminated database locking errors by isolating writes to WebSocket worker
- **Code Quality**: Fixed cleanup task data loss risk with individual ticker processing
- **Testing**: Added comprehensive test coverage (19 new tests for API server and WebSocket worker)
- **Documentation**: Added complete multi-worker deployment guide
- **Configuration**: Updated supervisord.conf with explicit worker definitions and correct port allocation

## [0.4.3] - 2026-01-20

- **Premarket Volume Script Enhancement**: Updated `scripts/premarket_volume.py`
  - Removed interval parameter (now hardcoded to 1m - only interval with premarket data)
  - Increased data retrieval from 30 to 90 days for maximum premarket data points
  - Improved error messages explaining EODHD API premarket data limitations
  - Updated documentation to clarify that only 1-minute intervals include premarket hours (4:00-9:30 AM ET)
  - Simplified CLI usage: `python premarket_volume.py AAPL.US` (no interval parameter needed)

## [0.4.2] - 2025-12-26

- **Bug Fix**: Fixed `delete_all_tickers()` to consistently delete candle data
  - **⚠️ BREAKING CHANGE**: `DELETE /tickers?confirm=true` now deletes candles (previously preserved them)
  - This brings batch ticker deletion in line with single ticker deletion behavior
  - Migration: Use new `POST /candles/cleanup` endpoint to remove orphaned candles from legacy data
- **New Endpoint**: Added `POST /candles/cleanup` to remove orphaned candles
- **Documentation**: Added comprehensive breaking change notice and migration guide
- **Tooling**: Added `scripts/cleanup_orphaned_candles.sh` for automated cleanup

## [0.4.2] - 2025-12-13

- **Admin UI Improvements**: Enhanced admin dashboard user experience
  - Removed unused `ADMIN_SESSION_SECRET` from configuration (auto-generated internally)
  - Fixed Configuration display to show human-readable format (e.g., "5 minutes" instead of "5 min")
  - Added oldest/newest candle timestamps to Database statistics display
  - Candle data now sorted with newest candles on top for better usability
  - Config form inputs now show current values as placeholders for better UX

## [0.4.1] - 2025-12-13

- **New Endpoint**: Added `GET /candles/all` to retrieve candles for ALL tracked tickers
  - Requires `confirm=true` and `max_tickers=N` parameters for safety
  - Returns flat list with ticker field included in each candle
  - Supports same filters as single ticker endpoint (count, timestamps, include_current)

## [0.4.0] - 2025-12-13

- **Admin Web UI**: Added Flask-based admin panel with sqowe branding
- **Interactive Dashboard**: Real-time system monitoring with Chart.js visualizations
- **Ticker Management UI**: Visual interface for managing tickers
- **Candle Data Viewer**: Browse and visualize OHLCV candles with interactive charts
- **Configuration UI**: Web interface for updating service configuration
- **Multi-process Container**: Supervisord manages both REST API and admin UI
- **Configurable Access**: Admin UI host configurable for localhost or external access

## [0.3.1] - 2025-12-11

- **SQLite performance tuning**: Added WAL mode, `synchronous=NORMAL`, and `busy_timeout=5000` for better read/write concurrency
- **Stats caching**: `get_stats()` now caches results for 5 seconds to reduce database load from `/status` polling
- **Documentation**: Added `docs/sqlite-performance-tuning.md` with implementation details
