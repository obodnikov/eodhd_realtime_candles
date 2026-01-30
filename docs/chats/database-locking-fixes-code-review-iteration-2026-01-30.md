# Database Locking Fixes - Code Review Iteration 2 - 2026-01-30

## Problem

SQLite database locking errors under high-concurrency multi-worker load:

```
sqlite3.OperationalError: database is locked
```

Occurring in:
1. WebSocket tick callbacks (`websocket_manager.py`)
2. Cleanup operations (`storage.py`)
3. API requests updating `last_candle_request_at` (`routes.py`)

## Root Cause

SQLite's default locking was too aggressive for multi-worker, high-concurrency scenarios. Multiple processes/threads attempting simultaneous writes caused lock contention.

## Solution Implemented

### Option 1: Aggressive SQLite Tuning

**File: `src/storage.py`**

Changes to `_get_connection()`:
- Connection timeout: `10s` (balanced - enough for retries, won't hang APIs)
- Busy timeout: `10000ms` (10 seconds)
- Cache size: `-10000` (10MB cache for better performance)
- Enhanced comments explaining multi-worker tuning

**Centralized Retry Logic with Exponential Backoff:**

Created `_execute_with_retry()` helper method to eliminate code duplication:

```python
def _execute_with_retry(
    self,
    operation: Callable[[], Any],
    operation_name: str,
    is_critical: bool = True,
    max_retries: Optional[int] = None
) -> Optional[Any]:
```

**Configuration:**
- `MAX_RETRIES = 3`
- `RETRY_BASE_DELAY = 0.05` (50ms base delay)
- Exponential backoff: 50ms, 100ms, 200ms

**Refactored Methods:**

1. **`save_candle()`** - Critical operation, raises on failure after retries
2. **`update_ticker_last_request()`** - Non-critical, logs but doesn't raise
3. **`cleanup_old_candles()`** - Non-critical, logs but doesn't raise

### Option 2: Non-blocking last_candle_request Updates

**File: `src/api/routes.py`**

Wrapped all `update_ticker_last_request()` calls in try-except blocks:

```python
# Update last request timestamp (non-blocking, fire-and-forget)
try:
    await asyncio.to_thread(self.storage.update_ticker_last_request, ticker)
except Exception as e:
    logger.warning(f"Failed to update last_request for {ticker}: {e}")
```

This prevents API calls from failing if the metadata update fails. The `last_candle_request_at` field is non-critical metadata used for monitoring - it should never block actual data retrieval.

**Affected endpoints:**
- `GET /candles/{ticker}` 
- `GET /candles/{ticker}/latest`
- `GET /candles/{ticker}/{minutes}` (aggregated)
- `GET /candles/all`
- `POST /candles/multi`

## Code Review Fixes (Iteration 2)

### HIGH Priority Issues Addressed

1. **Reduced timeout from 30s to 10s** - Prevents long API hangs while still allowing retries
2. **Comprehensive test coverage** - Added `tests/test_storage_retry.py` with 16 tests covering:
   - Retry success scenarios
   - Exponential backoff timing
   - Critical vs non-critical operation behavior
   - Concurrent operations
   - Connection configuration validation

### MEDIUM Priority Issues Addressed

3. **Eliminated code duplication** - Extracted retry logic into `_execute_with_retry()` helper
4. **Improved retry timing** - Reduced base delay from 100ms to 50ms for faster recovery
5. **Added cache size documentation** - Noted memory considerations in comments

## Benefits

1. **Reduced Lock Contention**: 10s timeout with retry logic handles transient locks
2. **Automatic Recovery**: Exponential backoff retry logic handles transient locks efficiently
3. **No API Failures**: Non-critical metadata updates can't fail API responses
4. **Better Performance**: Increased cache size reduces disk I/O
5. **Maintainable Code**: DRY principle - single retry implementation
6. **Fast Recovery**: 50ms base delay means quick recovery from transient locks

## Testing

All tests pass:
- `test_storage_retry.py` - 16 new tests for retry logic (100% pass)
- `test_last_candle_request.py` - 10 tests (100% pass)
- `test_storage_websocket_status.py` - 13 tests (100% pass)

**Test Coverage:**
- Retry success on first attempt
- Retry success after multiple lock errors
- Exponential backoff timing verification
- Critical operations raise on failure
- Non-critical operations return None on failure
- Custom max_retries parameter
- Non-lock errors fail immediately
- Concurrent save operations don't deadlock
- Connection configuration validation (timeout, WAL, busy_timeout, cache_size)

## Deployment Notes

- Changes are backward compatible
- No database schema changes required
- WAL mode already enabled (from previous optimization)
- Monitor logs for "Database locked" warnings to track retry frequency

## Monitoring

Watch for these log messages:
- `WARNING: Database locked during {operation}, retry X/3 after Yms`
- `ERROR: Failed {operation} after 3 attempts: database is locked`
- `WARNING: Failed to update last_request for {ticker}: database is locked`

If retries are frequent, consider:
1. Reducing write frequency
2. Batching updates
3. Moving to PostgreSQL for higher concurrency needs

## Performance Characteristics

**Retry Timing:**
- Attempt 1: Immediate
- Attempt 2: +50ms delay
- Attempt 3: +100ms delay
- Total max delay: 150ms for 3 retries

**Timeout Behavior:**
- Connection timeout: 10s (SQLite level)
- Busy timeout: 10s (SQLite level)
- Combined with retries: Up to 10s + 150ms worst case

**Memory:**
- Cache size: 10MB per connection
- Thread-local connections: 1 per worker thread
- Typical deployment: 4-8 workers = 40-80MB cache total
