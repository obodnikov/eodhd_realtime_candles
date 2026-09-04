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

## [0.9.14] - 2026-09-04

### Added
- **`subscription_health` in `GET /status`.** Reports freshness per subscribed
  ticker, not just per connection: how many are ticking, how many have been
  silent past `SUBSCRIPTION_SILENCE_MINUTES` (default 15), which have never
  produced a tick at all, and the silent ones listed longest-first.

  This exists because two tickers went quiet in production for two days and
  nothing noticed. SPCX and DASH streamed on 2 September and stopped; the
  connection stayed healthy, the other 49 tickers were fine, and no log line
  said otherwise. Over the same session the consolidated tape showed 106 million
  shares for SPCX. EODHD accepts a subscription silently and never streams a
  symbol it does not carry, which is exactly the failure its own reliability
  guidance names: *"Partial symbol starvation: some symbols keep updating while
  others stop. A connection-level health check can look fine while one symbol is
  stale."*

  Silence is reported, not judged. Outside the main session most symbols are
  legitimately quiet, so no threshold can separate "thin stock" from "dead
  subscription" on its own; the consumer decides. `last_tick_at` on
  `GET /tickers` is unchanged and remains the per-row source.

### Added (tooling)
- **`analysis/compare_with_intraday.py`** — reproduces the measurement above
  against any session, so the figures in ARCHITECTURE.md §4.4 can be re-checked
  rather than taken on trust. Reads the service's own candles and the provider's
  full tape, reports volume share and false-empty minutes per ticker, and names
  any ticker producing no candle at all while the tape shows it trading. Refuses
  to run unless the service is aggregating at 1 minute, since the reference bars
  are 1-minute.

### Changed
- **`README.md` and `ARCHITECTURE.md` §4.4 now state what the feed actually
  carries.** The EODHD US stream is Cboe EDGX -- a single exchange, not the
  consolidated (SIP) tape -- so it excludes other venues, off-exchange trades
  and FINRA TRF prints. Measured against the same provider's Intraday Historical
  API across 51 tickers on 3 September 2026: **2.8% of consolidated volume**
  (0.7-5.6% per ticker, median 2.8%), and **88.7% of main-session minutes with
  no candle had trades elsewhere**.

  Prices are real executions, so OHLC is sound; it is volume and the presence of
  a candle that are partial. Anyone reading volume as shares traded on the market
  was reading it wrong, and nothing in the documentation said so.

  This also settles the empty-interval question for good: an absent candle means
  "no EDGX print this minute", not "nobody traded", so filling one would state
  something false almost nine times in ten. The *Candle correctness* rule in
  `AI_WEBSOCKET_ENGINE.md` stands unamended, and the audit added in 0.9.11 has
  served its purpose.

## [0.9.13] - 2026-09-04

### Fixed
- **The silent-feed watchdog tore down a healthy connection every 66 seconds all
  night: 873 reconnects across two nights, 437 per night.** EODHD replays the
  previous session's last trade when a subscription is made. The manager counted
  that snapshot as activity, so `received_tick_on_connection` went true, the
  watchdog stayed in its tight 60-second mode, and the relaxation logic that
  exists for exactly this case never engaged once — the "silent connection" path
  was taken zero times in two days. The trade itself was then dropped by the
  engine as stale, so no candle appeared and nothing in the log hinted at the
  cause.

  A trade now counts as evidence of a live feed only when its trade time is
  close to the present, judged by the same `TICK_MAX_AGE_SECONDS` the engine
  uses to drop stale ticks, so both agree on what a current tick is. Overnight
  the existing relaxation takes over: 300 s, then 600 s, then the
  `WS_MAX_SILENT_TIMEOUT` ceiling — about **33 reconnects a night instead of
  437**. Daytime behaviour is unchanged: the first real tick resets the counter
  and the tight watchdog returns, so a feed that genuinely dies is still caught
  in 60 seconds.

  Stale ticks are still forwarded to the engine, which remains the only place
  that decides whether to aggregate them. The manager only judges liveness.

### Added
- `WebSocketManager` takes `tick_max_age_seconds` (default `0`, meaning every
  tick counts, as before). The WebSocket worker passes the configured value.
- `fresh_tick_count` in `GET /status` alongside `tick_count`. A large gap
  between the two means the feed is mostly replaying old trades.

## [0.9.12] - 2026-09-04

### Fixed
- **The empty-interval audit counted one interval per run of silence instead of
  all of them, understating the extended session roughly fivefold.** The chain
  rule was asked of the engine, which only advances when a candle is actually
  written. Since the audit deliberately writes nothing, every interval after the
  first in a run was reported as `chain_broken` — but a real fill would have
  written a candle for the first, carrying the chain forward and filling the run
  end to end.

  The chain is now tracked by the audit task itself, which advances its own
  record both for real candles and for intervals it judges fillable, exactly as
  a real fill would. `CandleEngine.audit_empty_interval` is replaced by
  `CandleEngine.inspect_interval`, which reports only what the engine knows —
  the interval's state and the last traded close — and holds no policy, in
  keeping with "the engine is the mechanism, the worker is the policy".

  Numbers gathered with 0.9.11 are floors, not totals. In the first measurement
  run, against 51 tickers over two days, the audit reported 9.2% of extended
  session minutes as fillable where 45.0% of minutes had no candle at all; the
  main session showed 7.9% against 13.7%. The second figure of each pair is the
  one to act on.

### Changed
- Observation rows: `engine_reason` becomes `reason` and gains a `chain_intact`
  field. `reason` now reports the first failing condition rather than only the
  engine's view, with values `no_previous_candle`, `chain_broken`,
  `no_known_close`, `outside_session`, `feed_unsteady`, `subscription_changed`
  and `would_fill`. Existing observation files are not migrated — start a fresh
  file for a run on this version.

## [0.9.11] - 2026-09-02

### Added
- **Empty-interval audit — measurement only, writes no candles.** An optional task in the
  WebSocket worker (`empty_interval_audit_task` / `CandleEngine.audit_empty_interval`)
  records, for every interval that produced no candle, whether a zero-volume candle
  *could* have been written and at what price, appending one JSON line per observation to
  a file. It never creates a candle, never enqueues a write and changes no engine state.
  See [docs/EMPTY_INTERVAL_AUDIT.md](docs/EMPTY_INTERVAL_AUDIT.md).

  The point is to settle a question with numbers. An interval can produce no candle for
  two reasons: it had trades but the candle waited in memory for the ticker's next trade,
  or nothing traded at all. Until 0.9.10 these were indistinguishable, and the only
  estimate available — roughly half of missing minutes originating in the service —
  was measured before the timer-based close existed and mixed both causes together. The
  first cause is gone; this measures what remains, so filling empty intervals can be
  decided on observed volume rather than a projection.

- `EMPTY_INTERVAL_AUDIT` (default `off`): `off` / `regular` (09:30–16:00 ET) /
  `extended` (04:00–20:00 ET). Weekends excluded; the window is evaluated in New York
  local time, so daylight saving is handled. Holidays need no calendar — on a holiday the
  chain never starts. The task is not created at all when the mode is `off`.
- `EMPTY_INTERVAL_AUDIT_PATH` (default: `empty_interval_audit.jsonl` beside the database).
- Both are environment-only, like `CANDLE_CLOSE_GRACE_SECONDS`: the task runs in the
  WebSocket worker, so `PATCH /config` refuses them and names the variable to set instead.

### Changed
- The engine now keeps the last traded close per ticker in memory (`_last_close`), cleared
  alongside `_last_completed_start` in `remove_ticker` and `set_interval`. It is reported
  by the audit as the price a synthetic candle would carry; nothing writes it.

### Unchanged
- No candle is fabricated, no schema changes, and the *Candle correctness* rule in
  [AI_WEBSOCKET_ENGINE.md](AI_WEBSOCKET_ENGINE.md) is untouched. Deciding whether to write
  such candles is deliberately left until the measurement is in.

## [0.9.10] - 2026-09-02

### Added
- **Candles are completed when their interval ends, not when the next tick
  arrives.** A background task in the WebSocket worker
  (`candle_close_task` / `CandleEngine.close_due_candles`) completes any
  in-memory candle whose bucket has ended. Previously a finished bucket sat in
  memory until that ticker traded again, so a bar could be invisible to
  `include_current=false` readers for minutes — the delay scaling with how
  illiquid the ticker was. Timer-closed and tick-closed candles take the same
  completion path and are indistinguishable downstream.
- `CANDLE_CLOSE_GRACE_SECONDS` (default `2.0`): how long to wait past an
  interval's end before closing its candle. The grace exists because tick
  timestamps trail wall clock — a trade stamped `:59.8` may arrive at `:00.3`,
  and closing at exactly `:00.000` would drop it from its own bucket. Must be
  at least 0 and less than the candle interval. It is environment-only:
  `PATCH /config` refuses it, because the close task runs in the WebSocket
  worker and would never see a change made on an API worker.
- `late_tick_dropped_count` in `GET /status`, alongside the existing tick-drop
  counters.

### Changed
- **Behaviour change: a tick for an already-completed bucket is now dropped.**
  Ticks up to `TICK_MAX_AGE_SECONDS` (default 180) old are no longer guaranteed
  to be aggregated. This is required, not incidental: candle writes upsert on
  `(ticker, timestamp, interval_minutes)`, so once buckets close on a timer a
  late tick would start a fresh candle at the old bucket start and replace a
  properly closed bar with a one-tick one. That is silent data loss. The two
  situations that produce such ticks are a delayed feed — which the grace period
  covers in the normal case — and a burst of buffered ticks after a reconnect,
  where the affected intervals are of unknown quality anyway. Watch
  `late_tick_dropped_count`: if it is non-trivial in production, raise
  `CANDLE_CLOSE_GRACE_SECONDS` rather than removing the guard.
- The admin dashboard's active-candle panel now empties between intervals for
  quiet tickers, because there genuinely is no candle in progress. See
  [docs/ADMIN_UI.md](docs/ADMIN_UI.md).
- Aggregated candles (`GET /candles/{ticker}/{minutes}`) benefit automatically:
  aggregation reads completed base candles, so a period is no longer assembled
  from a partial set merely because its final bars had not been closed yet.

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
