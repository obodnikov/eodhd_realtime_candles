# AI rules — Admin UI (Python / Flask)

Scope: `src/admin/**` — the operator dashboard (`app.py`, `api_client.py`, `auth.py`, templates).
It is a **thin client over the REST API**, not a second backend. See
[ARCHITECTURE.md](ARCHITECTURE.md) §4.1 for placement; this file is the coding contract. REST rules
live in [AI_REST_API.md](AI_REST_API.md).

## Architecture boundary (hard rule)

- The admin UI talks to the system **only** through `APIClient` (HTTP to the REST API). It must
  **never** import `Storage`, open a DB connection, or touch SQLite/PostgreSQL directly, and never
  imports `candle_engine`/`websocket_manager`.
- All data reaches templates via `api_client` calls; keep the API as the single source of truth.
- Use the app factory (`create_app()`), store shared objects on `app.config`
  (`API_CLIENT`, `API_URL`, `API_KEY`), and read them from there — no module-level globals.

## Flask conventions

- Keep JS/CSS in `static/` and markup in Jinja2 `templates/`; no inline `<script>` logic beyond
  wiring. Escape user/data values in templates (autoescaping on — don't disable it).
- Page routes return `render_template(...)`; AJAX/action routes return `jsonify(...)` with an
  explicit status and the `{'success': bool, ...}` shape already used across `app.py`.
- On API-client failure, degrade gracefully: log the error and render the page with empty/`None`
  data (as existing handlers do) rather than a 500 stack trace.
- Register reusable Jinja filters (`datetime`, `number`) on the app; don't format in ad-hoc ways.

## Auth & sessions

- Protect every operator route with `@login_required`; the only unauthenticated routes are the
  login flow itself and static assets.
- Login verifies the operator key with `verify_api_key` against `API_KEY`; a missing `API_KEY`
  must **fail closed** (no login succeeds) — never default to allowing access.
- `app.secret_key` comes from `ADMIN_SESSION_SECRET` or a per-process random secret; never
  hard-code a secret. Don't store the API key or secrets in the rendered page or client-side JS.

## Deployment & binding

- Serve behind **waitress** (WSGI), not the Flask dev server, in the container.
- Default bind is `ADMIN_HOST=127.0.0.1` (localhost-only). Do not change the default to `0.0.0.0`;
  external exposure is an explicit operator opt-in and must stay documented as such.

## Testing

- `pytest` with Flask's `test_client()`. Cover: `@login_required` redirects when unauthenticated,
  login succeeds/fails correctly (including empty `API_KEY` failing closed), and action routes
  return the right status + JSON shape. Mock `APIClient` with `pytest-mock`; never hit a live API.
