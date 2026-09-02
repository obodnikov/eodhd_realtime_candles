# EODHD Real-Time Candle Aggregator

A real-time market-data microservice that connects to the EODHD WebSocket feed and aggregates
raw ticks into configurable OHLCV candles during market hours, exposing them through an aiohttp
REST API with a Flask admin UI. It exists because EODHD's Intraday Historical API only publishes
data 2–3 hours after close; this service fills that gap with sub-100ms candle updates. Its
defining characteristic is a multi-worker architecture (isolated WebSocket ingest + parallel API
workers) sharing a single SQLite-WAL or PostgreSQL backend under supervisord.

> Status: **implemented and in use** — v0.9.4. Multi-worker ingest/aggregation, REST API, admin UI,
> and SQLite/PostgreSQL backends are built and covered by pytest.
> [ARCHITECTURE.md](ARCHITECTURE.md) is the authoritative design source; [ROADMAP.md](ROADMAP.md)
> tracks planned work.
>
> Build & test: `pip install -r requirements.txt`, `pytest`, `docker compose up -d`.

## Read before making changes

1. [ARCHITECTURE.md](ARCHITECTURE.md) — system structure, components, stability zones.
2. The relevant `AI_*.md` file(s) for the code you are touching — coding rules (see below).
3. [docs/](docs/) — deployment, migration, and operational guides; [docs/chats/](docs/chats/) for
   prior implementation history and review context.

## Coding rules live in `AI_*.md` (do not duplicate them here or in ARCHITECTURE.md)

| File | Scope |
| --- | --- |
| [AI.md](AI.md) | General Python rules (PEP8, type hints, `.env`/dotenv, structure) — all `src/**`, `scripts/**`, `tests/**` |
| [AI_REST_API.md](AI_REST_API.md) | aiohttp REST API — `src/api/**`, `src/api_server.py`, `src/main.py` |
| [AI_WEBSOCKET_ENGINE.md](AI_WEBSOCKET_ENGINE.md) | WebSocket ingest + candle aggregation — `src/websocket_worker.py`, `src/websocket_manager.py`, `src/candle_engine.py`, `src/candle_aggregator.py` |
| [AI_FLASK.md](AI_FLASK.md) | Flask admin UI — `src/admin/**` |
| [AI_SQLITE.md](AI_SQLITE.md) | SQLite persistence — `src/storage.py` |
| [AI_POSTGRESQL.md](AI_POSTGRESQL.md) | PostgreSQL persistence + migrations — `src/storage_postgres.py`, `scripts/init_postgres.sql` |

`ARCHITECTURE.md` and the `AI_*.md` files must not redefine or duplicate each other's content.

## Working agreement

- **Propose before coding.** Never start editing code immediately after a user request. First
  read the applicable `AI_*.md` files and ARCHITECTURE.md, check `docs/chats/` for prior context,
  then propose specifics (which files, what changes) and wait for a clear "yes." Read-only work
  (reading, searching, analyzing, answering) needs no confirmation. Exception: if the user says
  "just do it" / "go ahead," proceed directly.
- **Don't commit unprompted.** Run `git add` / `git commit` / `git push` only when the user
  explicitly asks — never as an unrequested side-effect of another task.
- **Never fake candle data, drop ticks silently, or weaken a test or DB contract to make a check
  pass.** A green check must reflect real tick-to-candle correctness and durable persistence;
  surface the honest failure instead.
- **Stop and ask** if anything is unclear or contradictory.

## Tooling

- Python 3.9+ (uses `asyncio.to_thread`); async runtime is `asyncio`.
- Dependencies via `requirements.txt` (`pip install -r requirements.txt`); no `pyproject.toml`/lockfile.
- Runtime stack: `aiohttp` (API), `websockets` (feed client), `Flask` + `waitress` (admin UI),
  `sqlite3` (stdlib) or `psycopg2` (PostgreSQL), `supervisord` (process manager), Docker Compose.
- Tests: `pytest` with `pytest-asyncio` and `pytest-mock`. Run `pytest` from the repo root.
- Secrets/config: `.env` loaded via `python-dotenv`; document every var in `.env.example`. Never
  hard-code keys or paths.
- No linter or type-checker is configured (no ruff/flake8/black/mypy). Follow PEP8 + type hints by
  hand per [AI.md](AI.md); adding a gate is a project decision, not an assumption.
