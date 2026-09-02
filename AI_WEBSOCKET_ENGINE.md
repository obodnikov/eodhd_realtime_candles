# AI rules — WebSocket Ingest & Candle Engine (Python / asyncio)

Scope: `src/websocket_worker.py`, `src/websocket_manager.py`, `src/candle_engine.py`,
`src/candle_aggregator.py` — the single WebSocket worker that ingests EODHD ticks, aggregates them
into OHLCV candles, and flushes them to the DB. This is the hot path and the write-owner of the
system. See [ARCHITECTURE.md](ARCHITECTURE.md) §4.2–§4.3 and §5.2 for placement; this file is the
coding contract. Persistence rules live in [AI_SQLITE.md](AI_SQLITE.md) and
[AI_POSTGRESQL.md](AI_POSTGRESQL.md); REST rules in [AI_REST_API.md](AI_REST_API.md).

## Single-writer invariant

- Only the WebSocket worker writes candles and ticker status in production. API workers are
  read-mostly. Do not add candle/status writes to API handlers.
- Preserve the process split: no HTTP serving in this worker, no tick processing in API workers.

## Tick flow & backpressure (do not bypass)

- Ticks enter through `WebSocketManager.on_message` → the awaited `on_tick` callback → a **bounded**
  `asyncio.Queue(maxsize=TICK_QUEUE_MAXSIZE)`, consumed by `TICK_WORKER_CONCURRENCY` workers calling
  `CandleEngine.process_tick`. Keep this pipeline; do not call `process_tick` directly from the
  socket callback.
- Enqueue with `put_nowait` and handle `asyncio.QueueFull` explicitly (drop/log with a counter) —
  never let the queue grow unbounded and never silently swallow a dropped tick without metrics.
- `set_on_tick(..., fire_and_forget=False)`: the callback is awaited. Keep it cheap and
  non-blocking so the socket read loop keeps draining.

## No blocking the event loop

- **All DB access from this worker must go through `asyncio.to_thread(...)`** (`sqlite3`/`psycopg2`
  are synchronous). Never call storage methods directly from a coroutine.
- The hot path must not write to the DB per tick. `process_tick` updates in-memory candle state and
  enqueues writes; persistence happens only in the interval flush tasks
  (`flush_pending_candle_writes`, `flush_pending_ticker_statuses`).
- Use `await asyncio.sleep(...)` for pacing; never `time.sleep`.

## Background tasks

- Each background task (`cleanup_task`, ticker-sync, WS-status, active-candles, ticker-status
  flush, candle-write flush) must: catch `asyncio.CancelledError` and exit cleanly, log-and-continue
  on other exceptions (never let a task die silently), and be cancelled + awaited on shutdown.
- On shutdown, flush pending candle writes and ticker statuses before exit so no in-memory candle is
  lost. Preserve the existing final-flush sequence.
- Cleanup and WAL checkpoint (`storage.checkpoint_wal`) run on the cleanup timer, not per candle —
  keep expensive/batched work off the hot path.

## Reconnect & subscription

- Reconnect uses bounded backoff (`ws_reconnect_delay`); keep backoff bounded and log attempts. Do
  not busy-loop on connection failure.
- Ticker subscribe/unsubscribe is driven by the DB via the ticker-sync task (diff DB symbols vs
  subscribed set). Respect the EODHD `MAX_TICKERS` cap; don't subscribe beyond it.

## Candle correctness

- OHLCV rules are load-bearing: open = first tick of interval, high/low = running extremes,
  close = last tick, volume accumulates, `tick_count` increments. Never fabricate or interpolate
  candle values.
- Interval bucketing uses UTC. Keep timezone-aware UTC math; a candle's `timestamp` is the interval
  start (Unix seconds). `candle_aggregator.py` only combines completed base candles and must set
  `has_gaps` when expected < actual sub-candles — do not hide gaps.

## Testing

- `pytest` + `pytest-asyncio`. Cover: tick aggregation into correct OHLCV, queue-full handling,
  flush persists queued writes, backoff on reconnect, and graceful task cancellation/shutdown flush.
  Mock storage with `pytest-mock`; do not hit a real WebSocket.
