#!/usr/bin/env python3
"""
API Server Entry Point
======================

HTTP API server that handles REST requests.
Runs multiple instances for load balancing (configured in supervisord).

This worker:
- Handles HTTP API requests (GET/POST/DELETE)
- Reads from database (candles, tickers, status)
- Writes ticker add/remove operations
- Does NOT process WebSocket ticks (that's websocket_worker.py)

Usage:
    python -m src.api_server
"""

import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from .config import Config, ConfigManager
from .storage import Storage
from .storage_factory import create_storage, get_database_type
from .candle_engine import CandleEngine
from .websocket_manager import WebSocketManager
from .api import APIRoutes, create_auth_middleware, error_middleware, logging_middleware


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
    logging.getLogger('aiohttp').setLevel(logging.WARNING)


async def create_app(config: Config) -> web.Application:
    """Create and configure the API application."""
    
    # Create middleware list
    middlewares = [error_middleware, logging_middleware]
    
    # Add auth middleware if API key is configured
    if config.api_key:
        middlewares.insert(0, create_auth_middleware(config.api_key))
        logging.getLogger(__name__).info("API key authentication enabled")
    else:
        logging.getLogger(__name__).warning("API key authentication DISABLED")
    
    app = web.Application(middlewares=middlewares)
    
    # Initialize components (read-only mode for API workers)
    config_manager = ConfigManager(config)
    storage = create_storage(config)
    
    # CandleEngine in read-only mode (no tick processing)
    candle_engine = CandleEngine(
        storage=storage,
        interval_minutes=config.candle_interval_minutes,
        max_candles=config.max_candles_stored,
        save_every_n_ticks=config.candle_save_every_n_ticks,
        save_every_m_seconds=config.candle_save_every_m_seconds
    )
    
    # Create a dummy WebSocketManager for API compatibility
    # API workers don't actually connect to WebSocket
    ws_manager = WebSocketManager(
        api_key=config.eodhd_api_key,
        reconnect_delay=config.ws_reconnect_delay,
        ping_interval=config.ws_ping_interval,
        is_dummy=True  # Mark as dummy to distinguish from real WebSocket worker
    )
    
    # Store in app context
    app['config_manager'] = config_manager
    app['storage'] = storage
    app['candle_engine'] = candle_engine
    app['ws_manager'] = ws_manager
    
    # Setup routes
    APIRoutes(app)
    
    # Setup startup/shutdown hooks
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    return app


async def on_startup(app: web.Application):
    """Initialize services on startup."""
    logger = logging.getLogger(__name__)
    logger.info("API worker started (read-only mode)")
    logger.info("WebSocket processing handled by websocket_worker")


async def on_shutdown(app: web.Application):
    """Cleanup on shutdown."""
    logger = logging.getLogger(__name__)
    logger.info("API worker shutting down...")


def main():
    """Main entry point for API server."""
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
    logger.info("API Server Worker")
    logger.info("=" * 60)
    logger.info(f"HTTP server: {config.http_host}:{config.http_port}")
    logger.info(f"Database: {get_database_type()}")
    logger.info(f"Mode: Read-mostly (no WebSocket processing)")
    logger.info("=" * 60)

    # Create application
    async def create_app_wrapper():
        logger.info("Creating API application...")
        app = await create_app(config)
        logger.info("API application ready")
        return app

    # Run server
    try:
        web.run_app(
            create_app_wrapper(),
            host=config.http_host,
            port=config.http_port,
            handle_signals=True,
            access_log=logger,
            print=lambda msg: logger.info(f"aiohttp: {msg}") if msg else None
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error in API server: {e}", exc_info=True)


if __name__ == '__main__':
    main()
