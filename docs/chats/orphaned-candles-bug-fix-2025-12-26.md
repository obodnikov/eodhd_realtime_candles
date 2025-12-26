# Orphaned Candles Bug Fix - 2025-12-26

## Context

User reported an issue in production where the `/status` endpoint showed inconsistent data:
- `websocket.subscribed_tickers`: ["ALAB"] (1 ticker)
- `database.ticker_count`: 1
- `database.candles_per_ticker`: 46 different tickers (AAPL, AMD, AMZN, etc.)

This discrepancy indicated orphaned candle data in the database.

## Investigation

### Root Cause Identified

The bug was in `src/storage.py`:

1. **`remove_ticker(symbol)`** at line 179-198:
   - ✅ Correctly deletes ticker AND its candles
   - Used when removing individual tickers via `DELETE /tickers/{ticker}`

2. **`delete_all_tickers()`** at line 276-293:
   - ❌ BUG: Only deleted tickers, explicitly preserved candles
   - Used when removing all tickers via `DELETE /tickers?confirm=true`
   - Comment said: "Delete all tickers (candles are preserved)"

This inconsistency meant that batch deletion left orphaned candles in the database.

### How Production Got Into This State

Most likely scenario:
1. User previously tracked 46 tickers
2. User removed all tickers using `DELETE /tickers?confirm=true`
3. The `delete_all_tickers()` method removed tickers from the `tickers` table but left all candles in the `candles` table
4. User added back only ALAB ticker
5. Result: 1 tracked ticker but 46 tickers worth of candle data

## Solution Implemented

### 1. Fixed `delete_all_tickers()` Method

**File:** `src/storage.py:276-296`

**Before:**
```python
def delete_all_tickers(self) -> int:
    """Remove all tickers from tracking (preserves candle data)."""
    # Get count before deletion
    cursor.execute('SELECT COUNT(*) FROM tickers')
    count = cursor.fetchone()[0]

    # Delete all tickers (candles are preserved)  # ← BUG
    cursor.execute('DELETE FROM tickers')

    conn.commit()
    logger.info(f"Deleted all {count} tickers (candle data preserved)")
    return count
```

**After:**
```python
def delete_all_tickers(self) -> int:
    """Remove all tickers from tracking and delete their candle data."""
    # Get count before deletion
    cursor.execute('SELECT COUNT(*) FROM tickers')
    count = cursor.fetchone()[0]

    # Delete all candles first  # ← FIXED
    cursor.execute('DELETE FROM candles')

    # Delete all tickers
    cursor.execute('DELETE FROM tickers')

    conn.commit()
    logger.info(f"Deleted all {count} tickers and their candle data")
    return count
```

### 2. Added Cleanup Method for Orphaned Candles

**File:** `src/storage.py:298-320`

New method to handle existing orphaned data:

```python
def cleanup_orphaned_candles(self) -> int:
    """
    Remove candles for tickers that are no longer tracked.
    Returns count of deleted candle records.
    """
    conn = self._get_connection()
    cursor = conn.cursor()

    # Delete candles where ticker doesn't exist in tickers table
    cursor.execute('''
        DELETE FROM candles
        WHERE ticker NOT IN (SELECT symbol FROM tickers)
    ''')

    deleted = cursor.rowcount
    conn.commit()

    if deleted > 0:
        logger.info(f"Cleaned up {deleted} orphaned candles")
    else:
        logger.debug("No orphaned candles found")

    return deleted
```

### 3. Added API Endpoint

**File:** `src/api/routes.py:55` (route registration)
**File:** `src/api/routes.py:548-556` (handler)

```python
# Route
self.app.router.add_post('/candles/cleanup', self.cleanup_orphaned_candles)

# Handler
async def cleanup_orphaned_candles(self, request: web.Request) -> web.Response:
    """POST /candles/cleanup - Remove candles for tickers that are no longer tracked."""
    deleted = self.storage.cleanup_orphaned_candles()

    return web.json_response({
        'message': 'Orphaned candles cleaned up',
        'deleted_count': deleted,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })
```

### 4. Updated API Response Messages

**File:** `src/api/routes.py:307` and `src/api/routes.py:321`

Updated warning messages to reflect the new behavior:

**Before:**
```python
'warning': 'This will remove all tracked tickers (candle data will be preserved)'
```

**After:**
```python
'warning': 'This will remove all tracked tickers and their candle data'
```

### 5. Created Cleanup Script

**File:** `scripts/cleanup_orphaned_candles.sh`

Automated script that:
- Checks current database state
- Detects orphaned candles
- Prompts for confirmation
- Runs cleanup via API
- Verifies success

### 6. Updated Documentation

**Files Modified:**
- `README.md` - Added cleanup endpoint to API reference
- `README.md` - Updated ticker management notes about deletion behavior
- `docs/ORPHANED_CANDLES_FIX.md` - Comprehensive fix documentation

**README.md Changes:**

**Candle Data Endpoints Table:**
Added new row:
```markdown
| `POST` | `/candles/cleanup` | Remove orphaned candles (for tickers no longer tracked) |
```

**Ticker Management Notes:**
Changed from:
```markdown
- Candle data is **preserved** when tickers are removed
- Re-adding a ticker will restore access to its preserved candles
```

To:
```markdown
- When a ticker is removed, its candle data is **also deleted**
- Use `POST /candles/cleanup` to clean up any orphaned candles from legacy data
```

## Files Modified

1. `src/storage.py` - Fixed `delete_all_tickers()`, added `cleanup_orphaned_candles()`
2. `src/api/routes.py` - Added cleanup endpoint, updated response messages
3. `README.md` - Updated API documentation
4. `docs/ORPHANED_CANDLES_FIX.md` - Comprehensive documentation (new file)
5. `scripts/cleanup_orphaned_candles.sh` - Cleanup automation script (new file)
6. `docs/chats/orphaned-candles-bug-fix-2025-12-26.md` - This conversation log (new file)

## How to Fix Production Database

### Using the Script (Recommended)

```bash
export API_KEY="your_api_key"
./scripts/cleanup_orphaned_candles.sh
```

### Using the API Directly

```bash
curl -X POST \
  -H "X-API-Key: your_api_key" \
  http://your-server:8765/candles/cleanup
```

### Expected Result

For the user's production database:
- Before: 1 tracked ticker, 46 tickers with candles
- After: 1 tracked ticker, 1 ticker with candles
- Deleted: ~4,250 orphaned candle records (45 tickers × ~100 candles each)

## Verification

Check status after cleanup:

```bash
curl -H "X-API-Key: your_api_key" http://your-server:8765/status | jq .database
```

Should show:
```json
{
  "ticker_count": 1,
  "total_candles": 101,
  "complete_candles": 101,
  "incomplete_candles": 0,
  "candles_per_ticker": {
    "ALAB": 101
  }
}
```

## Future Prevention

With this fix implemented:
- ✅ Single ticker deletion (`remove_ticker()`) deletes candles
- ✅ Batch ticker deletion (`delete_all_tickers()`) deletes candles
- ✅ Behavior is now consistent
- ✅ Cleanup endpoint available for any legacy issues

## Related Conversations

This issue was related to previous discussions in:
- `docs/chats/admin-dashboard-ui-and-configuration-improvements-2025-12-13.md` - Database status display issues
- `docs/chats/code-review-analysis-and-app-improvement-suggestions-2025-12-11.md` - Database performance optimization

## Version Impact

This fix should be included in the next version release (v0.4.3 or v0.5.0 depending on versioning strategy).

## Testing Recommendations

1. **Test single ticker deletion:**
   ```bash
   # Add a ticker
   curl -X POST -H "X-API-Key: xxx" -H "Content-Type: application/json" \
     -d '{"tickers": ["TEST"]}' http://localhost:8765/tickers

   # Wait for some candles to be created

   # Remove the ticker
   curl -X DELETE -H "X-API-Key: xxx" http://localhost:8765/tickers/TEST

   # Verify candles are gone
   curl -H "X-API-Key: xxx" http://localhost:8765/status | jq .database.candles_per_ticker
   ```

2. **Test batch ticker deletion:**
   ```bash
   # Add multiple tickers
   curl -X POST -H "X-API-Key: xxx" -H "Content-Type: application/json" \
     -d '{"tickers": ["TEST1", "TEST2", "TEST3"]}' http://localhost:8765/tickers

   # Wait for some candles to be created

   # Remove all tickers
   curl -X DELETE -H "X-API-Key: xxx" \
     http://localhost:8765/tickers?confirm=true

   # Verify all candles are gone
   curl -H "X-API-Key: xxx" http://localhost:8765/status | jq .database
   ```

3. **Test cleanup endpoint:**
   ```bash
   # Manually create orphaned candles (for testing)
   # Then run cleanup
   curl -X POST -H "X-API-Key: xxx" http://localhost:8765/candles/cleanup

   # Verify orphaned candles were removed
   curl -H "X-API-Key: xxx" http://localhost:8765/status | jq .database
   ```

## Summary

This was a straightforward bug caused by inconsistent deletion behavior between two methods in the storage layer. The fix ensures that candle data is always deleted when tickers are removed, and provides a cleanup endpoint to handle any legacy orphaned data.
