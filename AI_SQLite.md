# AI Guidelines for SQLite in Real-Time Services

This file defines rules for SQLite usage in this real-time service to prevent blocking and latency spikes.

---

## 1. Connection Configuration

**ALWAYS** configure SQLite connections with these PRAGMAs:

```python
conn = sqlite3.connect(
    db_path,
    check_same_thread=False,
    timeout=5.0,  # seconds to wait when database is locked
)

conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
conn.execute("PRAGMA busy_timeout=5000;")
```

### Why:
- **WAL mode**: Readers don't block writers, writers don't block readers
- **synchronous=NORMAL**: Reduces disk fsyncs (acceptable for non-critical data)
- **busy_timeout**: Waits on locks instead of immediate failure
- **timeout**: Python-level wait for locked database

---

## 2. Connection Management

### DO:
- Use thread-local connections (`threading.local()`)
- Keep connections long-lived
- Initialize connection once per thread

### DON'T:
- Open/close connections per request
- Share connections across threads without protection
- Reinitialize connections in request handlers

```python
# CORRECT
class Storage:
    def __init__(self):
        self._local = threading.local()

    def _get_connection(self):
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(...)
        return self._local.connection
```

---

## 3. Expensive Operations

### Operations that block the event loop:
- `DELETE` with subqueries or large result sets
- `COUNT(*)` on entire tables
- `GROUP BY` aggregations
- `VACUUM`
- Any full-table scan

### Rules:

1. **Never run expensive queries in HTTP request handlers**
2. **Cache expensive query results** with TTL (e.g., 5 seconds)
3. **Batch large deletions** (LIMIT 500-1000 per batch)
4. **Schedule cleanup operations** on timer, not per-request

```python
# CORRECT - Cache expensive stats
STATS_CACHE_TTL = 5.0

def get_stats(self) -> dict:
    now = time.time()
    if self._stats_cache and (now - self._stats_cache_time) < self.STATS_CACHE_TTL:
        return self._stats_cache

    # ... expensive queries ...

    self._stats_cache = result
    self._stats_cache_time = now
    return result
```

---

## 4. Cleanup Operations

### DON'T:
```python
# BAD - Called on every candle completion, blocks event loop
def _complete_candle(self, ticker):
    self.storage.save_candle(candle)
    self.storage.cleanup_old_candles(ticker, self.max_candles)  # EXPENSIVE!
```

### DO:
```python
# GOOD - Cleanup on timer or threshold
def _complete_candle(self, ticker):
    self.storage.save_candle(candle)
    self._pending_cleanup.add(ticker)

# Separate cleanup task running every 30-60 seconds
async def _cleanup_task(self):
    while True:
        await asyncio.sleep(30)
        for ticker in self._pending_cleanup:
            self.storage.cleanup_old_candles(ticker, self.max_candles)
        self._pending_cleanup.clear()
```

---

## 5. Query Patterns

### Avoid:
```sql
-- BAD: Full table scan
SELECT COUNT(*) FROM candles;

-- BAD: Expensive subquery
DELETE FROM candles WHERE id NOT IN (
    SELECT id FROM candles ORDER BY timestamp DESC LIMIT ?
);
```

### Prefer:
```sql
-- GOOD: Use index, limit results
SELECT COUNT(*) FROM candles WHERE ticker = ? LIMIT 1000;

-- GOOD: Batch deletion
DELETE FROM candles WHERE ticker = ? AND timestamp < ? LIMIT 500;
```

---

## 6. Index Requirements

Always ensure indexes exist for:
- Columns used in WHERE clauses
- Columns used in ORDER BY
- Foreign key columns

```sql
CREATE INDEX IF NOT EXISTS idx_candles_ticker_timestamp
ON candles(ticker, timestamp DESC);
```

---

## 7. Health Endpoint Rule

**`/health` must NEVER touch the database.**

```python
# CORRECT
async def health(self, request):
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# WRONG - Can block on SQLite lock
async def health(self, request):
    stats = self.storage.get_stats()  # NO!
    return {"status": "healthy", "db_ok": stats is not None}
```

---

## 8. Monitoring & Debugging

Add timing logs for database operations:

```python
import time

def cleanup_old_candles(self, ticker: str, max_candles: int):
    start = time.monotonic()
    # ... operation ...
    duration = time.monotonic() - start
    if duration > 0.1:  # Log if > 100ms
        logger.warning(f"Slow cleanup for {ticker}: {duration:.3f}s")
```

---

## 9. WAL Mode File Handling

When using WAL mode, SQLite creates additional files:
- `database.db-wal` (write-ahead log)
- `database.db-shm` (shared memory)

### Docker volumes must preserve all three files together.

```yaml
# docker-compose.yml
volumes:
  - candle_data:/data  # Contains .db, .db-wal, .db-shm
```

---

## 10. Summary Checklist

Before merging any SQLite-related code:

- [ ] WAL mode enabled?
- [ ] busy_timeout configured?
- [ ] Thread-local connections used?
- [ ] No expensive queries in request handlers?
- [ ] Expensive results cached with TTL?
- [ ] Cleanup operations batched/scheduled?
- [ ] `/health` endpoint database-free?
- [ ] Timing logs for slow operations?
