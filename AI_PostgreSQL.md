# AI Guidelines for PostgreSQL in Production Services

This file defines reusable PostgreSQL rules for AI assistants working on backend systems.
It is intentionally framework-agnostic and applies across projects.

---

## 1. Core Principles

- Prioritize correctness and data integrity over micro-optimizations.
- Prefer additive, backward-compatible change paths.
- Keep behavior observable (metrics/logging for critical DB paths).
- Treat schema changes as operational events, not only code changes.

---

## 2. Schema & Migration Safety

### DO:
- Use additive migrations first (`ADD COLUMN`, new tables, new indexes).
- Make migrations idempotent when possible.
- Use explicit transaction boundaries for migration steps.
- Document rollout order when application and schema must be deployed in sequence.

### DON'T:
- Drop/rename columns or tables without explicit approval and rollback plan.
- Perform destructive data rewrites in a single unbounded migration.
- Assume zero-downtime for lock-heavy DDL.

### Required for risky schema changes:
- Forward plan
- Rollback plan
- Data backfill plan
- Verification queries

---

## 3. Query & Transaction Rules

### Query safety:
- Always use parameterized queries (never string-interpolated SQL).
- Keep query shape explicit and predictable.
- Prefer set-based SQL over row-by-row loops.

### Transaction safety:
- Keep transactions short to reduce lock contention.
- Use the narrowest scope needed for atomicity.
- Define expected isolation behavior when correctness depends on it.

### Retryable failures:
- Handle transient errors (deadlocks, serialization failures, brief connectivity loss).
- Use bounded retries with jitter/backoff.
- Preserve idempotency across retry paths.

---

## 4. Performance Rules

- Avoid write amplification in hot paths; batch/queue/flush when feasible.
- Verify critical queries with `EXPLAIN (ANALYZE, BUFFERS)` before/after major changes.
- Add indexes for frequent filters/sorts/joins, then validate actual planner usage.
- Avoid N+1 query patterns in API and background tasks.
- Keep lock hold time minimal in high-frequency code paths.

---

## 5. Data Integrity Rules

- Enforce invariants in the database where practical (`NOT NULL`, `CHECK`, FK, unique).
- Use explicit UTC timestamp semantics consistently.
- Keep application behavior compatible with both old and new schema during rollout.
- Never silently drop or ignore failed writes without explicit policy and metrics.

---

## 6. Operational Rules

- Every migration change must include a verification step for operators.
- Record schema/version state in a reliable place (migration table or equivalent).
- Emit actionable logs for DB failures (query context, error class, retry outcome).
- Ensure backup/restore expectations are documented for production-impacting changes.

---

## 7. Testing Requirements

Minimum expectations for PostgreSQL-impacting changes:
- Migration idempotency test (or explicit one-shot justification).
- Regression tests for failure/retry paths.
- Compatibility tests for mixed rollout states where applicable.
- Performance test or benchmark evidence for hot-query changes.

---

## 8. Review Checklist

Before merge, confirm:
1. Is schema change additive and rollout-safe?
2. Are queries parameterized and transaction boundaries minimal?
3. Are retry/error paths correct and observable?
4. Are verification steps and rollback documented?
5. Are tests covering migration + runtime behavior?

---

## 9. Conflict Resolution

If these rules conflict with project-specific constraints:
1. Stop implementation.
2. Identify the exact conflict.
3. Propose options with trade-offs.
4. Wait for explicit approval before proceeding.
