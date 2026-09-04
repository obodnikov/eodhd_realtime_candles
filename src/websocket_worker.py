#!/usr/bin/env python3
"""
WebSocket Worker Entry Point
=============================

Dedicated worker for WebSocket connection and tick processing.
Runs as a single process (configured in supervisord).

This worker:
- Connects to EODHD WebSocket feed
- Processes all tick data from subscribed tickers
- Aggregates ticks into OHLCV candles
- Writes candles to database
- Runs background cleanup tasks
- Does NOT handle HTTP requests (that's api_server.py)

Usage:
    python -m src.websocket_worker
"""

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .config import Config, ConfigManager
from .storage import Storage
from .storage_factory import create_storage, get_database_type
from .candle_engine import CandleEngine
from .websocket_manager import WebSocketManager


def setup_logging(level: str):
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Reduce noise from libraries
    logging.getLogger('websockets').setLevel(logging.WARNING)

    # Install in-memory log buffer for admin panel
    from .log_buffer import install_log_buffer
    install_log_buffer()


async def cleanup_task(storage, candle_engine: CandleEngine):
    """
    Background task that processes pending candle cleanup.
    
    Runs every 30 seconds to batch cleanup operations instead of
    running them on every candle completion (performance optimization).
    
    Processes tickers one-by-one and only removes from pending after
    successful cleanup to prevent data loss on task cancellation.
    """
    logger = logging.getLogger(__name__)
    logger.info("Background cleanup task started (30s interval)")
    
    while True:
        try:
            await asyncio.sleep(30)
            
            # Get pending tickers (don't clear yet - process individually)
            pending = candle_engine.get_pending_cleanup()
            if not pending:
                continue
            
            # Process each ticker individually
            max_candles = candle_engine.max_candles
            cleaned_count = 0
            
            # Copy to list to avoid modification during iteration
            for ticker in list(pending):
                try:
                    await asyncio.to_thread(
                        storage.cleanup_old_candles,
                        ticker,
                        max_candles
                    )
                    # Only remove from pending after successful cleanup
                    candle_engine.remove_from_pending_cleanup(ticker)
                    cleaned_count += 1
                except Exception as e:
                    logger.warning(f"Cleanup failed for {ticker}: {e}")
                    # Keep in pending for retry on next iteration
            
            if cleaned_count > 0:
                logger.debug(f"Batch cleanup completed for {cleaned_count} tickers")
            
            # Periodic WAL checkpoint to prevent unbounded WAL growth (SQLite only)
            if hasattr(storage, 'checkpoint_wal'):
                try:
                    await asyncio.to_thread(storage.checkpoint_wal)
                except Exception as e:
                    logger.warning(f"WAL checkpoint error: {e}")
                
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")


async def websocket_status_task(storage, ws_manager: WebSocketManager, get_tick_metrics):
    """
    Background task that periodically writes WebSocket status to database.
    
    Runs every 10 seconds to share WebSocket worker status with API workers.
    This enables API workers to show real WebSocket status in /status endpoint.
    
    Optimized to only write to database if status has actually changed.
    Compares all relevant fields including lists and timestamps.
    """
    logger = logging.getLogger(__name__)
    logger.info("WebSocket status update task started (10s interval)")
    
    last_status = None
    
    while True:
        try:
            await asyncio.sleep(10)
            
            # Get current WebSocket status
            status = ws_manager.get_status()
            status.update(get_tick_metrics())
            
            # Only update if status changed (optimization)
            # Compare all relevant fields including lists (convert to tuple for comparison)
            status_key = (
                status.get('connected'),
                tuple(sorted(status.get('subscribed_tickers', []))),  # Sort for consistent comparison
                status.get('subscribed_count'),
                tuple(sorted(status.get('pending_subscribe', []))),   # Sort for consistent comparison
                status.get('connection_count'),
                status.get('tick_count'),
                status.get('last_message'),
                status.get('tick_queue_size'),
                status.get('tick_queue_maxsize'),
                status.get('tick_enqueued_count'),
                status.get('tick_processed_count'),
                status.get('tick_dropped_count'),
                status.get('candle_write_queue_size'),
                status.get('candle_write_queue_maxsize'),
                status.get('candle_write_dropped_count'),
                status.get('stale_tick_dropped_count'),
                status.get('out_of_order_tick_dropped_count')
            )
            
            if last_status != status_key:
                # Write to database (non-blocking)
                await asyncio.to_thread(storage.update_websocket_status, status)
                
                logger.debug(f"Updated WebSocket status: connected={status.get('connected')}, tickers={status.get('subscribed_count')}")
                last_status = status_key
            else:
                logger.debug("WebSocket status unchanged, skipping DB write")
                
        except asyncio.CancelledError:
            logger.info("WebSocket status update task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in WebSocket status update task: {e}")


async def active_candles_task(storage, candle_engine: CandleEngine):
    """
    Background task that periodically writes active candles to database.
    
    Runs every 10 seconds to share active candles from WebSocket worker with API workers.
    This enables API workers to show real-time active candles on dashboard.
    
    Always writes to database (active candles change frequently).
    """
    logger = logging.getLogger(__name__)
    logger.info("Active candles update task started (10s interval)")
    
    while True:
        try:
            await asyncio.sleep(10)
            
            # Get current active candles summary
            active_candles = candle_engine.get_active_tickers_summary()
            
            # Write to database (non-blocking)
            await asyncio.to_thread(storage.update_active_candles, active_candles)
            
            logger.debug(f"Updated active candles: {len(active_candles)} candles")
                
        except asyncio.CancelledError:
            logger.info("Active candles update task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in active candles update task: {e}")


async def ticker_status_flush_task(candle_engine: CandleEngine, interval_seconds: float):
    """
    Periodically flush pending ticker status updates to storage.

    This decouples high-frequency ticks from per-tick ticker status writes.
    """
    logger = logging.getLogger(__name__)
    logger.info("Ticker status flush task started (%.2fs interval)", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(candle_engine.flush_pending_ticker_statuses)
        except asyncio.CancelledError:
            logger.info("Ticker status flush task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in ticker status flush task: {e}")


async def candle_write_flush_task(candle_engine: CandleEngine, interval_seconds: float = 0.25):
    """
    Periodically flush queued candle writes to storage.

    Short interval keeps candle persistence near-real-time while moving DB I/O
    out of the per-tick lock path.
    """
    logger = logging.getLogger(__name__)
    logger.info("Candle write flush task started (%.2fs interval)", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(candle_engine.flush_pending_candle_writes)
        except asyncio.CancelledError:
            logger.info("Candle write flush task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in candle write flush task: {e}")


async def ticker_sync_task(storage: Storage, ws_manager: WebSocketManager, sync_interval: int = 30):
    """
    Background task that syncs ticker subscriptions with database.
    
    Runs periodically to detect tickers added/removed via API workers.
    This is necessary because API workers have dummy WebSocketManagers that
    don't actually connect to EODHD - they only update the database.
    
    The WebSocket worker must poll the database to discover new tickers
    and subscribe to them on the real WebSocket connection.
    
    Args:
        storage: Storage instance for database access
        ws_manager: WebSocketManager instance for subscriptions
        sync_interval: Interval in seconds between sync checks (default: 30)
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Ticker sync task started ({sync_interval}s interval)")
    
    while True:
        try:
            await asyncio.sleep(sync_interval)
            
            # Get current tickers from database
            db_tickers = set(await asyncio.to_thread(storage.get_ticker_symbols))
            
            # Get currently subscribed tickers (explicit set conversion for type safety)
            subscribed = set(ws_manager.subscribed_tickers)
            
            # Find new tickers to subscribe
            new_tickers = db_tickers - subscribed
            if new_tickers:
                logger.info(f"Ticker sync: subscribing to {len(new_tickers)} new tickers: {new_tickers}")
                await ws_manager.subscribe(new_tickers)
            
            # Find removed tickers to unsubscribe
            removed_tickers = subscribed - db_tickers
            if removed_tickers:
                logger.info(f"Ticker sync: unsubscribing from {len(removed_tickers)} removed tickers: {removed_tickers}")
                await ws_manager.unsubscribe(removed_tickers)
                
        except asyncio.CancelledError:
            logger.info("Ticker sync task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in ticker sync task: {e}")


async def reconnect_request_task(storage: Storage, ws_manager: WebSocketManager,
                                 poll_interval: int = 10):
    """
    Background task that acts on reconnect requests recorded by API workers.

    API workers hold a dummy WebSocketManager and cannot reconnect the feed
    themselves, so POST /reconnect writes a timestamp to the database and this
    task carries it out on the real connection. Only requests newer than the
    one seen at startup are acted on, so an old row cannot cause a reconnect
    loop after a restart.

    Args:
        storage: Storage instance for database access
        ws_manager: The real WebSocketManager owning the EODHD connection
        poll_interval: Seconds between checks (default: 10)
    """
    logger = logging.getLogger(__name__)

    # Treat whatever is already stored as handled — only newer requests count.
    try:
        last_seen = await asyncio.to_thread(storage.get_websocket_reconnect_request)
    except Exception as e:
        logger.error(f"Could not read initial reconnect request: {e}")
        last_seen = None

    logger.info(f"Reconnect request task started ({poll_interval}s interval)")

    while True:
        try:
            await asyncio.sleep(poll_interval)

            requested_at = await asyncio.to_thread(
                storage.get_websocket_reconnect_request
            )
            if requested_at and requested_at != last_seen:
                last_seen = requested_at
                logger.info(
                    f"Reconnect requested at {requested_at} — restarting EODHD connection"
                )
                await ws_manager.stop()
                await ws_manager.start()

        except asyncio.CancelledError:
            logger.info("Reconnect request task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in reconnect request task: {e}")


async def candle_close_task(
    candle_engine: CandleEngine,
    grace_seconds: float,
    poll_interval_seconds: float = 1.0
):
    """
    Periodically complete candles whose interval has ended.

    Without this, a candle is only completed when the next tick for that ticker
    arrives, so a bucket that has ended can sit in memory indefinitely and stays
    invisible to include_current=False readers. For a ticker that trades every
    second this is imperceptible; for one that trades every few minutes it is
    the dominant source of delay.

    close_due_candles takes the engine's threading.Lock, which tick workers
    hold, so it runs in a thread rather than blocking the event loop.
    """
    logger = logging.getLogger(__name__)
    logger.info(
        "Candle close task started (%.2fs poll, %.2fs grace)",
        poll_interval_seconds,
        grace_seconds
    )

    while True:
        try:
            await asyncio.sleep(poll_interval_seconds)
            closed = await asyncio.to_thread(
                candle_engine.close_due_candles, None, grace_seconds
            )
            if closed:
                logger.debug(
                    "Closed %d candle(s) on time: %s",
                    len(closed),
                    ", ".join(c.ticker for c in closed)
                )
        except asyncio.CancelledError:
            logger.info("Candle close task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in candle close task: {e}")


# Regular US equity session and the extended window the feed also carries.
# Weekends are excluded here; holidays need no calendar, because on a holiday
# the chain never starts and every interval fails the engine's chain test.
_FILL_SESSIONS = {
    'regular': ((9, 30), (16, 0)),
    'extended': ((4, 0), (20, 0)),
}


def _inside_fill_session(bucket_start: int, mode: str) -> bool:
    """
    Is this interval inside the configured trading session?

    Args:
        bucket_start: Interval start, Unix seconds.
        mode: 'off', 'regular' (09:30-16:00 ET) or 'extended' (04:00-20:00 ET).

    Returns:
        True when the interval starts inside the window on a weekday.
        Always False for 'off' or an unknown mode.
    """
    window = _FILL_SESSIONS.get(mode)
    if window is None:
        return False

    try:
        eastern = ZoneInfo('America/New_York')
    except Exception:
        # Without a timezone database, silence cannot be judged. Say no.
        logging.getLogger(__name__).warning(
            "Timezone database unavailable; empty-interval audit disabled"
        )
        return False

    moment = datetime.fromtimestamp(bucket_start, tz=timezone.utc).astimezone(eastern)
    if moment.weekday() >= 5:
        return False

    (start_h, start_m), (end_h, end_m) = window
    minutes = moment.hour * 60 + moment.minute
    return (start_h * 60 + start_m) <= minutes < (end_h * 60 + end_m)


async def empty_interval_audit_task(
    candle_engine: CandleEngine,
    ws_manager: WebSocketManager,
    mode: str,
    audit_path: str,
    poll_interval_seconds: float = 1.0,
    settle_seconds: float = 3.0
):
    """
    Measure, without writing, which empty intervals could be filled.

    MEASUREMENT ONLY. This task never creates a candle and never touches the
    candles table. Once per interval it asks, for each subscribed ticker: did
    this interval produce no candle, and would every precondition for writing a
    zero-volume candle have held? Each answer is appended to a newline-delimited
    JSON file so the question "should the service fill empty intervals at all?"
    can be settled with observed numbers.

    The chain is tracked here rather than in the engine, and that distinction
    matters for the count. A real fill would write a candle for an empty
    interval and so carry the chain forward into the next one, filling a run of
    silent intervals end to end. Asking the engine instead would break the
    chain after the first, because nothing was actually written -- counting one
    interval per run rather than all of them, and understating long runs
    several-fold. This task therefore keeps its own record of which interval is
    covered per ticker, advancing it both for real candles and for intervals it
    judges fillable, exactly as a real fill would.

    Feed continuity is judged by comparing two samples one interval apart: the
    interval counts only if both show a live connection with an unchanged
    connection count and the ticker subscribed throughout. A reconnect inside
    the interval disqualifies it, because the ticks it may have missed are
    indistinguishable from an interval in which nothing traded.

    Args:
        candle_engine: Engine to interrogate (read-only).
        ws_manager: Source of connection and subscription samples.
        mode: 'regular' or 'extended'. The task is not started for 'off'.
        audit_path: File to append observations to.
        poll_interval_seconds: How often to check whether an interval is due.
        settle_seconds: Delay past an interval's end before judging it, so the
            candle close task has certainly finished with it.
    """
    logger = logging.getLogger(__name__)
    logger.info(
        "Empty-interval audit started (mode=%s, writing observations to %s)",
        mode,
        audit_path
    )
    logger.info(
        "Empty-interval audit is measurement only: no candle is ever written"
    )

    try:
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("Cannot create audit directory for %s: %s", audit_path, e)
        return

    previous_sample = None
    last_audited_bucket = None
    # ticker -> the latest interval covered by a candle, real or would-be-filled
    covered: Dict[str, int] = {}

    while True:
        try:
            await asyncio.sleep(poll_interval_seconds)

            interval_seconds = candle_engine.interval_seconds
            now = time.time()
            current_bucket = int(now // interval_seconds) * interval_seconds
            finished_bucket = current_bucket - interval_seconds

            # Wait until the close task has certainly dealt with this bucket.
            if now < current_bucket + settle_seconds:
                continue
            if last_audited_bucket == finished_bucket:
                continue

            status = ws_manager.get_status()
            sample = (
                bool(status.get('connected')),
                status.get('connection_count'),
                frozenset(t.upper() for t in status.get('subscribed_tickers', []))
            )

            earlier, previous_sample = previous_sample, sample
            last_audited_bucket = finished_bucket

            if earlier is None:
                # No opening sample for this interval, so continuity is unknown.
                continue

            feed_steady = (
                earlier[0] and sample[0] and earlier[1] == sample[1]
            )
            inside_session = _inside_fill_session(finished_bucket, mode)
            previous_bucket = finished_bucket - interval_seconds

            rows = []
            for ticker in sorted(sample[2]):
                info = await asyncio.to_thread(
                    candle_engine.inspect_interval, ticker, finished_bucket
                )

                if info['state'] != 'empty':
                    # The interval had trades. Nothing to measure, but the
                    # chain moves forward.
                    covered[ticker] = finished_bucket
                    continue

                chain_intact = covered.get(ticker) == previous_bucket
                subscribed_throughout = ticker in earlier[2]
                price = info['last_close']

                if not chain_intact:
                    reason = (
                        'chain_broken' if ticker in covered
                        else 'no_previous_candle'
                    )
                elif price is None:
                    reason = 'no_known_close'
                elif not inside_session:
                    reason = 'outside_session'
                elif not feed_steady:
                    reason = 'feed_unsteady'
                elif not subscribed_throughout:
                    reason = 'subscription_changed'
                else:
                    reason = 'would_fill'

                eligible = reason == 'would_fill'
                if eligible:
                    # A real fill would have written a candle here, so the chain
                    # continues into the next interval.
                    covered[ticker] = finished_bucket

                rows.append({
                    'ticker': ticker,
                    'bucket': finished_bucket,
                    'bucket_utc': datetime.fromtimestamp(
                        finished_bucket, tz=timezone.utc
                    ).isoformat(),
                    'interval_minutes': candle_engine.interval_minutes,
                    'would_fill': eligible,
                    'reason': reason,
                    'chain_intact': chain_intact,
                    'feed_steady': feed_steady,
                    'subscribed_throughout': subscribed_throughout,
                    'inside_session': inside_session,
                    'price': price,
                    'observed_at': datetime.now(timezone.utc).isoformat(),
                })

            if not rows:
                continue

            await asyncio.to_thread(_append_audit_rows, audit_path, rows)

            would_fill = sum(1 for r in rows if r['would_fill'])
            logger.info(
                "Empty-interval audit %s: %d ticker(s) with no candle, "
                "%d would have been filled",
                datetime.fromtimestamp(finished_bucket, tz=timezone.utc).strftime(
                    '%Y-%m-%d %H:%M UTC'
                ),
                len(rows),
                would_fill
            )

        except asyncio.CancelledError:
            logger.info("Empty-interval audit task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in empty-interval audit task: {e}")


def _append_audit_rows(audit_path: str, rows: list):
    """Append observation rows as newline-delimited JSON."""
    with open(audit_path, 'a', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')


async def run_worker(config: Config):
    """Run the WebSocket worker."""
    logger = logging.getLogger(__name__)
    
    # Initialize components
    config_manager = ConfigManager(config)
    storage = create_storage(config)

    # Attach storage to log buffer for cross-process log persistence
    from .log_buffer import get_log_buffer
    get_log_buffer().set_storage(storage)
    candle_engine = CandleEngine(
        storage=storage,
        interval_minutes=config.candle_interval_minutes,
        max_candles=config.max_candles_stored,
        save_every_n_ticks=config.candle_save_every_n_ticks,
        save_every_m_seconds=config.candle_save_every_m_seconds,
        ticker_status_update_interval_seconds=config.ticker_status_update_interval_seconds,
        candle_write_queue_maxsize=config.candle_write_queue_maxsize,
        tick_max_age_seconds=config.tick_max_age_seconds
    )
    ws_manager = WebSocketManager(
        api_key=config.eodhd_api_key,
        reconnect_delay=config.ws_reconnect_delay,
        ping_interval=config.ws_ping_interval,
        data_timeout=config.ws_data_timeout,
        max_silent_timeout=config.ws_max_silent_timeout
    )
    
    # Bounded queue + fixed workers to apply backpressure under high tick volume
    tick_queue: asyncio.Queue[tuple[str, float, int, int]] = asyncio.Queue(
        maxsize=config.tick_queue_maxsize
    )
    tick_enqueued = 0
    tick_dropped = 0
    tick_processed = 0
    
    # Create async tick handler that enqueues ticks without blocking WebSocket loop
    async def async_process_tick(ticker: str, price: float, volume: int, timestamp_ms: int):
        """
        Enqueue tick for bounded, worker-based processing.
        """
        nonlocal tick_enqueued, tick_dropped

        try:
            tick_queue.put_nowait((ticker, price, volume, timestamp_ms))
            tick_enqueued += 1
        except asyncio.QueueFull:
            tick_dropped += 1
            if tick_dropped % 1000 == 0:
                logger.warning(
                    "Tick queue full - dropped %d ticks (queue size=%d, max=%d)",
                    tick_dropped,
                    tick_queue.qsize(),
                    config.tick_queue_maxsize
                )

    async def tick_worker(worker_id: int):
        """Consume ticks from queue and process in thread pool."""
        nonlocal tick_processed

        while True:
            try:
                ticker, price, volume, timestamp_ms = await tick_queue.get()
                try:
                    try:
                        await asyncio.to_thread(
                            candle_engine.process_tick,
                            ticker,
                            price,
                            volume,
                            timestamp_ms
                        )
                        tick_processed += 1
                    except Exception as e:
                        logger.error(
                            "Tick worker %d failed processing %s at %d: %s",
                            worker_id,
                            ticker,
                            timestamp_ms,
                            e
                        )
                finally:
                    tick_queue.task_done()
            except asyncio.CancelledError:
                logger.debug("Tick worker %d cancelled", worker_id)
                break

    def get_tick_metrics() -> dict:
        """Expose queue metrics via shared websocket status row."""
        tick_metrics = {
            'tick_queue_size': tick_queue.qsize(),
            'tick_queue_maxsize': config.tick_queue_maxsize,
            'tick_enqueued_count': tick_enqueued,
            'tick_processed_count': tick_processed,
            'tick_dropped_count': tick_dropped
        }
        tick_metrics.update(candle_engine.get_candle_write_metrics())
        return tick_metrics
    
    # Wire up tick processing (async to avoid blocking event loop)
    ws_manager.set_on_tick(async_process_tick, fire_and_forget=False)

    # Start fixed-size tick processing workers
    tick_worker_handles = [
        asyncio.create_task(tick_worker(i))
        for i in range(config.tick_worker_concurrency)
    ]

    logger.info(
        "Tick processing queue initialized (workers=%d, maxsize=%d)",
        config.tick_worker_concurrency,
        config.tick_queue_maxsize
    )
    
    # Load existing tickers from database
    existing_tickers = storage.get_ticker_symbols()
    
    # Add default tickers if database is empty
    if not existing_tickers:
        for ticker in config_manager.config.default_tickers:
            storage.add_ticker(ticker)
        existing_tickers = config_manager.config.default_tickers
        logger.info(f"Initialized with default tickers: {existing_tickers}")
    else:
        logger.info(f"Loaded {len(existing_tickers)} tickers from database")
    
    # Start WebSocket connection
    await ws_manager.start()
    
    # Subscribe to all tickers
    if existing_tickers:
        await ws_manager.subscribe(set(existing_tickers))
    
    # Start background cleanup task
    cleanup_task_handle = asyncio.create_task(cleanup_task(storage, candle_engine))
    
    # Start WebSocket status update task
    status_task_handle = asyncio.create_task(
        websocket_status_task(storage, ws_manager, get_tick_metrics)
    )
    
    # Start active candles update task
    active_candles_task_handle = asyncio.create_task(active_candles_task(storage, candle_engine))
    
    # Start ticker sync task (detects tickers added/removed via API workers)
    ticker_sync_task_handle = asyncio.create_task(
        ticker_sync_task(storage, ws_manager, config.ticker_sync_interval_seconds)
    )

    # Start reconnect request task (carries out /reconnect calls made on API workers)
    reconnect_request_task_handle = asyncio.create_task(
        reconnect_request_task(storage, ws_manager)
    )

    # Start ticker status flush task
    ticker_status_flush_task_handle = asyncio.create_task(
        ticker_status_flush_task(
            candle_engine,
            config.ticker_status_update_interval_seconds
        )
    )

    # Start candle write flush task
    candle_write_flush_task_handle = asyncio.create_task(
        candle_write_flush_task(candle_engine)
    )

    # Start candle close task (completes candles whose interval has ended)
    candle_close_task_handle = asyncio.create_task(
        candle_close_task(candle_engine, config.candle_close_grace_seconds)
    )

    # Start empty-interval audit, when asked for. Measurement only: it writes
    # observations to a file and never creates a candle.
    empty_interval_audit_handle = None
    if config.empty_interval_audit != 'off':
        audit_path = config.empty_interval_audit_path or str(
            Path(config.database_path).parent / 'empty_interval_audit.jsonl'
        )
        empty_interval_audit_handle = asyncio.create_task(
            empty_interval_audit_task(
                candle_engine,
                ws_manager,
                config.empty_interval_audit,
                audit_path
            )
        )
    
    logger.info("WebSocket worker running")
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # Wait for shutdown signal
    await shutdown_event.wait()
    
    # Cleanup
    logger.info("Shutting down...")
    
    # Cancel background tasks
    cleanup_task_handle.cancel()
    status_task_handle.cancel()
    active_candles_task_handle.cancel()
    ticker_sync_task_handle.cancel()
    reconnect_request_task_handle.cancel()
    ticker_status_flush_task_handle.cancel()
    candle_write_flush_task_handle.cancel()
    # Stop closing candles before the final flush, so nothing is enqueued
    # after the last write.
    candle_close_task_handle.cancel()
    if empty_interval_audit_handle is not None:
        empty_interval_audit_handle.cancel()
    for handle in tick_worker_handles:
        handle.cancel()
    
    try:
        await cleanup_task_handle
    except asyncio.CancelledError:
        pass
    
    try:
        await status_task_handle
    except asyncio.CancelledError:
        pass
    
    try:
        await active_candles_task_handle
    except asyncio.CancelledError:
        pass
    
    try:
        await ticker_sync_task_handle
    except asyncio.CancelledError:
        pass

    try:
        await reconnect_request_task_handle
    except asyncio.CancelledError:
        pass

    try:
        await ticker_status_flush_task_handle
    except asyncio.CancelledError:
        pass

    try:
        await candle_write_flush_task_handle
    except asyncio.CancelledError:
        pass

    try:
        await candle_close_task_handle
    except asyncio.CancelledError:
        pass

    if empty_interval_audit_handle is not None:
        try:
            await empty_interval_audit_handle
        except asyncio.CancelledError:
            pass

    for handle in tick_worker_handles:
        try:
            await handle
        except asyncio.CancelledError:
            pass
    
    # Flush pending status/candle writes and complete in-progress candles.
    await asyncio.to_thread(candle_engine.flush_pending_ticker_statuses)
    await asyncio.to_thread(candle_engine.flush_pending_candle_writes)
    candle_engine.complete_all_candles()
    await asyncio.to_thread(candle_engine.flush_pending_candle_writes)
    
    # Process any remaining pending cleanups before shutdown
    pending = candle_engine.get_pending_cleanup()
    if pending:
        logger.info(f"Processing {len(pending)} pending cleanups before shutdown")
        max_candles = candle_engine.max_candles
        for ticker in pending:
            try:
                # Use asyncio.to_thread for non-blocking DB operation
                await asyncio.to_thread(
                    storage.cleanup_old_candles,
                    ticker,
                    max_candles
                )
            except Exception as e:
                logger.warning(f"Shutdown cleanup failed for {ticker}: {e}")
        candle_engine.clear_pending_cleanup()
    
    # Stop WebSocket
    await ws_manager.stop()
    
    logger.info("Shutdown complete")


def main():
    """Main entry point for WebSocket worker."""
    # Load environment variables from .env file
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)

    # Load configuration
    config = Config()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    # Validate configuration
    errors = config.validate()
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("WebSocket Worker")
    logger.info("=" * 60)
    logger.info(f"Candle interval: {config.candle_interval_minutes} minutes")
    logger.info(f"Max tickers: {config.max_tickers}")
    logger.info(f"Max candles per ticker: {config.max_candles_stored}")
    logger.info(f"Database: {get_database_type()}")
    logger.info(f"Mode: WebSocket + tick processing (no HTTP server)")
    logger.info("=" * 60)

    # Run worker
    try:
        asyncio.run(run_worker(config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error in WebSocket worker: {e}", exc_info=True)


if __name__ == '__main__':
    main()
