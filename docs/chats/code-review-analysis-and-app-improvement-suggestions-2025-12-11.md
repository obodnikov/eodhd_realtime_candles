# Claude Chat Export
**Generated: 2025-12-11 15:27:22**

---

👤 **USER:**
> I made a code review for storage.py code and whole app.
Check attached file with code review.
Check CLAUDE.md, AI.md and docs/ , docs/chats/ for documentation and previous conversations
Suggest improvements

I'll review the attached code review document and check the relevant files to understand the context and suggest improvements.

Now I have a complete picture. Let me summarize the code review and propose improvements.

---

## Code Review Summary & Improvement Proposals

Based on the attached review and my analysis of your codebase, here are the key findings and proposed solutions:

---

### Problem Identified

The `/health` endpoint shows sporadic 1-1.3 second delays even with direct container access. This is caused by **blocking SQLite operations** in a **single-worker async process**:

1. **`cleanup_old_candles()`** - called on **every candle completion** ([candle_engine.py:111](src/candle_engine.py#L111))
2. **`get_stats()`** - does 3 full-table scans on `/status` endpoint ([routes.py:90](src/api/routes.py#L90))
3. **No WAL mode** - default rollback journal causes read/write blocking
4. **Single worker** - `python -m src.main` runs one event loop, blocking all requests during SQLite I/O

---

### Proposed Improvements

#### 1. SQLite Tuning (WAL + busy_timeout)

Modify `_get_connection()` in [storage.py:65-73](src/storage.py#L65-L73):

```python
def _get_connection(self) -> sqlite3.Connection:
    if not hasattr(self._local, "connection"):
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=5.0,  # wait up to 5s on lock
        )
        conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        
        self._local.connection = conn
    return self._local.connection
```

**Effect**: Better read/write concurrency, smoother handling of concurrent access.

---

#### 2. Reduce `cleanup_old_candles()` Frequency

Currently called on **every completed candle**. Proposal: call it only when exceeding threshold by a margin (e.g., every 10 excess candles):

```python
# In candle_engine.py _complete_current_candle()
# Instead of always calling cleanup, check threshold
if some_condition:  # e.g., candle_count > max_candles + 10
    self.storage.cleanup_old_candles(ticker, self.max_candles)
```

Or move cleanup to a background task running every N minutes.

---

#### 3. Cache `get_stats()` Results

The `/status` endpoint triggers full-table scans. Add TTL-based caching:

```python
# Cache stats for 5-10 seconds
_stats_cache: Optional[dict] = None
_stats_cache_time: float = 0
STATS_CACHE_TTL = 5.0  # seconds

def get_stats(self) -> dict:
    now = time.time()
    if self._stats_cache and (now - self._stats_cache_time) < STATS_CACHE_TTL:
        return self._stats_cache
    # ... actual query ...
    self._stats_cache = result
    self._stats_cache_time = now
    return result
```

---

#### 4. Multiple Workers (Dockerfile)

Change [Dockerfile:30](Dockerfile#L30) from:
```dockerfile
CMD ["python", "-m", "src.main"]
```

To use uvicorn with multiple workers:
```dockerfile
CMD ["uvicorn", "src.main:create_app", "--host", "0.0.0.0", "--port", "8765", "--workers", "2", "--factory"]
```

**Note**: This requires refactoring `main.py` to expose an app factory compatible with uvicorn.

---

#### 5. (Optional) Batch Cleanup for Large Deletions

For `cleanup_old_candles()`, if many rows need deletion, do it in batches:

```python
def cleanup_old_candles(self, ticker: str, max_candles: int, batch_size: int = 500):
    # Delete in batches to avoid long locks
    while True:
        cursor.execute('''
            DELETE FROM candles WHERE id IN (
                SELECT id FROM candles WHERE ticker = ?
                ORDER BY timestamp ASC LIMIT ?
            ) AND (SELECT COUNT(*) FROM candles WHERE ticker = ?) > ?
        ''', (ticker, batch_size, ticker, max_candles))
        if cursor.rowcount == 0:
            break
        conn.commit()
```

---

### Priority Ranking

| Priority | Improvement | Complexity | Impact |
|----------|-------------|------------|--------|
| 1 | SQLite WAL + busy_timeout | Low | High |
| 2 | Cache `get_stats()` | Low | Medium |
| 3 | Reduce cleanup frequency | Low | Medium |
| 4 | Multiple workers | Medium | High |
| 5 | Batch cleanup | Medium | Low-Medium |

---

**Would you like me to implement any of these improvements?** I recommend starting with #1 (SQLite tuning) and #2 (stats caching) as they have low risk and high impact.

---

👤 **USER:**
> yes implement #1 and #2

I'll implement both improvements: SQLite WAL tuning and stats caching.

Now let me implement the stats caching. I need to add cache attributes to the `__init__` method and modify `get_stats()`:

Now let me update the `get_stats()` method to use caching:

Both improvements have been implemented:

### Summary of Changes in [storage.py](src/storage.py)

**1. SQLite WAL + busy_timeout tuning** ([lines 66-96](src/storage.py#L66-L96)):
- Added `timeout=5.0` to `sqlite3.connect()` - waits up to 5 seconds when database is locked
- Added `PRAGMA journal_mode=WAL` - enables Write-Ahead Logging for better read/write concurrency
- Added `PRAGMA synchronous=NORMAL` - reduces fsyncs for better performance
- Added `PRAGMA busy_timeout=5000` - waits 5 seconds on locks instead of failing immediately
- Wrapped PRAGMAs in try/except to not break startup if they fail

**2. TTL-based caching for `get_stats()`** ([lines 436-480](src/storage.py#L436-L480)):
- Added `STATS_CACHE_TTL = 5.0` class constant
- Added `_stats_cache` and `_stats_cache_time` instance attributes
- `get_stats()` now returns cached results if less than 5 seconds old
- Avoids expensive full-table scans on frequent `/status` requests

**Expected Impact:**
- WAL mode allows readers and writers to proceed concurrently (no more blocking `/health` during writes)
- Stats caching reduces database load from monitoring/UI polling `/status`
- `busy_timeout` prevents immediate "database is locked" errors during contention

**Note:** After deploying, you may want to delete the existing `.db` file or run `PRAGMA journal_mode=WAL` manually once to switch the database to WAL mode (it persists after first connection).

---

👤 **USER:**
> create implementation description under docs/ directory

Created [docs/sqlite-performance-tuning.md](docs/sqlite-performance-tuning.md) with:

- Problem description and root causes
- Implemented solutions with code snippets
- Expected impact table
- Deployment notes (WAL file handling, Docker volumes)
- Future improvements section for remaining optimizations

---

👤 **USER:**
> update README.md with version 0.3.1

Now let me add a changelog section to the README:

Updated [README.md](README.md):
- Version changed from v1.0 to **v0.3.1**
- Added **Changelog** section with v0.3.1 changes:
  - SQLite performance tuning (WAL, synchronous, busy_timeout)
  - Stats caching (5-second TTL)
  - New documentation file

