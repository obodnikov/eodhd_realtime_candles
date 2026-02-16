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
conn.execute("PRAGMA cache_size=-10000;")        # 10MB cache
conn.execute("PRAGMA wal_autocheckpoint=1000;")   # ~4MB WAL before auto-checkpoint
```

### Why:
- **WAL mode**: Readers don't block writers, writers don't block readers
- **synchronous=NORMAL**: Reduces disk fsyncs (acceptable for non-critical data)
- **busy_timeout**: Waits on locks instead of immediate failure
- **timeout**: Python-level wait for locked database
- **cache_size**: Larger page cache reduces disk reads under load
- **wal_autocheckpoint**: Prevents unbounded WAL file growth (default 1000 pages is ~4MB)

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

## 3. Async Handler Rule (CRITICAL)

**ALL SQLite operations in async HTTP handlers MUST run in a thread pool.**

SQLite operations are synchronous and will block the asyncio event loop, causing:
- Request timeouts
- WebSocket disconnections
- Degraded throughput under load

### DON'T:
```python
# BAD - Blocks the event loop!
async def get_candles(self, request):
    candles = self.storage.get_candles(ticker, count)  # BLOCKING!
    return web.json_response({'candles': candles})
```

### DO:
```python
# GOOD - Runs in thread pool, event loop stays responsive
import asyncio

async def get_candles(self, request):
    candles = await asyncio.to_thread(
        self.storage.get_candles,
        ticker,
        count
    )
    return web.json_response({'candles': candles})
```

### Pattern for multiple DB calls:
```python
async def handler(self, request):
    # Run each DB operation in thread pool
    exists = await asyncio.to_thread(self.storage.ticker_exists, ticker)
    if not exists:
        return web.json_response({'error': 'Not found'}, status=404)
    
    data = await asyncio.to_thread(self.storage.get_data, ticker)
    return web.json_response({'data': data})
```

### Why `asyncio.to_thread()`:
- Available in Python 3.9+ (use `loop.run_in_executor()` for older versions)
- Automatically uses the default thread pool executor
- Keeps the event loop free to handle other requests and WebSocket messages
- Essential for maintaining responsiveness under high load

---

## 4. Expensive Operations

### Operations that block the event loop:
- `DELETE` with subqueries or large result sets
- `COUNT(*)` on entire tables
- `GROUP BY` aggregations
- `VACUUM`
- Any full-table scan

### Rules:

1. **Never run expensive queries in HTTP request handlers synchronously**
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

## 5. Cleanup Operations

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
            await asyncio.to_thread(
                self.storage.cleanup_old_candles, 
                ticker, 
                self.max_candles
            )
        self._pending_cleanup.clear()
```

---

## 6. Query Patterns

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

-- GOOD: Batch deletion with timestamp cutoff
DELETE FROM candles WHERE ticker = ? AND timestamp < ? LIMIT 500;
```

---

## 7. Index Requirements

Always ensure indexes exist for:
- Columns used in WHERE clauses
- Columns used in ORDER BY
- Foreign key columns

```sql
CREATE INDEX IF NOT EXISTS idx_candles_ticker_timestamp
ON candles(ticker, timestamp DESC);

-- Composite index for filtered queries
CREATE INDEX IF NOT EXISTS idx_candles_ticker_complete_timestamp 
ON candles(ticker, is_complete, timestamp DESC);
```

---

## 8. Health Endpoint Rule

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

## 9. Monitoring & Debugging

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

## 10. WAL Mode File Handling & Checkpoint Management

When using WAL mode, SQLite creates additional files:
- `database.db-wal` (write-ahead log)
- `database.db-shm` (shared memory)

### Docker volumes must preserve all three files together.

```yaml
# docker-compose.yml
volumes:
  - candle_data:/data  # Contains .db, .db-wal, .db-shm
```

### WAL Checkpoint Rules (CRITICAL)

Without periodic checkpointing, the WAL file grows unbounded and causes:
- Disk space exhaustion (WAL can grow to tens of GB)
- `database is locked` errors on container restart (SQLite tries to recover a huge WAL)
- Startup failures across all workers

**ALWAYS:**
- Set `PRAGMA wal_autocheckpoint=1000` on every connection (built-in defense)
- Run `PRAGMA wal_checkpoint(PASSIVE)` periodically from the websocket worker (every 30s via cleanup task)
- Use PASSIVE mode so checkpoints never block readers or writers

```python
# CORRECT - Periodic checkpoint in Storage class
def checkpoint_wal(self) -> dict:
    conn = self._get_connection()
    result = conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchone()
    # result = (busy, log_pages, checkpointed_pages)
    return {
        'busy': result[0],
        'log_pages': result[1],
        'checkpointed_pages': result[2]
    }

# Called from websocket worker cleanup task (every 30s)
await asyncio.to_thread(storage.checkpoint_wal)
```

**DON'T:**
- Use `TRUNCATE` or `FULL` checkpoint modes in production (they block writers)
- Skip `wal_autocheckpoint` PRAGMA assuming defaults are enough
- Rely solely on SQLite's auto-checkpoint (it can be blocked by long-running readers)

### WAL Recovery Procedure

If the WAL file grows excessively large (e.g., after a crash or missed checkpoints):

```bash
# 1. Stop all processes accessing the database
docker stop <container>

# 2. Try a manual checkpoint first
sqlite3 /path/to/candles.db "PRAGMA wal_checkpoint(TRUNCATE);"

# 3. If checkpoint hangs or fails, remove WAL files (loses uncommitted data)
cp /path/to/candles.db /path/to/candles.db.backup
rm /path/to/candles.db-wal /path/to/candles.db-shm

# 4. Restart
docker start <container>
```

---

## 11. Summary Checklist

Before merging any SQLite-related code:

- [ ] WAL mode enabled?
- [ ] busy_timeout configured?
- [ ] Thread-local connections used?
- [ ] **All DB calls in async handlers wrapped with `asyncio.to_thread()`?**
- [ ] No expensive queries in request handlers?
- [ ] Expensive results cached with TTL?
- [ ] Cleanup operations batched/scheduled?
- [ ] `/health` endpoint database-free?
- [ ] Timing logs for slow operations?
- [ ] `wal_autocheckpoint` configured on connections?
- [ ] Periodic `PRAGMA wal_checkpoint(PASSIVE)` in websocket worker cleanup task?
