"""
API integration tests for ticker endpoints.

Tests the GET /tickers/{ticker} endpoint including authentication
and data retrieval.
"""

import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.storage import Storage
from src.config import Config, ConfigManager
from src.api.routes import APIRoutes
from src.api.middleware import create_auth_middleware


class TestGetSingleTickerEndpoint(AioHTTPTestCase):
    """Test cases for GET /tickers/{ticker} endpoint."""

    async def get_application(self):
        """Create test application."""
        # Create temporary database
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()

        # Create test app
        app = web.Application()

        # Create components
        config = Config()
        config.api_key = 'test_api_key'
        config.database_path = self.temp_db.name

        self.storage = Storage(config.database_path)
        config.persist_config = False
        config_manager = ConfigManager(config)

        # Mock candle engine and websocket manager
        self.candle_engine = type('MockCandleEngine', (), {
            'get_active_tickers': lambda: [],
            'remove_ticker': lambda ticker: None,
            'set_interval': lambda interval: None,
            'set_max_candles': lambda max: None,
            'get_current_candle': lambda ticker: None,
            'get_active_tickers_summary': lambda: []
        })()

        self.ws_manager = type('MockWebSocketManager', (), {
            'get_status': lambda: {'connected': False},
            'subscribe': lambda tickers: None,
            'unsubscribe': lambda tickers: None,
            'clear_subscriptions': lambda: None,
            'start': lambda: None,
            'stop': lambda: None
        })()

        # Store in app context
        app['config_manager'] = config_manager
        app['storage'] = self.storage
        app['candle_engine'] = self.candle_engine
        app['ws_manager'] = self.ws_manager

        # Setup middleware and routes
        app.middlewares.append(create_auth_middleware(config.api_key))
        APIRoutes(app)

        return app

    async def asyncTearDown(self):
        """Clean up after tests."""
        await super().asyncTearDown()
        try:
            os.unlink(self.temp_db.name)
        except Exception:
            pass

    @unittest_run_loop
    async def test_get_single_ticker_success(self):
        """Test successful retrieval of existing ticker."""
        # Add a ticker to the database
        self.storage.add_ticker('AAPL')

        # Make request
        resp = await self.client.request(
            'GET',
            '/tickers/AAPL',
            headers={'X-API-Key': 'test_api_key'}
        )

        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertIn('ticker', data)
        self.assertIn('timestamp', data)
        self.assertEqual(data['ticker']['symbol'], 'AAPL')
        self.assertEqual(data['ticker']['status'], 'active')
        self.assertEqual(data['ticker']['candle_count'], 0)

    @unittest_run_loop
    async def test_get_single_ticker_case_insensitive(self):
        """Test ticker lookup is case-insensitive."""
        # Add a ticker to the database
        self.storage.add_ticker('MSFT')

        # Make request with lowercase ticker
        resp = await self.client.request(
            'GET',
            '/tickers/msft',
            headers={'X-API-Key': 'test_api_key'}
        )

        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['ticker']['symbol'], 'MSFT')

    @unittest_run_loop
    async def test_get_single_ticker_not_found(self):
        """Test 404 response for non-existent ticker."""
        # Make request for ticker that doesn't exist
        resp = await self.client.request(
            'GET',
            '/tickers/NONEXIST',
            headers={'X-API-Key': 'test_api_key'}
        )

        self.assertEqual(resp.status, 404)

        data = await resp.json()
        self.assertIn('error', data)
        self.assertIn('Ticker not found', data['error'])

    @unittest_run_loop
    async def test_get_single_ticker_missing_auth(self):
        """Test 401 response when API key is missing."""
        # Add a ticker
        self.storage.add_ticker('GOOGL')

        # Make request without API key
        resp = await self.client.request(
            'GET',
            '/tickers/GOOGL'
        )

        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_get_single_ticker_invalid_auth(self):
        """Test 401 response with invalid API key."""
        # Add a ticker
        self.storage.add_ticker('TSLA')

        # Make request with wrong API key
        resp = await self.client.request(
            'GET',
            '/tickers/TSLA',
            headers={'X-API-Key': 'wrong_key'}
        )

        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_get_single_ticker_with_metadata(self):
        """Test ticker response includes all metadata fields."""
        # Add a ticker
        self.storage.add_ticker('NVDA')

        # Update ticker status with price info
        self.storage.update_ticker_status(
            'NVDA',
            'active',
            last_tick_at='2026-01-25T10:00:00Z',
            last_price=500.50
        )

        # Make request
        resp = await self.client.request(
            'GET',
            '/tickers/NVDA',
            headers={'X-API-Key': 'test_api_key'}
        )

        self.assertEqual(resp.status, 200)

        data = await resp.json()
        ticker = data['ticker']

        # Verify all metadata fields are present
        self.assertEqual(ticker['symbol'], 'NVDA')
        self.assertEqual(ticker['status'], 'active')
        self.assertEqual(ticker['last_price'], 500.50)
        self.assertEqual(ticker['last_tick_at'], '2026-01-25T10:00:00Z')
        self.assertIn('added_at', ticker)
        self.assertIn('candle_count', ticker)
        self.assertIn('last_candle_request_at', ticker)


if __name__ == '__main__':
    unittest.main()
