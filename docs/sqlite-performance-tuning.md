# SQLite Performance Tuning

## Problem

The `/health` endpoint showed sporadic 1-1.3 second delays even with direct container access (bypassing nginx). Investigation revealed the issue was **blocking SQLite operations** in a **single-worker async process**.

### Root Causes

1. **Default rollback journal mode** - causes read/write blocking
2. **No busy_timeout** - immediate failure on lock contention
3. **Expensive `get_stats()` queries** - 3 full-table scans on every `/status` request
4. **`cleanup_old_candles()` called on every candle completion** - heavy DELETE with subquery

## Implemented Solutions

### 1. SQLite WAL Mode + busy_timeout

**File:** `src/storage.py` - `_get_connection()` method

```python
conn = sqlite3.connect(
    self.db_path,
    check_same_thread=False,
    timeout=5.0,  # seconds to wait when database is locked
)

# Enable WAL mode for better read/write concurrency
conn.execute("PRAGMA journal_mode=WAL;")
# Reduce fsyncs - acceptable trade-off for this use case
conn.execute("PRAGMA synchronous=NORMAL;")
# Wait up to 5 seconds if database is locked
conn.execute("PRAGMA busy_timeout=5000;")
```

**Effects:**
- **WAL (Write-Ahead Logging)**: Readers don't block writers and vice versa. Multiple readers can proceed concurrently with a single writer.
- **synchronous=NORMAL**: Reduces disk fsyncs. Small risk of data loss on OS crash (not process crash), acceptable for this use case.
- **busy_timeout=5000**: Instead of immediately throwing "database is locked", SQLite waits up to 5 seconds for the lock to be released.

### 2. TTL-Based Caching for `get_stats()`

**File:** `src/storage.py` - `get_stats()` method

```python
STATS_CACHE_TTL = 5.0  # seconds

def get_stats(self) -> dict:
    now = time.time()

    # Return cached stats if still valid
    if self._stats_cache and (now - self._stats_cache_time) < self.STATS_CACHE_TTL:
        return self._stats_cache

    # ... expensive queries ...

    # Update cache
    self._stats_cache = result
    self._stats_cache_time = now
    return result
```

**Effects:**
- Stats are cached for 5 seconds
- Monitoring/UI polling `/status` won't trigger full-table scans on every request
- Reduces database load significantly under frequent polling

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| `/health` latency spikes | 1-1.3s | < 10ms |
| Read/write blocking | Yes | No (WAL) |
| Lock contention errors | Immediate fail | 5s retry |
| `/status` DB queries | Every request | Every 5s max |

## Deployment Notes

1. **WAL mode persists** - Once enabled, the database file stays in WAL mode. Two additional files appear: `candles.db-wal` and `candles.db-shm`.

2. **Existing database** - WAL mode is applied on first connection after deployment. No manual migration needed.

3. **Docker volumes** - Ensure the `/data` volume persists the `-wal` and `-shm` files alongside the main `.db` file.

## Future Improvements (Not Yet Implemented)

1. **Reduce `cleanup_old_candles()` frequency** - Currently called on every candle completion. Could be batched or throttled.

2. **Multiple uvicorn workers** - Would allow one worker to handle `/health` while another is busy with SQLite.

3. **Batch deletions** - For large cleanup operations, delete in batches of 500-1000 rows to reduce lock duration.

## References

- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [SQLite PRAGMA Statements](https://www.sqlite.org/pragma.html)
- [SQLite Locking](https://www.sqlite.org/lockingv3.html)
