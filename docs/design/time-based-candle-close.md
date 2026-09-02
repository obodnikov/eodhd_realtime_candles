# EODHD Candle Aggregator — Time-Based Candle Closing and Empty-Minute Fill

Design plan for making **every elapsed interval produce exactly one determinate answer**. Today a
candle is completed only when the *next* tick for that ticker arrives, so a bucket that has ended
can sit in memory indefinitely, and a minute in which nothing traded produces no row at all.
Consumers cannot tell "nobody traded", "the candle is still in memory", and "the feed was down"
apart — all three look like a missing row.

> Status: **Not started — design only.** Two tasks, deliberately separable: §3 closes real candles
> on a timer and invents nothing; §4 writes a zero-volume candle for a minute that demonstrably had
> no trades. §3 is a straight improvement and can ship alone. §4 changes what "a candle" means and
> **requires an explicit amendment to [AI_WEBSOCKET_ENGINE.md](../../AI_WEBSOCKET_ENGINE.md)** — see
> §4.3 — so it needs the owner's decision before any code is written.
>
> Read first: [ARCHITECTURE.md](../../ARCHITECTURE.md) §4.2, §5.2 and §7,
> [AI_WEBSOCKET_ENGINE.md](../../AI_WEBSOCKET_ENGINE.md), [AI.md](../../AI.md), and
> [AI_SQLITE.md](../../AI_SQLITE.md) / [AI_POSTGRESQL.md](../../AI_POSTGRESQL.md) for the write path.

---

## 1. Goal & scope

**Use case.** A downstream board polls `GET /candles/{ticker}?include_current=false` once a minute
and computes indicators from the closed bars. It needs the bar for minute *M* to be available
shortly after *M* ends, and it needs to distinguish an untraded minute from a lost one.

**In scope**

- A background task that completes any in-memory candle whose interval has ended, independently of
  tick arrival (§3).
- A guard so a late tick cannot resurrect and overwrite an already-completed bucket (§3.2).
- An optional background task that writes a zero-volume candle for an interval that ended with no
  ticks, under strict preconditions (§4).
- Counters for both, surfaced through `/status`.

**Out of scope**

- Pushing candle-close events to clients. The `set_on_candle_complete` hook exists in
  [`src/candle_engine.py`](../../src/candle_engine.py) and has no subscriber; wiring a push channel
  to it is a separate design. This document only makes the hook fire on time.
- Any change to the REST contract, the `candles` schema, or the auth model.
- Back-filling history. Both tasks only ever act on the interval that just ended.
- Aggregation (`GET /candles/{ticker}/{minutes}`). It reads completed base candles and benefits
  automatically; `has_gaps` keeps working unchanged.

### Settled decisions

- **The two tasks are independent and land in that order.** §3 makes real data available on time and
  invents nothing. §4 is only worth discussing once §3 has removed the "stuck in memory" cause,
  because today the two causes are mixed together in the same symptom.
- **The synthetic-candle marker already exists in the schema.** A real candle always has
  `tick_count >= 1`; a synthesised one carries `tick_count = 0`. No new column, no new response
  field, no migration. Consumers that want only traded minutes filter on `tick_count > 0`.
- **Neither task ever writes a price that was not traded.** A synthetic candle's four prices are all
  the previous candle's close — the last actually traded price. Nothing is averaged, interpolated,
  or carried across a gap of unknown cause.
- **A real candle always beats a synthetic one**, whatever the arrival order.
- **The engine stays the mechanism, the worker stays the policy.** Session windows, feed continuity
  and eligibility are decided in [`src/websocket_worker.py`](../../src/websocket_worker.py); the
  engine exposes primitives and keeps no notion of market hours.

---

## 2. Background — why a minute can have no candle

`CandleEngine.process_tick` completes the previous bucket inside the branch that handles an incoming
tick ([`src/candle_engine.py`](../../src/candle_engine.py), `if candle_start > current.start_timestamp`).
There is no timer anywhere in the completion path. Three consequences, in the order they bite:

1. **A traded minute stays invisible until the next trade.** The bucket is finished, its OHLCV is
   final, and it sits in `_current_candles` where `include_current=false` cannot see it. For a
   ticker that trades every second this is imperceptible; for one that trades every few minutes it
   is the dominant source of delay.
2. **An untraded minute produces nothing at all**, and looks identical to case 1 and to an outage.
3. **Interval-count indicators drift.** A consumer computing EMA(3) or RSI(5) over "the last N bars"
   silently uses a different span of wall-clock time per ticker.

A downstream board (Terra Runtime) measured this against its own per-minute journal — 39 841 records
over 15 sessions, 9 US equities, 11–31 Aug 2026. Splitting two-minute gaps by how much wall time
passed between polls separates the two causes:

| Ticker | gap from the source (no candle existed) | gap from the consumer's polling |
| --- | ---: | ---: |
| ALAB | 214 | 176 |
| VRT | 194 | 151 |
| COHR | 145 | 177 |
| AMAT | 122 | 151 |
| ORCL | 103 | 127 |
| KLAC | 88 | 140 |
| CRWV | 78 | 92 |
| MU | 68 | 56 |
| NVDA | 42 | 46 |
| **Total** | **1054** | **1116** |

Roughly half of the missing minutes originate here, and the share tracks liquidity as expected. The
measurement is indicative, not exact: only gaps of exactly two minutes were classified, and it
cannot separate case 1 from case 2 — which is precisely the ambiguity this design removes.

---

## 3. Task 1 — close a candle when its interval ends

### 3.1 Behaviour

A background task in the WebSocket worker wakes shortly after each interval boundary and completes
every in-memory candle whose bucket has ended. Completion is the existing path — same `Candle`
object, same write queue, same `_pending_cleanup` entry, same `_on_candle_complete` callback — so
nothing downstream distinguishes a timer-closed candle from a tick-closed one. That is the point:
they are the same candle, delivered on time.

The task closes bucket *B* once `now >= B_end + candle_close_grace_seconds`. The grace exists because
tick timestamps trail wall clock: a trade stamped `:59.8` may reach the queue at `:00.3`, and closing
at exactly `:00.000` would drop it from its own bucket.

### 3.2 The late-tick hazard — the one thing that can go wrong

`process_tick` accepts any tick newer than `tick_max_age_seconds` (default **180**). Today that is
harmless: an old tick lands in its bucket, which is still open or recreated, and the bucket closes
later. Once buckets close on a timer, a tick arriving for an already-completed bucket takes the
`ticker not in self._current_candles` branch and starts a **new** current candle at that old
`start_timestamp`. When it is completed, `save_candle` runs `INSERT OR REPLACE` against
`UNIQUE(ticker, timestamp, interval_minutes)` and the properly-closed bar is replaced by a one-tick
one. That is silent data loss and must be designed out, not discovered in production.

**Decision: drop ticks whose bucket is already completed, and count them.** The engine records
`_last_completed_start[ticker]`; `process_tick` gains a guard next to the existing stale and
out-of-order guards:

```python
last_done = self._last_completed_start.get(ticker)
if last_done is not None and candle_start <= last_done:
    self._late_tick_dropped_count += 1
    return
```

This is a **behaviour change** and must be stated in the changelog: ticks up to
`tick_max_age_seconds` old are no longer guaranteed to be aggregated. The two situations that
produce them are a delayed feed (rare, and the grace period covers the normal case) and a burst of
buffered ticks after a reconnect (where the affected minutes are of unknown quality anyway). The
counter is what makes the trade-off auditable: if `late_tick_dropped` is non-trivial in production,
raise `candle_close_grace_seconds` rather than removing the guard.

Rejected alternative: lag the close by `tick_max_age_seconds`. It removes the hazard but makes a
candle visible three minutes after its interval ends, which is worse than today's behaviour for the
liquid case and defeats the goal.

### 3.3 Changes by file

**[`src/candle_engine.py`](../../src/candle_engine.py)**

- New state in `__init__`: `self._last_completed_start: Dict[str, int] = {}` and
  `self._late_tick_dropped_count = 0`.
- `_complete_current_candle_locked` records `self._last_completed_start[ticker] = current.start_timestamp`
  before deleting the entry from `_current_candles`.
- New public method:

  ```python
  def close_due_candles(self, now_timestamp: Optional[int] = None,
                        grace_seconds: float = 0.0) -> List[Candle]:
      """Complete every in-memory candle whose interval has ended.

      Returns the candles completed by this call (empty list is the normal case).
      """
  ```

  It takes `self._lock` once, computes the cutoff bucket from
  `now_timestamp - grace_seconds`, and calls `_complete_current_candle_locked` for every ticker
  whose `start_timestamp` is below it. It does no I/O — completion enqueues, as it does today.
- `process_tick`: the late-tick guard from §3.2, placed after the existing `tick_max_age_seconds`
  and out-of-order checks so the existing counters keep their meaning.
- `remove_ticker` drops the ticker's `_last_completed_start` entry; `set_interval` clears the whole
  map, because bucket boundaries change with the interval.
- `get_candle_write_metrics()` reports `late_tick_dropped`.

**[`src/websocket_worker.py`](../../src/websocket_worker.py)**

- New task, modelled on `candle_write_flush_task`:

  ```python
  async def candle_close_task(candle_engine: CandleEngine, grace_seconds: float,
                              poll_interval_seconds: float = 1.0):
  ```

  It sleeps `poll_interval_seconds` and calls
  `await asyncio.to_thread(candle_engine.close_due_candles, None, grace_seconds)`. The call is
  wrapped in a thread because it takes the engine's `threading.Lock`, which tick workers hold; the
  event loop must not wait on it. It catches `asyncio.CancelledError` and exits, logs and continues
  on anything else — the contract every task in this file already follows.
- Wire it in `main()` beside the other task handles, and cancel + await it in the shutdown sequence
  before the final `complete_all_candles` / flush pair. Order matters: the close task must be
  stopped before the final flush so it cannot enqueue after the last write.

**[`src/config.py`](../../src/config.py)**

- `candle_close_grace_seconds: float`, env `CANDLE_CLOSE_GRACE_SECONDS`, default `2.0`.
- `validate()` rejects a negative value and anything `>= interval_seconds`.
- Add to `get_public_config` alongside the other candle-engine tuning values.

### 3.4 What this changes for an operator

A ticker with no current candle disappears from `active_candles_status` between intervals, so the
admin UI's active-candle panel will blink empty for quiet tickers. That is accurate — there is no
candle in progress — but it is a visible change worth noting in [docs/ADMIN_UI.md](../ADMIN_UI.md).

---

## 4. Task 2 — a zero-volume candle for an untraded interval

### 4.1 Values written

| Field | Value | Why |
| --- | --- | --- |
| `open`, `high`, `low`, `close` | the **previous candle's close** | the last actually traded price; nothing invented, no range implied |
| `volume` | `0` | a measured zero, not "unknown" |
| `tick_count` | `0` | the marker: no real candle can have this |
| `is_complete` | `true` | the interval has ended; nothing more can arrive for it |
| `timestamp`, `datetime_utc`, `interval_minutes` | as for any candle | |

Explicitly **not**: the previous candle's high/low (implies a range that did not occur), an average
of anything, or a null volume (`0` here is the observation).

### 4.2 Preconditions — when a synthetic candle must not be written

All five must hold. Any failure means "unknown", and unknown stays absent.

1. **Chain intact.** `_last_completed_start[ticker] == B - interval_seconds` — the immediately
   preceding interval produced a candle (real or synthetic). This single rule does most of the work:
   it prevents filling after an outage, prevents fabricating a session's opening minutes before the
   first trade, and removes any need for a market-holiday calendar, because on a holiday the chain
   never starts.
2. **Feed continuously connected across the interval.** The task samples
   `(connected, connection_count)` from `WebSocketManager.get_status()` on each run; the interval is
   eligible only if both samples show `connected=True` with an unchanged `connection_count`. A
   reconnect inside the interval disqualifies it.
3. **Ticker subscribed across the interval**, by the same two-sample rule against
   `subscribed_tickers`.
4. **Inside the configured session window** (§4.5). Without this, an illiquid ticker accumulates flat
   candles all night at its last close, which would be plainly wrong.
5. **No candle already exists for the bucket** — neither completed nor in `_current_candles`. A
   bucket with an open current candle had ticks and is not empty.

### 4.3 The rule conflict this creates — read before implementing

[AI_WEBSOCKET_ENGINE.md](../../AI_WEBSOCKET_ENGINE.md) states, under *Candle correctness*: "Never
fabricate or interpolate candle values." [CLAUDE.md](../../CLAUDE.md) states: "Never fake candle
data." Task 2 writes a row for an interval in which no tick arrived. That is close enough to the
prohibition that it cannot be shipped as a quiet exception.

The argument for the amendment: a candle written under all five preconditions of §4.2 is a
*recorded observation* — the feed was connected, the ticker was subscribed, the previous interval
produced a candle, and nothing traded. No price is invented; the price is the last traded price and
the volume is a true zero. Interpolation is what happens when the outcome is unknown and a value is
supplied anyway, and §4.2 exists to make that case impossible.

If the owner accepts it, the same change set must add to *Candle correctness* in
`AI_WEBSOCKET_ENGINE.md`:

> A zero-volume candle may be written for an interval that ended with no ticks, and only when the
> feed was continuously connected, the ticker continuously subscribed, the preceding interval
> produced a candle, and the interval falls inside the configured session window. It carries
> `tick_count = 0`, and its four prices are the previous candle's close. Any interval failing one of
> those conditions stays absent. This is the single exception to "never fabricate"; do not widen it.

If the owner does not accept it, **drop Task 2** and take the alternative in §10 instead.

### 4.4 Changes by file

**[`src/candle_engine.py`](../../src/candle_engine.py)**

- New state: `self._last_close: Dict[str, float] = {}`, written by
  `_complete_current_candle_locked`; `self._synthetic_candle_count = 0`.
- New public method:

  ```python
  def synthesize_empty_candle(self, ticker: str, bucket_start: int) -> Optional[Candle]:
      """Write a zero-volume candle for an interval that had no ticks.

      Returns None when the engine's own conditions fail: no known previous close,
      a broken chain, or an existing candle for the bucket. Caller-side eligibility
      (feed continuity, subscription, session window) is not checked here.
      """
  ```

  Under `self._lock` it enforces preconditions 1 and 5, builds the `Candle`, enqueues it through
  `_enqueue_candle_write_locked`, updates `_last_completed_start` and `_last_close`, fires
  `_on_candle_complete`, and increments the counter. It reuses the existing enqueue path so queue
  saturation and eviction behave identically.
- `_enqueue_candle_write_locked` gains one rule: **a pending entry with `tick_count >= 1` is never
  replaced by one with `tick_count == 0`**, mirroring the existing "never let an incomplete update
  overwrite a completed candle" guard. With §3.2 in place a late real candle for a filled bucket
  should be impossible; this is the belt-and-braces backstop.
- `get_candle_write_metrics()` reports `synthetic_candles_written`.

**[`src/websocket_worker.py`](../../src/websocket_worker.py)**

- New task `empty_minute_fill_task(candle_engine, ws_manager, config)`, scheduled to run once per
  interval a little after the close task (see §5). It holds the previous run's
  `(connected, connection_count, subscribed_tickers)` snapshot, evaluates preconditions 2–4, and
  calls `await asyncio.to_thread(candle_engine.synthesize_empty_candle, ticker, bucket)` per eligible
  ticker. It logs one summary line per run, not one per ticker.
- Session-window helper, local to this module — `zoneinfo` is stdlib and the container carries the
  system tz database:

  ```python
  def _inside_fill_session(bucket_start: int, mode: str) -> bool:
      """mode: 'off' | 'regular' (09:30–16:00 ET) | 'extended' (04:00–20:00 ET)."""
  ```

  Weekends are excluded; holidays are handled by precondition 1, not by a calendar.
- Wire and cancel it alongside the others; when the mode is `off` the task is not started at all.

**[`src/config.py`](../../src/config.py)**

- `empty_minute_fill: str`, env `EMPTY_MINUTE_FILL`, default **`off`**. Values `off` / `regular` /
  `extended`. `validate()` rejects anything else.
- Add to `get_public_config`.

**[`.env.example`](../../.env.example) and [README.md](../../README.md)**

- Document `CANDLE_CLOSE_GRACE_SECONDS` and `EMPTY_MINUTE_FILL` in the configuration reference.
- In *Response Formats → Candle Object*, state that `tick_count: 0` marks a synthesised
  zero-volume candle and that clients wanting only traded intervals filter on `tick_count > 0`.

### 4.5 Why the session window has to be new work

The service has no notion of market hours anywhere — no timezone handling beyond UTC bucketing, no
session constants, no calendar. That is correct for tick aggregation, which should record whatever
arrives. Task 2 is the first feature that needs to know when silence is meaningful, which is why the
default is `off` and why the window lives in the worker rather than the engine.

---

## 5. How the two tasks interact

Ordering within one interval boundary *B_end*:

```
B_end + grace          candle_close_task completes real candles for bucket B
B_end + grace + delta  empty_minute_fill_task fills tickers that produced nothing
```

`delta` must be greater than zero so the fill task never races a bucket the close task is about to
complete; one second is ample, since both act on in-memory state. Precondition 5 makes the race
harmless even if the ordering slips, but relying on that would be sloppy.

Both tasks enqueue rather than write; `candle_write_flush_task` persists them within 0.25 s, as it
does for tick-closed candles today.

---

## 6. Edge cases & robustness

- **Interval change while running.** `set_interval` force-completes current candles; it must also
  clear `_last_completed_start` and `_last_close`, otherwise the chain rule compares against a bucket
  boundary from the old grid and the first fill after the change would be wrong.
- **Ticker removed.** `remove_ticker` clears both maps for that ticker. A ticker re-added later
  starts with no chain and therefore no fill until its first real candle.
- **Shutdown.** The close task is cancelled *before* the existing `complete_all_candles` and final
  flush, so the shutdown sequence in `main()` keeps its current guarantees. The fill task is
  cancelled first and never runs during shutdown — a partial interval is not an empty one.
- **Restart.** Both maps are in-memory only. After a restart there is no chain, so no fill happens
  until each ticker has produced one real candle. Deliberate: after a gap of unknown length, silence
  is not evidence.
- **Queue saturation.** Synthetic candles go through the same bounded queue and the same eviction
  policy. Under saturation they are dropped like any other write and counted in
  `candle_write_dropped`.
- **Clock skew between the worker and the feed.** The grace period absorbs the normal case; a large
  skew shows up as a rising `late_tick_dropped`, which is why the counter is not optional.
- **Multi-worker.** Both tasks live in the WebSocket worker only. The single-writer invariant in
  [AI_WEBSOCKET_ENGINE.md](../../AI_WEBSOCKET_ENGINE.md) is unchanged; API workers gain nothing to do.
- **PostgreSQL backend.** No schema change, and `save_candle` upsert semantics are equivalent on both
  backends. Nothing in `scripts/init_postgres.sql` moves.

---

## 7. What consumers see

- **`GET /candles/{ticker}?include_current=false`** — same shape, bars appear promptly after their
  interval ends, and (with fill enabled) the minute grid has no holes inside a session.
- **`GET /candles/{ticker}/{minutes}`** — aggregation reads completed base candles, so 15-minute
  bars stop being assembled from a partial set. `has_gaps` and `actual_candles` keep their meaning
  and will simply be true and full more often.
- **Volume statistics improve.** A consumer computing "median volume for this minute-of-session"
  currently samples only intervals that had trades, which biases the median upward and understates
  relative volume for quiet names. Zero-volume rows correct that.
- **ATR-style measures shrink for quiet tickers**, because a flat interval has zero true range. This
  is arithmetically correct but it is a level change, and any consumer with an ATR threshold should
  be told before fill is switched on.
- **Opting out costs one predicate**: `tick_count > 0`.

---

## 8. Testing

`pytest` from the repo root. Extend [`tests/test_candle_engine.py`](../../tests/test_candle_engine.py)
and [`tests/test_websocket_worker.py`](../../tests/test_websocket_worker.py), matching the
`unittest.TestCase` + temporary-SQLite style already in those files. No real WebSocket, no real
clock — pass `now_timestamp` explicitly.

**Task 1**

- A bucket with ticks and no following tick is completed by `close_due_candles` and becomes visible
  to `get_candles(include_current=False)`.
- `close_due_candles` is a no-op inside the grace window and completes once past it.
- A second call does not re-complete or duplicate.
- A tick for an already-completed bucket is dropped, leaves the stored candle untouched, and
  increments `late_tick_dropped`.
- A tick for the *current* bucket after an earlier bucket was timer-closed is processed normally.
- `set_interval` and `remove_ticker` clear `_last_completed_start`.
- The shutdown sequence still flushes everything after the task is cancelled.

**Task 2**

- A fill writes exactly the values in §4.1 for exactly one bucket.
- No fill when the preceding bucket has no candle (chain broken).
- No fill when `connection_count` changed between samples, or `connected` was false at either.
- No fill when the ticker was not subscribed at both samples.
- No fill outside the session window, and none at all when the mode is `off`.
- No fill when a current candle exists for the bucket.
- A pending real candle is not replaced by a synthetic one for the same key, in either enqueue order.
- `_inside_fill_session` boundaries: 09:29 / 09:30 / 15:59 / 16:00 ET, a Saturday, and a DST
  changeover day.

---

## 9. File-by-file checklist

| File | Task | Change |
| --- | --- | --- |
| [`src/candle_engine.py`](../../src/candle_engine.py) | 1 | `_last_completed_start`, `close_due_candles`, late-tick guard + counter, clear on `set_interval` / `remove_ticker` |
| [`src/candle_engine.py`](../../src/candle_engine.py) | 2 | `_last_close`, `synthesize_empty_candle`, real-beats-synthetic rule in `_enqueue_candle_write_locked`, counter |
| [`src/websocket_worker.py`](../../src/websocket_worker.py) | 1 | `candle_close_task`, wiring, shutdown ordering |
| [`src/websocket_worker.py`](../../src/websocket_worker.py) | 2 | `empty_minute_fill_task`, `_inside_fill_session`, feed-continuity sampling |
| [`src/config.py`](../../src/config.py) | 1, 2 | `candle_close_grace_seconds`, `empty_minute_fill`, `validate()`, `get_public_config` |
| [`src/api/routes.py`](../../src/api/routes.py) | 1, 2 | nothing — new counters ride along in `/status` via `get_candle_write_metrics()` |
| [`.env.example`](../../.env.example) | 1, 2 | both new variables, with comments |
| [`README.md`](../../README.md) | 1, 2 | configuration reference; `tick_count: 0` note on the Candle Object; changelog entry |
| [`ARCHITECTURE.md`](../../ARCHITECTURE.md) | 1, 2 | §4.3 background-automation list gains both tasks; §5.2 flow diagram gains the timer path |
| [`AI_WEBSOCKET_ENGINE.md`](../../AI_WEBSOCKET_ENGINE.md) | 2 | the *Candle correctness* amendment quoted in §4.3 — **only with the owner's approval** |
| [`docs/ADMIN_UI.md`](../ADMIN_UI.md) | 1 | note that the active-candle panel empties between intervals for quiet tickers |
| [`tests/test_candle_engine.py`](../../tests/test_candle_engine.py) | 1, 2 | cases from §8 |
| [`tests/test_websocket_worker.py`](../../tests/test_websocket_worker.py) | 1, 2 | task lifecycle, session window, continuity sampling |

Schema, auth, the process split and the REST contract are untouched — none of the "do not change
without explicit approval" items in [ARCHITECTURE.md](../../ARCHITECTURE.md) §7 are in play.

---

## 10. Open questions

1. **Does the owner accept the `AI_WEBSOCKET_ENGINE.md` amendment in §4.3?** Task 2 does not start
   until this is answered. If the answer is no, the conservative alternative is to store nothing and
   instead report emptiness: add an `empty_intervals: [timestamp, …]` list to the candle responses,
   computed from the interval grid and the feed-continuity record. Same information, no synthetic
   rows — but a new response field, and every consumer has to act on it, which is why it is the
   second choice rather than the first.
2. **Which session window is the default once fill is enabled** — `regular` or `extended`? The
   service currently ingests pre-market and after-hours ticks, so `extended` is the consistent
   answer, but it produces far more synthetic rows for quiet tickers and interacts with
   `MAX_CANDLES_STORED` retention.
3. **Should `CANDLE_CLOSE_GRACE_SECONDS` and `EMPTY_MINUTE_FILL` be runtime-editable through
   `PATCH /config`,** or stay environment-only? Runtime editing is convenient for tuning the grace
   against observed `late_tick_dropped`, but the config surface is currently a short, deliberate list.
4. **Is `tick_max_age_seconds = 180` still the right value** after §3.2? With buckets closing on a
   timer, most of what that window admits is now dropped anyway. Lowering it would make the intent
   explicit; it is a separate decision and is not changed by this design.
5. **How is `late_tick_dropped` watched after rollout?** The counter is only useful if somebody looks
   at it. `/status` exposes it, but there is no alerting in the service today.
