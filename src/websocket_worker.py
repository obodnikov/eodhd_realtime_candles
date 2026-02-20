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
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

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
                status.get('candle_write_dropped_count')
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


async def run_worker(config: Config):
    """Run the WebSocket worker."""
    logger = logging.getLogger(__name__)
    
    # Initialize components
    config_manager = ConfigManager(config)
    storage = create_storage(config)
    candle_engine = CandleEngine(
        storage=storage,
        interval_minutes=config.candle_interval_minutes,
        max_candles=config.max_candles_stored,
        save_every_n_ticks=config.candle_save_every_n_ticks,
        save_every_m_seconds=config.candle_save_every_m_seconds,
        ticker_status_update_interval_seconds=config.ticker_status_update_interval_seconds,
        candle_write_queue_maxsize=config.candle_write_queue_maxsize
    )
    ws_manager = WebSocketManager(
        api_key=config.eodhd_api_key,
        reconnect_delay=config.ws_reconnect_delay,
        ping_interval=config.ws_ping_interval
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
    ticker_status_flush_task_handle.cancel()
    candle_write_flush_task_handle.cancel()
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
        await ticker_status_flush_task_handle
    except asyncio.CancelledError:
        pass

    try:
        await candle_write_flush_task_handle
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
