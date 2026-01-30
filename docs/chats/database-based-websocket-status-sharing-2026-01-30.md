# Database-Based WebSocket Status Sharing

**Date**: 2026-01-30  
**Context**: Multi-worker architecture needs real WebSocket status visibility in API workers

## Problem

In the multi-worker architecture (v0.6.0), API workers have dummy WebSocketManager instances and cannot see the real WebSocket connection status from the WebSocket worker. The `/status` endpoint was returning `connected: null` which was not helpful.

## Solution: Database-Based Status Sharing

Implemented Option A: Store WebSocket status in SQLite database for cross-process visibility.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                         │
│                                                             │
│  ┌────────────────┐         ┌──────────────────────────┐   │
│  │ WebSocket      │         │ SQLite Database          │   │
│  │ Worker         │─writes──▶│ websocket_status table  │   │
│  │ (every 10s)    │         │ (single row, id=1)       │   │
│  └────────────────┘         └──────────┬───────────────┘   │
│                                        │                    │
│                                   reads│                    │
│                                        │                    │
│  ┌────────────────┐                   │                    │
│  │ API Worker 00  │───────────────────┘                    │
│  │ Port 8765      │                                        │
│  └────────────────┘                                        │
│                                                             │
│  ┌────────────────┐                                        │
│  │ API Worker 01  │───────────────────┐                    │
│  │ Port 8766      │                   │                    │
│  └────────────────┘                   │                    │
│                                        │                    │
└────────────────────────────────────────┼────────────────────┘
                                         │
                                    reads│
```

### Implementation Details

**1. Database Schema (src/storage.py)**

Added `websocket_status` table:
```sql
CREATE TABLE IF NOT EXISTS websocket_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single row
    connected INTEGER NOT NULL,
    subscribed_tickers TEXT NOT NULL,       -- JSON array
    subscribed_count INTEGER NOT NULL,
    pending_subscribe TEXT NOT NULL,        -- JSON array
    connection_count INTEGER NOT NULL,
    tick_count INTEGER NOT NULL,
    last_message TEXT,
    last_update TEXT NOT NULL
)
```

**2. Storage Methods (src/storage.py)**

- `update_websocket_status(status: Dict)` - Write status (REPLACE for upsert)
- `get_websocket_status() -> Optional[Dict]` - Read status with staleness check
  - Returns `None` if no status written yet
  - Adds `is_stale: True` if last update > 30 seconds ago
  - Adds `age_seconds` field

**3. WebSocket Worker (src/websocket_worker.py)**

Added `websocket_status_task()`:
- Runs every 10 seconds
- Gets status from `ws_manager.get_status()`
- Writes to database via `asyncio.to_thread()` (non-blocking)
- Logs updates at DEBUG level

**4. API Routes (src/api/routes.py)**

Updated `/status` endpoint:
- If `ws_manager.is_dummy` (API worker):
  - Read status from database
  - If `None`: Show "not started yet" message
  - If stale: Add warning with age
- If real WebSocket worker:
  - Use `ws_manager.get_status()` directly

## Benefits

1. **Real Status**: API workers show actual WebSocket connection state
2. **Freshness Indicator**: Staleness detection (> 30s) warns of issues
3. **Simple**: No new dependencies, uses existing SQLite
4. **Reliable**: Status persists across restarts
5. **Performance**: 10s update interval is lightweight

## Status Response Examples

### Healthy WebSocket (from API worker)
```json
{
  "websocket": {
    "connected": true,
    "subscribed_tickers": ["AAPL", "GOOGL", ...],
    "subscribed_count": 45,
    "pending_subscribe": [],
    "connection_count": 1,
    "tick_count": 12543,
    "last_message": "2026-01-30T10:45:23.123Z",
    "last_update": "2026-01-30T10:45:30.456Z",
    "is_stale": false,
    "age_seconds": 7.2
  }
}
```

### Stale Status (WebSocket worker stopped)
```json
{
  "websocket": {
    "connected": true,
    "subscribed_count": 45,
    "last_update": "2026-01-30T10:40:00.000Z",
    "is_stale": true,
    "age_seconds": 345.6,
    "note": "WebSocket status is stale (last update 346s ago)"
  }
}
```

### Not Started Yet
```json
{
  "websocket": {
    "connected": false,
    "subscribed_tickers": ["AAPL", "GOOGL", ...],
    "subscribed_count": 45,
    "note": "WebSocket worker not started yet or status not available"
  }
}
```

## Compliance

✅ **AI_SQLite.md**: All DB operations use `asyncio.to_thread()`  
✅ **ARCHITECTURE.md**: Preserves SQLite-based architecture  
✅ **No Breaking Changes**: Existing API contracts maintained  
✅ **Performance**: Minimal overhead (10s update interval)

## Files Modified

- `src/storage.py` - Added table and methods
- `src/websocket_worker.py` - Added status update task
- `src/api/routes.py` - Read from database
- `src/websocket_manager.py` - Added `is_dummy` flag (previous change)

## Related Documents

- Multi-Worker Architecture: `docs/MULTI_WORKER_DEPLOYMENT.md`
- Code Review Fixes: `docs/chats/code-review-fixes-multi-worker-2026-01-30.md`
- Architecture: `ARCHITECTURE.md`
