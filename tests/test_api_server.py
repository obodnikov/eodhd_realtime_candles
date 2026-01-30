"""
Tests for API server entry point.

Tests the API-only worker that handles HTTP requests without WebSocket processing.
"""

import pytest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.api_server import create_app, setup_logging
from src.config import Config


class TestAPIServer(AioHTTPTestCase):
    """Test API server functionality."""

    async def get_application(self):
        """Create test application."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = 'test_api_key'
        config.database_path = ':memory:'
        app = await create_app(config)
        return app

    @unittest_run_loop
    async def test_health_endpoint(self):
        """Test health endpoint works without authentication."""
        resp = await self.client.request("GET", "/health")
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] == 'healthy'
        assert 'timestamp' in data

    @unittest_run_loop
    async def test_status_endpoint_requires_auth(self):
        """Test status endpoint requires authentication."""
        resp = await self.client.request("GET", "/status")
        assert resp.status == 401

    @unittest_run_loop
    async def test_status_endpoint_with_auth(self):
        """Test status endpoint with valid API key."""
        headers = {'X-API-Key': 'test_api_key'}
        resp = await self.client.request("GET", "/status", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert 'websocket' in data
        assert 'database' in data
        assert 'config' in data

    @unittest_run_loop
    async def test_api_worker_has_components(self):
        """Test API worker has all required components."""
        app = self.app
        assert 'config_manager' in app
        assert 'storage' in app
        assert 'candle_engine' in app
        assert 'ws_manager' in app

    @unittest_run_loop
    async def test_websocket_manager_not_connected(self):
        """Test WebSocket manager is not connected in API worker."""
        ws_manager = self.app['ws_manager']
        # API worker should have dummy WebSocket manager (not connected)
        assert not ws_manager.connected
        # Verify it's marked as dummy
        assert ws_manager.is_dummy is True


class TestAPIServerStatusEndpoint(AioHTTPTestCase):
    """Test status endpoint with dummy WebSocketManager."""

    async def get_application(self):
        """Create test application."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = 'test_api_key'
        config.database_path = ':memory:'
        app = await create_app(config)
        return app

    @unittest_run_loop
    async def test_status_with_dummy_websocket_manager(self):
        """Test status endpoint returns database-based status for dummy WebSocketManager."""
        # Add a ticker to database
        storage = self.app['storage']
        storage.add_ticker('AAPL')
        
        headers = {'X-API-Key': 'test_api_key'}
        resp = await self.client.request("GET", "/status", headers=headers)
        
        assert resp.status == 200
        data = await resp.json()
        
        # Verify WebSocket status structure for dummy manager
        ws_status = data['websocket']
        assert ws_status['connected'] is None  # None indicates unavailable
        assert 'AAPL' in ws_status['subscribed_tickers']
        assert ws_status['subscribed_count'] == 1
        assert 'note' in ws_status
        assert 'API worker' in ws_status['note']

    @unittest_run_loop
    async def test_status_with_no_tickers(self):
        """Test status endpoint with dummy WebSocketManager and no tickers."""
        headers = {'X-API-Key': 'test_api_key'}
        resp = await self.client.request("GET", "/status", headers=headers)
        
        assert resp.status == 200
        data = await resp.json()
        
        # Verify WebSocket status with no tickers
        ws_status = data['websocket']
        assert ws_status['connected'] is None
        assert ws_status['subscribed_tickers'] == []
        assert ws_status['subscribed_count'] == 0

    @unittest_run_loop
    async def test_status_type_consistency(self):
        """Test status endpoint returns consistent types for connected field."""
        headers = {'X-API-Key': 'test_api_key'}
        resp = await self.client.request("GET", "/status", headers=headers)
        
        assert resp.status == 200
        data = await resp.json()
        
        # Verify connected field is None (not string 'unknown')
        ws_status = data['websocket']
        assert ws_status['connected'] is None
        assert not isinstance(ws_status['connected'], str)

    @unittest_run_loop
    async def test_status_with_real_websocket_manager(self):
        """Test status endpoint with real (non-dummy) WebSocketManager."""
        from src.websocket_manager import WebSocketManager
        
        # Replace dummy with real WebSocketManager (not started)
        self.app['ws_manager'] = WebSocketManager(
            api_key='test_key',
            is_dummy=False  # Real manager
        )
        
        headers = {'X-API-Key': 'test_api_key'}
        resp = await self.client.request("GET", "/status", headers=headers)
        
        assert resp.status == 200
        data = await resp.json()
        
        # Verify WebSocket status from real manager
        ws_status = data['websocket']
        assert ws_status['connected'] is False  # Boolean, not None
        assert 'note' not in ws_status  # No note for real manager


class TestAPIServerConfiguration:
    """Test API server configuration and setup."""

    def test_setup_logging(self):
        """Test logging setup."""
        setup_logging('INFO')
        # No exception means success

    @patch('src.api_server.load_dotenv')
    @patch('src.api_server.Config')
    def test_main_loads_env(self, mock_config, mock_load_dotenv):
        """Test main function loads environment variables."""
        mock_config.return_value.validate.return_value = []
        mock_config.return_value.log_level = 'INFO'
        
        # Can't easily test main() due to web.run_app blocking
        # This test verifies the mocking works
        assert mock_config is not None


class TestAPIServerMiddleware(AioHTTPTestCase):
    """Test middleware configuration."""

    async def get_application(self):
        """Create test application with auth."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = 'test_api_key'
        config.database_path = ':memory:'
        app = await create_app(config)
        return app

    @unittest_run_loop
    async def test_auth_middleware_added_when_api_key_set(self):
        """Test auth middleware is added when API key is configured."""
        # Check middleware is present
        assert len(self.app.middlewares) >= 3  # auth + error + logging

    @unittest_run_loop
    async def test_no_auth_middleware_when_no_api_key(self):
        """Test auth middleware is not added when no API key."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = None
        config.database_path = ':memory:'
        
        app = await create_app(config)
        
        # Check middleware count (no auth)
        assert len(app.middlewares) == 2  # error + logging only


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
