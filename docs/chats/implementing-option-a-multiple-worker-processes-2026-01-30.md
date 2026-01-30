# Implementing Option A: Multiple Worker Processes in Supervisord

**Date**: 2026-01-30  
**Context**: Resolving SQLite database locking issues by separating API and WebSocket processing

## Background

After implementing tick-save frequency reduction (90%+ reduction in DB writes), occasional "database is locked" errors still occurred with 50 tickers. Option A provides clean separation of concerns without the complexity of ticker sharding.

## Implementation: Option A - Separate API and WebSocket Processes

### Architecture

```
supervisord
├── websocket_worker (WebSocket + tick processing) - 1 process
├── api_worker_00 (HTTP API) - Port 8765
├── api_worker_01 (HTTP API) - Port 8766
└── admin_ui (Admin dashboard) - Port 5000
```

### Files Created

**1. src/api_server.py** (~170 lines)
- API-only entry point for HTTP requests
- Handles GET/POST/DELETE operations
- Read-mostly operations (can write ticker add/remove)
- Does NOT process WebSocket ticks
- Creates dummy WebSocketManager for API compatibility
- All DB calls wrapped in `asyncio.to_thread()` per AI_SQLite.md

**2. src/websocket_worker.py** (~250 lines)
- Dedicated WebSocket worker
- Connects to EODHD WebSocket feed
- Processes all tick data
- Aggregates ticks into OHLCV candles
- Writes candles to database
- Runs background cleanup task (30s interval)
- No HTTP server (pure worker)
- All DB calls wrapped in `asyncio.to_thread()` per AI_SQLite.md

### Files Modified

**3. supervisord.conf**
- Removed: `main_api` (single process)
- Added: `websocket_worker` (priority 1, single process)
- Added: `api_worker` (priority 2, numprocs=2, ports 8765-8766)
- Kept: `admin_ui` (priority 3, port 5000)
- Environment variable: `HTTP_PORT="876%(process_num)d"` for port allocation

**4. .env.example**
- Added worker configuration documentation
- Explained port allocation (8765, 8766 for API workers)
- Documented architecture (2 API + 1 WebSocket + 1 Admin)

**5. src/main.py**
- Kept unchanged as fallback for local development
- Can still run single-process mode: `python -m src.main`

## Key Design Decisions

### 1. API Workers (Read-Mostly)
- Handle HTTP requests only
- Read from database (candles, tickers, status)
- Can write ticker add/remove operations
- Do NOT connect to WebSocket
- Do NOT process ticks
- Dummy WebSocketManager for API route compatibility

### 2. WebSocket Worker (Write-Heavy)
- Single process (no coordination needed)
- Connects to EODHD WebSocket
- Processes all tick data
- Writes all candles to database
- Runs background cleanup task
- No HTTP server

### 3. Communication
- No IPC needed
- Workers communicate via SQLite database only
- WAL mode supports multiple readers + 1 writer
- Thread-local connections per worker (existing pattern)

### 4. Port Allocation
- API worker 00: Port 8765 (HTTP_PORT=8765)
- API worker 01: Port 8766 (HTTP_PORT=8766)
- WebSocket worker: No HTTP port
- Admin UI: Port 5000

## Benefits

1. **True Parallelism**: API requests handled by 2+ workers
2. **Isolated Tick Processing**: WebSocket worker not affected by API load
3. **Better CPU Utilization**: Multi-core systems fully utilized
4. **Reduced DB Lock Contention**: Writes isolated to 1 worker
5. **Simple Implementation**: No ticker sharding complexity
6. **Easy Scaling**: Add more API workers by changing `numprocs`

## Performance Expectations

**Before (Single Process)**:
- 50 tickers → occasional "database is locked"
- CPU: ~30-40% on single core
- Memory: ~100MB
- API response time: Variable under load

**After (Multiple Workers)**:
- 50 tickers → no locking (writes isolated to WebSocket worker)
- CPU: ~60-80% across multiple cores
- Memory: ~250-300MB (3 processes: 2 API + 1 WebSocket)
- API response time: 50% improvement (parallel request handling)

## Compliance with Project Rules

✅ **CLAUDE.md**: Proposed solution before coding  
✅ **AI_SQLite.md**: All DB calls in async handlers use `asyncio.to_thread()`  
✅ **AI.md**: Using `python-dotenv` for `.env` loading  
✅ **ARCHITECTURE.md**: Preserving WAL mode, thread-local connections, stats caching  
✅ **Stability Zones**: Not changing core engine, only deployment structure

## Testing Strategy

1. ✅ Test in single-process mode first (`python -m src.main`)
2. ⏳ Enable multi-process mode in supervisord
3. ⏳ Verify API requests work across both workers
4. ⏳ Verify WebSocket processes ticks correctly
5. ⏳ Load test with 50 tickers

## Rollback Plan

- Keep `src/main.py` unchanged as fallback
- Can switch supervisord back to single process
- No database schema changes (zero risk)
- Revert supervisord.conf to previous version

## Next Steps

1. Test multi-process deployment in Docker
2. Monitor for database locking errors
3. Measure API response time improvement
4. Consider adding more API workers if needed (change `numprocs`)
5. Document any issues or optimizations needed

## Related Documents

- Implementation Plan: `docs/chats/resolving-sqlite-database-locking-in-ticker-monitoring-system-2026-01-30.md`
- Architecture: `ARCHITECTURE.md`
- SQLite Rules: `AI_SQLite.md`
- Performance Optimization: `docs/chats/performance-optimization-for-high-volume-ticker-monitoring-system-2026-01-28.md`
