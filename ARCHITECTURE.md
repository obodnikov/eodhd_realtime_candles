# ARCHITECTURE.md

**Version**: 0.6.0
**Last Updated**: 2026-01-30
**Project**: EODHD Real-Time Candle Aggregator

---

## 1. Purpose of This Document

This document serves as the **architectural source of truth** for the EODHD Real-Time Candle Aggregator.

**What it does:**
- Maps system components and their relationships
- Defines stability zones (what's safe to change vs. what's not)
- Points to AI coding rules (does NOT define them)
- Provides data flow diagrams for understanding runtime behavior
- Guides AI assistants and developers on architectural decisions

**What it does NOT do:**
- Define coding standards (see AI*.md files in Section 8)
- Duplicate implementation details (see docs/ directory)
- Describe planned features (see ROADMAP.md)

**Audience:** AI coding assistants, new developers, architectural reviewers

---

## 2. High-Level System Overview

**Project Type:** Real-time financial data aggregation microservice  
**Primary Purpose:** Convert EODHD WebSocket tick data into OHLCV candles during market hours (solves 2-3 hour delay in historical API)

**Tech Stack:**

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Main API | aiohttp 3.9+ | Async REST API (2 workers: ports 8765-8766) |
| Admin UI | Flask 3.0+ | Web management interface (port 5000) |
| WebSocket | websockets 12.0+ | EODHD real-time feed client (1 worker) |
| Database | SQLite3 (WAL mode) | Candle + ticker persistence |
| Process Manager | supervisord | Multi-process container orchestration |
| Deployment | Docker + docker-compose | Containerized deployment |

**Architecture Pattern:**

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
│  └────┬────────┘ └──┬─────────┘ └┬────────────┘ └───┬───┘ │
│       │             │             │                  │     │
│       │ writes      │ reads       │ reads            │ API │
│       │             │             │                  │calls│
│       ▼             ▼             ▼                  ▼     │
│  ┌─────────────────────────────────────────────────────┐  │
│  │         SQLite Database (WAL mode)                  │  │
│  │         /data/candles.db                            │  │
│  │         Tables: tickers, candles, config            │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Key Architecture Benefits (v0.6.0):**
- **True Parallelism**: 2 API workers handle HTTP requests concurrently
- **Isolated Processing**: WebSocket worker dedicated to tick processing
- **Better CPU Utilization**: Multi-core systems fully utilized
- **Reduced DB Locking**: Writes isolated to WebSocket worker (eliminates "database is locked" errors)
- **Easy Scaling**: Add more API workers by changing supervisord config

---

## 3. Repository Structure

```
eodhd_realtime_candles/
├── src/                          # Main application code
│   ├── main.py                   # Legacy entry point (single-process mode)
│   ├── api_server.py             # API worker entry point (multi-process)
│   ├── websocket_worker.py       # WebSocket worker entry point (multi-process)
│   ├── config.py                 # Configuration management + persistence
│   ├── storage.py                # SQLite database layer (WAL mode)
│   ├── candle_engine.py          # Tick → OHLCV aggregation logic
│   ├── candle_aggregator.py      # On-demand candle interval aggregation
│   ├── websocket_manager.py      # EODHD WebSocket client
│   ├── api/                      # REST API layer
│   │   ├── routes.py             # All HTTP endpoints
│   │   └── middleware.py         # Auth, logging, error handling
│   └── admin/                    # Flask admin web UI
│       ├── app.py                # Flask application entry point
│       ├── auth.py               # Session-based authentication
│       ├── api_client.py         # HTTP client for main API
│       ├── templates/            # Jinja2 HTML templates
│       └── static/               # CSS, JS, images (sqowe branding)
│
├── tests/                        # pytest test suite
│   ├── test_storage_cleanup.py   # Database cleanup tests
│   ├── test_candle_engine.py     # Aggregation logic tests
│   ├── test_candle_aggregator.py # Interval aggregation tests
│   ├── test_api_server.py        # API worker tests
│   └── test_websocket_worker.py  # WebSocket worker tests
│
├── docs/                         # Documentation (NOT in root)
│   ├── chats/                    # Conversation history (context for AI)
│   ├── ADMIN_UI.md               # Admin interface guide
│   ├── MULTI_WORKER_DEPLOYMENT.md # Multi-worker architecture guide
│   ├── IMPLEMENTATION_v0.4.0.md  # v0.4.0 implementation details
│   ├── ORPHANED_CANDLES_FIX.md   # Bug fix documentation
│   └── sqlite-performance-tuning.md  # SQLite optimization notes
│
├── scripts/                      # Operational scripts
│   └── cleanup_orphaned_candles.sh  # Database maintenance
│
├── n8n_workflows/                # Integration examples
│   └── realtime_momentum.json    # n8n workflow template
│
├── AI*.md                        # Coding rules (root level)
│   ├── AI.md                     # General Python guidelines
│   ├── AI-PYTHON-REST-API.md     # REST API patterns
│   ├── AI_FLASK.md               # Flask web app rules
│   └── AI_SQLite.md              # SQLite performance rules
│
├── CLAUDE.md                     # AI behavior contract
├── ARCHITECTURE.md               # This file
├── README.md                     # User documentation
├── ROADMAP.md                    # Future features
├── requirements.txt              # Python dependencies
├── Dockerfile                    # Container image
├── docker-compose.yml            # Deployment configuration
├── supervisord.conf              # Process manager config
└── .env.example                  # Environment template

**Critical Paths:**
- Entry (Production): `src/api_server.py` (API workers), `src/websocket_worker.py` (WebSocket worker), `src/admin/app.py` (Admin UI)
- Entry (Dev): `src/main.py` (single-process mode)
- Config: `.env` → `src/config.py` → `/data/config.json` (runtime overrides)
- Database: `/data/candles.db` (Docker) or `./data/candles.db` (local dev)
- Tests: `tests/` (pytest)
```

---

## 4. Core Components

### 4.1 Frontend (Admin UI)

**Technology:** Flask 3.0 + Jinja2 + Chart.js  
**Status:** ✅ Stable (v0.4.0+)  
**Port:** 5000 (default: localhost only)

**Responsibilities:**
- Session-based authentication (same API key as main API)
- Dashboard: system status, WebSocket health, database stats
- Ticker management: add/remove tickers via web UI
- Candle viewer: Chart.js visualizations
- Configuration: runtime config updates

**Key Files:**
- `src/admin/app.py` - Flask routes and application
- `src/admin/api_client.py` - HTTP client for main API
- `src/admin/templates/` - Jinja2 templates (sqowe branding)

### 4.2 Backend (Multi-Worker Architecture)

**Technology:** aiohttp 3.9 (async) + supervisord  
**Status:** ✅ Stable (v0.6.0)  
**Architecture:** Separate API and WebSocket processes

**Worker Processes:**

| Worker | File | Count | Ports | Responsibility | Status |
|--------|------|-------|-------|----------------|--------|
| **API Workers** | `api_server.py` | 2 | 8765-8766 | HTTP requests, read-mostly DB ops | ✅ Stable |
| **WebSocket Worker** | `websocket_worker.py` | 1 | None | Tick processing, candle writes | ✅ Stable |
| **Admin UI** | `admin/app.py` | 1 | 5000 | Web dashboard | ✅ Stable |

**Core Components (Shared):**

| Component | File | Responsibility | Status |
|-----------|------|----------------|--------|
| **CandleEngine** | `candle_engine.py` | Tick aggregation into OHLCV candles | ✅ Stable |
| **CandleAggregator** | `candle_aggregator.py` | On-demand interval aggregation | ✅ Stable |
| **Storage** | `storage.py` | SQLite operations (WAL mode, thread-local) | ✅ Stable |
| **WebSocketManager** | `websocket_manager.py` | EODHD feed connection + reconnection | ✅ Stable |
| **APIRoutes** | `api/routes.py` | REST endpoints (20 routes) | 🔄 Semi-Stable |
| **ConfigManager** | `config.py` | Runtime config with persistence | ✅ Stable |

**Worker Responsibilities:**

**API Workers (2 instances):**
- Handle HTTP REST requests in parallel
- Read from database (candles, tickers, status)
- Write ticker add/remove operations
- Do NOT process WebSocket ticks
- Load balanced across 2 processes

**WebSocket Worker (1 instance):**
- Connects to EODHD WebSocket feed
- Processes all tick data
- Aggregates ticks into OHLCV candles
- Writes candles to database
- Runs background cleanup task (30s interval)
- No HTTP server (pure worker)

**Key Endpoints:**
- `/health` - No auth, no DB access (per AI_SQLite.md rule)
- `/status` - Cached stats (5s TTL to prevent DB blocking)
- `/tickers` - Add/remove/list tracked symbols
- `/tickers/{ticker}` - Get single ticker information (v0.4.4)
- `/candles/{ticker}` - Query OHLCV data
- `/candles/{ticker}/{minutes}` - Aggregated candles at custom interval (v0.4.5)
- `/candles/cleanup` - Remove orphaned candles (v0.4.3)
- `/config` - Runtime configuration management

### 4.3 Jobs/Automation

**Background Tasks:**
- **Candle cleanup**: Runs every 30s in WebSocket worker (batched for performance)
- **Candle completion**: Event-driven by time intervals (CandleEngine)
- **WebSocket reconnection**: Automatic on disconnect with configurable delay

**No scheduled jobs** - all operations are event-driven or on-demand.

### 4.4 External Integrations

**EODHD WebSocket API:**
- URL: `wss://ws.eodhistoricaldata.com/ws/us`
- Authentication: API key in query param
- Protocol: Subscribe to tickers, receive tick data
- Reconnection: Automatic with configurable delay (default 5s)
- Connection: WebSocket worker only

**n8n Integration:**
- HTTP Request nodes call REST API (any API worker)
- Example workflow: `n8n_workflows/realtime_momentum.json`
- Polling recommended: 5-10 second intervals

---

## 5. Data Flow & Runtime Model

### 5.1 Authentication Flow

```
Client Request
     │
     ▼
┌─────────────────────────────────────┐
│  Auth Middleware (middleware.py)   │
│  - Check X-API-Key header           │
│  - Check Authorization: Bearer      │
│  - Check ?api_key= query param      │
└─────────┬───────────────────────────┘
          │
    ┌─────┴─────┐
    │ Valid?    │
    └─────┬─────┘
          │
    ┌─────▼─────┐         ┌──────────────┐
    │   YES     │         │     NO       │
    └─────┬─────┘         └──────┬───────┘
          │                      │
          ▼                      ▼
   ┌────────────┐         ┌────────────┐
   │ Process    │         │ 401        │
   │ Request    │         │ Unauthorized│
   └────────────┘         └────────────┘

Exception: /health endpoint bypasses auth
```

### 5.2 Main Business Logic Flow (Tick → Candle)

```
EODHD WebSocket
     │ tick data: {"s":"AAPL","p":245.67,"v":100}
     ▼
┌──────────────────────────────────────┐
│  WebSocketManager.on_message()       │
│  - Parse JSON                        │
│  - Extract ticker, price, volume     │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  CandleEngine.process_tick()         │
│  - Get/create current candle         │
│  - Update OHLCV values               │
│  - Check if interval complete        │
└──────────┬───────────────────────────┘
           │
     ┌─────┴──────┐
     │ Complete?  │
     └─────┬──────┘
           │
    ┌──────▼──────┐              ┌──────────────┐
    │    YES      │              │     NO       │
    └──────┬──────┘              └──────┬───────┘
           │                            │
           ▼                            ▼
┌──────────────────────┐      ┌──────────────────┐
│ Storage.save_candle()│      │ Keep in memory   │
│ - INSERT INTO candles│      │ (current candle) │
│ - Mark complete=true │      └──────────────────┘
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Cleanup (batched, not per-candle)  │
│  - Runs on timer (30-60s)            │
│  - DELETE old candles (LIMIT 500)    │
└──────────────────────────────────────┘
```

### 5.3 Configuration Loading Hierarchy

```
1. Environment Variables (.env file)
   ↓ loaded by python-dotenv
2. Config() dataclass defaults
   ↓ applied at startup
3. Persisted Overrides (/data/config.json)
   ↓ loaded by ConfigManager
4. Runtime Updates (PATCH /config)
   ↓ saved to config.json if PERSIST_CONFIG=true

Priority: Runtime > Persisted > Env > Defaults
```

---

## 6. Configuration & Environment Assumptions

### 6.1 Environment Variables

**Required:**
- `EODHD_API_KEY` - EODHD API key with WebSocket access
- `API_KEY` - Authentication key for REST API (optional but recommended)

**Optional (with defaults):**
- `HTTP_PORT=8765` - REST API port
- `ADMIN_PORT=5000` - Admin UI port
- `ADMIN_HOST=127.0.0.1` - Admin bind address (localhost only by default)
- `CANDLE_INTERVAL_MINUTES=5` - 1, 5, 15, 30, or 60
- `MAX_CANDLES_STORED=100` - Per ticker
- `MAX_TICKERS=50` - EODHD WebSocket limit
- `DATABASE_PATH` - Auto-detects: `/data/candles.db` (Docker) or `./data/candles.db` (local)
- `PERSIST_CONFIG=true` - Save runtime config changes

**See:** `.env.example` for complete list

### 6.2 Deployment Assumptions

**Docker (Production):**
- Volume: `/data` for database + config persistence
- Ports: 8765 (API), 5000 (Admin UI)
- Supervisord manages both processes
- WAL mode files: `.db`, `.db-wal`, `.db-shm` (all in volume)

**Local Development:**
- Python 3.9+
- `python-dotenv` loads `.env` file (MUST be explicit, not automatic)
- Database: `./data/candles.db` (relative to project root)
- Run: `python -m src.main` (API), `python -m src.admin.app` (Admin)

---

## 7. Stability Zones

Map of components to stability levels:

| Component | Status | Change Risk | Notes |
|-----------|--------|-------------|-------|
| **Core Engine** | | | |
| `candle_engine.py` | ✅ Stable | LOW | Aggregation logic is production-tested |
| `candle_aggregator.py` | ✅ Stable | LOW | On-demand interval aggregation (v0.4.5) |
| `storage.py` | ✅ Stable | LOW | SQLite layer with WAL mode optimizations |
| `websocket_manager.py` | ✅ Stable | LOW | Reconnection logic is reliable |
| **API Layer** | | | |
| `api/routes.py` | 🔄 Semi-Stable | MEDIUM | New endpoints added frequently |
| `api/middleware.py` | ✅ Stable | LOW | Auth logic is settled |
| **Worker Entry Points** | | | |
| `api_server.py` | ✅ Stable | LOW | Multi-worker API entry point (v0.6.0) |
| `websocket_worker.py` | ✅ Stable | LOW | Dedicated WebSocket worker (v0.6.0) |
| `main.py` | ✅ Stable | LOW | Legacy single-process mode (fallback) |
| **Configuration** | | | |
| `config.py` | ✅ Stable | LOW | ConfigManager with persistence is stable |
| **Admin UI** | | | |
| `admin/app.py` | 🔄 Semi-Stable | MEDIUM | UI features may evolve |
| `admin/templates/` | 🔄 Semi-Stable | MEDIUM | sqowe branding is stable, features may change |
| **Database Schema** | | | |
| `tickers` table | ✅ Stable | LOW | Schema is finalized |
| `candles` table | ✅ Stable | LOW | Schema is finalized |
| `config` table | ✅ Stable | LOW | Simple key-value store |
| **Deployment** | | | |
| `Dockerfile` | ✅ Stable | LOW | Multi-process setup is working |
| `supervisord.conf` | ✅ Stable | LOW | Multi-worker configuration (v0.6.0) |
| **Planned Features** | | | |
| Prometheus metrics | 🔮 Planned | N/A | See ROADMAP.md v1.1 |
| Multi-interval support | ✅ Implemented | LOW | v0.4.5 - GET /candles/{ticker}/{minutes} |
| Technical indicators | 🔮 Planned | N/A | See ROADMAP.md v2.0 |

**DO NOT CHANGE without explicit approval:**
- Database schema (breaking change for existing deployments)
- SQLite WAL mode configuration (performance-critical)
- Authentication mechanism (security-critical)
- WebSocket protocol handling (EODHD integration)
- Multi-worker architecture (v0.6.0 - production-tested)

**Safe to modify:**
- Admin UI templates (visual changes)
- New API endpoints (additive changes)
- Configuration defaults (non-breaking)
- Documentation
- Number of API workers in supervisord.conf (scaling)

---

## 8. AI Coding Rules and Behavioral Contracts

### 8.1 Statement

**This document does NOT define coding rules.** All coding standards, formatting rules, and stack-specific practices are defined in dedicated AI*.md files.

### 8.2 AI Rules Files

| File | Purpose | Scope |
|------|---------|-------|
| `CLAUDE.md` | AI behavior contract | Workflow: read AI*.md first, propose before coding |
| `AI.md` | General Python guidelines | PEP8, type hints, docstrings, project structure |
| `AI-PYTHON-REST-API.md` | REST API patterns | FastAPI/aiohttp, Pydantic, error handling |
| `AI_FLASK.md` | Flask web app rules | Templates, routes, services separation |
| `AI_SQLite.md` | SQLite performance rules | **CRITICAL**: WAL mode, caching, no DB in /health |

**Most Critical:** `AI_SQLite.md` - Contains performance rules that prevent production issues (WAL mode, busy_timeout, stats caching, /health endpoint must not touch DB)

### 8.3 Rule Precedence Hierarchy

When conflicts arise, apply rules in this order (highest to lowest priority):

```
1. User's explicit instruction in current conversation
   ↓
2. Stack-specific AI rules (AI_SQLite.md, AI_FLASK.md, AI-PYTHON-REST-API.md)
   ↓
3. General AI rules (AI.md)
   ↓
4. This architecture document (ARCHITECTURE.md)
   ↓
5. Language/framework conventions (PEP8, Flask best practices)
```

**Example:** If user says "add print statements for debugging" but AI.md says "use logging, not print", follow user instruction (Rule 1 wins).

### 8.4 Conflict Resolution Process

When encountering conflicting guidance:

1. **STOP** - Do not proceed with implementation
2. **IDENTIFY** - State which rules/documents conflict
3. **ASK** - Present options to user with trade-offs
4. **WAIT** - Get explicit decision before coding

**Example:**
```
"I see a conflict:
- AI_SQLite.md says: 'Cache expensive queries with 5s TTL'
- Your request: 'Make /status return real-time data'

Options:
A) Cache with 1s TTL (compromise)
B) Add ?force_refresh=true parameter (user choice)
C) Keep 5s cache, document limitation

Which approach do you prefer?"
```

### 8.5 Key Architectural Decisions to Preserve

**These decisions are foundational - do not change without architectural review:**

1. **SQLite with WAL mode** - Chosen for simplicity, no external DB needed
2. **aiohttp for main API** - Async required for WebSocket + HTTP concurrency
3. **Flask for admin UI** - Separate process, simpler than async templates
4. **Supervisord for multi-process** - Single container, multiple workers
5. **Multi-worker architecture (v0.6.0)** - Separate API and WebSocket processes for performance and stability
6. **Thread-local SQLite connections** - Required for thread safety
7. **Stats caching (5s TTL)** - Prevents /status endpoint from blocking on DB locks
8. **No DB access in /health** - Critical for load balancer health checks
9. **Candle deletion with ticker removal** - Consistency (fixed in v0.4.3)
10. **Tick-save frequency reduction** - Save every 10 ticks or 5s to reduce DB write pressure

---

## 9. Quick Start for AI Assistants

### 9.1 Pre-Flight Checklist

Before making ANY code changes:

- [ ] Read `CLAUDE.md` for workflow rules
- [ ] Check relevant AI*.md files for stack-specific rules
- [ ] Review `docs/chats/` for recent implementation context
- [ ] Check Section 7 (Stability Zones) for change risk
- [ ] Propose solution and wait for explicit approval (per CLAUDE.md)

### 9.2 Where to Find Information

| Need | Location |
|------|----------|
| Coding standards | `AI.md`, `AI-PYTHON-REST-API.md`, `AI_FLASK.md`, `AI_SQLite.md` |
| Architecture overview | This file (ARCHITECTURE.md) |
| Multi-worker architecture | `docs/MULTI_WORKER_DEPLOYMENT.md` |
| User documentation | `README.md` |
| Implementation details | `docs/IMPLEMENTATION_v0.4.0.md` |
| Recent changes/bugs | `docs/chats/` directory |
| Future plans | `ROADMAP.md` |
| API endpoints | `README.md` (API Reference section) |
| Configuration options | `.env.example` |
| Database schema | `src/storage.py` (init_db method) |
| Deployment | `Dockerfile`, `docker-compose.yml`, `supervisord.conf` |

### 9.3 Common Tasks

**Add new API endpoint:**
1. Check `AI-PYTHON-REST-API.md` for patterns
2. Add route in `src/api/routes.py`
3. Update `README.md` API Reference table
4. Add test in `tests/`

**Modify database:**
1. Check `AI_SQLite.md` for performance rules
2. Update schema in `src/storage.py`
3. Consider migration path for existing deployments
4. Update tests

**Change admin UI:**
1. Check `AI_FLASK.md` for Flask patterns
2. Modify templates in `src/admin/templates/`
3. Update `docs/ADMIN_UI.md` if user-facing

**Fix bug:**
1. Check `docs/chats/` for similar issues
2. Create new chat log in `docs/chats/` with date
3. Document root cause and fix
4. Add regression test

**Scale API workers:**
1. Edit `supervisord.conf`
2. Change `numprocs` for api_worker (or add explicit workers)
3. Ensure port allocation is correct
4. Test with `supervisorctl status`

---

**End of ARCHITECTURE.md**
