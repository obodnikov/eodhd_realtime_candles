# ARCHITECTURE.md

**Version**: 0.9.4
**Last Updated**: 2026-02-20
**Project**: EODHD Real-Time Candle Aggregator

---

## 1. Purpose of This Document
This document is the architectural source of truth for this service.

What it does:
- Maps major components and responsibilities
- Defines runtime data flow and deployment model
- Marks stability zones (safe vs risky changes)
- Points to AI behavior/rule sources (does not duplicate them)

What it does not do:
- Replace coding standards (see Section 8)
- Replace endpoint docs (see README.md)
- Replace implementation history/chats

Audience: AI assistants, maintainers, reviewers.

---

## 2. High-Level System Overview
Project type: real-time market data aggregation microservice.
Primary purpose: convert EODHD WebSocket ticks to OHLCV candles during market hours.

Tech stack:
| Layer | Technology | Purpose |
|-------|-----------|---------|
| API | aiohttp 3.9+ | REST API workers |
| Admin UI | Flask 3.0+ | Operator dashboard |
| WebSocket | websockets 12+ | EODHD stream client |
| Storage | SQLite (WAL) or PostgreSQL | Candles/tickers/status persistence |
| Process manager | supervisord | Multi-process orchestration |
| Deployment | Docker + compose | Production runtime |

Architecture pattern (multi-worker):
```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │            Supervisord (process manager)            │   │
│  └────┬─────────────┬─────────────┬──────────────┬─────┘   │
│       │             │             │              │         │
│  ┌────▼────────┐ ┌──▼─────────┐ ┌▼────────────┐ ┌▼──────┐ │
│  │ WebSocket   │ │ API Worker │ │ API Worker  │ │ Admin │ │
│  │ Worker      │ │ 00         │ │ 01          │ │ UI    │ │
│  │ (ticks)     │ │ 8765       │ │ 8766        │ │ 5000  │ │
│  └────┬────────┘ └────┬───────┘ └────┬────────┘ └──┬───┘ │
│       │ write-heavy    │ read/write     │ read/write  │    │
│       └────────────────┴─────────────────┴────────────┘    │
│                         ▼                                   │
│        ┌──────────────────────────────────────────────┐     │
│        │ Shared DB (SQLite WAL OR PostgreSQL)        │     │
│        │ tables: tickers, candles, config,           │     │
│        │ websocket_status, active_candles            │     │
│        └──────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

Key benefits (v0.9.4):
- Isolated tick ingestion and aggregation in dedicated worker
- Backpressure via bounded tick queue to avoid unbounded async task growth
- Reduced lock hold time by asynchronous candle/status flush paths
- Supports SQLite and PostgreSQL deployments

---

## 3. Repository Structure
```
eodhd_realtime_candles/
├── src/
│   ├── main.py                  # Single-process mode (dev/fallback)
│   ├── api_server.py            # API worker entry point
│   ├── websocket_worker.py      # WebSocket worker entry point
│   ├── websocket_manager.py     # Feed client + subscription handling
│   ├── candle_engine.py         # Tick aggregation + queued persistence
│   ├── storage.py               # SQLite storage + schema/migrations
│   ├── storage_postgres.py      # PostgreSQL storage + migrations
│   ├── storage_factory.py       # DATABASE_TYPE switch
│   ├── config.py                # Env/runtime config
│   ├── api/                     # HTTP routes/middleware
│   └── admin/                   # Flask admin app
├── scripts/
│   └── init_postgres.sql        # PostgreSQL schema + additive migrations
├── tests/
│   ├── test_candle_engine.py
│   ├── test_websocket_worker.py
│   └── ...
├── .kiro/steering/project-rules.md
├── CLAUDE.md
├── README.md
└── ARCHITECTURE.md
```

Critical paths:
- Production entry: `src/websocket_worker.py`, `src/api_server.py`, `src/admin/app.py`
- Storage path: `src/storage_factory.py` -> `src/storage.py` or `src/storage_postgres.py`
- Config source: `.env` + runtime overrides

---

## 4. Core Components
### 4.1 Frontend (Admin UI)

Technology: Flask + Jinja2 + Chart.js
Status: ✅ Stable
Responsibilities:
- Operator auth/session
- Status/ticker/candle dashboards
- Configuration control surface

### 4.2 Backend Workers and Shared Services

Worker status:

| Worker | File | Role | Status |
|-------|------|------|--------|
| API worker(s) | `src/api_server.py` | REST handling | ✅ Stable |
| WebSocket worker | `src/websocket_worker.py` | Tick ingest + aggregation + flush tasks | 🔄 Semi-Stable |
| Admin UI | `src/admin/app.py` | UI + API proxying | ✅ Stable |

Shared component status:

| Component | File | Role | Status |
|----------|------|------|--------|
| Candle engine | `src/candle_engine.py` | OHLCV state + queued writes | 🔄 Semi-Stable |
| WS manager | `src/websocket_manager.py` | Connection/reconnect/dispatch | 🔄 Semi-Stable |
| Storage (SQLite) | `src/storage.py` | SQLite persistence | ✅ Stable |
| Storage (Postgres) | `src/storage_postgres.py` | PostgreSQL persistence | 🔄 Semi-Stable |
| Config manager | `src/config.py` | Env + runtime config | ✅ Stable |

### 4.3 Background Automation

WebSocket worker background tasks:
- Cleanup task (batched candle retention)
- Ticker sync task (DB-driven subscribe/unsubscribe)
- WebSocket status task (shared status row)
- Active candles task (dashboard sharing)
- Ticker status flush task (interval-based persistence)
- Candle write flush task (short interval async DB flush)
- Tick workers consuming bounded queue

### 4.4 External Integrations

- EODHD WebSocket (`wss://ws.eodhistoricaldata.com/ws/us`)
- Optional client/integration workflows via REST (e.g., n8n)

---

## 5. Data Flow & Runtime Model
### 5.1 Request/Auth Flow

REST request -> auth middleware (`X-API-Key` / bearer / query) -> route -> storage/service.
`/health` remains low-cost and should avoid DB dependency.

### 5.2 Tick-to-Candle Flow (v0.9.4)

```
EODHD message
   │
   ▼
WebSocketManager.on_message()
   │ parse + normalize tick
   ▼
async on_tick callback (awaited)
   │
   ▼
Bounded tick queue (maxsize = TICK_QUEUE_MAXSIZE)
   │ consumed by N workers
   ▼
CandleEngine.process_tick()
   │ updates in-memory candle state
   │ enqueues candle/status writes (no direct hot-path DB write)
   ▼
Flush tasks (interval-based)
   ├─ flush_pending_ticker_statuses()
   └─ flush_pending_candle_writes()
      ▼
Storage backend (SQLite/PostgreSQL)
```

### 5.3 Config Loading Hierarchy

Priority order:
1. Runtime updates (PATCH /config)
2. Persisted overrides
3. Environment variables
4. Dataclass defaults

---

## 6. Configuration & Environment Assumptions
Required:
- `EODHD_API_KEY`
- `API_KEY` (recommended for auth)

Important optional vars:
- `DATABASE_TYPE=sqlite|postgres`
- `CANDLE_INTERVAL_MINUTES`, `MAX_CANDLES_STORED`, `MAX_TICKERS`
- `CANDLE_SAVE_EVERY_N_TICKS`, `CANDLE_SAVE_EVERY_M_SECONDS`
- `TICKER_STATUS_UPDATE_INTERVAL_SECONDS`
- `CANDLE_WRITE_QUEUE_MAXSIZE`
- `TICK_QUEUE_MAXSIZE`
- `TICK_WORKER_CONCURRENCY`
- Postgres vars: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, pool sizes

Deployment assumptions:
- Production commonly runs multi-worker under supervisord
- SQLite deployments require writable `/data` volume for DB/WAL
- PostgreSQL deployments require reachable DB and schema init/migrations

---

## 7. Stability Zones

| Area | Status | Risk | Notes |
|-----|--------|------|-------|
| `src/candle_aggregator.py` | ✅ Stable | LOW | Mature, bounded scope |
| `src/api/middleware.py` | ✅ Stable | LOW | Auth logic settled |
| `src/api/routes.py` | 🔄 Semi-Stable | MEDIUM | Endpoint surface evolves |
| `src/websocket_worker.py` | 🔄 Semi-Stable | MEDIUM | Recently changed queue/flush orchestration |
| `src/candle_engine.py` | 🔄 Semi-Stable | MEDIUM | Recently changed queued write/eviction logic |
| `src/websocket_manager.py` | 🔄 Semi-Stable | MEDIUM | Callback mode changed to awaited option |
| `src/storage.py` | ✅ Stable | LOW | Stable SQLite path + additive migrations |
| `src/storage_postgres.py` | 🔄 Semi-Stable | MEDIUM | Active migration/ops path |
| `scripts/init_postgres.sql` | ⚠️ Sensitive | HIGH | Schema/migration script; coordinate with prod DB ops |
| `src/config.py` | ✅ Stable | LOW | New queue/status env vars added |
| Deployment configs | ✅ Stable | LOW | Supervisord/docker baseline stable |
| Planned observability expansion | 🔮 Planned | N/A | See roadmap/docs |

Do-not-change without explicit approval:
- Schema semantics (`candles`, `tickers`, status tables)
- Auth model
- WebSocket protocol handling
- Multi-worker process split

Safe-to-change (with tests):
- Additive endpoints, docs, UI templates
- Config defaults and non-breaking tuning values

---

## 8. AI Coding Rules and Behavioral Contracts
This document does not define coding standards; it references rule sources.

Primary rule files:
- `~/.codex/AGENTS.md` (confirm-before-action workflow)
- `.kiro/steering/project-rules.md` (rule discovery/priority contract)
- `CLAUDE.md` (assistant behavior contract)
- `AI.md` (general Python rules)
- `AI-PYTHON-REST-API.md` (REST patterns)
- `AI_FLASK.md` (Flask patterns)
- `AI_SQLite.md` (SQLite performance rules)
- `AI_PostgreSQL.md` (PostgreSQL migration/runtime rules)

Precedence (high -> low):
1. Explicit user instruction
2. `~/.codex/AGENTS.md` and `.kiro/steering/project-rules.md`
3. Stack-specific AI rules (`AI_*.md`)
4. `AI.md`
5. This architecture document
6. Language/framework conventions

When rules conflict: stop, identify conflict, propose options, wait for explicit user decision.

---

## 9. Quick Start for AI Assistants

Before changing code:
- Read Section 7 (stability zones)
- Read relevant rule files in Section 8
- Check recent chats/review notes for regressions
- Propose concrete edits and get explicit approval

Fast navigation:
- Runtime orchestration: `src/websocket_worker.py`
- Tick/candle core: `src/candle_engine.py`
- WebSocket I/O: `src/websocket_manager.py`
- DB abstraction: `src/storage_factory.py`
- SQLite storage: `src/storage.py`
- PostgreSQL storage: `src/storage_postgres.py`
- Env/runtime config: `src/config.py`
- API surface: `src/api/routes.py`
- Operational schema: `scripts/init_postgres.sql`

Common tasks:
- Add endpoint: update routes + tests + README
- Tune throughput: adjust queue/flush env vars + validate `/status` metrics
- DB migration: keep additive, verify on staging/prod before rollout

---

**End of ARCHITECTURE.md**
