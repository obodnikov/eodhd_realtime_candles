# Active Candles Dashboard Fix - Multi-Worker Architecture

**Date**: 2026-01-30  
**Issue**: Dashboard shows empty ticker list despite active WebSocket connection and data flow  
**Root Cause**: Multi-worker architecture - API workers have dummy CandleEngine with no in-memory active candles

## Problem Analysis

### Symptoms
- Dashboard shows application status boxes (WebSocket, Database, Config)
- "Active Candles" section is empty (no ticker list)
- Status endpoint returns `"active_candles": []`
- WebSocket is connected with 45 subscribed tickers
- Database has 38,763 candles stored
- 23,970 ticks received

### Root Cause
In multi-worker deployment:
- **WebSocket Worker**: Has real `CandleEngine` with active candles in memory
- **API Workers** (2 instances): Have **dummy `CandleEngine`** with empty `_current_candles` dict
- **Dashboard queries API worker** → Gets empty `active_candles` array

```
┌─────────────────┐         ┌──────────────────┐
│ WebSocket Worker│         │   API Worker     │
│                 │         │                  │
│ ✅ Real Candle  │         │ ❌ Dummy Candle  │
│    Engine       │         │    Engine        │
│ ✅ Has active   │         │ ❌ Empty active  │
│    candles      │         │    candles []    │
└─────────────────┘         └──────────────────┘
                                     ↑
                                     │
                              Dashboard queries
                              this worker
```

## Solution Implemented

Following the same pattern used for WebSocket status sharing, we store active candles in the database.

### Changes Made

#### 1. Database Schema (`src/storage.py`)

Added new table for active candles status:

```sql
CREATE TABLE IF NOT EXISTS active_candles_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single row
    data TEXT NOT NULL,                     -- JSON array of active candles
    updated_at TEXT NOT NULL                -- Unix timestamp
)
```

#### 2. Storage Methods (`src/storage.py`)

Added two new methods:

**`update_active_candles(candles: List[Dict[str, Any]])`**
- Called by WebSocket worker to write active candles to database
- Uses REPLACE to upsert single row (id=1)
- Stores JSON array of candle summaries

**`get_active_candles(stale_threshold_seconds: int = 30) -> Optional[List[Dict[str, Any]]]`**
- Called by API workers to read active candles from database
- Returns None if no data or data is stale (> threshold seconds old)
- Handles JSON parsing errors gracefully

#### 3. WebSocket Worker (`src/websocket_worker.py`)

Added new background task:

**`active_candles_task(storage, candle_engine)`**
- Runs every 10 seconds
- Gets active candles from `candle_engine.get_active_tickers_summary()`
- Writes to database via `storage.update_active_candles()`
- Runs in background alongside cleanup and WebSocket status tasks

Task lifecycle:
- Started after WebSocket connection established
- Cancelled on shutdown signal
- Properly cleaned up with other background tasks

#### 4. API Routes (`src/api/routes.py`)

Modified `/status` endpoint:

```python
if self.ws_manager.is_dummy:
    # API worker - read from database
    active_candles = await asyncio.to_thread(
        self.storage.get_active_candles,
        stale_threshold
    )
    if active_candles is None:
        active_candles = []
else:
    # WebSocket worker - read from memory
    active_candles = self.candle_engine.get_active_tickers_summary()
```

#### 5. Tests (`tests/test_storage_active_candles.py`)

Created comprehensive test suite:
- ✅ Write and read active candles
- ✅ Empty list handling
- ✅ Staleness detection
- ✅ Upsert behavior (replace previous data)
- ✅ Field preservation
- ✅ Concurrent updates
- ✅ Special characters in data

All 8 tests passed successfully.

## Benefits

1. **Dashboard shows real-time active candles** from WebSocket worker
2. **No inter-process HTTP calls** needed (database-based communication)
3. **Consistent with existing pattern** (WebSocket status uses same approach)
4. **Works with any number of API workers** (scales horizontally)
5. **Minimal performance impact** (small JSON, 10-second interval)
6. **Stale data detection** (returns empty if data > 30 seconds old)

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                         │
│                                                             │
│  ┌──────────────┐                    ┌──────────────┐      │
│  │  WebSocket   │                    │  API Worker  │      │
│  │  Worker      │                    │  (Dashboard) │      │
│  │              │                    │              │      │
│  │ CandleEngine │                    │ Dummy Engine │      │
│  │ (real)       │                    │ (empty)      │      │
│  └──────┬───────┘                    └──────▲───────┘      │
│         │                                   │              │
│         │ Every 10s:                        │ On /status:  │
│         │ get_active_tickers_summary()      │ get_active_  │
│         │                                   │ candles()    │
│         ▼                                   │              │
│  ┌──────────────────────────────────────────┴──────────┐  │
│  │         SQLite Database (WAL mode)                  │  │
│  │         active_candles_status table                 │  │
│  │         (single row, JSON data)                     │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

- **Update interval**: 10 seconds (same as WebSocket status)
- **Stale threshold**: 30 seconds (configurable via `ws_status_stale_seconds`)
- **Storage**: Single row in `active_candles_status` table
- **Format**: JSON array of candle summaries

## Active Candle Data Structure

Each active candle contains:
```json
{
    "ticker": "AAPL",
    "ticks": 42,
    "current_price": 150.25,
    "low": 149.50,
    "high": 151.00,
    "started": 1234567890,
    "started_ago": "5m ago"
}
```

## Deployment

No configuration changes needed. The feature works automatically:

1. **Existing deployments**: Database migration happens automatically on startup
2. **New deployments**: Table created during `_init_db()`
3. **Single-process mode**: Still works (reads from memory, not database)
4. **Multi-worker mode**: Automatically uses database sharing

## Testing

Run tests:
```bash
python -m pytest tests/test_storage_active_candles.py -v
```

Expected: 8 passed (Windows file cleanup errors are harmless)

## Related Files

- `src/storage.py` - Database schema and methods
- `src/websocket_worker.py` - Background task to write active candles
- `src/api/routes.py` - Status endpoint reads from database
- `tests/test_storage_active_candles.py` - Test suite
- `docs/MULTI_WORKER_DEPLOYMENT.md` - Architecture documentation

## Future Enhancements

Potential improvements:
1. Add `active_candles_count` to status response metadata
2. Add dashboard refresh indicator when data is stale
3. Add admin UI button to force refresh active candles
4. Add metrics for active candles update frequency

## Verification

After deployment, verify:

1. **Check status endpoint**:
   ```bash
   curl http://localhost:8765/status | jq '.active_candles'
   ```
   Should return array of active candles (not empty)

2. **Check dashboard**:
   - Navigate to http://localhost:5000/dashboard
   - Should see "Active Candles" table with ticker list
   - Each row shows: ticker, price, range, ticks, started time

3. **Check logs**:
   ```bash
   docker-compose logs -f | grep "Active candles update"
   ```
   Should see updates every 10 seconds

## Rollback

If issues occur, no rollback needed:
- New table is harmless if unused
- Old code ignores new table
- Feature is backward compatible

To disable (not recommended):
- Comment out `active_candles_task` in `websocket_worker.py`
- API workers will return empty array (same as before fix)
