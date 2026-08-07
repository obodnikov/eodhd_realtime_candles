---
name: review-fix-loop
description: >
  Autonomous iterative code review and fix loop using an external review service.
  Use this skill whenever the user asks for code review, wants to run a review loop,
  says "review my code", "fix review issues", "run the review loop", or asks to
  get code approved by the review service. Also trigger when the user mentions
  iterating on review feedback or fixing blocking issues from a review report.
---

# Review-Fix Loop

Run an autonomous iterative code review loop against the external review service.
The goal is to reach `APPROVED` status by fixing all blocking issues, without
requiring human intervention between iterations.

## Authority within this loop

Invoking this skill is an explicit, user-initiated autonomous run — it grants the
"just do it" exception to CLAUDE.md's propose-before-coding rule **for code fixes,
for the duration of the loop**. Do not pause for confirmation between iterations.
You still must NOT run `git add` / `git commit` / `git push`.

Fixes must obey the per-component rule files: [AI.md](../../../AI.md),
[AI_REST_API.md](../../../AI_REST_API.md),
[AI_WEBSOCKET_ENGINE.md](../../../AI_WEBSOCKET_ENGINE.md),
[AI_FLASK.md](../../../AI_FLASK.md), [AI_SQLITE.md](../../../AI_SQLITE.md), and
[AI_POSTGRESQL.md](../../../AI_POSTGRESQL.md). Do not weaken a test, drop ticks, or
loosen a DB/safety contract to make a check pass.

## HARD LIMIT — 5 ITERATIONS MAXIMUM

You MUST track your iteration count explicitly. Before each review run, output:

> **ITERATION N/5**

where N is the current iteration number starting at 1.

- If N would exceed 5, STOP IMMEDIATELY. Do not run the review again.
- If you complete iteration 5 and the result is not `APPROVED`, STOP IMMEDIATELY.
- This limit is absolute and non-negotiable regardless of how close to approval
  you believe you are.
- UNDER NO CIRCUMSTANCES may you run `code-review.sh` more than 5 times total.

## Before Starting

Before your first iteration, state:

> Starting review-fix loop. Hard cap: 5 iterations. I will count each one explicitly.

## Prerequisites

The external review script must exist and be executable:
```
$HOME/mbin/code-review.sh
```

Verification uses this project's own toolchain: `pytest` (with `pytest-asyncio` and
`pytest-mock`), plus Docker for infra changes. There is no linter/type-checker
configured. Run `pip install -r requirements.txt` first if dependencies are
missing. Run only the checks relevant to what you changed:

| Changed area | Verify with |
|---|---|
| `src/api/**`, `src/api_server.py`, `src/main.py` | `pytest tests/test_api_*.py tests/test_routes_*.py` |
| `src/websocket_worker.py`, `src/websocket_manager.py`, `src/candle_engine.py`, `src/candle_aggregator.py` | `pytest tests/test_websocket_*.py tests/test_candle_*.py` |
| `src/admin/**` | `pytest tests/` (admin-relevant tests) |
| `src/storage.py` | `pytest tests/test_storage_*.py` |
| `src/storage_postgres.py`, `scripts/init_postgres.sql` | `pytest tests/test_storage_postgres.py tests/test_storage_factory.py` |
| `scripts/**` | `pytest tests/test_manage_tickers.py tests/test_premarket_volume.py tests/test_trading_preparation.py` |
| `Dockerfile*`, `docker-compose*.yml`, `supervisord.conf` | `docker compose build` (and `docker compose config` to validate) |
| anything broad / unsure | `pytest` (full suite) |

Per-area rules live in the `AI_*.md` files at the repo root. When in doubt, run
the full `pytest` suite rather than skipping coverage.

## The Loop

Execute the following steps in a tight loop. Do **not** pause or ask for
confirmation between iterations — the whole point is autonomy. Only stop when
approval is reached or the iteration cap is hit.

### Step 1: Declare iteration and run the review

Output your current iteration count:

> **ITERATION N/5**

If N > 5, STOP. You have hit the cap. Skip to the Termination section below.

Then run:
```bash
$HOME/mbin/code-review.sh
```
Timeout: 15 minutes.

### Step 2: Interpret the output

Read the first line of stdout carefully. The script signals its verdict through
a keyword prefix:

| Prefix | Meaning | Action |
|---|---|---|
| `APPROVED` | All checks passed, no issues remain | Stop. Report success to the user. |
| `NO_CHANGES` | No diff to review (clean working tree) | Stop. Tell the user there's nothing to review. |
| `NEEDS_CHANGES` | Issues found; the rest of stdout is a markdown review report | Proceed to Step 3. |

If the script times out or returns unexpected output, treat it as a failure,
report it, and stop.

### Step 3: Fix the issues

The markdown review report that follows `NEEDS_CHANGES` classifies issues by
severity. Address them in priority order:

1. **Fix all HIGH (blocking) issues first.** These must be resolved before
   anything else — they are the ones preventing approval.
2. **Then fix MEDIUM issues.** These are important but not strictly blocking.
   Use your judgment on LOW issues — fix them if they're quick and obvious, but
   don't let them prevent approval.

After applying fixes, verify with the checks for the area(s) you changed (see the
Prerequisites table). At minimum run the matching `pytest` subset; if you touched
several layers, run the full `pytest` suite. If any check fails, fix it before the
next review round — don't push broken fixes forward.

### Step 4: Increment and repeat

Add 1 to your iteration counter. Go back to Step 1.

Remember: if the next iteration would be N > 5, you MUST stop instead.

## Termination (iteration cap reached)

If you have completed 5 iterations without receiving `APPROVED`:

1. STOP. Do not run the review again.
2. Summarize the remaining issues from the last review report.
3. Report to the user what is still outstanding so they can decide how to proceed.
4. State clearly: "Reached maximum of 5 iterations without approval."

## Important Notes

- **Stay autonomous.** Do not ask "should I fix this?" or "is this approach OK?"
  between iterations. Fix the issues, verify, and move on.
- **Read the report carefully.** Each review run may surface new issues that
  weren't visible before (e.g., cascading effects of earlier fixes). Treat each
  report as the current ground truth.
- **Don't over-engineer fixes.** Make the minimal change that addresses the
  reviewer's concern while keeping the codebase healthy.
- **Count every review run.** Each execution of `code-review.sh` counts as one
  iteration regardless of its outcome. There is no "free" run.
