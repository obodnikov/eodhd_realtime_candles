# AI rules — REST API (Python / aiohttp)

Scope: `src/api/**`, `src/api_server.py`, `src/main.py` — the aiohttp REST layer that serves
candles, tickers, config, and status. API workers run read-mostly and share the DB with the
WebSocket worker. See [ARCHITECTURE.md](ARCHITECTURE.md) §4.2 and §5.1 for placement; this file is
the coding contract. Persistence rules live in [AI_SQLITE.md](AI_SQLITE.md) and
[AI_POSTGRESQL.md](AI_POSTGRESQL.md); tick/aggregation rules in
[AI_WEBSOCKET_ENGINE.md](AI_WEBSOCKET_ENGINE.md).

## Framework — aiohttp only

- This project uses **aiohttp** (`aiohttp.web`). Do not introduce FastAPI, Flask, pydantic, or
  SQLAlchemy into the API layer.
- Register routes in `APIRoutes._setup_routes()`. Handlers are `async def (self, request) -> web.Response`.
  Order routes from most-specific to least-specific (e.g. `/candles/{ticker}/latest` before
  `/candles/{ticker}`) — aiohttp matches in registration order.
- Access shared services through the app context via the existing properties
  (`self.storage`, `self.candle_engine`, `self.config_manager`, `self.ws_manager`), not globals.
- Return `web.json_response(...)`; set explicit `status=` for non-200 responses.

## Async discipline (event loop must never block)

- **Every storage / blocking call inside a handler must be wrapped in `asyncio.to_thread(...)`.**
  `sqlite3` and `psycopg2` are synchronous and will stall the loop and drop WebSocket throughput.
- Do not perform CPU-heavy work or `time.sleep` in handlers; use `await asyncio.sleep`.
- `/health` must stay database-free and cheap — no `get_stats`, no DB reads.
- API workers run with a dummy `WebSocketManager` (`ws_manager.is_dummy`); read live WS/candle
  state from the DB (`get_websocket_status`, `get_active_candles`) rather than in-process state.

## Auth & middleware

- Middleware order is fixed: `create_auth_middleware` (if `API_KEY` set) → `error_middleware`
  → `logging_middleware`. Keep `error_middleware` and `logging_middleware` always present.
- Auth accepts `X-API-Key`, `Authorization: Bearer <key>`, or `?api_key=`. `/health` is the only
  unauthenticated route — never exempt others without explicit approval.
- If `API_KEY` is unset, auth is disabled and a warning is logged; never silently accept requests
  in a way that hides a missing key in production.
- Let `error_middleware` own the 500 path: raise `web.HTTPException` subclasses or return a
  `web.json_response({'error': ..., 'message': ...}, status=...)`. Never swallow an exception and
  return 200.

## Request/response contract

- Error bodies use `{'error': <short>, 'message': <detail>}`; keep this shape consistent.
- Destructive endpoints require explicit guards: `DELETE /tickers` (all) needs
  `ALLOW_DELETE_ALL_TICKERS=true` **and** `?confirm=true`; `GET /candles/all` needs `confirm=true`
  and `max_tickers=N`. Do not weaken these safety gates.
- Validate and coerce query params (`count`, timestamps, `minutes`) with clear 400s on bad input;
  never trust raw strings in queries.
- Timestamps are Unix seconds in payloads; human-readable fields are UTC (`datetime_utc`). Keep
  `datetime.now(timezone.utc)` — never naive `datetime.now()`.
- When you add or change an endpoint, update the API Reference in [README.md](README.md) and add a
  test in `tests/test_api_*.py`.

## Testing

- Use `pytest` + `pytest-asyncio`; exercise handlers with `aiohttp.test_utils` (`TestClient` /
  `AppRunner`) or by calling handlers with a mocked app context.
- Cover auth (valid/invalid/missing key), the confirmation-gated destructive routes, and at least
  one success + one error path per new endpoint.
