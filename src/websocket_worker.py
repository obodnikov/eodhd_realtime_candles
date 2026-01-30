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


async def cleanup_task(storage: Storage, candle_engine: CandleEngine):
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
                
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")


async def websocket_status_task(storage: Storage, ws_manager: WebSocketManager):
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
            
            # Only update if status changed (optimization)
            # Compare all relevant fields including lists (convert to tuple for comparison)
            status_key = (
                status.get('connected'),
                tuple(sorted(status.get('subscribed_tickers', []))),  # Sort for consistent comparison
                status.get('subscribed_count'),
                tuple(sorted(status.get('pending_subscribe', []))),   # Sort for consistent comparison
                status.get('connection_count'),
                status.get('tick_count'),
                status.get('last_message')
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


async def active_candles_task(storage: Storage, candle_engine: CandleEngine):
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


async def run_worker(config: Config):
    """Run the WebSocket worker."""
    logger = logging.getLogger(__name__)
    
    # Initialize components
    config_manager = ConfigManager(config)
    storage = Storage(config.database_path)
    candle_engine = CandleEngine(
        storage=storage,
        interval_minutes=config.candle_interval_minutes,
        max_candles=config.max_candles_stored
    )
    ws_manager = WebSocketManager(
        api_key=config.eodhd_api_key,
        reconnect_delay=config.ws_reconnect_delay,
        ping_interval=config.ws_ping_interval
    )
    
    # Create async tick handler that runs DB operations in thread pool
    async def async_process_tick(ticker: str, price: float, volume: int, timestamp_ms: int):
        """Async wrapper for tick processing - runs DB ops in thread pool."""
        await asyncio.to_thread(candle_engine.process_tick, ticker, price, volume, timestamp_ms)
    
    # Wire up tick processing (async to avoid blocking event loop)
    ws_manager.set_on_tick(async_process_tick)
    
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
    status_task_handle = asyncio.create_task(websocket_status_task(storage, ws_manager))
    
    # Start active candles update task
    active_candles_task_handle = asyncio.create_task(active_candles_task(storage, candle_engine))
    
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
    
    # Complete any in-progress candles
    candle_engine.complete_all_candles()
    
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
    logger.info(f"Database: {config.database_path}")
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
