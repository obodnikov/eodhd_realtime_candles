#!/usr/bin/env python3
"""
EODHD Real-Time Candle Aggregator Service
==========================================

Main entry point that initializes all components and starts the service.

Usage:
    python -m src.main
    
    # Or with docker-compose
    docker-compose up
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from .config import Config, ConfigManager
from .storage import Storage
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
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)


async def create_app(config: Config) -> web.Application:
    """Create and configure the aiohttp application."""
    
    # Create middleware list
    middlewares = [error_middleware, logging_middleware]
    
    # Add auth middleware if API key is configured
    if config.api_key:
        middlewares.insert(0, create_auth_middleware(config.api_key))
        logging.getLogger(__name__).info("API key authentication enabled")
    else:
        logging.getLogger(__name__).warning("API key authentication DISABLED - not recommended for production")
    
    app = web.Application(middlewares=middlewares)
    
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
    
    # Wire up tick processing
    ws_manager.set_on_tick(candle_engine.process_tick)
    
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
    
    storage: Storage = app['storage']
    ws_manager: WebSocketManager = app['ws_manager']
    config_manager: ConfigManager = app['config_manager']
    
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
    
    logger.info("Service started successfully")


async def on_shutdown(app: web.Application):
    """Cleanup on shutdown."""
    logger = logging.getLogger(__name__)
    
    ws_manager: WebSocketManager = app['ws_manager']
    candle_engine: CandleEngine = app['candle_engine']
    
    logger.info("Shutting down...")
    
    # Complete any in-progress candles
    candle_engine.complete_all_candles()
    
    # Stop WebSocket
    await ws_manager.stop()
    
    logger.info("Shutdown complete")


def main():
    """Main entry point."""
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
    logger.info("EODHD Real-Time Candle Aggregator")
    logger.info("=" * 60)
    logger.info(f"Candle interval: {config.candle_interval_minutes} minutes")
    logger.info(f"Max tickers: {config.max_tickers}")
    logger.info(f"Max candles per ticker: {config.max_candles_stored}")
    logger.info(f"Database: {config.database_path}")
    logger.info(f"HTTP server: {config.http_host}:{config.http_port}")
    logger.info("=" * 60)

    # Create application factory for web.run_app
    async def create_app_wrapper():
        """Create and configure the application."""
        logger.info("Creating application...")
        app = await create_app(config)
        logger.info("Application created successfully")

        # Log after app is created but before server starts
        logger.info("")
        logger.info("API Endpoints:")
        logger.info("  GET  /health              - Health check")
        logger.info("  GET  /status              - Detailed status")
        logger.info("  GET  /config              - Get configuration")
        logger.info("  PATCH /config             - Update configuration")
        logger.info("  POST /config/reset        - Reset to defaults")
        logger.info("  GET  /tickers             - List tracked tickers")
        logger.info("  POST /tickers             - Add tickers")
        logger.info("  DELETE /tickers/{ticker}  - Remove ticker")
        logger.info("  GET  /candles/{ticker}    - Get candles")
        logger.info("  POST /candles/multi       - Get multiple tickers")
        logger.info("")

        return app

    # Run using aiohttp's web.run_app()
    # This properly manages the event loop, signal handling, and graceful shutdown
    try:
        logger.info("Starting web.run_app()...")
        logger.info(f"Will bind to {config.http_host}:{config.http_port}")
        web.run_app(
            create_app_wrapper(),
            host=config.http_host,
            port=config.http_port,
            handle_signals=True,
            access_log=logger,  # Enable access logging
            print=lambda msg: logger.info(f"aiohttp: {msg}") if msg else None
        )
        logger.info("web.run_app() exited")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error in web.run_app(): {e}", exc_info=True)


if __name__ == '__main__':
    main()
