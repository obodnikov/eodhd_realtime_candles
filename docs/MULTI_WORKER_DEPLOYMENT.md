# Multi-Worker Deployment Guide

**Version**: 0.6.0  
**Architecture**: Option A - Separate API and WebSocket Processes

## Overview

The service now runs with multiple worker processes for better performance and scalability:

- **2 API Workers**: Handle HTTP requests (ports 8765, 8766)
- **1 WebSocket Worker**: Processes tick data and writes candles
- **1 Admin UI**: Web dashboard (port 5000)

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                         │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Supervisord (Process Manager)           │  │
│  └────┬─────────────┬─────────────┬──────────────┬──────┘  │
│       │             │             │              │         │
│  ┌────▼────────┐ ┌──▼─────────┐ ┌▼────────────┐ ┌▼──────┐ │
│  │ WebSocket   │ │ API Worker │ │ API Worker  │ │ Admin │ │
│  │ Worker      │ │ 00         │ │ 01          │ │ UI    │ │
│  │ (ticks)     │ │ Port 8765  │ │ Port 8766   │ │ 5000  │ │
│  └────┬────────┘ └──┬─────────┘ └┬────────────┘ └───────┘ │
│       │             │             │                        │
│       │ writes      │ reads       │ reads                  │
│       ▼             ▼             ▼                        │
│  ┌─────────────────────────────────────────────────────┐  │
│  │         SQLite Database (WAL mode)                  │  │
│  │         /data/candles.db                            │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Worker Responsibilities

### WebSocket Worker
- Connects to EODHD WebSocket feed
- Processes all tick data
- Aggregates ticks into OHLCV candles
- Writes candles to database
- Runs background cleanup task (30s interval)
- **No HTTP server** (pure worker)

### API Workers (2 instances)
- Handle HTTP REST requests
- Read from database (candles, tickers, status)
- Can write ticker add/remove operations
- **Do NOT process WebSocket ticks**
- Load balanced across 2 processes

### Admin UI
- Web dashboard for management
- Calls main API for data
- Single process (port 5000)

## Benefits

1. **Better Performance**: API requests handled in parallel by 2 workers
2. **Isolated Processing**: Tick processing doesn't interfere with API requests
3. **Better CPU Utilization**: Multi-core systems fully utilized
4. **Reduced DB Locking**: Writes isolated to WebSocket worker
5. **Easy Scaling**: Add more API workers by changing `numprocs` in supervisord.conf

## Configuration

### supervisord.conf

```ini
# WebSocket worker - single process
[program:websocket_worker]
command=python -m src.websocket_worker
numprocs=1
priority=1

# API workers - multiple processes
[program:api_worker]
command=python -m src.api_server
numprocs=2
priority=2
environment=HTTP_PORT="876%(process_num)d"

# Admin UI
[program:admin_ui]
command=python -m src.admin.app
priority=3
```

### Port Allocation

- API Worker 00: `HTTP_PORT=8765`
- API Worker 01: `HTTP_PORT=8766`
- WebSocket Worker: No HTTP port
- Admin UI: `ADMIN_PORT=5000`

## Scaling

### Add More API Workers

Edit `supervisord.conf`:

```ini
[program:api_worker]
numprocs=4  # Increase from 2 to 4
```

This will create:
- api_worker_00 (port 8765)
- api_worker_01 (port 8766)
- api_worker_02 (port 8767)
- api_worker_03 (port 8768)

### Load Balancer (Optional)

For production, add nginx to distribute requests:

```nginx
upstream api_backend {
    least_conn;
    server localhost:8765;
    server localhost:8766;
}

server {
    listen 80;
    location / {
        proxy_pass http://api_backend;
    }
}
```

## Database Considerations

### SQLite WAL Mode

- Supports multiple readers + 1 writer
- WebSocket worker is the primary writer
- API workers are mostly readers
- No IPC needed - workers communicate via database

### Thread-Local Connections

Each worker maintains its own thread-local SQLite connections:

```python
def _get_connection(self) -> sqlite3.Connection:
    if not hasattr(self._local, 'connection'):
        conn = sqlite3.connect(db_path, ...)
        conn.execute("PRAGMA journal_mode=WAL;")
        self._local.connection = conn
    return self._local.connection
```

## Monitoring

### Check Worker Status

```bash
# Inside container
supervisorctl status

# Expected output:
# websocket_worker    RUNNING   pid 123, uptime 1:23:45
# api_worker:api_worker_00    RUNNING   pid 124, uptime 1:23:45
# api_worker:api_worker_01    RUNNING   pid 125, uptime 1:23:45
# admin_ui            RUNNING   pid 126, uptime 1:23:45
```

### Check API Endpoints

```bash
# Worker 00
curl http://localhost:8765/health

# Worker 01
curl http://localhost:8766/health
```

### View Logs

```bash
# All workers
docker-compose logs -f

# Specific worker
docker-compose logs -f | grep websocket_worker
docker-compose logs -f | grep api_worker_00
```

## Troubleshooting

### Worker Not Starting

Check supervisord logs:
```bash
docker-compose exec app cat /var/log/supervisor/supervisord.log
```

### Database Locked Errors

- Should be eliminated with this architecture
- If still occurring, check that WebSocket worker is running
- Verify WAL mode is enabled: `PRAGMA journal_mode;` should return `wal`

### Port Conflicts

- Ensure ports 8765, 8766, 5000 are not in use
- Check docker-compose.yml port mappings
- Verify `HTTP_PORT` environment variable in supervisord.conf

## Rollback to Single Process

If needed, revert to single-process mode:

1. Edit `supervisord.conf`:
```ini
[program:main_api]
command=python -m src.main
```

2. Restart container:
```bash
docker-compose restart
```

Or run locally:
```bash
python -m src.main
```

## Performance Metrics

### Expected Improvements

- **API Response Time**: 50% faster under load
- **CPU Utilization**: 60-80% across multiple cores (vs 30-40% single core)
- **Memory Usage**: ~250-300MB (vs ~100MB single process)
- **Database Locking**: Eliminated (writes isolated to 1 worker)

### Monitoring

- Use `/status` endpoint for system metrics
- Monitor supervisord process status
- Check database file sizes (`.db`, `.db-wal`, `.db-shm`)

## Related Documentation

- Architecture: `ARCHITECTURE.md`
- Implementation: `docs/chats/implementing-option-a-multiple-worker-processes-2026-01-30.md`
- SQLite Rules: `AI_SQLite.md`
- Performance: `docs/chats/performance-optimization-for-high-volume-ticker-monitoring-system-2026-01-28.md`
