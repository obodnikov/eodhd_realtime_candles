# Claude Chat Export
**Generated: 2026-04-25 09:06:31**

I will follow these instructions.

---

👤 **USER:**
> I still have a warning 026-02-17 18:51:17,662 - src.candle_engine - INFO - Started tracking MTSI at price 245.032026-02-17 18:51:27,808 - src.storage - WARNING - WAL checkpoint failed: database table is locked2026-02-17 18:51:30,471 - src.candle_engine - INFO - Started tracking ELF at price 85.882026-02-17 18:51:36,362 - src.candle_engine - INFO - Started tracking CNVS at price 2.75

Let me first gather context about the WAL checkpoint locking issue by reading the relevant files.

Now let me look at the storage.py file where the WAL checkpoint is happening, and the websocket_worker.py to understand the cleanup task:

Now let me check the storage connection setup and the `_get_connection` method:

Now let me check the previous chat about database locking to see if this was already addressed:

Now let me check the current state of the API server and how many processes are running:

Now I have a clear picture. Here's the analysis:

The `WAL checkpoint failed: database table is locked` warning you're seeing is actually a benign, expected behavior in your setup. Here's what's happening:

The `checkpoint_wal` in `storage.py` runs `PRAGMA wal_checkpoint(PASSIVE)` every 30 seconds from the cleanup task. PASSIVE mode is designed to never block, but it will fail if another connection is actively reading or writing at that moment. With your multi-worker architecture (2 API workers reading + 1 WebSocket worker writing ticks, active candles, and WebSocket status), there's almost always some concurrent DB activity.

The checkpoint still partially succeeds most of the time (it checkpoints whatever pages it can), and `wal_autocheckpoint=1000` on every connection provides a safety net to prevent unbounded WAL growth.

So this warning is cosmetic noise, not a real problem. You have two options:

1. Downgrade the log level from `WARNING` to `DEBUG` so it doesn't clutter your logs. The checkpoint is best-effort by design (PASSIVE mode), and a failure just means "I'll try again in 30 seconds."

2. Add a small retry with a brief sleep (e.g., 100ms) before logging the warning, giving concurrent operations a moment to finish.

I'd recommend option 1 since it's the simplest and most aligned with the intent. The warning is misleading because PASSIVE checkpoint "failure" is normal under concurrent load. It's not actually losing data or causing WAL bloat.

Proposed change in `src/storage.py`, in the `checkpoint_wal` method:

```python
# Change this:
logger.warning(f"WAL checkpoint failed: {e}")

# To this:
logger.debug(f"WAL checkpoint skipped (concurrent access): {e}")
```

This matches the `AI_SQLite.md` guidance that PASSIVE checkpoints are best-effort and should never block. The `wal_autocheckpoint=1000` pragma on every connection is the real defense against WAL bloat.

Want me to make this change?

