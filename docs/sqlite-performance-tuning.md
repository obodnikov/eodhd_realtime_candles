# **AI.md — Engineering Rules for EODHD Real-Time Candle Service**

## **1. Core Architecture Rules**

### **1.1 Never block the main event loop**

The service is real-time and event-driven.
**Any operation that can take >1ms must be executed outside the main HTTP worker**, especially:

* SQLite `DELETE`, `COUNT(*)`, `VACUUM`, full scans
* Batch candle cleanups
* Deep historical recalculations
* File I/O
* External API calls

**Blocking operations must run in:**

* background tasks
* dedicated worker threads
* scheduled maintenance jobs
* a separate service if needed

**Do NOT perform expensive operations inside HTTP request handlers.**

---

## **2. Database Rules (SQLite)**

### **2.1 Always configure SQLite for high-concurrency**

Every connection **must** include:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
```

These improve concurrency and prevent write locks from freezing HTTP responses.

### **2.2 Cleanup must be incremental**

Never run large `DELETE` queries inline.
Rules:

* Use batch deletion (LIMIT 500–1000)
* Cleanup only on a timer (e.g. every 30–60 sec)
* Never cleanup inside a request handler
* Avoid running `COUNT(*)` over entire tables frequently

### **2.3 Never open or close SQLite connections per request**

Always use:

* a single connection per thread (`thread_local`)
* long-lived connections only
* no reinitialization in request handling paths

---

## **3. HTTP API Rules**

### **3.1 `/health` must always return in < 50 ms**

Never call:

* DB operations
* background services
* anything asynchronous that may block

`/health` must be pure in-memory:

```python
return {"status": "healthy", "timestamp": ...}
```

### **3.2 Use multiple HTTP workers**

When deploying under uvicorn/gunicorn:

```
workers >= number_of_cpu_cores / 2
```

Minimum rule: **never run with a single worker in production**.

### **3.3 All HTTP handlers MUST be non-blocking**

Even reading small DB chunks must use threadpool execution:

```python
from fastapi.concurrency import run_in_threadpool
result = await run_in_threadpool(storage.get_candles, ticker, count)
```

(Or aiojobs if staying on aiohttp.)

---

## **4. Concurrency Rules**

### **4.1 WebSocket ingestion must be isolated**

The ingestion loop must NOT:

* hold locks needed by SQLite readers
* execute heavy computations inline
* block the global event loop

It must push data into a queue, and a dedicated worker must write to SQLite.

### **4.2 No shared mutable global state**

If global state is required:

* protect access via locks
* prefer message queues (asyncio.Queue)
* avoid global dicts or lists modified across threads

---

## **5. Docker & Deployment Rules**

### **5.1 Always specify resource expectations**

The service must declare:

* minimum CPUs: `1–2`
* memory: `512MB–2GB`
* no strict container memory limit unless required

Do not rely on “default unlimited Docker resources”, because that may vary between hosts.

### **5.2 Use a production ASGI server**

Never run with:

```bash
python -m src.main
```

Use:

```
uvicorn src.main:app --host 0.0.0.0 --port 8765 --workers 2
```

or

```
gunicorn -k uvicorn.workers.UvicornWorker -w 2 src.main:app
```

### **5.3 Health checks must time out fast**

Dockerfile:

```
HEALTHCHECK --interval=60s --timeout=3s --retries=2 CMD wget -qO- http://localhost:8765/health || exit 1
```

Never allow the health check to hang the container.

---

## **6. Code Quality Rules**

### **6.1 Any new endpoint must follow this checklist**

Before merging, the endpoint must:

* return in < 200ms under load
* NOT perform DB writes
* NOT perform large DB reads
* NOT touch SQLite schema
* log duration for troubleshooting

### **6.2 All heavy operations must include duration logging**

Examples:

```python
start = time.monotonic()
...
logger.info("cleanup done in %.3f sec", time.monotonic() - start)
```

### **6.3 Any async code must be actually async**

Avoid:

* time.sleep
* blocking libraries
* synchronous network calls
* synchronous file I/O

Use async alternatives or `run_in_executor`.

---

## **7. Scalability Path Rules**

Before adding new features, evaluate whether the following is required:

* Migration from SQLite → PostgreSQL
* Offloading ingestion to a separate microservice
* Offloading cleanup to a cron job
* Switching from aiohttp → FastAPI for API growth and ergonomics
* Running multiple replicas behind nginx / Traefik

---

## **8. When rewriting to FastAPI is allowed**

Rewrite is justified only if:

* the API surface grows (5–10+ endpoints)
* you need OpenAPI docs
* you need standard auth
* you want multi-worker performance without manual aiohttp tuning
* you want long-term maintainability

FastAPI rewrite is **not allowed solely to “fix latency”** unless data layer and concurrency design are also fixed.

---

## **9. Rules for Future Contributors (LLM or human)**

### **9.1 Never introduce new blocking operations**

If adding a new feature, you must explain:

* how it avoids blocking
* how it interacts with SQLite
* how it affects worker concurrency
* what happens if two heavy tasks overlap

### **9.2 Always justify DB schema changes**

Every schema change must consider:

* impact on real-time ingestion
* cost of migration
* cost of full-table scans
* effect on cleanup logic

### **9.3 PR must include performance considerations**

Every PR must answer:

* What is the worst-case impact on latency?
* Can this block `/health`?
* Should this run in a background worker?
* Does this increase DB I/O significantly?

---

## **10. Golden Rule**

> **Nothing heavy ever runs inline with request processing — all heavy work is delegated.
> The HTTP layer must stay responsive under any internal load.**

---
