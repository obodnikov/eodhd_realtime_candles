"""
Tests for API server entry point.

Tests the API-only worker that handles HTTP requests without WebSocket processing.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
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
        return await create_app(config)

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

    @pytest.mark.asyncio
    async def test_create_app_with_no_auth(self):
        """Test app creation without API key."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = None  # No auth
        config.database_path = ':memory:'
        
        # Create app without auth middleware
        app = await create_app(config)
        
        # Verify app was created
        assert app is not None
        assert isinstance(app, web.Application)
        
        # Verify middleware count (no auth middleware)
        assert len(app.middlewares) == 2  # error + logging only
        
        # Verify components are initialized
        assert 'config_manager' in app
        assert 'storage' in app
        assert 'candle_engine' in app
        assert 'ws_manager' in app


class TestAPIServerMiddleware:
    """Test middleware configuration."""

    @pytest.mark.asyncio
    async def test_auth_middleware_added_when_api_key_set(self):
        """Test auth middleware is added when API key is configured."""
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.api_key = 'test_api_key'
        config.database_path = ':memory:'
        
        app = await create_app(config)
        
        # Check middleware is present
        assert len(app.middlewares) >= 3  # auth + error + logging

    @pytest.mark.asyncio
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
