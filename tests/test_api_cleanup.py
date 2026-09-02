"""
API integration tests for cleanup endpoint.

Tests the POST /candles/cleanup endpoint including authentication,
authorization, and data consistency.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.storage import Storage, Candle
from src.config import Config, ConfigManager
from src.api.routes import APIRoutes
from src.api.middleware import create_auth_middleware
from src.candle_engine import CandleEngine
from src.websocket_manager import WebSocketManager


class TestCleanupAPIEndpoint(AioHTTPTestCase):
    """Test cases for POST /candles/cleanup endpoint."""

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
            'get_current_candle': lambda ticker: None
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

    def _create_test_candle(self, ticker: str, timestamp: int = 1700000000) -> Candle:
        """Helper to create a test candle."""
        return Candle(
            ticker=ticker,
            timestamp=timestamp,
            datetime_utc=datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=10000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )

    @unittest_run_loop
    async def test_cleanup_requires_authentication(self):
        """Test that cleanup endpoint requires authentication."""
        resp = await self.client.post('/candles/cleanup')
        self.assertEqual(resp.status, 401)

        data = await resp.json()
        self.assertIn('error', data)

    @unittest_run_loop
    async def test_cleanup_success_no_orphans(self):
        """Test cleanup succeeds when there are no orphans."""
        # Add tracked ticker with candles
        self.storage.add_ticker('AAPL')
        candle = self._create_test_candle('AAPL')
        self.storage.save_candle(candle)

        # Call cleanup endpoint
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['deleted_count'], 0)
        self.assertIn('message', data)
        self.assertIn('timestamp', data)

        # Verify candle still exists
        candles = self.storage.get_candles('AAPL', count=10)
        self.assertEqual(len(candles), 1)

    @unittest_run_loop
    async def test_cleanup_success_with_orphans(self):
        """Test cleanup successfully removes orphaned candles."""
        # Add tracked ticker
        self.storage.add_ticker('AAPL')
        candle1 = self._create_test_candle('AAPL', 1700000000)
        self.storage.save_candle(candle1)

        # Add orphaned candles
        for ticker in ['ORPHAN1', 'ORPHAN2']:
            for i in range(3):
                candle = self._create_test_candle(ticker, 1700000000 + (i * 300))
                self.storage.save_candle(candle)

        # Verify before cleanup
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 7)  # 1 + 6 orphans

        # Call cleanup endpoint
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['deleted_count'], 6)

        # Verify after cleanup
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 1)
        self.assertIn('AAPL', stats['candles_per_ticker'])
        self.assertNotIn('ORPHAN1', stats['candles_per_ticker'])
        self.assertNotIn('ORPHAN2', stats['candles_per_ticker'])

    @unittest_run_loop
    async def test_cleanup_with_bearer_token(self):
        """Test cleanup works with Bearer token authentication."""
        # Add orphaned candle
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # Call with Bearer token
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'Authorization': 'Bearer test_api_key'}
        )
        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['deleted_count'], 1)

    @unittest_run_loop
    async def test_cleanup_with_query_param(self):
        """Test cleanup works with query parameter authentication."""
        # Add orphaned candle
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # Call with query param
        resp = await self.client.post('/candles/cleanup?api_key=test_api_key')
        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['deleted_count'], 1)

    @unittest_run_loop
    async def test_cleanup_invalid_api_key(self):
        """Test cleanup rejects invalid API key."""
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'invalid_key'}
        )
        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_cleanup_empty_database(self):
        """Test cleanup on empty database."""
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        self.assertEqual(resp.status, 200)

        data = await resp.json()
        self.assertEqual(data['deleted_count'], 0)

    @unittest_run_loop
    async def test_cleanup_idempotent(self):
        """Test that cleanup can be called multiple times safely."""
        # Add orphaned candles
        candle = self._create_test_candle('ORPHAN')
        self.storage.save_candle(candle)

        # First cleanup
        resp1 = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        data1 = await resp1.json()
        self.assertEqual(data1['deleted_count'], 1)

        # Second cleanup - should delete nothing
        resp2 = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        data2 = await resp2.json()
        self.assertEqual(data2['deleted_count'], 0)

        # Third cleanup - should still delete nothing
        resp3 = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        data3 = await resp3.json()
        self.assertEqual(data3['deleted_count'], 0)

    @unittest_run_loop
    async def test_cleanup_response_format(self):
        """Test that cleanup response has correct format."""
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        self.assertEqual(resp.status, 200)

        data = await resp.json()

        # Check required fields
        self.assertIn('message', data)
        self.assertIn('deleted_count', data)
        self.assertIn('timestamp', data)

        # Check types
        self.assertIsInstance(data['message'], str)
        self.assertIsInstance(data['deleted_count'], int)
        self.assertIsInstance(data['timestamp'], str)

        # Validate timestamp format (ISO 8601)
        try:
            datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        except ValueError:
            self.fail("Invalid timestamp format")


class TestCleanupAPIIntegration(AioHTTPTestCase):
    """Integration tests for cleanup with other endpoints."""

    async def get_application(self):
        """Create test application."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()

        app = web.Application()

        config = Config()
        config.api_key = 'test_api_key'
        config.database_path = self.temp_db.name
        config.allow_delete_all_tickers = True  # Enable for testing

        self.storage = Storage(config.database_path)
        config.persist_config = False
        config_manager = ConfigManager(config)

        self.candle_engine = type('MockCandleEngine', (), {
            'get_active_tickers': lambda: [],
            'remove_ticker': lambda ticker: None,
            'set_interval': lambda interval: None,
            'set_max_candles': lambda max: None,
            'get_current_candle': lambda ticker: None
        })()

        self.ws_manager = type('MockWebSocketManager', (), {
            'get_status': lambda: {'connected': False},
            'subscribe': lambda tickers: None,
            'unsubscribe': lambda tickers: None,
            'clear_subscriptions': lambda: None,
            'start': lambda: None,
            'stop': lambda: None
        })()

        app['config_manager'] = config_manager
        app['storage'] = self.storage
        app['candle_engine'] = self.candle_engine
        app['ws_manager'] = self.ws_manager

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
    async def test_cleanup_after_delete_all_tickers(self):
        """Test that cleanup finds no orphans after DELETE /tickers?confirm=true."""
        # Add tickers
        await self.client.post(
            '/tickers',
            json={'tickers': ['AAPL', 'MSFT']},
            headers={'X-API-Key': 'test_api_key'}
        )

        # Add candles manually
        for ticker in ['AAPL', 'MSFT']:
            candle = Candle(
                ticker=ticker,
                timestamp=1700000000,
                datetime_utc=datetime.fromtimestamp(1700000000, tz=timezone.utc).isoformat(),
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=10000,
                tick_count=50,
                is_complete=True,
                interval_minutes=5
            )
            self.storage.save_candle(candle)

        # Delete all tickers
        await self.client.delete(
            '/tickers?confirm=true',
            headers={'X-API-Key': 'test_api_key'}
        )

        # Run cleanup - should find no orphans
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )
        data = await resp.json()
        self.assertEqual(data['deleted_count'], 0)


class TestCleanupAPIEdgeCases(AioHTTPTestCase):
    """Edge case and error scenario tests for cleanup endpoint."""

    async def get_application(self):
        """Create test application."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()

        app = web.Application()

        config = Config()
        config.api_key = 'test_api_key'
        config.database_path = self.temp_db.name

        self.storage = Storage(config.database_path)
        config.persist_config = False
        config_manager = ConfigManager(config)

        self.candle_engine = type('MockCandleEngine', (), {
            'get_active_tickers': lambda: [],
            'remove_ticker': lambda ticker: None,
            'set_interval': lambda interval: None,
            'set_max_candles': lambda max: None,
            'get_current_candle': lambda ticker: None
        })()

        self.ws_manager = type('MockWebSocketManager', (), {
            'get_status': lambda: {'connected': False},
            'subscribe': lambda tickers: None,
            'unsubscribe': lambda tickers: None,
            'clear_subscriptions': lambda: None,
            'start': lambda: None,
            'stop': lambda: None
        })()

        app['config_manager'] = config_manager
        app['storage'] = self.storage
        app['candle_engine'] = self.candle_engine
        app['ws_manager'] = self.ws_manager

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
    async def test_cleanup_with_database_locked(self):
        """Test cleanup behavior when database is locked."""
        import sqlite3
        import threading
        import time

        # Add orphaned candle
        candle = Candle(
            ticker='ORPHAN',
            timestamp=1700000000,
            datetime_utc=datetime.fromtimestamp(1700000000, tz=timezone.utc).isoformat(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=10000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )
        self.storage.save_candle(candle)

        # Create a long-running transaction to lock the database
        def lock_database():
            conn = sqlite3.connect(self.temp_db.name, timeout=1.0)
            cursor = conn.cursor()
            cursor.execute('BEGIN EXCLUSIVE')
            time.sleep(2)  # Hold lock for 2 seconds
            conn.rollback()
            conn.close()

        # Start locking thread
        thread = threading.Thread(target=lock_database)
        thread.start()

        time.sleep(0.1)  # Let lock acquire

        # Try cleanup - should handle busy database gracefully
        # The cleanup has a 5 second busy_timeout, so it should wait
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )

        thread.join()

        # Should either succeed (waited for lock) or return 500 (timeout)
        self.assertIn(resp.status, [200, 500])

    @unittest_run_loop
    async def test_cleanup_with_corrupted_candles_table(self):
        """Test cleanup with invalid data in candles table."""
        # Manually insert invalid candle data
        conn = self.storage._get_connection()
        cursor = conn.cursor()

        # Insert candle with empty ticker (should be prevented by schema, but testing edge case)
        try:
            cursor.execute('''
                INSERT INTO candles (ticker, timestamp, datetime_utc, open, high, low, close,
                                    volume, tick_count, is_complete, interval_minutes)
                VALUES ('', 1700000000, '2023-11-15T00:00:00', 100, 105, 95, 102, 10000, 50, 1, 5)
            ''')
            conn.commit()
        except Exception:
            # If constraint prevents this, that's actually good
            pass

        # Cleanup should handle this gracefully
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )

        # Should succeed (empty ticker will be considered orphan)
        self.assertEqual(resp.status, 200)

    @unittest_run_loop
    async def test_cleanup_returns_500_on_storage_error(self):
        """Test that cleanup returns 500 if storage layer fails."""
        # Close the database connection to simulate storage failure
        import sqlite3

        # Add a candle first
        candle = Candle(
            ticker='ORPHAN',
            timestamp=1700000000,
            datetime_utc=datetime.fromtimestamp(1700000000, tz=timezone.utc).isoformat(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=10000,
            tick_count=50,
            is_complete=True,
            interval_minutes=5
        )
        self.storage.save_candle(candle)

        # Corrupt the database file
        conn = self.storage._get_connection()
        conn.close()

        # Delete the database file to cause an error
        try:
            os.unlink(self.temp_db.name)
        except:
            pass

        # Cleanup should fail gracefully
        resp = await self.client.post(
            '/candles/cleanup',
            headers={'X-API-Key': 'test_api_key'}
        )

        # Should return 500 server error
        self.assertEqual(resp.status, 500)

    @unittest_run_loop
    async def test_cleanup_concurrent_requests(self):
        """Test multiple concurrent cleanup requests."""
        import asyncio

        # Add orphaned candles
        for i in range(10):
            candle = Candle(
                ticker=f'ORPHAN{i}',
                timestamp=1700000000 + i,
                datetime_utc=datetime.fromtimestamp(1700000000 + i, tz=timezone.utc).isoformat(),
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=10000,
                tick_count=50,
                is_complete=True,
                interval_minutes=5
            )
            self.storage.save_candle(candle)

        # Send multiple concurrent cleanup requests
        tasks = []
        for _ in range(5):
            task = self.client.post(
                '/candles/cleanup',
                headers={'X-API-Key': 'test_api_key'}
            )
            tasks.append(task)

        responses = await asyncio.gather(*tasks)

        # All should succeed
        for resp in responses:
            self.assertEqual(resp.status, 200)

        # Verify database is clean
        stats = self.storage.get_stats()
        self.assertEqual(stats['total_candles'], 0)


if __name__ == '__main__':
    unittest.main()
